"""Persistent S7 edit projects, audio assets, outputs, and private resources."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Literal
from uuid import uuid4

from content_factory_contracts import validate_or_raise
from content_factory_media.tools import MediaToolError, find_tool

from .s7_projects import apply_project_changes

MAX_AUDIO_BYTES = 200 * 1024 * 1024
_AUDIO_TYPES = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".flac": "audio/flac", ".ogg": "audio/ogg",
}


class EditNotFoundError(LookupError):
    pass


class EditConflictError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _decode(value: str) -> dict[str, Any]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("剪辑数据损坏")
    return payload


def _upstream_lineage(project: Mapping[str, Any]) -> tuple[object, ...]:
    """Identify the upstream script selection that produced an edit timeline."""

    return (
        project.get("script_revision"),
        project.get("selected_version_id"),
        project.get("product_id"),
        bool(project.get("fixture_data")),
    )


class EditStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.database_path = self.root / "editing.sqlite3"
        self.audio_root = (self.root / "audio").resolve()
        self.output_root = (self.root / "outputs").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.audio_root.mkdir(parents=True, exist_ok=True)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS edit_projects (
                    project_id TEXT PRIMARY KEY, script_id TEXT NOT NULL UNIQUE, product_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, status TEXT NOT NULL, project_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS edit_project_revisions (
                    project_id TEXT NOT NULL, revision INTEGER NOT NULL, action TEXT NOT NULL,
                    actor TEXT NOT NULL, project_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, revision)
                );
                CREATE TABLE IF NOT EXISTS edit_audio_assets (
                    asset_id TEXT PRIMARY KEY, asset_json TEXT NOT NULL, managed_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS render_outputs (
                    output_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, variant_id TEXT NOT NULL,
                    status TEXT NOT NULL, output_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS render_resources (
                    resource_ref TEXT PRIMARY KEY, output_id TEXT NOT NULL, kind TEXT NOT NULL,
                    managed_path TEXT NOT NULL, mime_type TEXT NOT NULL, download_name TEXT NOT NULL,
                    FOREIGN KEY(output_id) REFERENCES render_outputs(output_id)
                );
                CREATE INDEX IF NOT EXISTS idx_outputs_project ON render_outputs(project_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS render_output_recovery_audit (
                    audit_id TEXT PRIMARY KEY, output_id TEXT NOT NULL, output_json TEXT NOT NULL,
                    resources_json TEXT NOT NULL, reason TEXT NOT NULL, discarded_at TEXT NOT NULL
                );
                """
            )

    def create_project(self, project: Mapping[str, Any], *, actor: str) -> tuple[dict[str, Any], bool]:
        payload = copy.deepcopy(dict(project))
        validate_or_raise("edit_project", payload)
        clean_actor = actor.strip()
        if not clean_actor:
            raise ValueError("请填写剪辑工程创建人")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT project_json FROM edit_projects WHERE script_id=?", (payload["script_id"],)).fetchone()
            if existing is not None:
                current = _decode(existing["project_json"])
                if _upstream_lineage(current) == _upstream_lineage(payload):
                    return current, True
                refreshed = copy.deepcopy(payload)
                refreshed["project_id"] = current["project_id"]
                refreshed["revision"] = int(current["revision"]) + 1
                refreshed["created_at"] = current["created_at"]
                refreshed["updated_at"] = now_iso()
                refreshed["status"] = "draft"
                refreshed["settings"] = copy.deepcopy(current["settings"])
                for variant in refreshed["variants"]:
                    variant["latest_output_id"] = None
                validate_or_raise("edit_project", refreshed)
                encoded = _json(refreshed)
                connection.execute(
                    "UPDATE edit_projects SET product_id=?,revision=?,status=?,project_json=?,updated_at=? WHERE project_id=?",
                    (
                        refreshed["product_id"], refreshed["revision"], refreshed["status"], encoded,
                        refreshed["updated_at"], refreshed["project_id"],
                    ),
                )
                connection.execute(
                    "INSERT INTO edit_project_revisions VALUES(?,?,'script_rebased',?,?,?)",
                    (
                        refreshed["project_id"], refreshed["revision"], clean_actor, encoded,
                        refreshed["updated_at"],
                    ),
                )
                return refreshed, False
            encoded = _json(payload)
            connection.execute(
                "INSERT INTO edit_projects VALUES(?,?,?,?,?,?,?,?)",
                (payload["project_id"], payload["script_id"], payload["product_id"], payload["revision"],
                 payload["status"], encoded, payload["created_at"], payload["updated_at"]),
            )
            connection.execute(
                "INSERT INTO edit_project_revisions VALUES(?,?,'created',?,?,?)",
                (payload["project_id"], payload["revision"], clean_actor, encoded, payload["updated_at"]),
            )
        return payload, False

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT project_json FROM edit_projects WHERE project_id=?", (project_id,)).fetchone()
        if row is None:
            raise EditNotFoundError("找不到这个剪辑工程")
        payload = _decode(row["project_json"])
        validate_or_raise("edit_project", payload)
        return payload

    def list_projects(self, *, include_fixtures: bool = False) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT project_json FROM edit_projects ORDER BY updated_at DESC").fetchall()
        projects = [_decode(row["project_json"]) for row in rows]
        return [item for item in projects if include_fixtures or not item.get("fixture_data")]

    def save_project(
        self, project_id: str, *, expected_revision: int, actor: str,
        settings: Mapping[str, Any], variants: list[Mapping[str, Any]],
        related: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        clean_actor = actor.strip()
        if not clean_actor:
            raise ValueError("请填写修改人")
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT project_json FROM edit_projects WHERE project_id=?", (project_id,)).fetchone()
            if row is None:
                raise EditNotFoundError("找不到这个剪辑工程")
            current = _decode(row["project_json"])
            if current["revision"] != expected_revision:
                raise EditConflictError("剪辑工程已被修改，请刷新后再保存")
            updated = apply_project_changes(current, settings=settings, variants=variants)
            updated["revision"] = expected_revision + 1
            updated["updated_at"] = now_iso()
            validate_or_raise("edit_project", updated, related=related)
            encoded = _json(updated)
            connection.execute(
                "UPDATE edit_projects SET revision=?,status=?,project_json=?,updated_at=? WHERE project_id=?",
                (updated["revision"], updated["status"], encoded, updated["updated_at"], project_id),
            )
            connection.execute(
                "INSERT INTO edit_project_revisions VALUES(?,?,'updated',?,?,?)",
                (project_id, updated["revision"], clean_actor, encoded, updated["updated_at"]),
            )
        return updated

    def project_revisions(self, project_id: str) -> list[dict[str, Any]]:
        self.get_project(project_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT revision,action,actor,created_at FROM edit_project_revisions WHERE project_id=? ORDER BY revision DESC",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _probe_audio(path: Path) -> tuple[int, int, int]:
        try:
            completed = subprocess.run(
                [find_tool("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, shell=False,
            )
            payload = json.loads(completed.stdout)
            audio = next(item for item in payload["streams"] if item.get("codec_type") == "audio")
            duration = payload.get("format", {}).get("duration") or audio.get("duration")
            return round(float(duration) * 1000), int(audio["sample_rate"]), int(audio.get("channels", 1))
        except (OSError, subprocess.SubprocessError, KeyError, StopIteration, TypeError, ValueError) as exc:
            raise MediaToolError("无法读取有效音频流") from exc

    def add_audio(
        self, *, kind: Literal["bgm", "sound_effect"], name: str, original_name: str,
        license_note: str, imported_by: str, fixture_data: bool, stream: BinaryIO,
    ) -> tuple[dict[str, Any], bool]:
        safe_name = Path(original_name or "").name
        extension = Path(safe_name).suffix.lower()
        if extension not in _AUDIO_TYPES:
            raise ValueError("音频仅支持 MP3、WAV、M4A、AAC、FLAC 和 OGG")
        clean_name, clean_license, clean_actor = name.strip(), license_note.strip(), imported_by.strip()
        if not clean_name or len(clean_license) < 2 or not clean_actor:
            raise ValueError("请填写音频名称、版权说明和导入人")
        temporary = self.audio_root / f".{uuid4().hex}.upload"
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("wb") as output:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_AUDIO_BYTES:
                        raise ValueError("单个音频不能超过 200 MB")
                    digest.update(chunk)
                    output.write(chunk)
            if size == 0:
                raise ValueError("音频文件为空")
            checksum = digest.hexdigest()
            with self._connect() as connection:
                for row in connection.execute("SELECT asset_json FROM edit_audio_assets").fetchall():
                    existing = _decode(row["asset_json"])
                    if existing["sha256"] == checksum and existing["kind"] == kind:
                        return existing, True
            duration_ms, sample_rate, channels = self._probe_audio(temporary)
            asset_id = f"edit_audio_{uuid4().hex}"
            final_path = self.audio_root / f"{asset_id}{extension}"
            os.replace(temporary, final_path)
            created = now_iso()
            asset = {
                "schema_version": "1.0.0", "fixture_data": bool(fixture_data), "asset_id": asset_id,
                "kind": kind, "name": clean_name[:500], "original_name": safe_name,
                "mime_type": _AUDIO_TYPES[extension], "sha256": checksum, "size_bytes": size,
                "duration_ms": duration_ms, "sample_rate": sample_rate, "channels": channels,
                "license_note": clean_license[:1000], "imported_by": clean_actor[:500], "created_at": created,
            }
            validate_or_raise("edit_audio_asset", asset)
            with self._lock, self._connect() as connection:
                connection.execute("INSERT INTO edit_audio_assets VALUES(?,?,?,?)", (asset_id, _json(asset), str(final_path), created))
            return asset, False
        finally:
            temporary.unlink(missing_ok=True)

    def list_audio(self, *, include_fixtures: bool = False) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT asset_json FROM edit_audio_assets ORDER BY created_at DESC").fetchall()
        items = [_decode(row["asset_json"]) for row in rows]
        return [item for item in items if include_fixtures or not item.get("fixture_data")]

    def get_audio(self, asset_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT asset_json FROM edit_audio_assets WHERE asset_id=?", (asset_id,)).fetchone()
        if row is None:
            raise EditNotFoundError("找不到这个音频")
        asset = _decode(row["asset_json"])
        validate_or_raise("edit_audio_asset", asset)
        return asset

    def audio_path(self, asset_id: str) -> Path:
        with self._connect() as connection:
            row = connection.execute("SELECT managed_path FROM edit_audio_assets WHERE asset_id=?", (asset_id,)).fetchone()
        if row is None:
            raise EditNotFoundError("找不到这个音频")
        path = Path(row["managed_path"]).resolve()
        if not path.is_relative_to(self.audio_root) or not path.is_file():
            raise EditNotFoundError("音频文件不存在")
        return path

    def create_output(
        self, output: Mapping[str, Any], *, resources: Mapping[str, tuple[str | Path, str, str]],
    ) -> dict[str, Any]:
        payload = copy.deepcopy(dict(output))
        validate_or_raise("render_output", payload)
        expected_refs = set(payload["resources"].values()) - {True, False}
        if set(resources) != expected_refs:
            raise ValueError("成片资源不完整")
        checked: dict[str, tuple[Path, str, str]] = {}
        for resource_ref, (path_value, mime_type, download_name) in resources.items():
            path = Path(path_value).resolve()
            if not path.is_relative_to(self.output_root) or not path.is_file():
                raise ValueError("成片资源超出受控目录或不存在")
            checked[resource_ref] = (path, mime_type, Path(download_name).name)
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO render_outputs VALUES(?,?,?,?,?,?,?)",
                (payload["output_id"], payload["project_id"], payload["variant_id"], payload["status"],
                 _json(payload), payload["created_at"], payload["updated_at"]),
            )
            for resource_ref, (path, mime_type, download_name) in checked.items():
                connection.execute(
                    "INSERT INTO render_resources VALUES(?,?,?,?,?,?)",
                    (resource_ref, payload["output_id"], resource_ref.split("_")[1], str(path), mime_type, download_name),
                )
            self._mark_project_output(connection, payload)
        return payload

    def _mark_project_output(self, connection: sqlite3.Connection, output: Mapping[str, Any]) -> None:
        row = connection.execute("SELECT project_json FROM edit_projects WHERE project_id=?", (output["project_id"],)).fetchone()
        if row is None:
            raise EditNotFoundError("找不到成片对应的剪辑工程")
        project = _decode(row["project_json"])
        if project["revision"] != output["project_revision"]:
            raise EditConflictError("剪辑工程已在渲染期间变更，请重新渲染")
        variant = next((item for item in project["variants"] if item["id"] == output["variant_id"]), None)
        if variant is None:
            raise ValueError("找不到成片对应的剪辑版本")
        variant["latest_output_id"] = output["output_id"]
        project["status"] = output["status"]
        project["updated_at"] = output["updated_at"]
        validate_or_raise("edit_project", project)
        connection.execute(
            "UPDATE edit_projects SET status=?,project_json=?,updated_at=? WHERE project_id=?",
            (project["status"], _json(project), project["updated_at"], project["project_id"]),
        )

    def get_output(self, output_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT output_json FROM render_outputs WHERE output_id=?", (output_id,)).fetchone()
        if row is None:
            raise EditNotFoundError("找不到这个成片")
        return _decode(row["output_json"])

    def discard_incomplete_output(self, output_id: str, *, reason: str) -> dict[str, str]:
        """Archive and remove an unreviewed broken output so its task can be retried."""

        clean_reason = reason.strip()[:500]
        if not clean_reason:
            raise ValueError("请记录成片恢复原因")
        discarded_at = now_iso()
        audit_id = f"render_recovery_{uuid4().hex}"
        output_directory = (self.output_root / output_id).resolve()
        if not output_directory.is_relative_to(self.output_root):
            raise ValueError("成片恢复目录不安全")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT output_json FROM render_outputs WHERE output_id=?", (output_id,),
            ).fetchone()
            if row is None:
                raise EditNotFoundError("找不到这个成片")
            output = _decode(row["output_json"])
            if output.get("status") != "video_review" or output.get("review", {}).get("status") != "pending":
                raise EditConflictError("已审核成片不能由中断恢复流程清理")
            resource_rows = connection.execute(
                """SELECT resource_ref,kind,managed_path,mime_type,download_name
                   FROM render_resources WHERE output_id=? ORDER BY resource_ref""",
                (output_id,),
            ).fetchall()
            connection.execute(
                "INSERT INTO render_output_recovery_audit VALUES(?,?,?,?,?,?)",
                (
                    audit_id, output_id, row["output_json"],
                    json.dumps([dict(item) for item in resource_rows], ensure_ascii=False, separators=(",", ":")),
                    clean_reason, discarded_at,
                ),
            )
            connection.execute("DELETE FROM render_resources WHERE output_id=?", (output_id,))
            connection.execute("DELETE FROM render_outputs WHERE output_id=?", (output_id,))

            project_row = connection.execute(
                "SELECT project_json FROM edit_projects WHERE project_id=?", (output["project_id"],),
            ).fetchone()
            if project_row is not None:
                project = _decode(project_row["project_json"])
                variant = next(
                    (item for item in project["variants"] if item["id"] == output["variant_id"]), None,
                )
                if variant is not None and variant.get("latest_output_id") == output_id:
                    variant["latest_output_id"] = None
                remaining_rows = connection.execute(
                    "SELECT output_json FROM render_outputs WHERE project_id=? ORDER BY created_at DESC",
                    (output["project_id"],),
                ).fetchall()
                referenced_ids = {
                    item["latest_output_id"] for item in project["variants"] if item.get("latest_output_id")
                }
                remaining = [_decode(item["output_json"]) for item in remaining_rows]
                latest = next((item for item in remaining if item["output_id"] in referenced_ids), None)
                project["status"] = latest["status"] if latest is not None else "draft"
                project["updated_at"] = discarded_at
                validate_or_raise("edit_project", project)
                connection.execute(
                    "UPDATE edit_projects SET status=?,project_json=?,updated_at=? WHERE project_id=?",
                    (project["status"], _json(project), discarded_at, project["project_id"]),
                )
        # The database audit retains every former managed path.  Cleanup is
        # best effort because antivirus/indexers can briefly hold Windows files.
        shutil.rmtree(output_directory, ignore_errors=True)
        return {"audit_id": audit_id, "output_id": output_id, "reason": clean_reason, "discarded_at": discarded_at}

    def recovery_audits(self) -> list[dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT audit_id,output_id,reason,discarded_at FROM render_output_recovery_audit ORDER BY discarded_at DESC",
            ).fetchall()
        return [dict(row) for row in rows]

    def list_outputs(self, *, project_id: str | None = None, include_fixtures: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT output_json FROM render_outputs"
        values: tuple[object, ...] = ()
        if project_id:
            sql += " WHERE project_id=?"
            values = (project_id,)
        sql += " ORDER BY created_at DESC"
        with self._connect() as connection:
            items = [_decode(row["output_json"]) for row in connection.execute(sql, values).fetchall()]
        return [item for item in items if include_fixtures or not item.get("fixture_data")]

    def review_output(
        self, output_id: str, *, decision: Literal["approved", "rejected"], reviewer: str, note: str | None,
    ) -> dict[str, Any]:
        clean_reviewer = reviewer.strip()
        clean_note = (note or "").strip() or None
        if not clean_reviewer:
            raise ValueError("请填写审核人")
        if decision == "rejected" and not clean_note:
            raise ValueError("驳回成片必须填写原因")
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT output_json FROM render_outputs WHERE output_id=?", (output_id,)).fetchone()
            if row is None:
                raise EditNotFoundError("找不到这个成片")
            output = _decode(row["output_json"])
            if output["review"]["status"] != "pending":
                raise EditConflictError("这个成片已审核，不能重复审核")
            project_row = connection.execute("SELECT project_json FROM edit_projects WHERE project_id=?", (output["project_id"],)).fetchone()
            if project_row is None:
                raise EditNotFoundError("找不到成片对应的剪辑工程")
            project = _decode(project_row["project_json"])
            current_variant = next((item for item in project["variants"] if item["id"] == output["variant_id"]), None)
            if project["revision"] != output["project_revision"] or not current_variant or current_variant.get("latest_output_id") != output_id:
                raise EditConflictError("这是旧工程修订的成片，请审核当前修订重新渲染的成片")
            changed = now_iso()
            output["status"] = decision
            output["review"] = {
                "status": decision, "reviewed_by": clean_reviewer[:100], "reviewed_at": changed, "note": clean_note,
            }
            output["updated_at"] = changed
            validate_or_raise("render_output", output)
            connection.execute(
                "UPDATE render_outputs SET status=?,output_json=?,updated_at=? WHERE output_id=?",
                (decision, _json(output), changed, output_id),
            )
            project["status"] = decision
            project["updated_at"] = changed
            connection.execute(
                "UPDATE edit_projects SET status=?,project_json=?,updated_at=? WHERE project_id=?",
                (decision, _json(project), changed, project["project_id"]),
            )
        return output

    def resource(self, output_id: str, resource_ref: str) -> tuple[Path, str, str]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT managed_path,mime_type,download_name FROM render_resources WHERE output_id=? AND resource_ref=?",
                (output_id, resource_ref),
            ).fetchone()
        if row is None:
            raise EditNotFoundError("找不到这个成片文件")
        path = Path(row["managed_path"]).resolve()
        if not path.is_relative_to(self.output_root) or not path.is_file():
            raise EditNotFoundError("成片文件不存在")
        output = self.get_output(output_id)
        if resource_ref == output['resources']['video_ref']:
            with path.open('rb') as handle:
                digest = hashlib.file_digest(handle, 'sha256').hexdigest()
            if path.stat().st_size != output['media']['size_bytes'] or digest != output['media']['sha256']:
                raise EditConflictError('成片文件已变化，请恢复原文件或重新渲染审核')
        return path, str(row["mime_type"]), str(row["download_name"])

    def counts(self) -> dict[str, int]:
        with self._connect() as connection:
            projects = connection.execute("SELECT COUNT(*) AS value FROM edit_projects").fetchone()["value"]
            outputs = connection.execute("SELECT status,COUNT(*) AS value FROM render_outputs GROUP BY status").fetchall()
        result = {"projects": int(projects), "video_review": 0, "approved": 0, "rejected": 0}
        for row in outputs:
            result[str(row["status"])] = int(row["value"])
        return result
