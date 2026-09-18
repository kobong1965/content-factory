"""Versioned local report editing and explicit human review for S3."""

from __future__ import annotations

import copy
import json
import os
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from content_factory_contracts import validate_or_raise

_lock = threading.Lock()
_EDITABLE_FIELDS = {
    "summary", "shots", "timeline", "content_structure", "emotion_curve", "comments",
    "comment_insights", "evidence", "scores", "pattern_candidates",
}


class ReportConflictError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def load_report(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("分析报告无法读取") from exc
    if not isinstance(payload, dict):
        raise ValueError("分析报告顶层不是对象")
    validate_or_raise("analysis", payload)
    return payload


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _archive(path: Path, report: Mapping[str, Any]) -> None:
    revisions = path.parent / "revisions"
    revisions.mkdir(parents=True, exist_ok=True)
    target = revisions / f"analysis-report-r{report['revision']:04d}.json"
    if not target.exists():
        _atomic(target, report)


def update_report(path: str | Path, *, expected_revision: int, changes: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(changes) - _EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"这些字段不能人工修改：{', '.join(sorted(unknown))}")
    report_path = Path(path).expanduser().resolve()
    with _lock:
        current = load_report(report_path)
        if current["revision"] != expected_revision:
            raise ReportConflictError("报告已经被其他人修改，请刷新后再保存")
        updated = copy.deepcopy(current)
        updated.update(copy.deepcopy(dict(changes)))
        updated["revision"] = current["revision"] + 1
        updated["status"] = "draft"
        updated["review"] = {"reviewer": None, "reviewed_at": None, "note": None}
        validate_or_raise("analysis", updated)
        _archive(report_path, current)
        _atomic(report_path, updated)
        return updated


def review_report(
    path: str | Path,
    *,
    expected_revision: int,
    status: Literal["reviewed", "accepted"],
    reviewer: str,
    note: str | None,
) -> dict[str, Any]:
    clean_reviewer = reviewer.strip()
    if not clean_reviewer:
        raise ValueError("请填写审核人")
    report_path = Path(path).expanduser().resolve()
    with _lock:
        current = load_report(report_path)
        if current["revision"] != expected_revision:
            raise ReportConflictError("报告已经被其他人修改，请刷新后再审核")
        updated = copy.deepcopy(current)
        updated["revision"] = current["revision"] + 1
        updated["status"] = status
        updated["review"] = {"reviewer": clean_reviewer, "reviewed_at": _now_iso(), "note": note.strip() if note else None}
        validate_or_raise("analysis", updated)
        _archive(report_path, current)
        _atomic(report_path, updated)
        return updated
