from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import threading
import time

import pytest
from fastapi.testclient import TestClient

from content_factory_api import s3, s4, s5, s5_queue as s5_queue_module
from content_factory_api.main import app
from content_factory_api.s3_analysis import AnalysisArtifacts
from content_factory_api.s3_gateway import GatewayResult
from content_factory_api.s3_queue import AnalysisTaskQueue
from content_factory_api.s3_settings import GatewayConfig
from content_factory_api.s3_skills import ViralSkillStore, refresh_skill_candidates
from content_factory_api.s4_store import ProductStore
from content_factory_api.s5_generation import (
    ScriptArtifacts,
    ScriptGenerationError,
    build_generation_context,
    machine_review_issues,
    model_output_schema,
    process_script_generation,
)
from content_factory_api.s5_production_policy import (
    LOCKED_CAMERA,
    LOCKED_EQUIPMENT,
    LOCKED_SCENE,
    build_production_policy,
)
from content_factory_api.s5_queue import ScriptTaskQueue
from content_factory_api.s5_scripts import (
    ScriptConflictError,
    get_script,
    list_scripts,
    review_script,
    skill_usage_counts,
    update_script,
)
from content_factory_api.s5_sources import (
    list_script_products,
    list_templates,
    resolve_product,
    script_product_eligibility,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PROJECT_ROOT / "packages" / "contracts" / "fixtures"
CONFIG = GatewayConfig(
    "http://127.0.0.1:9999/v1", "fixture-model", "responses", "fixture-secret", "2026-08-29T06:00:00Z",
)


def _json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _accepted_analysis(queue: AnalysisTaskQueue, root: Path) -> dict:
    report = _json("analysis.valid.json")
    report["fixture_data"] = False
    report["status"] = "accepted"
    report["revision"] = 2
    report["review"] = {
        "reviewer": "分析审核人", "reviewed_at": "2026-08-29T08:00:00Z", "note": "机制可用于脚本",
    }
    media = root / "media-result.json"
    media.write_text("{}", encoding="utf-8")
    task = queue.enqueue(
        media_task_id="media_00000000000000000000000000000001", media_result_path=media,
        workspace_path=root / "analysis", input_payload={"metric_snapshots": [], "comments": []}, fixture_data=False,
    )
    claimed = queue.claim_next()
    assert claimed is not None and claimed.task_id == task.task_id
    task_dir = Path(claimed.workspace_path)
    task_dir.mkdir(parents=True, exist_ok=True)
    report_path = task_dir / "analysis-report.json"
    ocr_path = task_dir / "ocr-result.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    ocr_path.write_text("{}", encoding="utf-8")
    assert claimed.worker_id is not None
    queue.complete(
        task.task_id,
        AnalysisArtifacts(report["analysis_id"], report_path, ocr_path),
        worker_id=claimed.worker_id,
    )
    return report


def _active_product(store: ProductStore) -> dict:
    fixture = _json("product.valid.json")
    created = store.create(sku="XZ-2308", name="垂感直筒西裤", actor="运营甲")
    changes = {
        key: deepcopy(fixture[key])
        for key in (
            "sources", "facts", "selling_point_fact_ids", "forbidden_expressions", "unprovable_claims",
            "brand_boundary_confirmed_by", "brand_boundary_confirmed_at", "shooting_constraints",
        )
    }
    saved = store.update(created["product_id"], expected_revision=1, changes=changes, actor="运营甲")
    return store.set_status(saved["product_id"], expected_revision=2, status="active", actor="负责人乙")


def _input(product: dict, analysis: dict, *, version_count: int = 3) -> dict:
    eligibility = script_product_eligibility(product)
    return {
        "product": product,
        "analysis": analysis,
        "pattern": deepcopy(analysis["pattern_candidates"][0]),
        "request": {"content_goal": "seeding", "target_audience": "关注通勤裤装的成年男性", "version_count": version_count},
        "production_policy": build_production_policy(product, eligibility),
    }


def _approved_skill(queue: AnalysisTaskQueue, root: Path) -> tuple[ViralSkillStore, dict]:
    store = ViralSkillStore(root / "skills" / "viral-skills.sqlite3")
    refresh_skill_candidates(store, queue)
    candidate = store.list_candidates()[0]
    skill = store.approve_candidate(
        candidate["candidate_id"],
        expected_candidate_revision=candidate["revision"],
        expected_skill_revision=None,
        reviewer="内容负责人",
        reuse_mode="reuse",
        name=candidate["suggested_name"],
        mechanism=candidate["suggested_mechanism"],
        note="已核对原话、动作和时间码",
    )
    return store, skill


def _candidate() -> dict:
    fixture = _json("script.valid.json")
    candidate = {key: deepcopy(fixture[key]) for key in ("versions", "shooting_order", "material_checklist")}
    for version in candidate["versions"]:
        for shot in version["shots"]:
            shot["camera"] = LOCKED_CAMERA
            shot["transition"] = "无（连续长镜头）"
            shot["source_shot_id"] = None
    candidate["shooting_order"] = [{
        "scene": LOCKED_SCENE,
        "equipment": LOCKED_EQUIPMENT,
        "shot_ids": [shot["id"] for version in candidate["versions"] for shot in version["shots"]],
    }]
    return candidate


def _fake_gateway(captured: dict | None = None):
    def call(_config, **kwargs):
        if captured is not None:
            captured.update(kwargs)
        return GatewayResult(content=_candidate(), response_id="response_fixture_s5", raw_bytes=100)

    return call


def _completed_script_queue(tmp_path: Path) -> tuple[ScriptTaskQueue, dict, dict, dict]:
    analysis = _json("analysis.valid.json")
    analysis["status"] = "accepted"
    analysis["revision"] = 2
    analysis["review"] = {"reviewer": "审核人", "reviewed_at": "2026-08-29T08:00:00Z", "note": None}
    product_store = ProductStore(tmp_path / "products")
    product = _active_product(product_store)
    payload = _input(product, analysis)
    queue = ScriptTaskQueue(tmp_path / "s5" / "tasks.sqlite3")
    task = queue.enqueue(workspace_path=tmp_path / "s5", input_payload=payload)

    def processor(record, callback):
        return process_script_generation(
            task_id=record.task_id, input_payload=payload, task_directory=record.workspace_path,
            config=CONFIG, progress=callback, gateway_caller=_fake_gateway(),
        )

    queue.run_pending(gateway_config=CONFIG, processor=processor)
    completed = queue.get(task.task_id)
    assert completed is not None and completed.status == "completed" and completed.script_id
    return queue, product, analysis, payload


def _completed_skill_script_queue(tmp_path: Path) -> tuple[ScriptTaskQueue, dict, dict, dict, dict]:
    analysis = _json("analysis.valid.json")
    analysis["status"] = "accepted"
    analysis["revision"] = 2
    analysis["review"] = {"reviewer": "审核人", "reviewed_at": "2026-09-07T08:00:00Z", "note": None}
    product = _active_product(ProductStore(tmp_path / "products"))
    skill = _json("viral-skill.valid.json")
    payload = _input(product, analysis)
    payload["skill"] = skill
    queue = ScriptTaskQueue(tmp_path / "s5" / "tasks.sqlite3")
    task = queue.enqueue(workspace_path=tmp_path / "s5", input_payload=payload)

    def processor(record, callback):
        return process_script_generation(
            task_id=record.task_id, input_payload=payload, task_directory=record.workspace_path,
            config=CONFIG, progress=callback, gateway_caller=_fake_gateway(),
        )

    queue.run_pending(gateway_config=CONFIG, processor=processor)
    completed = queue.get(task.task_id)
    assert completed is not None and completed.status == "completed" and completed.script_id
    return queue, product, analysis, payload, skill


def test_s5_routes_are_exposed() -> None:
    paths = app.openapi()["paths"]
    assert "/s5/readiness" in paths
    assert "/s5/skills" in paths
    assert "/s5/generations" in paths
    assert "/s5/scripts/{script_id}/review" in paths
    assert "/s5/tasks/{task_id}/retry" in paths


def test_only_accepted_analyses_and_active_products_are_listed(tmp_path: Path) -> None:
    analysis_queue = AnalysisTaskQueue(tmp_path / "runtime" / "s3.sqlite3")
    skill_store = ViralSkillStore(tmp_path / "analysis" / "skills" / "viral-skills.sqlite3")
    product_store = ProductStore(tmp_path / "products")
    created = product_store.create(sku="XZ-2308", name="垂感直筒西裤", actor="运营甲")

    assert list_templates(analysis_queue, skill_store) == []
    assert list_script_products(product_store) == []

    _accepted_analysis(analysis_queue, tmp_path)
    active = _active_product(ProductStore(tmp_path / "active-products"))

    refresh_skill_candidates(skill_store, analysis_queue)
    assert list_templates(analysis_queue, skill_store) == []
    candidate = skill_store.list_candidates()[0]
    skill_store.approve_candidate(
        candidate["candidate_id"], expected_candidate_revision=candidate["revision"],
        expected_skill_revision=None, reviewer="内容负责人", reuse_mode="reuse",
        name=candidate["suggested_name"], mechanism=candidate["suggested_mechanism"], note=None,
    )
    assert len(list_templates(analysis_queue, skill_store)) == 1
    assert list_script_products(ProductStore(tmp_path / "active-products"))[0]["product_id"] == active["product_id"]
    assert created["status"] == "draft"


def test_script_eligible_draft_product_is_listed_and_resolved(tmp_path: Path) -> None:
    store = ProductStore(tmp_path / "products")
    fixture = _json("product.valid.json")
    created = store.create(sku="XZ-DRAFT", name="黑灰色男士牛仔裤", actor="运营甲")
    fact = deepcopy(fixture["facts"][0])
    saved = store.update(
        created["product_id"],
        expected_revision=1,
        actor="运营甲",
        changes={
            "sources": deepcopy(fixture["sources"]),
            "facts": [fact],
            "selling_point_fact_ids": [fact["id"]],
            "forbidden_expressions": [],
            "unprovable_claims": [],
            "brand_boundary_confirmed_by": None,
            "brand_boundary_confirmed_at": None,
            "shooting_constraints": deepcopy(created["shooting_constraints"]),
        },
    )

    listed = list_script_products(store)

    assert saved["status"] == "draft"
    assert saved["completeness"]["ratio"] < 1
    assert listed[0]["product_id"] == saved["product_id"]
    assert listed[0]["usable_fact_ids"] == [fact["id"]]
    assert resolve_product(store, saved["product_id"])["revision"] == saved["revision"]


def test_generation_sends_text_only_and_builds_a_valid_pending_script(tmp_path: Path) -> None:
    product = _active_product(ProductStore(tmp_path / "products"))
    product["facts"][0]["source_ref"] = str(tmp_path / "private-product-source.pdf")
    analysis = _json("analysis.valid.json")
    analysis["status"] = "accepted"
    analysis["revision"] = 2
    analysis["review"] = {"reviewer": "审核人", "reviewed_at": "2026-08-29T08:00:00Z", "note": None}
    captured: dict = {}

    artifacts = process_script_generation(
        task_id="script_task_00000000000000000000000000000001",
        input_payload=_input(product, analysis), task_directory=tmp_path / "task",
        config=CONFIG, gateway_caller=_fake_gateway(captured),
    )
    script = json.loads(artifacts.result_path.read_text(encoding="utf-8"))
    context = json.loads(captured["context_json"])

    assert script["review"]["status"] == "pending"
    assert len(script["versions"]) == 3
    assert script["selected_version_id"] == script["versions"][0]["id"]
    assert script["product_revision"] == product["revision"]
    assert captured["keyframe_data_urls"] == []
    assert captured["schema_name"] == "content_factory_script_package"
    assert context["hard_rules"]["original_video_or_local_files_included"] is False
    assert "local_path" not in captured["context_json"]
    assert str(tmp_path) not in captured["context_json"]
    assert "source_ref" not in context["product"]["confirmed_facts"][0]
    assert context["hard_rules"]["production_mode"] == "fixed_livestream_long_take"
    assert context["hard_rules"]["performer_count"] == 1
    assert context["hard_rules"]["camera_must_remain_fixed"] is True
    assert context["hard_rules"]["detail_overlays_are_optional"] is True
    assert script["production_mode"]["kind"] == "fixed_livestream_long_take"
    assert script["production_mode"]["performer_count"] == 1
    first_shot = script["versions"][0]["shots"][0]
    assert first_shot["camera"] == "固定直播间竖屏机位（不移动）"
    assert first_shot["delivery"]["tone"]
    assert first_shot["delivery"]["pacing"]
    assert first_shot["performance"]["expression"]
    assert first_shot["performance"]["eye_line"]
    assert first_shot["detail_overlay"]["mode"] in {"none", "optional_detail"}
    assert first_shot["evidence_ids"]


def test_generation_freezes_approved_skill_revision_and_cross_video_evidence(tmp_path: Path) -> None:
    queue, _product, _analysis, payload, skill = _completed_skill_script_queue(tmp_path)
    completed = queue.list()[0]
    assert completed.result_path

    script = json.loads(Path(completed.result_path).read_text(encoding="utf-8"))
    frozen = json.loads(Path(completed.input_path or "").read_text(encoding="utf-8"))
    context = json.loads(build_generation_context(payload))

    assert script["skill_id"] == skill["skill_id"]
    assert script["skill_revision"] == skill["revision"]
    assert frozen["skill"]["occurrence_count"] == 2
    assert context["approved_skill"]["skill_id"] == skill["skill_id"]
    assert context["approved_skill"]["distinct_video_count"] == 2
    assert len(context["approved_skill"]["supporting_occurrences"]) == 2
    assert skill_usage_counts(queue) == {skill["skill_id"]: 1}


def test_generation_context_caps_cross_video_skill_evidence_without_mutating_frozen_input(
    tmp_path: Path,
) -> None:
    product = _active_product(ProductStore(tmp_path / "products"))
    analysis = _json("analysis.valid.json")
    skill = _json("viral-skill.valid.json")
    representative = deepcopy(skill["occurrences"][0])
    occurrences = []
    for index in range(12):
        occurrence = deepcopy(representative)
        suffix = f"{index:032x}"
        occurrence["occurrence_id"] = f"occurrence_{suffix}"
        occurrence["video_id"] = f"video_{suffix[-24:]}"
        occurrence["analysis_revision"] = index + 1
        occurrence["accepted_at"] = f"2026-09-07T{index:02d}:00:00Z"
        occurrence["steps"][0]["evidence"][0]["qualified_evidence_id"] = (
            f"analysis_{suffix}:evidence_{suffix[-24:]}"
        )
        occurrences.append(occurrence)
    # The representative must be included first even when newer occurrences
    # would otherwise win the quality ordering.
    skill["representative_occurrence_id"] = occurrences[0]["occurrence_id"]
    skill["occurrences"] = occurrences
    skill["occurrence_count"] = len(occurrences)
    skill["distinct_video_count"] = len(occurrences)
    payload = _input(product, analysis)
    payload["skill"] = skill

    context = json.loads(build_generation_context(payload))["approved_skill"]

    assert len(payload["skill"]["occurrences"]) == 12
    assert context["total_occurrence_count"] == 12
    assert context["total_distinct_video_count"] == 12
    assert context["included_occurrence_count"] == 5
    assert context["included_distinct_video_count"] == 5
    assert context["supporting_occurrences_truncated"] is True
    assert len(context["supporting_occurrences"]) == 5
    assert context["supporting_occurrences"][0]["occurrence_id"] == occurrences[0]["occurrence_id"]
    assert len({item["video_id"] for item in context["supporting_occurrences"]}) == 5


def test_approval_rejects_disabled_or_changed_frozen_skill(tmp_path: Path) -> None:
    queue, product, analysis, _payload, skill = _completed_skill_script_queue(tmp_path)
    script_id = queue.list()[0].script_id
    assert script_id is not None

    disabled = {**skill, "status": "disabled"}
    with pytest.raises(ValueError, match="爆点 Skill.*停用|停用.*爆点 Skill"):
        review_script(
            queue, script_id, expected_revision=1, status="approved", reviewer="审核乙", note=None,
            current_product=product, current_analysis=analysis, current_skill=disabled,
        )

    changed = {**skill, "revision": skill["revision"] + 1}
    with pytest.raises(ValueError, match="爆点 Skill.*变化|变化.*爆点 Skill"):
        review_script(
            queue, script_id, expected_revision=1, status="approved", reviewer="审核乙", note=None,
            current_product=product, current_analysis=analysis, current_skill=changed,
        )

    stale_source = {
        **skill,
        "source_status": "updated",
        "update_available": True,
        "eligibility": {
            "s5_eligible": False,
            "reason_code": "source_updated",
            "reason": "Skill 的候选证据发生变化，请核对新证据",
        },
    }
    with pytest.raises(ValueError, match="候选证据发生变化"):
        review_script(
            queue, script_id, expected_revision=1, status="approved", reviewer="审核乙", note=None,
            current_product=product, current_analysis=analysis, current_skill=stale_source,
        )


def test_machine_review_only_counts_reshoots_in_selected_shooting_version() -> None:
    script = _json("script.valid.json")
    script["selected_version_id"] = "version_demo_b"
    for item in script["material_checklist"]:
        item["status"] = "reshoot"

    issues = machine_review_issues(script)

    reshoot_issue = next(item for item in issues if item["code"] == "reshoot_materials")
    assert reshoot_issue["message"] == "有 1 个镜头需要补拍，批准前请确认拍摄安排。"


def test_machine_review_legacy_script_without_selection_uses_first_version() -> None:
    script = _json("script.valid.json")
    script.pop("selected_version_id", None)
    for item in script["material_checklist"]:
        item["status"] = "reshoot"

    issues = machine_review_issues(script)

    reshoot_issue = next(item for item in issues if item["code"] == "reshoot_materials")
    assert reshoot_issue["message"] == "有 1 个镜头需要补拍，批准前请确认拍摄安排。"


def test_new_generation_schema_locks_long_take_fields_and_one_shooting_order(tmp_path: Path) -> None:
    product = _active_product(ProductStore(tmp_path / "products"))
    analysis = _json("analysis.valid.json")
    payload = _input(product, analysis)

    schema = model_output_schema(3, payload["production_policy"])
    shot_schema = schema["$defs"]["storyboardShot"]

    assert set(("delivery", "performance", "detail_overlay", "evidence_ids")).issubset(
        shot_schema["required"]
    )
    assert shot_schema["properties"]["camera"] == {"const": LOCKED_CAMERA}
    assert shot_schema["properties"]["source_shot_id"] == {"const": None}
    assert schema["properties"]["shooting_order"]["minItems"] == 1
    assert schema["properties"]["shooting_order"]["maxItems"] == 1
    order = schema["properties"]["shooting_order"]["items"]["properties"]
    assert order["scene"] == {"const": LOCKED_SCENE}
    assert order["equipment"] == {"const": LOCKED_EQUIPMENT}


def test_selected_material_context_only_contains_same_product_metadata(tmp_path: Path) -> None:
    product = _active_product(ProductStore(tmp_path / "products"))
    analysis = _json("analysis.valid.json")
    selected_source = {"asset_id": "asset_owned", "label": "同款腰头", "kind": "video", "source_ref": "file:///private-original.mp4", "managed_original_path": "E:/private-original.mp4"}
    product["sources"].append(deepcopy(selected_source))
    payload = {
        "product": product, "analysis": analysis,
        "pattern": deepcopy(analysis["pattern_candidates"][0]),
        "request": {"content_goal": "seeding", "target_audience": "通勤成年男性", "version_count": 3},
        "production_policy": build_production_policy(product, {"eligible": True, "usable_fact_ids": [product["selling_point_fact_ids"][0]], "unknown_fields": [], "reasons": []}),
        "selected_product_assets": [selected_source, {"asset_id": "asset_other_product", "label": "不应进入上下文", "kind": "video"}],
        "product_workspace": {"notes": "待确认功效不能自动变事实"},
    }
    before = deepcopy(payload)
    raw = build_generation_context(payload)
    context = json.loads(raw)
    assert context["available_same_product_materials"] == [{"asset_id": "asset_owned", "label": "同款腰头", "kind": "video"}]
    assert "private-original" not in raw
    assert "不应进入上下文" not in raw
    assert "待确认功效不能自动变事实" not in raw
    assert context["hard_rules"]["material_names_are_not_confirmed_product_facts"] is True
    assert context["hard_rules"]["material_list_is_not_a_claim_that_files_were_analyzed"] is True
    assert payload == before


def test_generation_context_only_contains_script_eligible_facts_and_unknown_fields(tmp_path: Path) -> None:
    product = _active_product(ProductStore(tmp_path / "products"))
    analysis = _json("analysis.valid.json")
    eligibility = {
        "eligible": True,
        "usable_fact_ids": [product["selling_point_fact_ids"][0]],
        "unknown_fields": ["价格", "尺码"],
        "reasons": [],
    }
    payload = {
        "product": product,
        "analysis": analysis,
        "pattern": deepcopy(analysis["pattern_candidates"][0]),
        "request": {"content_goal": "seeding", "target_audience": "通勤成年男性", "version_count": 3},
        "production_policy": build_production_policy(product, eligibility),
    }

    context = json.loads(build_generation_context(payload))

    assert [fact["id"] for fact in context["product"]["confirmed_facts"]] == eligibility["usable_fact_ids"]
    assert context["product"]["unknown_fields"] == ["价格", "尺码"]
    assert "facts" not in context["product"]
    first_step = context["source_analysis"]["viral_evidence"]["steps"][0]
    assert first_step["evidence_ids"]
    assert first_step["evidence"][0]["id"] in first_step["evidence_ids"]


@pytest.mark.parametrize("violation", ["moving_camera", "multiple_groups", "unconfirmed_fact"])
def test_generation_rejects_model_output_that_overrides_server_policy(
    tmp_path: Path, violation: str,
) -> None:
    product = _active_product(ProductStore(tmp_path / "products"))
    analysis = _json("analysis.valid.json")
    candidate = _candidate()
    if violation == "moving_camera":
        candidate["versions"][0]["shots"][0]["camera"] = "手持推进"
    elif violation == "multiple_groups":
        candidate["shooting_order"].append(deepcopy(candidate["shooting_order"][0]))
    else:
        candidate["versions"][0]["fact_ids"].append("fact_price_demo_001")

    def invalid_gateway(_config, **_kwargs):
        return GatewayResult(content=candidate, response_id="response_invalid_policy", raw_bytes=100)

    with pytest.raises(ScriptGenerationError, match="生产策略|固定机位|单一拍摄顺序|未确认商品事实"):
        process_script_generation(
            task_id="script_task_10000000000000000000000000000001",
            input_payload=_input(product, analysis),
            task_directory=tmp_path / violation,
            config=CONFIG,
            gateway_caller=invalid_gateway,
        )


def test_script_queue_limits_concurrency_and_recovers(tmp_path: Path) -> None:
    queue = ScriptTaskQueue(tmp_path / "queue.sqlite3")
    product = _active_product(ProductStore(tmp_path / "products"))
    analysis = _json("analysis.valid.json")
    for _ in range(4):
        queue.enqueue(workspace_path=tmp_path / "tasks", input_payload=_input(product, analysis))
    lock = threading.Lock()
    both_workers_started = threading.Event()
    active = 0
    started = 0
    peak = 0

    def processor(task, _callback):
        nonlocal active, peak, started
        with lock:
            active += 1
            started += 1
            peak = max(peak, active)
            if started >= 2:
                both_workers_started.set()
        # Keep the first task alive long enough for the second worker to
        # start, even on a slow Windows filesystem. A genuinely serial queue
        # still fails the peak assertion after this bounded wait.
        both_workers_started.wait(timeout=2)
        result = Path(task.workspace_path) / "script-package.json"
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text("{}", encoding="utf-8")
        with lock:
            active -= 1
        return ScriptArtifacts(f"script_{task.task_id[-32:]}", result)

    queue.run_pending(gateway_config=CONFIG, max_workers=9, processor=processor)

    assert peak == 2
    assert queue.counts()["completed"] == 4


def test_script_queue_recovers_interrupted_and_manually_retries_failed_task(tmp_path: Path) -> None:
    queue = ScriptTaskQueue(tmp_path / "queue.sqlite3")
    product = _active_product(ProductStore(tmp_path / "products"))
    task = queue.enqueue(workspace_path=tmp_path / "tasks", input_payload=_input(product, _json("analysis.valid.json")))
    assert queue.claim_next() is not None

    reopened = ScriptTaskQueue(tmp_path / "queue.sqlite3")
    assert reopened.recover_interrupted() == 1
    assert reopened.get(task.task_id).status == "pending"  # type: ignore[union-attr]
    reopened.run_pending(
        gateway_config=CONFIG,
        processor=lambda _task, _callback: (_ for _ in ()).throw(ScriptGenerationError("结构错误")),
    )
    failed = reopened.get(task.task_id)
    assert failed is not None and failed.status == "failed"

    retried = reopened.retry(task.task_id)
    assert retried.status == "pending" and retried.max_attempts == 3

    def succeed(record, _callback):
        result = Path(record.workspace_path) / "script-package.json"
        result.write_text("{}", encoding="utf-8")
        return ScriptArtifacts(f"script_{record.task_id[-32:]}", result)

    reopened.run_pending(gateway_config=CONFIG, processor=succeed)
    assert reopened.get(task.task_id).status == "completed"  # type: ignore[union-attr]


def test_concurrent_script_retry_grants_only_one_attempt_budget(tmp_path: Path) -> None:
    queue = ScriptTaskQueue(tmp_path / "queue.sqlite3")
    product = _active_product(ProductStore(tmp_path / "products"))
    task = queue.enqueue(
        workspace_path=tmp_path / "tasks",
        input_payload=_input(product, _json("analysis.valid.json")),
        max_attempts=4,
    )
    claimed = queue.claim_next()
    assert claimed is not None
    queue.fail(task.task_id, ScriptGenerationError("人工重试前的最终失败"))
    barrier = threading.Barrier(2)

    def retry_once() -> str:
        barrier.wait(timeout=5)
        try:
            queue.retry(task.task_id)
            return "accepted"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: retry_once(), range(2)))

    assert sorted(results) == ["accepted", "rejected"]
    refreshed = queue.get(task.task_id)
    assert refreshed is not None
    assert refreshed.status == "pending"
    assert refreshed.max_attempts == 5


