from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from content_factory_contracts import compute_product_completeness, read_gold_set_readiness, validate_document, validate_or_raise
from content_factory_contracts.validation import product_script_eligibility
from content_factory_contracts.validation import ContractValidationError, load_json

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PACKAGE_ROOT / "fixtures"
PROJECT_ROOT = PACKAGE_ROOT.parents[1]


@pytest.fixture(scope="module")
def analysis() -> dict:
    return load_json(FIXTURES / "analysis.valid.json")


@pytest.fixture(scope="module")
def product() -> dict:
    return load_json(FIXTURES / "product.valid.json")


@pytest.fixture(scope="module")
def script() -> dict:
    return load_json(FIXTURES / "script.valid.json")


def test_all_frozen_contract_fixtures_are_valid(analysis: dict, product: dict, script: dict) -> None:
    validate_or_raise("analysis", analysis)
    validate_or_raise("product", product)
    validate_or_raise("script", script, related={"analysis": analysis, "product": product})
    validate_or_raise("gold_case", load_json(FIXTURES / "gold-case.valid.json"))
    validate_or_raise("gold_manifest", load_json(FIXTURES / "gold-manifest.valid.json"))
    validate_or_raise("media_task", load_json(FIXTURES / "media-task.valid.json"))
    validate_or_raise("media_result", load_json(FIXTURES / "media-result.valid.json"))
    validate_or_raise("ocr_result", load_json(FIXTURES / "ocr-result.valid.json"))
    validate_or_raise("analysis_task", load_json(FIXTURES / "analysis-task.valid.json"))
    validate_or_raise("gateway_settings", load_json(FIXTURES / "gateway-settings-v2.1.valid.json"))
    validate_or_raise("script_task", load_json(FIXTURES / "script-task.valid.json"))
    validate_or_raise("material", load_json(FIXTURES / "material.valid.json"), related={"product": product})
    validate_or_raise("shooting_task", load_json(FIXTURES / "shooting-task.valid.json"), related={"script": script})
    validate_or_raise("material_import_task", load_json(FIXTURES / "material-import-task.valid.json"))
    validate_or_raise("material_usage", load_json(FIXTURES / "material-usage.valid.json"))
    edit_project = load_json(FIXTURES / "edit-project.valid.json")
    render_task = load_json(FIXTURES / "render-task.valid.json")
    validate_or_raise(
        "edit_project", edit_project,
        related={"script": script, "materials": {"material_demo_001": load_json(FIXTURES / "material.valid.json")}},
    )
    validate_or_raise("render_task", render_task)
    validate_or_raise(
        "render_output", load_json(FIXTURES / "render-output.valid.json"),
        related={"render_task": render_task, "edit_project": edit_project},
    )
    validate_or_raise("edit_audio_asset", load_json(FIXTURES / "edit-audio-asset.valid.json"))
    publication = load_json(FIXTURES / "publication.valid.json")
    validate_or_raise("publication", publication)
    validate_or_raise("metric_snapshot", load_json(FIXTURES / "metric-snapshot.valid.json"), related={"publication": publication})
    validate_or_raise("metric_import_draft", load_json(FIXTURES / "metric-import-draft.valid.json"))
    validate_or_raise("learning_report", load_json(FIXTURES / "learning-report.valid.json"))
    validate_or_raise("viral_skill", load_json(FIXTURES / "viral-skill.valid.json"))


def test_viral_skill_commonality_uses_distinct_video_ids() -> None:
    invalid = load_json(FIXTURES / "viral-skill.valid.json")
    invalid["occurrences"][1]["video_id"] = invalid["occurrences"][0]["video_id"]

    issues = validate_document("viral_skill", invalid)

    assert any("distinct_video_count" in issue and "video_id" in issue for issue in issues)


def test_reusable_viral_skill_requires_timed_dialogue_or_action_evidence() -> None:
    invalid = load_json(FIXTURES / "viral-skill.valid.json")
    for occurrence in invalid["occurrences"]:
        occurrence["has_primary_evidence"] = False
        occurrence["start_ms"] = None
        occurrence["end_ms"] = None
        for step in occurrence["steps"]:
            for evidence in step["evidence"]:
                evidence["start_ms"] = None
                evidence["end_ms"] = None
                evidence["exact_dialogue"] = None
                evidence["action"] = None

    issues = validate_document("viral_skill", invalid)

    assert any("正向复用 Skill" in issue and "时间码" in issue for issue in issues)


