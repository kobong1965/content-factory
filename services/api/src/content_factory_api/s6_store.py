"""Persistent S6 material profiles, revisions, matches, and usage audit."""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from content_factory_contracts import validate_or_raise

from .s6_capture import normalize_material_capture_metadata


class MaterialNotFoundError(LookupError):
    pass


class MaterialConflictError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class MaterialStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS materials (
                    material_id TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    media_result_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(product_id, sha256)
                );
                CREATE INDEX IF NOT EXISTS idx_materials_product ON materials(product_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS material_revisions (
                    material_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(material_id, revision)
                );
                CREATE TABLE IF NOT EXISTS material_matches (
                    script_id TEXT NOT NULL,
                    script_shot_id TEXT NOT NULL,
                    material_id TEXT NOT NULL,
                    clip_id TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    repeat_risk TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(script_id, script_shot_id)
                );
                DROP INDEX IF EXISTS idx_match_unique_clip;
                CREATE INDEX IF NOT EXISTS idx_match_clip
                    ON material_matches(script_id, material_id, clip_id);
                CREATE TABLE IF NOT EXISTS material_usage_events (
                    event_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    material_id TEXT NOT NULL,
                    clip_id TEXT NOT NULL,
                    script_id TEXT NOT NULL,
                    script_shot_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_usage_clip
                    ON material_usage_events(material_id, clip_id, created_at DESC);
                """
            )

    @staticmethod
    def _decode(value: str) -> dict[str, Any]:
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise ValueError("素材档案格式错误")
        return dict(normalize_material_capture_metadata(payload))

    def create(self, profile: Mapping[str, Any], *, media_result_path: str | Path, actor: str) -> tuple[dict[str, Any], bool]:
        payload = copy.deepcopy(dict(profile))
        normalize_material_capture_metadata(payload)
        validate_or_raise("material", payload)
        result_path = str(Path(media_result_path).expanduser().resolve())
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT profile_json FROM materials WHERE product_id=? AND sha256=?",
                (payload["product_id"], payload["file"]["sha256"]),
            ).fetchone()
            if existing is not None:
                return self._decode(existing["profile_json"]), True
            connection.execute(
                "INSERT INTO materials VALUES (?, ?, ?, ?, ?, ?, ?)",
                (payload["material_id"], payload["product_id"], payload["file"]["sha256"], encoded,
                 result_path, payload["created_at"], payload["updated_at"]),
            )
            connection.execute(
                "INSERT INTO material_revisions VALUES (?, ?, 'imported', ?, ?, ?)",
                (payload["material_id"], payload["revision"], actor, encoded, payload["updated_at"]),
            )
        return payload, False

    def get(self, material_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT profile_json FROM materials WHERE material_id=?", (material_id,)).fetchone()
        if row is None:
            raise MaterialNotFoundError("找不到这个素材")
        return self._decode(row["profile_json"])

    def media_result_path(self, material_id: str) -> Path:
        with self._connect() as connection:
            row = connection.execute("SELECT media_result_path FROM materials WHERE material_id=?", (material_id,)).fetchone()
        if row is None:
            raise MaterialNotFoundError("找不到这个素材")
        return Path(row["media_result_path"]).resolve()

    def list(self, *, product_id: str | None = None, query: str = "", purpose: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT profile_json FROM materials"
        params: list[Any] = []
        if product_id:
            sql += " WHERE product_id=?"
            params.append(product_id)
        sql += " ORDER BY updated_at DESC"
        clean_query = query.strip().casefold()
        results: list[dict[str, Any]] = []
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        for row in rows:
            profile = self._decode(row["profile_json"])
            search = json.dumps(profile, ensure_ascii=False).casefold()
            if clean_query and clean_query not in search:
                continue
            if purpose and not any(purpose in clip.get("purpose_tags", []) for clip in profile["clips"]):
                continue
            results.append(profile)
        return results

    def update(
        self, material_id: str, *, expected_revision: int, archive: Mapping[str, Any],
        clips: list[Mapping[str, Any]], actor: str,
    ) -> dict[str, Any]:
        clean_actor = actor.strip()
        if not clean_actor:
            raise ValueError("请填写修改人")
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT profile_json FROM materials WHERE material_id=?", (material_id,)).fetchone()
            if row is None:
                raise MaterialNotFoundError("找不到这个素材")
            current = self._decode(row["profile_json"])
            if current["revision"] != expected_revision:
                raise MaterialConflictError("素材已被其他人修改，请刷新后再保存")
            updated = copy.deepcopy(current)
            updated["archive"] = copy.deepcopy(dict(archive))
            updated["clips"] = copy.deepcopy([dict(item) for item in clips])
            updated["revision"] += 1
            updated["updated_at"] = now_iso()
            validate_or_raise("material", updated)
            encoded = json.dumps(updated, ensure_ascii=False, separators=(",", ":"))
            connection.execute(
                "UPDATE materials SET profile_json=?, updated_at=? WHERE material_id=?",
                (encoded, updated["updated_at"], material_id),
            )
            connection.execute(
                "INSERT INTO material_revisions VALUES (?, ?, 'edited', ?, ?, ?)",
                (material_id, updated["revision"], clean_actor, encoded, updated["updated_at"]),
            )
        return updated

    def replace_after_recognition(self, profile: Mapping[str, Any], *, actor: str = "中转站识别") -> dict[str, Any]:
        payload = copy.deepcopy(dict(profile))
        validate_or_raise("material", payload)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            exists = connection.execute("SELECT 1 FROM materials WHERE material_id=?", (payload["material_id"],)).fetchone()
            if exists is None:
                raise MaterialNotFoundError("找不到这个素材")
            connection.execute(
                "UPDATE materials SET profile_json=?, updated_at=? WHERE material_id=?",
                (encoded, payload["updated_at"], payload["material_id"]),
            )
            connection.execute(
                "INSERT INTO material_revisions VALUES (?, ?, 'recognized', ?, ?, ?)",
                (payload["material_id"], payload["revision"], actor, encoded, payload["updated_at"]),
            )
        return payload

    def revisions(self, material_id: str) -> list[dict[str, Any]]:
        self.get(material_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT revision, action, actor, created_at FROM material_revisions WHERE material_id=? ORDER BY revision DESC",
                (material_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def matches(self, script_id: str) -> dict[str, dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM material_matches WHERE script_id=?", (script_id,)).fetchall()
        return {row["script_shot_id"]: dict(row) for row in rows}

    def confirm_match(
        self, *, script_id: str, script_shot_id: str, suggestion: Mapping[str, Any], actor: str,
        allow_reuse: bool = False,
    ) -> dict[str, Any]:
        clean_actor = actor.strip()
        if not clean_actor:
            raise ValueError("请填写匹配确认人")
        created = now_iso()
        event = {
            "schema_version": "1.0.0", "event_id": f"usage_event_{uuid4().hex}", "action": "confirmed",
            "material_id": suggestion["material_id"], "clip_id": suggestion["clip_id"], "script_id": script_id,
            "script_shot_id": script_shot_id, "actor": clean_actor, "created_at": created,
        }
        validate_or_raise("material_usage", event)
        with self._lock, self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM material_matches WHERE script_id=? AND script_shot_id=?", (script_id, script_shot_id),
            ).fetchone():
                raise MaterialConflictError("这个脚本镜头已有素材，请先撤销原匹配")
            if not allow_reuse and connection.execute(
                "SELECT 1 FROM material_matches WHERE script_id=? AND material_id=? AND clip_id=?",
                (script_id, suggestion["material_id"], suggestion["clip_id"]),
            ).fetchone():
                raise MaterialConflictError("同一拍摄任务不能重复使用同一个素材片段")
            try:
                connection.execute(
                    "INSERT INTO material_matches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (script_id, script_shot_id, suggestion["material_id"], suggestion["clip_id"], suggestion["score"],
                     suggestion["repeat_risk"], suggestion["reason"], clean_actor, created),
                )
            except sqlite3.IntegrityError as exc:
                raise MaterialConflictError("同一拍摄任务不能重复使用同一个素材片段") from exc
            connection.execute(
                "INSERT INTO material_usage_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event["event_id"], event["action"], event["material_id"], event["clip_id"], event["script_id"],
                 event["script_shot_id"], event["actor"], event["created_at"]),
            )
        return event

    def release_match(self, *, script_id: str, script_shot_id: str, actor: str) -> dict[str, Any]:
        clean_actor = actor.strip()
        if not clean_actor:
            raise ValueError("请填写撤销人")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM material_matches WHERE script_id=? AND script_shot_id=?", (script_id, script_shot_id),
            ).fetchone()
            if row is None:
                raise MaterialNotFoundError("这个脚本镜头还没有已确认素材")
            event = {
                "schema_version": "1.0.0", "event_id": f"usage_event_{uuid4().hex}", "action": "released",
                "material_id": row["material_id"], "clip_id": row["clip_id"], "script_id": script_id,
                "script_shot_id": script_shot_id, "actor": clean_actor, "created_at": now_iso(),
            }
            validate_or_raise("material_usage", event)
            connection.execute("DELETE FROM material_matches WHERE script_id=? AND script_shot_id=?", (script_id, script_shot_id))
            connection.execute(
                "INSERT INTO material_usage_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event["event_id"], event["action"], event["material_id"], event["clip_id"], event["script_id"],
                 event["script_shot_id"], event["actor"], event["created_at"]),
            )
        return event

    def usage(self, material_id: str, clip_id: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [material_id]
        sql = "SELECT * FROM material_usage_events WHERE material_id=?"
        if clip_id:
            sql += " AND clip_id=?"
            params.append(clip_id)
        sql += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [{"schema_version": "1.0.0", **dict(row)} for row in rows]

    def counts(self) -> dict[str, int]:
        profiles = self.list()
        return {"materials": len(profiles), "clips": sum(len(item["clips"]) for item in profiles)}
