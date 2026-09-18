"""Persistent S8 publications, metric snapshots, import drafts, and reports."""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from content_factory_contracts import validate_or_raise


class FeedbackNotFoundError(LookupError):
    pass


class FeedbackConflictError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode(value: str) -> dict[str, Any]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("发布复盘数据损坏")
    return payload


def _date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return parsed.astimezone(UTC)


def _upgrade_publication(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep additive S8 changes readable without discarding existing local records."""
    if "business_review" not in payload:
        payload["business_review"] = {"status": "pending", "reviewed_by": None, "reviewed_at": None, "note": ""}
    return payload


def _validate_published_at(value: str) -> None:
    if _date(value) > datetime.now(UTC) + timedelta(minutes=5):
        raise ValueError("作品发布时间不能晚于当前时间")


class FeedbackStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.database_path = self.root / "feedback.sqlite3"
        self.import_root = (self.root / "imports").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.import_root.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS publications (
                    publication_id TEXT PRIMARY KEY, output_id TEXT NOT NULL UNIQUE, work_id TEXT NOT NULL UNIQUE,
                    product_id TEXT NOT NULL, fixture_data INTEGER NOT NULL, status TEXT NOT NULL,
                    publication_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_publications_product ON publications(product_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS metric_snapshots (
                    snapshot_id TEXT PRIMARY KEY, publication_id TEXT NOT NULL, captured_at TEXT NOT NULL,
                    fixture_data INTEGER NOT NULL, snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(publication_id, captured_at),
                    FOREIGN KEY(publication_id) REFERENCES publications(publication_id)
                );
                CREATE INDEX IF NOT EXISTS idx_snapshots_publication ON metric_snapshots(publication_id, captured_at DESC);
                CREATE TABLE IF NOT EXISTS metric_import_drafts (
                    draft_id TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL, status TEXT NOT NULL,
                    fixture_data INTEGER NOT NULL, draft_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_import_source ON metric_import_drafts(source_sha256, created_at DESC);
                CREATE TABLE IF NOT EXISTS learning_reports (
                    report_id TEXT PRIMARY KEY, product_id TEXT NOT NULL, fixture_data INTEGER NOT NULL,
                    report_json TEXT NOT NULL, generated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reports_product ON learning_reports(product_id, generated_at DESC);
                CREATE TABLE IF NOT EXISTS feedback_audit (
                    event_id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
                    action TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _audit(connection: sqlite3.Connection, entity_type: str, entity_id: str, action: str, actor: str, note: str) -> None:
        connection.execute(
            "INSERT INTO feedback_audit VALUES(?,?,?,?,?,?,?)",
            (f"audit_{uuid4().hex}", entity_type, entity_id, action, actor[:100], note[:1000], now_iso()),
        )

    def create_publication(
        self, *, output: Mapping[str, Any], project: Mapping[str, Any], work_url: str, work_id: str,
        account_label: str, title: str, published_at: str, actor: str, note: str = "",
    ) -> tuple[dict[str, Any], bool]:
        clean_actor = actor.strip()
        if not clean_actor:
            raise ValueError("请填写发布登记人")
        _validate_published_at(published_at)
        variant = next((item for item in project.get("variants", []) if item.get("id") == output.get("variant_id")), None)
        if not isinstance(variant, Mapping):
            raise ValueError("找不到成片对应的剪辑版本")
        changed = now_iso()
        payload: dict[str, Any] = {
            "schema_version": "1.0.0", "fixture_data": bool(output["fixture_data"]), "revision": 1,
            "publication_id": f"publication_{uuid4().hex}", "platform": "douyin_cn",
            "output_id": output["output_id"], "project_id": output["project_id"],
            "project_revision": output["project_revision"], "variant_id": output["variant_id"],
            "script_id": project["script_id"], "script_version_id": variant["script_version_id"],
            "product_id": project["product_id"], "status": "published", "work_url": work_url.strip(),
            "work_id": work_id.strip(), "account_label": account_label.strip(), "title": title.strip(),
            "published_at": published_at, "registered_by": clean_actor, "note": note.strip(),
            "output_sha256": output["media"]["sha256"],
            "business_review": {"status": "pending", "reviewed_by": None, "reviewed_at": None, "note": ""},
            "history": [{"revision": 1, "action": "registered", "actor": clean_actor, "at": changed, "note": note.strip()}],
            "created_at": changed, "updated_at": changed,
        }
        validate_or_raise("publication", payload, related={"render_output": output, "edit_project": project})
        with self._lock, self._connect() as connection:
            existing = connection.execute("SELECT publication_json FROM publications WHERE output_id=?", (output["output_id"],)).fetchone()
            if existing is not None:
                return _upgrade_publication(_decode(existing["publication_json"])), True
            try:
                connection.execute(
                    "INSERT INTO publications VALUES(?,?,?,?,?,?,?,?,?)",
                    (payload["publication_id"], payload["output_id"], payload["work_id"], payload["product_id"],
                     int(payload["fixture_data"]), payload["status"], _json(payload), changed, changed),
                )
            except sqlite3.IntegrityError as exc:
                raise FeedbackConflictError("这个抖音作品号已经登记") from exc
            self._audit(connection, "publication", payload["publication_id"], "registered", clean_actor, note)
        return payload, False

    def get_publication(self, publication_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT publication_json FROM publications WHERE publication_id=?", (publication_id,)).fetchone()
        if row is None:
            raise FeedbackNotFoundError("找不到这个发布记录")
        payload = _upgrade_publication(_decode(row["publication_json"]))
        validate_or_raise("publication", payload)
        return payload

    def list_publications(self, *, include_fixtures: bool = False, product_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT publication_json FROM publications"
        values: tuple[object, ...] = ()
        if product_id:
            sql += " WHERE product_id=?"
            values = (product_id,)
        sql += " ORDER BY created_at DESC"
        with self._connect() as connection:
            items = [_upgrade_publication(_decode(row["publication_json"])) for row in connection.execute(sql, values).fetchall()]
        return [item for item in items if include_fixtures or not item["fixture_data"]]

    def update_publication(
        self, publication_id: str, *, expected_revision: int, actor: str, action: Literal["corrected", "archived", "restored"],
        work_url: str | None = None, work_id: str | None = None, account_label: str | None = None,
        title: str | None = None, published_at: str | None = None, note: str = "",
    ) -> dict[str, Any]:
        clean_actor = actor.strip()
        if not clean_actor:
            raise ValueError("请填写修改人")
        if action in {"archived", "restored"} and not note.strip():
            raise ValueError("归档或恢复必须填写原因")
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT publication_json FROM publications WHERE publication_id=?", (publication_id,)).fetchone()
            if row is None:
                raise FeedbackNotFoundError("找不到这个发布记录")
            payload = _upgrade_publication(_decode(row["publication_json"]))
            if payload["revision"] != expected_revision:
                raise FeedbackConflictError("发布记录已被修改，请刷新后重试")
            if published_at is not None:
                _validate_published_at(published_at)
            if action == "archived" and payload["status"] != "published":
                raise FeedbackConflictError("只有发布中的记录可以归档")
            if action == "restored" and payload["status"] != "archived":
                raise FeedbackConflictError("只有已归档记录可以恢复")
            for field, value in (("work_url", work_url), ("work_id", work_id), ("account_label", account_label), ("title", title), ("published_at", published_at)):
                if value is not None:
                    payload[field] = value.strip()
            if action == "archived":
                payload["status"] = "archived"
            elif action == "restored":
                payload["status"] = "published"
            if action == "corrected" and payload["business_review"]["status"] == "confirmed":
                payload["business_review"] = {"status": "pending", "reviewed_by": None, "reviewed_at": None, "note": ""}
            payload["revision"] += 1
            payload["note"] = note.strip() if note.strip() else payload["note"]
            payload["updated_at"] = now_iso()
            payload["history"].append({"revision": payload["revision"], "action": action, "actor": clean_actor, "at": payload["updated_at"], "note": note.strip()})
            validate_or_raise("publication", payload)
            try:
                connection.execute(
                    "UPDATE publications SET work_id=?,status=?,publication_json=?,updated_at=? WHERE publication_id=?",
                    (payload["work_id"], payload["status"], _json(payload), payload["updated_at"], publication_id),
                )
            except sqlite3.IntegrityError as exc:
                raise FeedbackConflictError("这个抖音作品号已经登记") from exc
            self._audit(connection, "publication", publication_id, action, clean_actor, note)
        return payload

    def confirm_business_review(
        self, publication_id: str, *, expected_revision: int, reviewed_by: str, note: str,
    ) -> dict[str, Any]:
        actor = reviewed_by.strip()
        if not actor:
            raise ValueError("请填写业务确认人")
        if not note.strip():
            raise ValueError("请填写复盘确认说明")
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT publication_json FROM publications WHERE publication_id=?", (publication_id,)).fetchone()
            if row is None:
                raise FeedbackNotFoundError("找不到这个发布记录")
            payload = _upgrade_publication(_decode(row["publication_json"]))
            if payload["revision"] != expected_revision:
                raise FeedbackConflictError("发布记录已被修改，请刷新后重试")
            if payload["status"] != "published":
                raise FeedbackConflictError("已归档作品不能确认闭环")
            if payload["business_review"]["status"] == "confirmed":
                return payload
            snapshot_count = connection.execute(
                "SELECT COUNT(*) value FROM metric_snapshots WHERE publication_id=?", (publication_id,),
            ).fetchone()["value"]
            if snapshot_count < 2:
                raise ValueError("至少需要两个不同时刻的已确认指标快照，才能由业务人员确认闭环")
            changed = now_iso()
            payload["revision"] += 1
            payload["business_review"] = {"status": "confirmed", "reviewed_by": actor, "reviewed_at": changed, "note": note.strip()}
            payload["updated_at"] = changed
            payload["history"].append({
                "revision": payload["revision"], "action": "loop_confirmed", "actor": actor,
                "at": changed, "note": note.strip(),
            })
            validate_or_raise("publication", payload)
            connection.execute(
                "UPDATE publications SET publication_json=?,updated_at=? WHERE publication_id=?",
                (_json(payload), changed, publication_id),
            )
            self._audit(connection, "publication", publication_id, "loop_confirmed", actor, note)
        return payload

    def _neighbor_snapshot(self, connection: sqlite3.Connection, publication_id: str, captured_at: str, direction: str) -> dict[str, Any] | None:
        operator, order = ("<", "DESC") if direction == "previous" else (">", "ASC")
        row = connection.execute(
            f"SELECT snapshot_json FROM metric_snapshots WHERE publication_id=? AND captured_at {operator} ? ORDER BY captured_at {order} LIMIT 1",
            (publication_id, captured_at),
        ).fetchone()
        return _decode(row["snapshot_json"]) if row else None

    def _prepare_snapshot(
        self, connection: sqlite3.Connection, *, publication: Mapping[str, Any], captured_at: str,
        source: str, confidence: str, confirmed_by: str, metrics: Mapping[str, Any],
        is_correction: bool, correction_reason: str | None,
    ) -> dict[str, Any]:
        published = _date(str(publication["published_at"]))
        captured = _date(captured_at)
        observation = max(0, round((captured - published).total_seconds() / 60))
        changed = now_iso()
        payload: dict[str, Any] = {
            "schema_version": "1.0.0", "fixture_data": bool(publication["fixture_data"]),
            "snapshot_id": f"metric_snapshot_{uuid4().hex}", "publication_id": publication["publication_id"],
            "captured_at": captured_at, "observation_minutes": observation, "source": source,
            "confidence": confidence, "confirmed_by": confirmed_by.strip(), "confirmed_at": changed,
            "is_correction": is_correction, "correction_reason": (correction_reason or "").strip() or None,
            "metrics": copy.deepcopy(dict(metrics)), "created_at": changed,
        }
        previous = self._neighbor_snapshot(connection, publication["publication_id"], captured_at, "previous")
        related: dict[str, Any] = {"publication": publication}
        if previous:
            related["previous_snapshot"] = previous
        validate_or_raise("metric_snapshot", payload, related=related)
        following = self._neighbor_snapshot(connection, publication["publication_id"], captured_at, "next")
        if following and not is_correction:
            for field in ("views", "followers_gained", "likes", "comments", "favorites", "shares", "product_clicks", "orders", "gmv_cents"):
                current, later = payload["metrics"].get(field), following["metrics"].get(field)
                if isinstance(current, (int, float)) and isinstance(later, (int, float)) and current > later:
                    raise ValueError(f"累计指标 {field} 不能高于后一条快照；如需更正请标记修正快照")
        return payload

    def add_snapshot(
        self, publication_id: str, *, captured_at: str, source: str, confidence: str, confirmed_by: str,
        metrics: Mapping[str, Any], is_correction: bool = False, correction_reason: str | None = None,
    ) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            publication = self._publication_from_connection(connection, publication_id)
            payload = self._prepare_snapshot(
                connection, publication=publication, captured_at=captured_at, source=source,
                confidence=confidence, confirmed_by=confirmed_by, metrics=metrics,
                is_correction=is_correction, correction_reason=correction_reason,
            )
            try:
                connection.execute(
                    "INSERT INTO metric_snapshots VALUES(?,?,?,?,?,?)",
                    (payload["snapshot_id"], publication_id, captured_at, int(payload["fixture_data"]), _json(payload), payload["created_at"]),
                )
            except sqlite3.IntegrityError as exc:
                raise FeedbackConflictError("这个作品在该采集时间已经有快照") from exc
            self._audit(connection, "snapshot", payload["snapshot_id"], "created", confirmed_by, payload["source"])
        return payload

    @staticmethod
    def _publication_from_connection(connection: sqlite3.Connection, publication_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT publication_json FROM publications WHERE publication_id=?", (publication_id,)).fetchone()
        if row is None:
            raise FeedbackNotFoundError("找不到指标对应的发布记录")
        return _upgrade_publication(_decode(row["publication_json"]))

    def list_snapshots(self, *, publication_id: str | None = None, include_fixtures: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT snapshot_json FROM metric_snapshots"
        values: tuple[object, ...] = ()
        if publication_id:
            sql += " WHERE publication_id=?"
            values = (publication_id,)
        sql += " ORDER BY captured_at ASC"
        with self._connect() as connection:
            items = [_decode(row["snapshot_json"]) for row in connection.execute(sql, values).fetchall()]
        return [item for item in items if include_fixtures or not item["fixture_data"]]

    def create_import_draft(
        self, *, import_kind: Literal["csv", "ocr"], fixture_data: bool, source_name: str, source_sha256: str,
        raw_text: str, candidates: list[Mapping[str, Any]], errors: list[str], created_by: str,
    ) -> tuple[dict[str, Any], bool]:
        changed = now_iso()
        payload: dict[str, Any] = {
            "schema_version": "1.0.0", "fixture_data": fixture_data, "draft_id": f"metric_import_{uuid4().hex}",
            "import_kind": import_kind, "status": "pending", "source_name": source_name[:255],
            "source_sha256": source_sha256, "raw_text": raw_text[:20000],
            "candidates": [copy.deepcopy(dict(item)) for item in candidates], "errors": errors,
            "confirmed_snapshot_ids": [], "created_by": created_by.strip(), "created_at": changed, "updated_at": changed,
        }
        validate_or_raise("metric_import_draft", payload)
        with self._lock, self._connect() as connection:
            existing_rows = connection.execute(
                "SELECT draft_json FROM metric_import_drafts WHERE source_sha256=? AND status='pending' ORDER BY created_at DESC",
                (source_sha256,),
            ).fetchall()
            candidate_targets = [(item["publication_id"], item["captured_at"]) for item in payload["candidates"]]
            for existing in existing_rows:
                prior = _decode(existing["draft_json"])
                prior_targets = [(item["publication_id"], item["captured_at"]) for item in prior["candidates"]]
                if prior["import_kind"] == import_kind and prior_targets == candidate_targets:
                    return prior, True
            connection.execute(
                "INSERT INTO metric_import_drafts VALUES(?,?,?,?,?,?,?)",
                (payload["draft_id"], source_sha256, "pending", int(fixture_data), _json(payload), changed, changed),
            )
            self._audit(connection, "import", payload["draft_id"], "created", created_by, import_kind)
        return payload, False

    def get_import_draft(self, draft_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT draft_json FROM metric_import_drafts WHERE draft_id=?", (draft_id,)).fetchone()
        if row is None:
            raise FeedbackNotFoundError("找不到这个导入草稿")
        return _decode(row["draft_json"])

    def list_import_drafts(self, *, include_fixtures: bool = False) -> list[dict[str, Any]]:
        with self._connect() as connection:
            items = [_decode(row["draft_json"]) for row in connection.execute("SELECT draft_json FROM metric_import_drafts ORDER BY created_at DESC").fetchall()]
        return [item for item in items if include_fixtures or not item["fixture_data"]]

    def confirm_import(self, draft_id: str, *, confirmed_by: str, candidates: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        overrides = {item.get("candidate_id"): item for item in (candidates or [])}
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT draft_json FROM metric_import_drafts WHERE draft_id=?", (draft_id,)).fetchone()
            if row is None:
                raise FeedbackNotFoundError("找不到这个导入草稿")
            draft = _decode(row["draft_json"])
            if draft["status"] != "pending":
                raise FeedbackConflictError("这个导入草稿已经处理")
            snapshots: list[dict[str, Any]] = []
            ordered = sorted(draft["candidates"], key=lambda item: (item["publication_id"], item["captured_at"]))
            for candidate in ordered:
                override = overrides.get(candidate["candidate_id"], {})
                publication = self._publication_from_connection(connection, candidate["publication_id"])
                if publication["fixture_data"] != draft["fixture_data"]:
                    raise ValueError("导入草稿与发布记录不能混用样例和正式数据")
                payload = self._prepare_snapshot(
                    connection, publication=publication,
                    captured_at=str(override.get("captured_at", candidate["captured_at"])),
                    source="csv" if draft["import_kind"] == "csv" else "screenshot_ocr",
                    confidence=str(override.get("confidence", "confirmed")), confirmed_by=confirmed_by,
                    metrics=override.get("metrics", candidate["metrics"]),
                    is_correction=bool(override.get("is_correction", False)),
                    correction_reason=override.get("correction_reason"),
                )
                try:
                    connection.execute(
                        "INSERT INTO metric_snapshots VALUES(?,?,?,?,?,?)",
                        (payload["snapshot_id"], payload["publication_id"], payload["captured_at"],
                         int(payload["fixture_data"]), _json(payload), payload["created_at"]),
                    )
                except sqlite3.IntegrityError as exc:
                    raise FeedbackConflictError("导入中存在重复采集时间，整批未保存") from exc
                snapshots.append(payload)
            draft["status"] = "confirmed"
            draft["confirmed_snapshot_ids"] = [item["snapshot_id"] for item in snapshots]
            draft["updated_at"] = now_iso()
            validate_or_raise("metric_import_draft", draft)
            connection.execute(
                "UPDATE metric_import_drafts SET status='confirmed',draft_json=?,updated_at=? WHERE draft_id=?",
                (_json(draft), draft["updated_at"], draft_id),
            )
            self._audit(connection, "import", draft_id, "confirmed", confirmed_by, f"{len(snapshots)} 条快照")
        return {"draft": draft, "snapshots": snapshots}

    def reject_import(self, draft_id: str, *, actor: str, note: str) -> dict[str, Any]:
        if not note.strip():
            raise ValueError("拒绝导入必须填写原因")
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT draft_json FROM metric_import_drafts WHERE draft_id=?", (draft_id,)).fetchone()
            if row is None:
                raise FeedbackNotFoundError("找不到这个导入草稿")
            draft = _decode(row["draft_json"])
            if draft["status"] != "pending":
                raise FeedbackConflictError("这个导入草稿已经处理")
            draft["status"] = "rejected"
            draft["updated_at"] = now_iso()
            validate_or_raise("metric_import_draft", draft)
            connection.execute(
                "UPDATE metric_import_drafts SET status='rejected',draft_json=?,updated_at=? WHERE draft_id=?",
                (_json(draft), draft["updated_at"], draft_id),
            )
            self._audit(connection, "import", draft_id, "rejected", actor, note)
        return draft

    def save_report(self, report: Mapping[str, Any]) -> dict[str, Any]:
        payload = copy.deepcopy(dict(report))
        validate_or_raise("learning_report", payload)
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO learning_reports VALUES(?,?,?,?,?)",
                (payload["report_id"], payload["product_id"], int(payload["fixture_data"]), _json(payload), payload["generated_at"]),
            )
        return payload

    def list_reports(self, *, include_fixtures: bool = False, product_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT report_json FROM learning_reports"
        values: tuple[object, ...] = ()
        if product_id:
            sql += " WHERE product_id=?"
            values = (product_id,)
        sql += " ORDER BY generated_at DESC"
        with self._connect() as connection:
            items = [_decode(row["report_json"]) for row in connection.execute(sql, values).fetchall()]
        return [item for item in items if include_fixtures or not item["fixture_data"]]

    def counts(self) -> dict[str, int]:
        with self._connect() as connection:
            publications = connection.execute("SELECT COUNT(*) value FROM publications").fetchone()["value"]
            snapshots = connection.execute("SELECT COUNT(*) value FROM metric_snapshots").fetchone()["value"]
            pending = connection.execute("SELECT COUNT(*) value FROM metric_import_drafts WHERE status='pending'").fetchone()["value"]
            reports = connection.execute("SELECT COUNT(*) value FROM learning_reports").fetchone()["value"]
            candidate_rows = connection.execute(
                """SELECT p.publication_json, COUNT(s.snapshot_id) snapshot_count
                   FROM publications p LEFT JOIN metric_snapshots s ON s.publication_id=p.publication_id
                   WHERE p.fixture_data=0 AND p.status='published' GROUP BY p.publication_id"""
            ).fetchall()
            closed = sum(
                1 for row in candidate_rows
                if row["snapshot_count"] >= 2
                and _upgrade_publication(_decode(row["publication_json"]))["business_review"]["status"] == "confirmed"
            )
        return {"publications": publications, "snapshots": snapshots, "pending_imports": pending, "reports": reports, "closed_real_loops": closed}
