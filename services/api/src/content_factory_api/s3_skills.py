"""Persistent candidate aggregation and human-approved viral Skill snapshots."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse

from content_factory_contracts import validate_or_raise

from .s3_queue import AnalysisTaskQueue, AnalysisTaskRecord
from .s3_reports import load_report


SKILL_SCHEMA_VERSION = "1.0.0"
CLUSTER_POLICY_VERSION = "conservative-mechanism-v1"
MECHANISM_KEYS = {
    "result_then_visual_proof",
    "question_then_demonstration",
    "pain_point_then_solution",
    "contrast_then_proof",
    "identity_scenario",
    "value_anchor",
    "curiosity_gap",
    "social_proof",
    "direct_product_pitch",
    "verbal_overload_failure",
    "fit_reassurance",
    "other",
}
_AVOID_MARKERS = (
    "失效", "失败", "反例", "风险", "低效", "无效", "堆砌", "过载", "不可信", "无法证明", "不建议",
)


class SkillConflictError(RuntimeError):
    pass


class SkillNotFoundError(LookupError):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str | bytes) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _source_name(source_uri: object, source_id: str) -> str:
    value = str(source_uri or "").strip()
    parsed = urlparse(value)
    if parsed.scheme and parsed.path:
        candidate = Path(unquote(parsed.path)).name
    else:
        candidate = Path(value).name
    return candidate or source_id


def _inferred_reuse_mode(pattern: Mapping[str, Any]) -> Literal["reuse", "avoid", "uncertain"]:
    explicit = pattern.get("reuse_mode")
    if explicit in {"reuse", "avoid", "uncertain"}:
        return explicit
    text = " ".join(
        str(value)
        for value in (
            pattern.get("name", ""),
            pattern.get("mechanism", ""),
            *pattern.get("failure_signals", []),
        )
    )
    return "avoid" if any(marker in text for marker in _AVOID_MARKERS) else "uncertain"


def _normalize_signature_text(value: object) -> str:
    text = str(value or "").casefold()
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", text)


def _cluster_key(pattern: Mapping[str, Any], reuse_mode: str) -> str:
    mechanism_key = pattern.get("mechanism_key")
    if mechanism_key in MECHANISM_KEYS - {"other"}:
        identity: object = {
            "policy": CLUSTER_POLICY_VERSION,
            "reuse_mode": reuse_mode,
            "mechanism_key": mechanism_key,
        }
    else:
        identity = {
            "policy": CLUSTER_POLICY_VERSION,
            "reuse_mode": reuse_mode,
            "mechanism_key": "other",
            "name": _normalize_signature_text(pattern.get("name")),
            "mechanism": _normalize_signature_text(pattern.get("mechanism")),
            "steps": [
                _normalize_signature_text(item.get("description"))
                for item in pattern.get("steps", [])
                if isinstance(item, Mapping)
            ],
            "necessary_conditions": sorted(
                _normalize_signature_text(item) for item in pattern.get("necessary_conditions", [])
            ),
            "failure_signals": sorted(
                _normalize_signature_text(item) for item in pattern.get("failure_signals", [])
            ),
        }
    return _sha256(_canonical_json(identity))


def _evidence_snapshot(report: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    shots = {item.get("id"): item for item in report.get("shots", []) if isinstance(item, Mapping)}
    timelines = {item.get("id"): item for item in report.get("timeline", []) if isinstance(item, Mapping)}
    keyframes = {item.get("id"): item for item in report.get("keyframes", []) if isinstance(item, Mapping)}
    metrics = {item.get("id"): item for item in report.get("metric_snapshots", []) if isinstance(item, Mapping)}
    comments = {item.get("id"): item for item in report.get("comments", []) if isinstance(item, Mapping)}
    source_type = evidence.get("source_type")
    source_id = evidence.get("source_id")
    source: Mapping[str, Any] = {}
    if source_type in {"shot", "transcript"}:
        source = shots.get(source_id, {})
    elif source_type == "timeline":
        source = timelines.get(source_id, {})
    elif source_type == "keyframe":
        keyframe = keyframes.get(source_id, {})
        source = shots.get(keyframe.get("shot_id"), {})
    metric = metrics.get(source_id, {}) if source_type == "metric" else {}
    comment = comments.get(source_id, {}) if source_type == "comment" else {}
    dialogue = _clean_text(source.get("transcript"))
    subtitle = _clean_text(source.get("subtitle"))
    action = _clean_text(source.get("performer_action"))
    visual_event = _clean_text(source.get("visual_event"))
    if visual_event is None and source:
        visual_event = _clean_text("；".join(
            value
            for value in (
                _clean_text(source.get("composition")),
                _clean_text(source.get("product_exposure")),
            )
            if value
        ))
    if action is None and source_type == "timeline":
        action = visual_event
    analysis_id = str(report["analysis_id"])
    evidence_id = str(evidence["id"])
    metric_values = metric.get("values") if isinstance(metric.get("values"), Mapping) else None
    return {
        "evidence_id": evidence_id,
        "qualified_evidence_id": f"{analysis_id}:{evidence_id}",
        "claim": str(evidence["claim"]),
        "source_type": source_type,
        "source_id": source_id,
        "start_ms": evidence.get("start_ms"),
        "end_ms": evidence.get("end_ms"),
        "confidence": evidence.get("confidence"),
        "is_inference": bool(evidence.get("is_inference")),
        "exact_dialogue": dialogue,
        "subtitle": subtitle,
        "action": action,
        "visual_event": visual_event,
        "metric_values": copy.deepcopy(dict(metric_values)) if metric_values is not None else None,
        "comment_text": _clean_text(comment.get("text")),
    }


def _occurrence(
    task: AnalysisTaskRecord,
    report: Mapping[str, Any],
    pattern: Mapping[str, Any],
    report_sha256: str,
) -> dict[str, Any]:
    evidence_by_id = {
        item.get("id"): item
        for item in report.get("evidence", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    occurrence_steps: list[dict[str, Any]] = []
    all_evidence: list[dict[str, Any]] = []
    for step in pattern.get("steps", []):
        if not isinstance(step, Mapping):
            continue
        snapshots = [
            _evidence_snapshot(report, evidence_by_id[evidence_id])
            for evidence_id in step.get("evidence_ids", [])
            if evidence_id in evidence_by_id
        ]
        if not snapshots:
            continue
        all_evidence.extend(snapshots)
        occurrence_steps.append({
            "order": int(step["order"]),
            "description": str(step["description"]),
            "evidence": snapshots,
        })
    timed = [
        item for item in all_evidence
        if isinstance(item.get("start_ms"), int) and isinstance(item.get("end_ms"), int)
    ]
    has_primary = any(item.get("exact_dialogue") or item.get("action") for item in timed)
    source = report["source"]
    occurrence_identity = f"{report['analysis_id']}:{pattern['id']}"
    return {
        "occurrence_id": f"occurrence_{_sha256(occurrence_identity)[:32]}",
        "analysis_task_id": task.task_id,
        "media_task_id": task.media_task_id,
        "analysis_id": report["analysis_id"],
        "analysis_revision": int(report["revision"]),
        "report_sha256": report_sha256,
        "video_id": report["video_id"],
        "source_id": source["source_id"],
        "source_type": source["source_type"],
        "source_name": _source_name(source.get("source_uri"), source["source_id"]),
        "pattern_id": pattern["id"],
        "pattern_name": pattern["name"],
        "pattern_mechanism": pattern["mechanism"],
        "accepted_by": report["review"]["reviewer"],
        "accepted_at": report["review"]["reviewed_at"],
        "start_ms": min((item["start_ms"] for item in timed), default=None),
        "end_ms": max((item["end_ms"] for item in timed), default=None),
        "has_primary_evidence": has_primary,
        "steps": occurrence_steps,
    }


def _evidence_level(occurrences: Iterable[Mapping[str, Any]]) -> str:
    found_inference = False
    for occurrence in occurrences:
        for step in occurrence.get("steps", []):
            for evidence in step.get("evidence", []):
                if evidence.get("source_type") == "metric" and isinstance(evidence.get("metric_values"), Mapping):
                    if any(value is not None for value in evidence["metric_values"].values()):
                        return "metric_correlation"
                found_inference = found_inference or bool(evidence.get("is_inference"))
    return "content_inference" if found_inference else "content_observation"


def _candidate_from_group(cluster_key: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    entries = sorted(entries, key=lambda item: item["occurrence"]["occurrence_id"])
    representative = max(
        entries,
        key=lambda item: (
            bool(item["occurrence"]["has_primary_evidence"]),
            item["occurrence"]["analysis_revision"],
            item["occurrence"]["accepted_at"],
            item["occurrence"]["occurrence_id"],
        ),
    )
    occurrences_by_id = {item["occurrence"]["occurrence_id"]: item["occurrence"] for item in entries}
    occurrences = sorted(occurrences_by_id.values(), key=lambda item: (item["video_id"], item["occurrence_id"]))
    video_count = len({item["video_id"] for item in occurrences})
    pattern = representative["pattern"]
    return {
        "schema_version": SKILL_SCHEMA_VERSION,
        "candidate_id": f"candidate_{cluster_key[:32]}",
        "cluster_key": cluster_key,
        "active": True,
        "suggested_name": pattern["name"],
        "suggested_mechanism": pattern["mechanism"],
        "suggested_reuse_mode": representative["reuse_mode"],
        "classification": "common_candidate" if video_count >= 2 else "single_video",
        "is_common": video_count >= 2,
        "evidence_level": _evidence_level(occurrences),
        "causality_status": "not_established",
        "distinct_video_count": video_count,
        "occurrence_count": len(occurrences),
        "steps": [
            {"order": int(item["order"]), "description": str(item["description"])}
            for item in pattern.get("steps", [])
            if isinstance(item, Mapping)
        ],
        "necessary_conditions": list(dict.fromkeys(str(item) for item in pattern.get("necessary_conditions", []))),
        "failure_signals": list(dict.fromkeys(str(item) for item in pattern.get("failure_signals", []))),
        "occurrences": occurrences,
    }


def _skill_source_metadata(
    skill: Mapping[str, Any],
    *,
    candidate_exists: bool,
    candidate_active: bool = False,
    candidate_revision: int | None = None,
) -> dict[str, Any]:
    if not candidate_exists:
        source_status = "missing"
    elif not candidate_active:
        source_status = "inactive"
    elif candidate_revision != int(skill["based_on_candidate_revision"]):
        source_status = "updated"
    else:
        source_status = "current"

    if skill["status"] != "approved":
        reason_code = "skill_disabled"
        reason = "Skill 已停用，请先确认来源后重新启用"
    elif skill["reuse_mode"] != "reuse":
        reason_code = "avoid_only"
        reason = "避坑 Skill 仅供参考，不能用于 S5 脚本生成"
    elif source_status == "missing":
        reason_code = "source_missing"
        reason = "Skill 的候选证据已不可用，请重新确认"
    elif source_status == "inactive":
        reason_code = "source_inactive"
        reason = "Skill 的来源证据已失效，请重新确认候选"
    elif source_status == "updated":
        reason_code = "source_updated"
        reason = "Skill 的候选证据发生变化，请核对新证据并更新正式 Skill"
    else:
        reason_code = "eligible"
        reason = None
    return {
        "source_status": source_status,
        "update_available": source_status != "current",
        "eligibility": {
            "s5_eligible": reason_code == "eligible",
            "reason_code": reason_code,
            "reason": reason,
        },
    }


class ViralSkillStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS viral_skill_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    cluster_key TEXT NOT NULL UNIQUE,
                    revision INTEGER NOT NULL,
                    active INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    refreshed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS viral_skills (
                    skill_id TEXT PRIMARY KEY,
                    origin_candidate_id TEXT NOT NULL UNIQUE,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reuse_mode TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(origin_candidate_id) REFERENCES viral_skill_candidates(candidate_id)
                );
                CREATE TABLE IF NOT EXISTS viral_skill_versions (
                    skill_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(skill_id, revision),
                    FOREIGN KEY(skill_id) REFERENCES viral_skills(skill_id)
                );
                CREATE INDEX IF NOT EXISTS idx_viral_candidates_active
                    ON viral_skill_candidates(active, refreshed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_viral_skills_status
                    ON viral_skills(status, reuse_mode, updated_at DESC);
                """
            )

    @staticmethod
    def _candidate_from_row(
        row: sqlite3.Row,
        linked_row: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = json.loads(row["payload_json"])
        payload.update({
            "revision": int(row["revision"]),
            "active": bool(row["active"]),
            "refreshed_at": row["refreshed_at"],
        })
        if linked_row is None:
            payload["linked_skill"] = None
        else:
            skill = json.loads(linked_row["payload_json"])
            payload["linked_skill"] = {
                "skill_id": linked_row["skill_id"],
                "revision": int(linked_row["revision"]),
                "status": linked_row["status"],
                "reuse_mode": linked_row["reuse_mode"],
                **_skill_source_metadata(
                    skill,
                    candidate_exists=True,
                    candidate_active=bool(row["active"]),
                    candidate_revision=int(row["revision"]),
                ),
            }
        return payload

    def reconcile_candidates(self, candidates: Iterable[Mapping[str, Any]]) -> None:
        now = _now_iso()
        incoming = {str(item["candidate_id"]): copy.deepcopy(dict(item)) for item in candidates}
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = {
                row["candidate_id"]: row
                for row in connection.execute("SELECT * FROM viral_skill_candidates").fetchall()
            }
            for candidate_id, core in incoming.items():
                core.pop("revision", None)
                core.pop("refreshed_at", None)
                core.pop("linked_skill", None)
                content_hash = _sha256(_canonical_json(core))
                row = rows.get(candidate_id)
                if row is not None and row["content_hash"] == content_hash and bool(row["active"]):
                    continue
                revision = 1 if row is None else int(row["revision"]) + 1
                payload = {**core, "revision": revision, "refreshed_at": now}
                if row is None:
                    connection.execute(
                        """INSERT INTO viral_skill_candidates
                           (candidate_id, cluster_key, revision, active, content_hash, payload_json, refreshed_at)
                           VALUES (?, ?, ?, 1, ?, ?, ?)""",
                        (candidate_id, core["cluster_key"], revision, content_hash, _canonical_json(payload), now),
                    )
                else:
                    connection.execute(
                        """UPDATE viral_skill_candidates
                           SET cluster_key=?, revision=?, active=1, content_hash=?, payload_json=?, refreshed_at=?
                           WHERE candidate_id=?""",
                        (core["cluster_key"], revision, content_hash, _canonical_json(payload), now, candidate_id),
                    )
            for candidate_id, row in rows.items():
                if candidate_id in incoming or not bool(row["active"]):
                    continue
                core = json.loads(row["payload_json"])
                core.update({"active": False})
                core.pop("revision", None)
                core.pop("refreshed_at", None)
                core.pop("linked_skill", None)
                revision = int(row["revision"]) + 1
                payload = {**core, "revision": revision, "refreshed_at": now}
                connection.execute(
                    """UPDATE viral_skill_candidates
                       SET revision=?, active=0, content_hash=?, payload_json=?, refreshed_at=? WHERE candidate_id=?""",
                    (revision, _sha256(_canonical_json(core)), _canonical_json(payload), now, candidate_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_candidates(
        self,
        *,
        active: bool | None = True,
        include_inactive_linked: bool = False,
    ) -> list[dict[str, Any]]:
        if active is True and include_inactive_linked:
            where = "WHERE (c.active=1 OR s.skill_id IS NOT NULL)"
            parameters: tuple[object, ...] = ()
        else:
            where = "" if active is None else "WHERE c.active=?"
            parameters = () if active is None else (int(active),)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT c.*, s.skill_id linked_skill_id, s.revision linked_revision,
                            s.status linked_status, s.reuse_mode linked_reuse_mode,
                            s.payload_json linked_payload_json
                     FROM viral_skill_candidates c
                     LEFT JOIN viral_skills s ON s.origin_candidate_id=c.candidate_id
                     {where}
                     ORDER BY c.active DESC,
                              json_extract(c.payload_json, '$.distinct_video_count') DESC,
                              c.refreshed_at DESC""",
                parameters,
            ).fetchall()
        summaries: list[dict[str, Any]] = []
        for row in rows:
            linked = None
            if row["linked_skill_id"]:
                linked = {
                    "skill_id": row["linked_skill_id"],
                    "revision": row["linked_revision"],
                    "status": row["linked_status"],
                    "reuse_mode": row["linked_reuse_mode"],
                    "payload_json": row["linked_payload_json"],
                }
            payload = self._candidate_from_row(row, linked)
            payload.pop("occurrences", None)
            summaries.append(payload)
        return summaries

    def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT c.*, s.skill_id linked_skill_id, s.revision linked_revision,
                          s.status linked_status, s.reuse_mode linked_reuse_mode,
                          s.payload_json linked_payload_json
                   FROM viral_skill_candidates c
                   LEFT JOIN viral_skills s ON s.origin_candidate_id=c.candidate_id
                   WHERE c.candidate_id=?""",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise SkillNotFoundError("找不到这个爆点 Skill 候选")
        linked = None
        if row["linked_skill_id"]:
            linked = {
                "skill_id": row["linked_skill_id"],
                "revision": row["linked_revision"],
                "status": row["linked_status"],
                "reuse_mode": row["linked_reuse_mode"],
                "payload_json": row["linked_payload_json"],
            }
        return self._candidate_from_row(row, linked)

    @staticmethod
    def _skill_from_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(row["payload_json"])
        validate_or_raise("viral_skill", payload)
        return payload

    def get_skill(self, skill_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM viral_skills WHERE skill_id=?", (skill_id,)).fetchone()
        if row is None:
            raise SkillNotFoundError("找不到这个正式爆点 Skill")
        return self._skill_from_row(row)

    @staticmethod
    def _skill_view_from_row(row: sqlite3.Row) -> dict[str, Any]:
        skill = ViralSkillStore._skill_from_row(row)
        return {
            **skill,
            **_skill_source_metadata(
                skill,
                candidate_exists=row["source_candidate_id"] is not None,
                candidate_active=bool(row["source_candidate_active"]),
                candidate_revision=(
                    int(row["source_candidate_revision"])
                    if row["source_candidate_revision"] is not None
                    else None
                ),
            ),
        }

    def get_skill_view(self, skill_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT s.*, c.candidate_id source_candidate_id,
                          c.revision source_candidate_revision, c.active source_candidate_active
                   FROM viral_skills s
                   LEFT JOIN viral_skill_candidates c ON c.candidate_id=s.origin_candidate_id
                   WHERE s.skill_id=?""",
                (skill_id,),
            ).fetchone()
        if row is None:
            raise SkillNotFoundError("找不到这个正式爆点 Skill")
        return self._skill_view_from_row(row)

    def list_skills(
        self,
        *,
        status: Literal["approved", "disabled"] | None = None,
        reuse_mode: Literal["reuse", "avoid"] | None = None,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        parameters: list[object] = []
        if status is not None:
            conditions.append("status=?")
            parameters.append(status)
        if reuse_mode is not None:
            conditions.append("reuse_mode=?")
            parameters.append(reuse_mode)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM viral_skills{where} ORDER BY updated_at DESC",
                tuple(parameters),
            ).fetchall()
        return [self._skill_from_row(row) for row in rows]

    def list_skill_views(
        self,
        *,
        status: Literal["approved", "disabled"] | None = None,
        reuse_mode: Literal["reuse", "avoid"] | None = None,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        parameters: list[object] = []
        if status is not None:
            conditions.append("s.status=?")
            parameters.append(status)
        if reuse_mode is not None:
            conditions.append("s.reuse_mode=?")
            parameters.append(reuse_mode)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT s.*, c.candidate_id source_candidate_id,
                           c.revision source_candidate_revision, c.active source_candidate_active
                    FROM viral_skills s
                    LEFT JOIN viral_skill_candidates c ON c.candidate_id=s.origin_candidate_id
                    {where}
                    ORDER BY s.updated_at DESC""",
                tuple(parameters),
            ).fetchall()
        return [self._skill_view_from_row(row) for row in rows]

    def approve_candidate(
        self,
        candidate_id: str,
        *,
        expected_candidate_revision: int,
        expected_skill_revision: int | None,
        reviewer: str,
        reuse_mode: Literal["reuse", "avoid"],
        name: str,
        mechanism: str,
        note: str | None,
    ) -> dict[str, Any]:
        clean_reviewer = " ".join(reviewer.split()).strip()
        clean_name = " ".join(name.split()).strip()
        clean_mechanism = " ".join(mechanism.split()).strip()
        clean_note = " ".join(note.split()).strip() if note else None
        if not clean_reviewer or not clean_name or not clean_mechanism:
            raise ValueError("审核人、Skill 名称和机制说明不能为空")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            candidate_row = connection.execute(
                "SELECT * FROM viral_skill_candidates WHERE candidate_id=?", (candidate_id,),
            ).fetchone()
            if candidate_row is None:
                raise SkillNotFoundError("找不到这个爆点 Skill 候选")
            if int(candidate_row["revision"]) != expected_candidate_revision:
                raise SkillConflictError("Skill 候选已更新，请刷新后重新确认")
            if not bool(candidate_row["active"]):
                raise ValueError("这个候选的来源报告已不再是接受状态")
            candidate = self._candidate_from_row(candidate_row)
            if reuse_mode == "reuse":
                video_ids = {item.get("video_id") for item in candidate.get("occurrences", [])}
                primary_video_ids = {
                    item.get("video_id") for item in candidate.get("occurrences", [])
                    if item.get("has_primary_evidence")
                }
                missing_primary_videos = sorted(str(item) for item in video_ids - primary_video_ids if item)
                if missing_primary_videos:
                    raise ValueError(
                        "正向复用 Skill 的每条视频都必须有带时间码的原话或动作证据："
                        + "、".join(missing_primary_videos)
                    )
            existing = connection.execute(
                "SELECT * FROM viral_skills WHERE origin_candidate_id=?", (candidate_id,),
            ).fetchone()
            if existing is None:
                if expected_skill_revision is not None:
                    raise SkillConflictError("正式 Skill 状态已变化，请刷新后重试")
                skill_id = f"skill_{_sha256('viral-skill-v1:' + candidate_id)[:32]}"
                revision = 1
                created_at = _now_iso()
                action = "approved"
            else:
                if expected_skill_revision != int(existing["revision"]):
                    raise SkillConflictError("正式 Skill 已被其他人修改，请刷新后重试")
                skill_id = existing["skill_id"]
                revision = int(existing["revision"]) + 1
                created_at = existing["created_at"]
                action = "updated"
            updated_at = _now_iso()
            occurrences = candidate["occurrences"]
            representative = max(
                occurrences,
                key=lambda item: (
                    bool(item.get("has_primary_evidence")),
                    item.get("analysis_revision", 0),
                    item.get("accepted_at", ""),
                    item.get("occurrence_id", ""),
                ),
            )
            skill = {
                "schema_version": SKILL_SCHEMA_VERSION,
                "skill_id": skill_id,
                "origin_candidate_id": candidate_id,
                "based_on_candidate_revision": int(candidate["revision"]),
                "revision": revision,
                "status": "approved",
                "reuse_mode": reuse_mode,
                "name": clean_name,
                "mechanism": clean_mechanism,
                "classification": candidate["classification"],
                "evidence_level": candidate["evidence_level"],
                "causality_status": "not_established",
                "distinct_video_count": candidate["distinct_video_count"],
                "occurrence_count": candidate["occurrence_count"],
                "representative_occurrence_id": representative["occurrence_id"],
                "steps": copy.deepcopy(candidate["steps"]),
                "necessary_conditions": copy.deepcopy(candidate["necessary_conditions"]),
                "failure_signals": copy.deepcopy(candidate["failure_signals"]),
                "occurrences": copy.deepcopy(occurrences),
                "review": {"reviewer": clean_reviewer, "reviewed_at": updated_at, "note": clean_note},
                "created_at": created_at,
                "updated_at": updated_at,
            }
            validate_or_raise("viral_skill", skill)
            encoded = _canonical_json(skill)
            if existing is None:
                connection.execute(
                    """INSERT INTO viral_skills
                       (skill_id, origin_candidate_id, revision, status, reuse_mode, payload_json, created_at, updated_at)
                       VALUES (?, ?, ?, 'approved', ?, ?, ?, ?)""",
                    (skill_id, candidate_id, revision, reuse_mode, encoded, created_at, updated_at),
                )
            else:
                cursor = connection.execute(
                    """UPDATE viral_skills
                       SET revision=?, status='approved', reuse_mode=?, payload_json=?, updated_at=?
                       WHERE skill_id=? AND revision=?""",
                    (revision, reuse_mode, encoded, updated_at, skill_id, expected_skill_revision),
                )
                if cursor.rowcount != 1:
                    raise SkillConflictError("正式 Skill 已被其他人修改，请刷新后重试")
            connection.execute(
                """INSERT INTO viral_skill_versions
                   (skill_id, revision, action, actor, created_at, payload_json) VALUES (?, ?, ?, ?, ?, ?)""",
                (skill_id, revision, action, clean_reviewer, updated_at, encoded),
            )
            connection.commit()
            return skill
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def set_skill_status(
        self,
        skill_id: str,
        *,
        expected_revision: int,
        status: Literal["approved", "disabled"],
        reviewer: str,
        note: str | None,
    ) -> dict[str, Any]:
        clean_reviewer = " ".join(reviewer.split()).strip()
        clean_note = " ".join(note.split()).strip() if note else None
        if not clean_reviewer:
            raise ValueError("请填写审核人")
        if status == "disabled" and not clean_note:
            raise ValueError("停用 Skill 时请填写原因")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM viral_skills WHERE skill_id=?", (skill_id,)).fetchone()
            if row is None:
                raise SkillNotFoundError("找不到这个正式爆点 Skill")
            if int(row["revision"]) != expected_revision:
                raise SkillConflictError("正式 Skill 已被其他人修改，请刷新后重试")
            if status == "approved":
                candidate = connection.execute(
                    "SELECT revision, active FROM viral_skill_candidates WHERE candidate_id=?",
                    (row["origin_candidate_id"],),
                ).fetchone()
                skill_before = json.loads(row["payload_json"])
                if (
                    candidate is None
                    or not bool(candidate["active"])
                    or int(candidate["revision"]) != int(skill_before["based_on_candidate_revision"])
                ):
                    raise ValueError("候选证据已变化，请从候选详情重新确认后启用")
            skill = json.loads(row["payload_json"])
            updated_at = _now_iso()
            skill.update({
                "revision": expected_revision + 1,
                "status": status,
                "review": {"reviewer": clean_reviewer, "reviewed_at": updated_at, "note": clean_note},
                "updated_at": updated_at,
            })
            validate_or_raise("viral_skill", skill)
            encoded = _canonical_json(skill)
            cursor = connection.execute(
                """UPDATE viral_skills SET revision=?, status=?, payload_json=?, updated_at=?
                   WHERE skill_id=? AND revision=?""",
                (skill["revision"], status, encoded, updated_at, skill_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise SkillConflictError("正式 Skill 已被其他人修改，请刷新后重试")
            connection.execute(
                """INSERT INTO viral_skill_versions
                   (skill_id, revision, action, actor, created_at, payload_json) VALUES (?, ?, ?, ?, ?, ?)""",
                (skill_id, skill["revision"], status, clean_reviewer, updated_at, encoded),
            )
            connection.commit()
            return skill
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def refresh_skill_candidates(
    store: ViralSkillStore,
    queue: AnalysisTaskQueue,
    *,
    include_fixtures: bool = False,
) -> dict[str, int]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for task in queue.completed_reports():
        if task.model_purpose != "analysis" or (task.fixture_data and not include_fixtures) or not task.result_path:
            continue
        report_path = Path(task.result_path).expanduser().resolve()
        try:
            report = load_report(report_path)
            report_hash = _sha256(report_path.read_bytes())
        except (OSError, ValueError):
            continue
        if report.get("status") != "accepted":
            continue
        if report.get("fixture_data") and not include_fixtures:
            continue
        if report.get("processing", {}).get("purpose") == "video_review":
            continue
        for pattern in report.get("pattern_candidates", []):
            if not isinstance(pattern, Mapping):
                continue
            reuse_mode = _inferred_reuse_mode(pattern)
            cluster_key = _cluster_key(pattern, reuse_mode)
            occurrence = _occurrence(task, report, pattern, report_hash)
            if not occurrence["steps"]:
                continue
            groups.setdefault(cluster_key, []).append({
                "pattern": copy.deepcopy(dict(pattern)),
                "occurrence": occurrence,
                "reuse_mode": reuse_mode,
            })
    candidates = [_candidate_from_group(cluster_key, entries) for cluster_key, entries in sorted(groups.items())]
    store.reconcile_candidates(candidates)
    summaries = store.list_candidates()
    return {
        "candidate_count": len(summaries),
        "common_candidate_count": sum(bool(item["is_common"]) for item in summaries),
        "approved_skill_count": sum(
            item["eligibility"]["s5_eligible"]
            for item in store.list_skill_views(status="approved", reuse_mode="reuse")
        ),
    }