def test_reusable_common_skill_requires_primary_evidence_from_every_video() -> None:
    invalid = load_json(FIXTURES / "viral-skill.valid.json")
    second = invalid["occurrences"][1]
    second["has_primary_evidence"] = False
    for step in second["steps"]:
        for evidence in step["evidence"]:
            evidence["exact_dialogue"] = None
            evidence["subtitle"] = None
            evidence["action"] = None
            evidence["visual_event"] = None

    issues = validate_document("viral_skill", invalid)

    assert any("每条计入统计的视频" in issue and second["video_id"] in issue for issue in issues)


def test_script_skill_reference_requires_id_and_revision_pair(
    analysis: dict, product: dict, script: dict,
) -> None:
    valid = deepcopy(script)
    valid["skill_id"] = "skill_11111111111111111111111111111111"
    valid["skill_revision"] = 1
    validate_or_raise("script", valid, related={"analysis": analysis, "product": product})

    invalid = deepcopy(valid)
    del invalid["skill_revision"]
    assert any("skill_revision" in issue for issue in validate_document(
        "script", invalid, related={"analysis": analysis, "product": product},
    ))


def test_gateway_settings_accepts_legacy_v2_without_provider_or_video_review() -> None:
    validate_or_raise("gateway_settings", load_json(FIXTURES / "gateway-settings-v2.0.valid.json"))


def test_gateway_settings_requires_video_review_route_for_v21() -> None:
    invalid = load_json(FIXTURES / "gateway-settings-v2.1.valid.json")
    del invalid["routing"]["video_review"]

    assert any("video_review" in issue for issue in validate_document("gateway_settings", invalid))


def test_gateway_settings_requires_provider_for_every_v21_model() -> None:
    invalid = load_json(FIXTURES / "gateway-settings-v2.1.valid.json")
    del invalid["models"][1]["provider"]

    assert any("provider" in issue for issue in validate_document("gateway_settings", invalid))


def test_gateway_settings_rejects_a_video_review_route_without_image_input() -> None:
    invalid = load_json(FIXTURES / "gateway-settings-v2.1.valid.json")
    invalid["models"][0]["modalities"] = ["text"]

    issues = validate_document("gateway_settings", invalid)

    assert any("视频审核" in issue and "image" in issue for issue in issues)


def test_analysis_rejects_a_shot_beyond_video_duration(analysis: dict) -> None:
    invalid = deepcopy(analysis)
    invalid["shots"][1]["end_ms"] = 7000

    assert any("超出总时长" in issue for issue in validate_document("analysis", invalid))


def test_analysis_rejects_an_incorrect_weighted_total(analysis: dict) -> None:
    invalid = deepcopy(analysis)
    invalid["scores"]["total_score"] = 90

    assert any("加权结果" in issue for issue in validate_document("analysis", invalid))


def test_product_rejects_an_unknown_selling_point(product: dict) -> None:
    invalid = deepcopy(product)
    invalid["selling_point_fact_ids"].append("fact_missing_001")

    assert any("不存在的商品事实" in issue for issue in validate_document("product", invalid))


def test_product_rejects_a_dangling_fact_source(product: dict) -> None:
    invalid = deepcopy(product)
    invalid["facts"][0]["source_id"] = "source_missing_001"

    assert any("资料来源" in issue for issue in validate_document("product", invalid))


def test_product_completeness_cannot_be_faked(product: dict) -> None:
    invalid = deepcopy(product)
    invalid["facts"] = [item for item in invalid["facts"] if item["field"] != "size"]

    assert any("固定规则" in issue for issue in validate_document("product", invalid))


def test_fixture_product_cannot_be_activated(product: dict) -> None:
    invalid = deepcopy(product)
    invalid["status"] = "active"

    assert any("工程样例" in issue for issue in validate_document("product", invalid))


def test_draft_product_can_be_script_eligible_without_every_optional_catalog_field(product: dict) -> None:
    valid = deepcopy(product)
    valid["fixture_data"] = False
    valid["status"] = "draft"
    valid["facts"] = [deepcopy(product["facts"][0])]
    valid["selling_point_fact_ids"] = [valid["facts"][0]["id"]]
    valid["completeness"] = compute_product_completeness(valid)

    eligibility = product_script_eligibility(valid)

    assert valid["completeness"]["ratio"] < 1
    assert eligibility["eligible"] is True
    assert eligibility["usable_fact_ids"] == valid["selling_point_fact_ids"]


