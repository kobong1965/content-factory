"""Versioned S5 script editing and explicit human review."""

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

from .s5_generation import machine_review_issues
from .s5_production_policy import (
    ProductionPolicyError,
    validate_candidate_against_policy,
    validate_policy_eligibility_snapshot,
    validate_script_for_approval,
)
from .s5_queue import ScriptTaskQueue, ScriptTaskRecord
from .s5_sources import script_product_eligibility

_lock = threading.RLock()
_EDITABLE_FIELDS = {
    "content_goal", "target_audience", "selected_version_id", "versions", "shooting_order", "material_checklist",
}


class ScriptConflictError(RuntimeError):
    pass


class ScriptNotFoundError(LookupError):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _load_json(path: Path, error_message: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(error_message) from exc
    if not isinstance(payload, dict):
        raise ValueError(error_message)
    return payload


def load_script(path: str | Path) -> dict[str, Any]:
    script_path = Path(path).expanduser().resolve()
    script = _load_json(script_path, "脚本文件无法读取")
    frozen = _load_json(script_path.parent / "input.json", "脚本冻结输入无法读取")
    validate_or_raise("script", script, related={"product": frozen["product"], "analysis": frozen["analysis"]})
    return script


def frozen_inputs(path: str | Path) -> dict[str, Any]:
    script_path = Path(path).expanduser().resolve()
    return _load_json(script_path.parent / "input.json", "脚本冻结输入无法读取")


def _task_for_script(queue: ScriptTaskQueue, script_id: str) -> ScriptTaskRecord:
    task = queue.completed_for_script(script_id)
    if task is not None:
        return task
    raise ScriptNotFoundError("找不到这个脚本")


def get_script(queue: ScriptTaskQueue, script_id: str) -> dict[str, Any]:
    return load_script(_task_for_script(queue, script_id).result_path or "")


def list_scripts(queue: ScriptTaskQueue) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for task in queue.completed():
        try:
            script = load_script(task.result_path)
            frozen = frozen_inputs(task.result_path)
        except ValueError:
            continue
        pattern = frozen["pattern"]
        skill = frozen.get("skill") if isinstance(frozen.get("skill"), Mapping) else None
        product = frozen["product"]
        summaries.append({
            "script_id": script["script_id"], "task_id": task.task_id, "product_name": product["name"],
            "product_sku": product["sku"], "pattern_name": skill.get("name", pattern["name"]) if skill else pattern["name"],
            "skill_id": skill.get("skill_id") if skill else None,
            "skill_revision": skill.get("revision") if skill else None,
            "review_status": script["review"]["status"], "revision": script["revision"],
            "version_count": len(script["versions"]), "target_audience": script["target_audience"],
            "updated_at": script["updated_at"],
        })
    return sorted(summaries, key=lambda item: item["updated_at"], reverse=True)


def skill_usage_counts(queue: ScriptTaskQueue) -> dict[str, int]:
    """Count completed script packages that froze each formal Skill revision."""

    counts: dict[str, int] = {}
    for task in queue.completed():
        try:
            load_script(task.result_path)
            frozen = frozen_inputs(task.result_path)
        except ValueError:
            continue
        skill = frozen.get("skill")
        if not isinstance(skill, Mapping):
            continue
        skill_id = skill.get("skill_id")
        if isinstance(skill_id, str) and skill_id:
            counts[skill_id] = counts.get(skill_id, 0) + 1
    return counts


def script_counts(queue: ScriptTaskQueue) -> dict[str, int]:
    counts = {"draft": 0, "pending": 0, "approved": 0, "rejected": 0, "accepted_real": 0}
    for task in queue.completed():
        try:
            script = load_script(task.result_path)
            frozen = frozen_inputs(task.result_path)
        except ValueError:
            continue
        status = script["review"]["status"]
        counts[status] += 1
        if status == "approved" and script.get("fixture_data") is False:
            try:
                validate_script_for_approval(script, frozen)
            except ProductionPolicyError:
                pass
            else:
                counts["accepted_real"] += 1
    return counts


def _append_revision(path: Path, script: Mapping[str, Any], *, action: str, actor: str) -> None:
    _atomic_json(path.parent / "revisions" / f"script-r{script['revision']:04d}.json", script)
    log_path = path.parent / "revision-log.json"
    try:
        log = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log = []
    if not isinstance(log, list):
        log = []
    log.append({
        "revision": script["revision"], "action": action, "actor": actor,
        "created_at": script["updated_at"], "review_status": script["review"]["status"],
    })
    _atomic_json(log_path, log)


def update_script(
    queue: ScriptTaskQueue, script_id: str, *, expected_revision: int, changes: Mapping[str, Any], actor: str,
) -> dict[str, Any]:
    unknown = set(changes) - _EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"这些脚本字段不能修改：{', '.join(sorted(unknown))}")
    clean_actor = actor.strip()
    if not clean_actor:
        raise ValueError("请填写本次修改人")
    path = Path(_task_for_script(queue, script_id).result_path or "").resolve()
    with _lock:
        current = load_script(path)
        if current["revision"] != expected_revision:
            raise ScriptConflictError("脚本已经被其他人修改，请刷新后再保存")
        frozen = frozen_inputs(path)
        updated = copy.deepcopy(current)
        updated.update(copy.deepcopy(dict(changes)))
        updated["revision"] = current["revision"] + 1
        updated["updated_at"] = _now_iso()
        updated["review"] = {
            "status": "pending", "checked_by": None, "checked_at": None, "note": None, "issues": [],
        }
        updated["review"]["issues"] = machine_review_issues(updated)
        if frozen.get("production_policy") is not None:
            validate_candidate_against_policy(updated, frozen)
        validate_or_raise("script", updated, related={"product": frozen["product"], "analysis": frozen["analysis"]})
        _atomic_json(path, updated)
        _append_revision(path, updated, action="updated", actor=clean_actor)
        return updated