def test_runner_and_retry_block_a_disabled_frozen_skill_before_model_call(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    analysis_root = tmp_path / "analysis"
    s5_root = tmp_path / "s5"
    monkeypatch.setenv("CONTENT_FACTORY_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("CONTENT_FACTORY_ANALYSIS_ROOT", str(analysis_root))
    monkeypatch.setenv("CONTENT_FACTORY_S5_DATA_DIR", str(s5_root))
    s3._queue_instances.clear()
    s3._skill_store_instances.clear()
    s5._queue_instances.clear()

    analysis = _accepted_analysis(s5.get_analysis_queue(), tmp_path)
    skill_store, skill = _approved_skill(s5.get_analysis_queue(), analysis_root)
    product = _active_product(ProductStore(tmp_path / "products"))
    payload = _input(product, analysis)
    payload["skill"] = skill
    task = s5.get_script_queue().enqueue(workspace_path=s5_root, input_payload=payload)
    disabled = skill_store.set_skill_status(
        skill["skill_id"], expected_revision=skill["revision"], status="disabled",
        reviewer="内容负责人", note="证据需重新核对",
    )
    assert disabled["status"] == "disabled"

    class Settings:
        @staticmethod
        def load():
            return CONFIG

    model_calls = 0

    def must_not_call_model(**_kwargs):
        nonlocal model_calls
        model_calls += 1
        raise AssertionError("disabled frozen Skill must be rejected before model generation")

    monkeypatch.setattr(s5, "get_gateway_settings_store", lambda: Settings())
    monkeypatch.setattr(s5_queue_module, "process_script_generation", must_not_call_model)

    s5._run_queue_safely()

    failed = s5.get_script_queue().get(task.task_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.attempt_count == 1
    assert failed.max_attempts == 2
    assert failed.error and "Skill" in failed.error and ("停用" in failed.error or "变化" in failed.error)
    assert model_calls == 0

    before_retry = failed
    response = TestClient(app).post(f"/s5/tasks/{task.task_id}/retry")

    assert response.status_code == 422, response.text
    after_retry = s5.get_script_queue().get(task.task_id)
    assert after_retry is not None
    assert after_retry.status == "failed"
    assert after_retry.attempt_count == before_retry.attempt_count
    assert after_retry.max_attempts == before_retry.max_attempts
    assert after_retry.error == before_retry.error
    assert model_calls == 0


@pytest.mark.parametrize("change", ["revision", "source"])
def test_frozen_skill_preflight_rejects_revised_or_invalidated_sources(
    monkeypatch,
    tmp_path: Path,
    change: str,
) -> None:
    analysis = _json("analysis.valid.json")
    product = _active_product(ProductStore(tmp_path / "products"))
    skill = _json("viral-skill.valid.json")
    payload = _input(product, analysis)
    payload["skill"] = skill
    queue = ScriptTaskQueue(tmp_path / "s5" / "tasks.sqlite3")
    task = queue.enqueue(workspace_path=tmp_path / "s5", input_payload=payload)

    if change == "revision":
        monkeypatch.setattr(
            s5,
            "resolve_template",
            lambda *_args, **_kwargs: (
                {}, analysis, payload["pattern"], {**skill, "revision": skill["revision"] + 1},
            ),
        )
        expected = "版本已发生变化"
    else:
        def invalid_source(*_args, **_kwargs):
            raise ValueError("Skill 的来源证据已失效")

        monkeypatch.setattr(s5, "resolve_template", invalid_source)
        expected = "来源发生变化"

    with pytest.raises(ScriptGenerationError, match=expected):
        s5._validate_frozen_generation_sources(task, payload)


def test_script_queue_marks_exhausted_interrupted_task_as_failed(tmp_path: Path) -> None:
    queue = ScriptTaskQueue(tmp_path / "queue.sqlite3")
    product = _active_product(ProductStore(tmp_path / "products"))
    task = queue.enqueue(
        workspace_path=tmp_path / "tasks",
        input_payload=_input(product, _json("analysis.valid.json")),
        max_attempts=1,
    )
    assert queue.claim_next() is not None

    reopened = ScriptTaskQueue(tmp_path / "queue.sqlite3")
    assert reopened.recover_interrupted() == 1
    recovered = reopened.get(task.task_id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.current_step == "failed"
    assert recovered.error == "任务在最后一次生成时被中断，请手动重试"


def test_script_queue_reconciles_a_committed_result_after_last_attempt_crash(tmp_path: Path) -> None:
    database = tmp_path / "queue.sqlite3"
    queue = ScriptTaskQueue(database)
    product = _active_product(ProductStore(tmp_path / "products"))
    analysis = _json("analysis.valid.json")
    payload = _input(product, analysis)
    task = queue.enqueue(workspace_path=tmp_path / "tasks", input_payload=payload, max_attempts=1)
    claimed = queue.claim_next()
    assert claimed is not None
    artifacts = process_script_generation(
        task_id=claimed.task_id,
        input_payload=payload,
        task_directory=claimed.workspace_path,
        config=CONFIG,
        gateway_caller=_fake_gateway(),
    )
    (Path(claimed.workspace_path) / "revisions" / "script-r0001.json").unlink()
    (Path(claimed.workspace_path) / "revision-log.json").unlink()

    reopened = ScriptTaskQueue(database)
    assert reopened.recover_interrupted() == 1
    recovered = reopened.get(task.task_id)

    assert recovered is not None
    assert recovered.status == "completed"
    assert recovered.current_step == "completed"
    assert recovered.progress == 100
    assert recovered.error is None
    assert recovered.script_id == artifacts.script_id
    assert recovered.result_path == str(artifacts.result_path.resolve())
    assert json.loads(
        (Path(claimed.workspace_path) / "revisions" / "script-r0001.json").read_text(encoding="utf-8")
    )["script_id"] == artifacts.script_id
    assert json.loads(
        (Path(claimed.workspace_path) / "revision-log.json").read_text(encoding="utf-8")
    )[0]["action"] == "generated"
    calls = 0

    def must_not_regenerate(_task, _callback):
        nonlocal calls
        calls += 1
        raise AssertionError("committed scripts must not call the model again")

    assert reopened.run_pending(gateway_config=CONFIG, processor=must_not_regenerate) == []
    assert calls == 0


def test_script_queue_rejects_an_invalid_result_after_last_attempt_crash(tmp_path: Path) -> None:
    database = tmp_path / "queue.sqlite3"
    queue = ScriptTaskQueue(database)
    product = _active_product(ProductStore(tmp_path / "products"))
    task = queue.enqueue(
        workspace_path=tmp_path / "tasks",
        input_payload=_input(product, _json("analysis.valid.json")),
        max_attempts=1,
    )
    claimed = queue.claim_next()
    assert claimed is not None
    result_path = Path(claimed.workspace_path) / "script-package.json"
    result_path.write_text("{}", encoding="utf-8")

    reopened = ScriptTaskQueue(database)
    assert reopened.recover_interrupted() == 1
    recovered = reopened.get(task.task_id)

    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.script_id is None
    assert recovered.result_path is None


def test_script_runner_recovers_interrupted_task_without_gateway_configuration(
    monkeypatch,
    tmp_path: Path,
) -> None:
    s5_root = tmp_path / "s5"
    database = s5_root / "script-tasks.sqlite3"
    monkeypatch.setenv("CONTENT_FACTORY_S5_DATA_DIR", str(s5_root))
    s5._queue_instances.clear()

    queue = ScriptTaskQueue(database)
    product = _active_product(ProductStore(tmp_path / "products"))
    task = queue.enqueue(
        workspace_path=s5_root,
        input_payload=_input(product, _json("analysis.valid.json")),
    )
    claimed = queue.claim_next()
    assert claimed is not None
    assert claimed.task_id == task.task_id
    assert claimed.attempt_count == 1

    class EmptySettings:
        @staticmethod
        def load():
            return None

    monkeypatch.setattr(s5, "get_gateway_settings_store", lambda: EmptySettings())
    s5._run_queue_safely()

    recovered = ScriptTaskQueue(database).get(task.task_id)
    assert recovered is not None
    assert recovered.status == "pending"
    assert recovered.current_step == "queued"
    assert recovered.attempt_count == 1


def test_saving_script_gateway_route_wakes_pending_script_task(monkeypatch, tmp_path: Path) -> None:
    s5_root = tmp_path / "s5"
    monkeypatch.setenv("CONTENT_FACTORY_S3_CONFIG_PATH", str(tmp_path / "gateway.json"))
    monkeypatch.setenv("CONTENT_FACTORY_S5_DATA_DIR", str(s5_root))
    s5._queue_instances.clear()

    queue = s5.get_script_queue()
    product = _active_product(ProductStore(tmp_path / "products"))
    task = queue.enqueue(
        workspace_path=s5_root,
        input_payload=_input(product, _json("analysis.valid.json")),
    )
    runner_calls = 0

    def run_fixture_queue() -> None:
        nonlocal runner_calls
        runner_calls += 1

        def succeed(record, _callback):
            result = Path(record.workspace_path) / "script-package.json"
            result.write_text("{}", encoding="utf-8")
            return ScriptArtifacts(f"script_{record.task_id[-32:]}", result)

        s5.get_script_queue().run_pending(gateway_config=CONFIG, processor=succeed)

    monkeypatch.setattr(s5, "_run_queue_safely", run_fixture_queue)
    store = s3.get_gateway_settings_store()
    verified = store.upsert_verified_model(
        base_url="http://127.0.0.1:9999/v1",
        upstream_model_id="fixture-model",
        display_name="已验证模型",
        provider="openai_compatible",
        api_mode="responses",
        api_key="fixture-secret-key",
    )
    model = verified.public_dict()["models"][0]
    model.pop("api_key_configured")
    model["display_name"] = "已验证脚本模型"
    response = TestClient(app).put(
        "/s3/gateway",
        json={
            "models": [model],
            "default_model_id": verified.model_id,
            "routing": dict(verified.routing),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["routing"]["script"] == verified.model_id
    assert runner_calls == 1
    completed = queue.get(task.task_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.attempt_count == 1


def test_completed_script_remains_addressable_after_more_than_500_newer_tasks(tmp_path: Path) -> None:
    queue, _product, _analysis, payload = _completed_script_queue(tmp_path)
    oldest = queue.list()[0]
    assert oldest.script_id is not None

    for _ in range(500):
        queue.enqueue(workspace_path=tmp_path / "newer", input_payload=payload)

    assert get_script(queue, oldest.script_id)["script_id"] == oldest.script_id
    assert any(item["script_id"] == oldest.script_id for item in list_scripts(queue))


def test_script_edit_review_history_and_conflict(tmp_path: Path) -> None:
    queue, product, analysis, _payload = _completed_script_queue(tmp_path)
    script_id = queue.list()[0].script_id
    assert script_id is not None
    current = json.loads(Path(queue.list()[0].result_path or "").read_text(encoding="utf-8"))
    changes = {
        key: deepcopy(current[key])
        for key in ("content_goal", "selected_version_id", "versions", "shooting_order", "material_checklist")
    }
    changes["target_audience"] = "关注垂感与通勤搭配的成年男性"
    changes["selected_version_id"] = current["versions"][1]["id"]

    updated = update_script(queue, script_id, expected_revision=1, changes=changes, actor="编导甲")
    approved = review_script(
        queue, script_id, expected_revision=2, status="approved", reviewer="审核乙", note="逐镜头已确认",
        current_product=product, current_analysis=analysis,
    )

    assert updated["revision"] == 2 and updated["review"]["status"] == "pending"
    assert updated["selected_version_id"] == current["versions"][1]["id"]
    assert approved["revision"] == 3 and approved["review"]["status"] == "approved"
    assert (Path(queue.list()[0].workspace_path) / "revisions" / "script-r0003.json").is_file()
    with pytest.raises(ScriptConflictError):
        update_script(queue, script_id, expected_revision=1, changes=changes, actor="编导甲")


def test_approval_rejects_stale_product_revision(tmp_path: Path) -> None:
    queue, product, analysis, _payload = _completed_script_queue(tmp_path)
    script_id = queue.list()[0].script_id
    assert script_id is not None
    stale = {**product, "revision": product["revision"] + 1}

    with pytest.raises(ValueError, match="商品资料在生成后发生变化"):
        review_script(
            queue, script_id, expected_revision=1, status="approved", reviewer="审核乙", note=None,
            current_product=stale, current_analysis=analysis,
        )


def test_legacy_script_remains_readable_but_cannot_be_silently_approved(tmp_path: Path) -> None:
    queue, product, analysis, _payload = _completed_script_queue(tmp_path)
    completed = queue.list()[0]
    assert completed.script_id is not None and completed.input_path
    frozen = json.loads(Path(completed.input_path).read_text(encoding="utf-8"))
    frozen.pop("production_policy")
    Path(completed.input_path).write_text(json.dumps(frozen, ensure_ascii=False), encoding="utf-8")

    assert get_script(queue, completed.script_id)["script_id"] == completed.script_id
    with pytest.raises(ValueError, match="旧脚本|重新生成"):
        review_script(
            queue,
            completed.script_id,
            expected_revision=1,
            status="approved",
            reviewer="审核乙",
            note=None,
            current_product=product,
            current_analysis=analysis,
        )


def test_s5_api_generation_edit_and_approval(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("CONTENT_FACTORY_ANALYSIS_ROOT", str(tmp_path / "analysis"))
    monkeypatch.setenv("CONTENT_FACTORY_S4_DATA_DIR", str(tmp_path / "s4"))
    monkeypatch.setenv("CONTENT_FACTORY_S5_DATA_DIR", str(tmp_path / "s5"))
    s3._queue_instances.clear()
    s3._skill_store_instances.clear()
    s4._stores.clear()
    s5._queue_instances.clear()

    analysis = _accepted_analysis(s5.get_analysis_queue(), tmp_path)
    _skill_store, approved_skill = _approved_skill(s5.get_analysis_queue(), tmp_path / "analysis")
    product = _active_product(s4.get_product_store())

    class Settings:
        @staticmethod
        def load():
            return CONFIG

    monkeypatch.setattr(s5, "get_gateway_settings_store", lambda: Settings())

    def run_fixture_queue() -> None:
        def processor(record, callback):
            payload = json.loads(Path(record.input_path).read_text(encoding="utf-8"))
            return process_script_generation(
                task_id=record.task_id, input_payload=payload, task_directory=record.workspace_path,
                config=CONFIG, progress=callback, gateway_caller=_fake_gateway(),
            )

        s5.get_script_queue().run_pending(gateway_config=CONFIG, processor=processor)

    monkeypatch.setattr(s5, "_run_queue_safely", run_fixture_queue)
    client = TestClient(app)
    template = client.get("/s5/templates").json()[0]

    created = client.post("/s5/generations", json={
        "skill_id": template["skill_id"], "product_id": product["product_id"],
        "content_goal": "seeding", "target_audience": "关注通勤裤装的成年男性", "version_count": 3,
    })
    assert created.status_code == 202, created.text
    task = client.get("/s5/tasks").json()[0]
    assert task["status"] == "completed"
    assert str(tmp_path) not in json.dumps(task, ensure_ascii=False)

    summary = client.get("/s5/scripts").json()[0]
    script = client.get(f"/s5/scripts/{summary['script_id']}").json()
    context = client.get(f"/s5/scripts/{summary['script_id']}/context").json()
    assert context["production_policy"]["production_mode"]["kind"] == "fixed_livestream_long_take"
    assert context["viral_evidence"]["steps"][0]["evidence"][0]["id"] == "evidence_demo_001"
    assert context["skill"]["skill_id"] == approved_skill["skill_id"]
    assert script["skill_id"] == approved_skill["skill_id"]
    save = client.put(f"/s5/scripts/{summary['script_id']}", json={
        "expected_revision": 1, "actor": "编导甲", "content_goal": script["content_goal"],
        "target_audience": "关注垂感与通勤搭配的成年男性",
        "selected_version_id": script["versions"][1]["id"], "versions": script["versions"],
        "shooting_order": script["shooting_order"], "material_checklist": script["material_checklist"],
    })
    assert save.status_code == 200, save.text
    assert save.json()["selected_version_id"] == script["versions"][1]["id"]
    approved = client.post(f"/s5/scripts/{summary['script_id']}/review", json={
        "expected_revision": 2, "status": "approved", "reviewer": "审核乙", "note": "事实和拍摄条件已确认",
    })
    assert approved.status_code == 200, approved.text
    assert approved.json()["review"]["status"] == "approved"
    assert len(client.get(f"/s5/scripts/{summary['script_id']}/versions").json()) == 3
    readiness = client.get("/s5/readiness").json()
    assert readiness["accepted_real_scripts"] == 1
    assert readiness["business_ready"] is True
    assert analysis["revision"] == 2