def test_script_eligibility_reports_specific_blockers_and_never_uses_unconfirmed_facts(product: dict) -> None:
    invalid = deepcopy(product)
    invalid["fixture_data"] = False
    invalid["status"] = "draft"
    invalid["sources"] = []
    invalid["facts"] = [deepcopy(product["facts"][0])]
    invalid["facts"][0]["confirmed_by"] = ""
    invalid["selling_point_fact_ids"] = [invalid["facts"][0]["id"]]

    eligibility = product_script_eligibility(invalid)

    assert eligibility["eligible"] is False
    assert eligibility["usable_fact_ids"] == []
    assert any("资料来源" in blocker for blocker in eligibility["blockers"])
    assert any("已确认" in blocker for blocker in eligibility["blockers"])


@pytest.mark.parametrize("status, fixture_data", [("archived", False), ("draft", True)])
def test_archived_or_fixture_product_is_never_script_eligible(
    product: dict, status: str, fixture_data: bool,
) -> None:
    invalid = deepcopy(product)
    invalid["status"] = status
    invalid["fixture_data"] = fixture_data

    eligibility = product_script_eligibility(invalid)

    assert eligibility["eligible"] is False


def test_fixed_livestream_script_rejects_moving_camera_instruction(
    analysis: dict, product: dict, script: dict,
) -> None:
    invalid = deepcopy(script)
    invalid["production_mode"] = {
        "kind": "fixed_livestream_long_take",
        "scene": "固定直播间",
        "camera": "固定直播间竖屏机位（不移动）",
        "lighting": "固定直播间灯光",
        "performer_count": 1,
        "primary_take": "continuous_long_take",
        "detail_overlays": "optional_reusable_same_product",
    }
    for version in invalid["versions"]:
        for shot in version["shots"]:
            shot["delivery"] = {"tone": "直接", "pacing": "正常", "emphasis": "关键词", "pause": "自然停顿"}
            shot["performance"] = {
                "expression": "直视镜头",
                "eye_line": "看镜头",
                "body_action": "原地展示",
                "product_action": "展示商品",
            }
            shot["detail_overlay"] = {"mode": "none", "detail_tag": None, "instruction": "保持主播画面"}
            shot["evidence_ids"] = ["evidence_demo_001"]
    invalid["versions"][0]["shots"][0]["camera"] = "摄影师手持推进并环绕主播"

    issues = validate_document("script", invalid, related={"analysis": analysis, "product": product})

    assert any("固定机位" in issue for issue in issues)


def test_fixed_livestream_script_requires_executable_delivery_and_performance(
    analysis: dict, product: dict, script: dict,
) -> None:
    invalid = deepcopy(script)
    del invalid["versions"][0]["shots"][0]["delivery"]

    issues = validate_document("script", invalid, related={"analysis": analysis, "product": product})

    assert any("delivery" in issue and "逐段" in issue for issue in issues)


def test_fixed_livestream_script_rejects_evidence_from_another_pattern_step(
    analysis: dict, product: dict, script: dict,
) -> None:
    invalid = deepcopy(script)
    invalid["versions"][0]["shots"][0]["evidence_ids"] = ["evidence_demo_002"]

    issues = validate_document("script", invalid, related={"analysis": analysis, "product": product})

    assert any("evidence_ids" in issue and "当前爆点步骤" in issue for issue in issues)


def test_fixed_livestream_script_rejects_second_presenter_or_outdoor_scene(
    analysis: dict, product: dict, script: dict,
) -> None:
    invalid = deepcopy(script)
    invalid["versions"][0]["shots"][0]["visual"] = "主播和助播一起走到户外街道展示商品"

    issues = validate_document("script", invalid, related={"analysis": analysis, "product": product})

    assert any("固定直播间" in issue and "助播" in issue for issue in issues)


def test_fixed_livestream_script_rejects_changed_shooting_scene(
    analysis: dict, product: dict, script: dict,
) -> None:
    invalid = deepcopy(script)
    invalid["shooting_order"][0]["scene"] = "室外街道"

    issues = validate_document("script", invalid, related={"analysis": analysis, "product": product})

    assert any("shooting_order" in issue and "固定直播间" in issue for issue in issues)