def review_script(
    queue: ScriptTaskQueue,
    script_id: str,
    *,
    expected_revision: int,
    status: Literal["approved", "rejected"],
    reviewer: str,
    note: str | None,
    current_product: Mapping[str, Any] | None,
    current_analysis: Mapping[str, Any] | None,
    current_skill: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    clean_reviewer = reviewer.strip()
    if not clean_reviewer:
        raise ValueError("请填写审核人")
    clean_note = note.strip() if note else None
    if status == "rejected" and not clean_note:
        raise ValueError("驳回脚本时请填写原因")
    path = Path(_task_for_script(queue, script_id).result_path or "").resolve()
    with _lock:
        current = load_script(path)
        if current["revision"] != expected_revision:
            raise ScriptConflictError("脚本已经被其他人修改，请刷新后再审核")
        frozen = frozen_inputs(path)
        if status == "approved":
            frozen_skill = frozen.get("skill")
            if isinstance(frozen_skill, Mapping):
                if current_skill is None:
                    raise ValueError("引用的爆点 Skill 已不存在或已停用，请重新选择后生成脚本")
                if current_skill.get("status") != "approved" or current_skill.get("reuse_mode") != "reuse":
                    raise ValueError("引用的爆点 Skill 已停用或不允许用于脚本，请重新生成")
                if current_skill.get("skill_id") != frozen_skill.get("skill_id"):
                    raise ValueError("引用的爆点 Skill 已变化，请重新生成脚本")
                if current_skill.get("revision") != frozen_skill.get("revision"):
                    raise ValueError("爆点 Skill 在生成后发生变化，请用最新版本重新生成脚本")
                current_eligibility = current_skill.get("eligibility")
                if (
                    isinstance(current_eligibility, Mapping)
                    and current_eligibility.get("s5_eligible") is not True
                ):
                    raise ValueError(
                        str(current_eligibility.get("reason") or "爆点 Skill 的来源证据已失效，请重新生成")
                    )
            if current_product is None:
                raise ValueError("商品已不存在，请重新确认商品后再批准")
            if current_product.get("revision") != current["product_revision"]:
                raise ValueError("商品资料在生成后发生变化，请用最新商品重新生成脚本")
            eligibility = script_product_eligibility(current_product)
            validate_policy_eligibility_snapshot(frozen, current_product, eligibility)
            if current_analysis is None or current_analysis.get("status") != "accepted":
                raise ValueError("来源分析已不是接受状态，请重新审核分析")
            if current_analysis.get("revision") != current["source_analysis_revision"]:
                raise ValueError("来源分析在生成后发生变化，请用最新分析重新生成脚本")
            validate_script_for_approval(current, frozen)
        updated = copy.deepcopy(current)
        updated["revision"] = current["revision"] + 1
        updated["updated_at"] = _now_iso()
        updated["review"] = {
            "status": status, "checked_by": clean_reviewer, "checked_at": updated["updated_at"],
            "note": clean_note, "issues": machine_review_issues(updated),
        }
        validate_or_raise("script", updated, related={"product": frozen["product"], "analysis": frozen["analysis"]})
        _atomic_json(path, updated)
        _append_revision(path, updated, action=status, actor=clean_reviewer)
        return updated


def list_revisions(queue: ScriptTaskQueue, script_id: str) -> list[dict[str, Any]]:
    path = Path(_task_for_script(queue, script_id).result_path or "").resolve().parent / "revision-log.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("脚本版本记录无法读取") from exc
    if not isinstance(payload, list):
        raise ValueError("脚本版本记录格式错误")
    return sorted((dict(item) for item in payload if isinstance(item, Mapping)), key=lambda item: item["revision"], reverse=True)