def test_script_rejects_unprovable_copy(analysis: dict, product: dict, script: dict) -> None:
    invalid = deepcopy(script)
    invalid["versions"][0]["primary_hook"] = "显瘦十斤，马上就能看到。"

    issues = validate_document("script", invalid, related={"analysis": analysis, "product": product})

    assert any("显瘦十斤" in issue for issue in issues)


def test_script_rejects_a_gap_in_a_version_timeline(analysis: dict, product: dict, script: dict) -> None:
    invalid = deepcopy(script)
    first = invalid["versions"][0]["shots"][0]
    first["start_ms"] = 100

    issues = validate_document("script", invalid, related={"analysis": analysis, "product": product})

    assert any("连续开始" in issue for issue in issues)


def test_script_rejects_duplicate_version_hooks(analysis: dict, product: dict, script: dict) -> None:
    invalid = deepcopy(script)
    invalid["versions"][1]["primary_hook"] = invalid["versions"][0]["primary_hook"]

    issues = validate_document("script", invalid, related={"analysis": analysis, "product": product})

    assert any("主钩子不能重复" in issue for issue in issues)


def test_script_rejects_unknown_source_shot(analysis: dict, product: dict, script: dict) -> None:
    invalid = deepcopy(script)
    invalid["versions"][0]["shots"][0]["source_shot_id"] = "shot_missing_001"

    issues = validate_document("script", invalid, related={"analysis": analysis, "product": product})

    assert any("source_shot_id" in issue for issue in issues)


def test_completed_script_task_requires_a_script_result() -> None:
    invalid = load_json(FIXTURES / "script-task.valid.json")
    invalid["script_id"] = None

    assert any("脚本结果" in issue for issue in validate_document("script_task", invalid))


def test_accepted_gold_case_cannot_be_a_fixture() -> None:
    invalid = load_json(FIXTURES / "gold-case.valid.json")
    invalid["annotation_status"] = "accepted"
    invalid["reviewer"] = "reviewer"
    invalid["accepted_at"] = "2026-08-29T06:30:00Z"

    with pytest.raises(ContractValidationError, match="fixture_data"):
        validate_or_raise("gold_case", invalid)


def test_real_manifest_reports_missing_business_inputs_without_hiding_engineering_readiness() -> None:
    readiness = read_gold_set_readiness(PROJECT_ROOT / "data" / "gold-set" / "manifest.json")

    assert readiness.engineering_ready is True
    assert readiness.business_ready is False
    assert readiness.accepted_videos == 0
    assert readiness.required_videos == 20
    assert readiness.accepted_products == 0
    assert readiness.required_products == 3
    assert readiness.pending_reason == "待补 20 条真实视频和 3 款真实商品"


def test_status_labels_do_not_bypass_missing_or_invalid_files(tmp_path: Path) -> None:
    manifest = load_json(PROJECT_ROOT / "data" / "gold-set" / "manifest.json")
    for slot in manifest["video_slots"]:
        slot["status"] = "accepted"
        slot["case_file"] = "missing-case.json"
    for slot in manifest["product_slots"]:
        slot["status"] = "accepted"
        slot["profile_file"] = "missing-product.json"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    readiness = read_gold_set_readiness(manifest_path)

    assert readiness.business_ready is False
    assert readiness.accepted_videos == 0
    assert readiness.accepted_products == 0


def test_media_result_rejects_a_gap_between_shots() -> None:
    invalid = load_json(FIXTURES / "media-result.valid.json")
    invalid["shots"][1]["start_ms"] = 2500

    assert any("连续开始" in issue for issue in validate_document("media_result", invalid))


def test_completed_media_task_requires_a_result() -> None:
    invalid = load_json(FIXTURES / "media-task.valid.json")
    invalid["result_path"] = None

    assert any("result_path" in issue for issue in validate_document("media_task", invalid))


def test_analysis_rejects_untraceable_summary_claim(analysis: dict) -> None:
    invalid = deepcopy(analysis)
    invalid["summary"]["overall_conclusion"]["evidence_ids"] = ["evidence_missing"]

    assert any("summary.claims" in issue for issue in validate_document("analysis", invalid))


def test_analysis_rejects_timeline_gap(analysis: dict) -> None:
    invalid = deepcopy(analysis)
    invalid["timeline"][1]["start_ms"] += 1

    assert any("timeline[1]" in issue and "连续开始" in issue for issue in validate_document("analysis", invalid))


def test_analysis_rejects_timeline_second_index_inconsistent_with_start(analysis: dict) -> None:
    invalid = deepcopy(analysis)
    invalid["timeline"][1]["second_index"] = 99

    assert any(
        "timeline[1].second_index" in issue and "start_ms//1000" in issue
        for issue in validate_document("analysis", invalid)
    )


def test_analysis_rejects_a_shot_without_keyframe(analysis: dict) -> None:
    invalid = deepcopy(analysis)
    invalid["keyframes"] = invalid["keyframes"][:1]

    assert any("每个镜头都必须有关键帧" in issue for issue in validate_document("analysis", invalid))


def test_draft_analysis_cannot_pretend_to_be_reviewed(analysis: dict) -> None:
    invalid = deepcopy(analysis)
    invalid["review"]["reviewer"] = "fixture-reviewer"
    invalid["review"]["reviewed_at"] = "2026-08-29T07:00:00Z"

    assert any("draft" in issue for issue in validate_document("analysis", invalid))


def test_completed_analysis_task_requires_ocr_and_report_paths() -> None:
    invalid = load_json(FIXTURES / "analysis-task.valid.json")
    invalid["ocr_result_path"] = None

    assert any("OCR" in issue for issue in validate_document("analysis_task", invalid))


def test_analysis_task_rejects_unknown_model_purpose() -> None:
    invalid = load_json(FIXTURES / "analysis-task.valid.json")
    invalid["model_purpose"] = "script"

    assert any("model_purpose" in issue for issue in validate_document("analysis_task", invalid))


def test_analysis_report_rejects_unknown_processing_purpose(analysis: dict) -> None:
    invalid = deepcopy(analysis)
    invalid["processing"]["purpose"] = "script"

    assert any("processing" in issue and "purpose" in issue for issue in validate_document("analysis", invalid))


def test_material_rejects_timeline_gap_and_wrong_quality_total() -> None:
    invalid = load_json(FIXTURES / "material.valid.json")
    invalid["clips"][1]["start_ms"] += 1
    invalid["clips"][0]["quality"]["overall"] = 100

    issues = validate_document("material", invalid)

    assert any("连续开始" in issue for issue in issues)
    assert any("四项质量均值" in issue for issue in issues)


def test_material_rejects_duplicate_source_shot() -> None:
    invalid = load_json(FIXTURES / "material.valid.json")
    invalid["clips"][1]["source_shot_id"] = invalid["clips"][0]["source_shot_id"]

    assert any("source_shot_id" in issue for issue in validate_document("material", invalid))


def test_structured_host_take_requires_one_controlled_full_file_clip() -> None:
    invalid = load_json(FIXTURES / "material.valid.json")
    invalid["capture_role"] = "host_take"

    issues = validate_document("material", invalid)

    assert any("受控全片主素材片段" in issue for issue in issues)


def test_material_revision_must_match_related_product(product: dict) -> None:
    invalid = load_json(FIXTURES / "material.valid.json")
    invalid["product_revision"] = product["revision"] + 1

    assert any("商品修订" in issue for issue in validate_document("material", invalid, related={"product": product}))


def test_shooting_task_state_is_derived_from_confirmed_requirements() -> None:
    invalid = load_json(FIXTURES / "shooting-task.valid.json")
    invalid["status"] = "ready_for_edit"

    assert any("status 应为 materials_uploaded" in issue for issue in validate_document("shooting_task", invalid))


def test_shooting_task_rejects_reusing_same_clip_twice() -> None:
    invalid = load_json(FIXTURES / "shooting-task.valid.json")
    first_match = deepcopy(invalid["requirements"][0]["confirmed_match"])
    invalid["requirements"][1]["match_status"] = "confirmed"
    invalid["requirements"][1]["confirmed_match"] = first_match
    invalid["matched_count"] = 2
    invalid["missing_count"] = 1

    assert any("不能重复确认" in issue for issue in validate_document("shooting_task", invalid))


def test_completed_material_import_requires_material_result() -> None:
    invalid = load_json(FIXTURES / "material-import-task.valid.json")
    invalid["material_id"] = None

    assert any("素材结果" in issue for issue in validate_document("material_import_task", invalid))


def test_edit_project_rejects_timeline_gap() -> None:
    invalid = load_json(FIXTURES / "edit-project.valid.json")
    invalid["variants"][0]["clips"][0]["timeline_start_ms"] = 100

    assert any("时间线必须" in issue for issue in validate_document("edit_project", invalid))


def test_edit_project_rejects_source_outside_confirmed_material_clip() -> None:
    invalid = load_json(FIXTURES / "edit-project.valid.json")
    invalid["variants"][0]["clips"][0]["source_end_ms"] = 2600
    material = load_json(FIXTURES / "material.valid.json")

    issues = validate_document("edit_project", invalid, related={"materials": {"material_demo_001": material}})

    assert any("超出已确认素材片段" in issue for issue in issues)


def test_edit_project_source_audio_must_match_material() -> None:
    invalid = load_json(FIXTURES / "edit-project.valid.json")
    invalid["variants"][0]["clips"][0]["has_source_audio"] = False
    material = load_json(FIXTURES / "material.valid.json")

    issues = validate_document("edit_project", invalid, related={"materials": {"material_demo_001": material}})

    assert any("源音轨标记" in issue for issue in issues)


def test_edit_project_revision_must_match_approved_script(script: dict) -> None:
    invalid = load_json(FIXTURES / "edit-project.valid.json")
    invalid["script_revision"] = script["revision"] + 1

    issues = validate_document("edit_project", invalid, related={"script": script})

    assert any("脚本修订" in issue for issue in issues)


def test_completed_render_task_requires_an_output() -> None:
    invalid = load_json(FIXTURES / "render-task.valid.json")
    invalid["output_id"] = None

    assert any("成片产物" in issue for issue in validate_document("render_task", invalid))


def test_render_output_status_must_follow_review() -> None:
    invalid = load_json(FIXTURES / "render-output.valid.json")
    invalid["status"] = "approved"

    assert any("review.status" in issue for issue in validate_document("render_output", invalid))


def test_rejected_render_output_requires_reviewer_and_reason() -> None:
    invalid = load_json(FIXTURES / "render-output.valid.json")
    invalid["status"] = "rejected"
    invalid["review"]["status"] = "rejected"

    issues = validate_document("render_output", invalid)

    assert any("审核人" in issue for issue in issues)
    assert any("驳回成片" in issue for issue in issues)


def test_publication_rejects_non_douyin_https_url() -> None:
    invalid = load_json(FIXTURES / "publication.valid.json")
    invalid["work_url"] = "https://evil.example/video/7600000000000000001"

    assert any("douyin.com" in issue for issue in validate_document("publication", invalid))


def test_publication_requires_current_approved_output() -> None:
    publication = load_json(FIXTURES / "publication.valid.json")
    output = load_json(FIXTURES / "render-output.valid.json")

    assert any("审核通过" in issue for issue in validate_document("publication", publication, related={"render_output": output}))


def test_publication_business_review_requires_named_human_and_note() -> None:
    invalid = load_json(FIXTURES / "publication.valid.json")
    invalid["business_review"] = {"status": "confirmed", "reviewed_by": None, "reviewed_at": None, "note": ""}

    issues = validate_document("publication", invalid)

    assert any("确认人" in issue for issue in issues)
    assert any("复盘说明" in issue for issue in issues)


def test_metric_snapshot_rejects_unconfirmed_formal_data() -> None:
    invalid = load_json(FIXTURES / "metric-snapshot.valid.json")
    invalid["confidence"] = "unconfirmed"

    assert any("导入草稿" in issue for issue in validate_document("metric_snapshot", invalid))


def test_metric_snapshot_requires_explicit_correction_for_decrease() -> None:
    previous = load_json(FIXTURES / "metric-snapshot.valid.json")
    invalid = deepcopy(previous)
    invalid["snapshot_id"] = "metric_snapshot_demo_002"
    invalid["metrics"]["views"] = previous["metrics"]["views"] - 1

    issues = validate_document("metric_snapshot", invalid, related={"previous_snapshot": previous})

    assert any("累计指标 views" in issue for issue in issues)


def test_learning_report_does_not_announce_winner_without_data() -> None:
    invalid = load_json(FIXTURES / "learning-report.valid.json")
    invalid["status"] = "insufficient_data"

    assert any("不能宣布赢家" in issue for issue in validate_document("learning_report", invalid))
