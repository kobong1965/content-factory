from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from content_factory_api import s5
from content_factory_api.s3_analysis import AnalysisArtifacts
from content_factory_api.s3_queue import AnalysisTaskQueue
from content_factory_api.s3_skills import ViralSkillStore, refresh_skill_candidates
from content_factory_api.s5_sources import list_templates, resolve_template


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PROJECT_ROOT / "packages" / "contracts" / "fixtures"


def _accepted_report(
    queue: AnalysisTaskQueue,
    root: Path,
    marker: str,
    *,
    video_id: str,
    primary_confidence: float = 0.95,
    primary_start_ms: int = 0,
    primary_end_ms: int = 3000,
) -> dict:
    report = json.loads((FIXTURES / "analysis.valid.json").read_text(encoding="utf-8"))
    report = deepcopy(report)
    report.update({
        "fixture_data": False,
        "analysis_id": f"analysis_{marker * 32}",
        "video_id": video_id,
        "status": "accepted",
        "revision": 2,
        "review": {
            "reviewer": "分析审核人",
            "reviewed_at": "2026-09-07T08:00:00Z",
            "note": "证据已核对",
        },
    })
    report["processing"]["purpose"] = "analysis"
    report["source"].update({
        "source_id": f"source_{marker * 24}",
        "source_uri": str(root / f"{marker}-爆款.mp4"),
    })
    report["pattern_candidates"][0].update({
        "id": f"pattern_{marker * 24}",
        "name": "结果先行配合细节证明",
        "mechanism": "先展示上身结果，再用近距离动作或细节证明。",
        "mechanism_key": "result_then_visual_proof",
        "reuse_mode": "reuse",
    })
    report["evidence"][0].update({
        "confidence": primary_confidence,
        "start_ms": primary_start_ms,
        "end_ms": primary_end_ms,
    })
    media = root / f"media-{marker}.json"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_text("{}", encoding="utf-8")
    task = queue.enqueue(
        media_task_id=f"media_{marker * 32}",
        media_result_path=media,
        workspace_path=root / "analysis",
        input_payload={"metric_snapshots": [], "comments": []},
        fixture_data=False,
    )
    claimed = queue.claim_next()
    assert claimed is not None and claimed.worker_id
    task_dir = Path(claimed.workspace_path)
    task_dir.mkdir(parents=True, exist_ok=True)
    report_path = task_dir / "analysis-report.json"
    ocr_path = task_dir / "ocr-result.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    ocr_path.write_text("{}", encoding="utf-8")
    queue.complete(
        task.task_id,
        AnalysisArtifacts(report["analysis_id"], report_path, ocr_path),
        worker_id=claimed.worker_id,
    )
    return report


def _approve(store: ViralSkillStore, *, reuse_mode: str = "reuse") -> dict:
    candidate = store.list_candidates()[0]
    return store.approve_candidate(
        candidate["candidate_id"],
        expected_candidate_revision=candidate["revision"],
        expected_skill_revision=None,
        reviewer="内容负责人",
        reuse_mode=reuse_mode,
        name=candidate["suggested_name"],
        mechanism=candidate["suggested_mechanism"],
        note="脚本适配前仍需核对商品事实",
    )


def test_s5_lists_only_explicitly_approved_reusable_skills(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "runtime" / "s3.sqlite3")
    store = ViralSkillStore(tmp_path / "analysis" / "skills.sqlite3")
    _accepted_report(queue, tmp_path / "analysis-a", "a", video_id="video_aaaaaaaaaaaaaaaaaaaaaaaa")
    _accepted_report(queue, tmp_path / "analysis-b", "b", video_id="video_bbbbbbbbbbbbbbbbbbbbbbbb")
    refresh_skill_candidates(store, queue)

    assert list_templates(queue, store) == []

    skill = _approve(store)
    templates = list_templates(queue, store, usage_counts={skill["skill_id"]: 2})

    assert len(templates) == 1
    assert templates[0]["template_id"] == skill["skill_id"]
    assert templates[0]["skill_id"] == skill["skill_id"]
    assert templates[0]["skill_revision"] == 1
    assert templates[0]["distinct_video_count"] == 2
    assert templates[0]["occurrence_count"] == 2
    assert templates[0]["script_usage_count"] == 2
    assert len(templates[0]["representative_sources"]) == 2

    disabled = store.set_skill_status(
        skill["skill_id"],
        expected_revision=skill["revision"],
        status="disabled",
        reviewer="内容负责人",
        note="暂时停用",
    )
    assert disabled["status"] == "disabled"
    assert list_templates(queue, store) == []


def test_representative_sources_choose_one_best_occurrence_per_video_and_use_its_evidence_range(
    tmp_path: Path,
) -> None:
    queue = AnalysisTaskQueue(tmp_path / "runtime" / "s3.sqlite3")
    store = ViralSkillStore(tmp_path / "analysis" / "skills.sqlite3")
    repeated_video = "video_111111111111111111111111"
    _accepted_report(
        queue,
        tmp_path / "lower-quality",
        "6",
        video_id=repeated_video,
        primary_confidence=0.40,
    )
    _accepted_report(
        queue,
        tmp_path / "best-quality",
        "7",
        video_id=repeated_video,
        primary_confidence=0.99,
        primary_start_ms=1200,
        primary_end_ms=2300,
    )
    _accepted_report(
        queue,
        tmp_path / "other-video",
        "8",
        video_id="video_222222222222222222222222",
        primary_confidence=0.96,
        primary_start_ms=400,
        primary_end_ms=2600,
    )
    refresh_skill_candidates(store, queue)
    skill = _approve(store)

    sources = list_templates(queue, store)[0]["representative_sources"]

    assert len(sources) == 2
    assert len({item["video_id"] for item in sources}) == 2
    selected = next(item for item in sources if item["video_id"] == repeated_video)
    assert selected["source_name"] == "7-爆款.mp4"
    assert (selected["start_ms"], selected["end_ms"]) == (1200, 2300)

    occurrences = {item["source_name"]: item for item in skill["occurrences"]}
    for source in sources:
        occurrence = occurrences[source["source_name"]]
        displayed_evidence = [
            evidence
            for step in occurrence["steps"]
            for evidence in step["evidence"]
            if evidence["start_ms"] == source["start_ms"]
            and evidence["end_ms"] == source["end_ms"]
            and evidence["exact_dialogue"] == source["dialogue"]
            and evidence["action"] == source["action"]
        ]
        assert displayed_evidence, "列表时间码必须来自同一条实际展示的 evidence"


def test_s5_readiness_explains_why_a_formal_skill_with_stale_sources_is_blocked(
    monkeypatch,
    tmp_path: Path,
) -> None:
    queue = AnalysisTaskQueue(tmp_path / "runtime" / "s3.sqlite3")
    store = ViralSkillStore(tmp_path / "analysis" / "skills.sqlite3")
    _accepted_report(queue, tmp_path / "analysis", "9", video_id="video_999999999999999999999999")
    refresh_skill_candidates(store, queue)
    _approve(store)
    task = queue.completed_reports()[0]
    report_path = Path(task.result_path or "")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["status"] = "reviewed"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    refresh_skill_candidates(store, queue)

    class EmptyScriptQueue:
        @staticmethod
        def counts() -> dict[str, int]:
            return {"pending": 0, "retry_wait": 0, "running": 0, "failed": 0}

    script_queue = EmptyScriptQueue()
    monkeypatch.setattr(s5, "_script_gateway", lambda: object())
    monkeypatch.setattr(s5, "get_analysis_queue", lambda: queue)
    monkeypatch.setattr(s5, "get_viral_skill_store", lambda: store)
    monkeypatch.setattr(s5, "get_script_queue", lambda: script_queue)
    monkeypatch.setattr(s5, "skill_usage_counts", lambda _queue: {})
    monkeypatch.setattr(s5, "get_product_store", lambda: object())
    monkeypatch.setattr(s5, "list_script_products", lambda _store: [{"product_id": "product_ready"}])
    monkeypatch.setattr(
        s5,
        "script_counts",
        lambda _queue: {"pending": 0, "draft": 0, "approved": 0, "accepted_real": 0},
    )

    readiness = s5.get_readiness()

    assert readiness.available_templates == 0
    assert readiness.pending_reason is not None
    assert "来源证据已失效" in readiness.pending_reason


def test_avoid_skill_never_enters_script_generation(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "runtime" / "s3.sqlite3")
    store = ViralSkillStore(tmp_path / "analysis" / "skills.sqlite3")
    _accepted_report(queue, tmp_path / "analysis", "c", video_id="video_cccccccccccccccccccccccc")
    refresh_skill_candidates(store, queue)
    _approve(store, reuse_mode="avoid")

    assert list_templates(queue, store) == []


def test_resolve_template_freezes_skill_and_requires_current_accepted_source(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "runtime" / "s3.sqlite3")
    store = ViralSkillStore(tmp_path / "analysis" / "skills.sqlite3")
    report = _accepted_report(queue, tmp_path / "analysis", "d", video_id="video_dddddddddddddddddddddddd")
    refresh_skill_candidates(store, queue)
    skill = _approve(store)

    template, resolved_report, pattern, resolved_skill = resolve_template(queue, store, skill["skill_id"])

    assert template["skill_id"] == skill["skill_id"]
    assert resolved_skill["revision"] == skill["revision"]
    assert resolved_report["analysis_id"] == report["analysis_id"]
    assert pattern["id"] == skill["occurrences"][0]["pattern_id"]

    task = queue.completed_reports()[0]
    current = json.loads(Path(task.result_path or "").read_text(encoding="utf-8"))
    current["status"] = "reviewed"
    Path(task.result_path or "").write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="来源分析"):
        resolve_template(queue, store, skill["skill_id"])


def test_resolve_template_loads_its_frozen_analysis_task_without_scanning_all_reports(
    monkeypatch,
    tmp_path: Path,
) -> None:
    queue = AnalysisTaskQueue(tmp_path / "runtime" / "s3.sqlite3")
    store = ViralSkillStore(tmp_path / "analysis" / "skills.sqlite3")
    report = _accepted_report(
        queue,
        tmp_path / "analysis",
        "0",
        video_id="video_000000000000000000000000",
    )
    refresh_skill_candidates(store, queue)
    skill = _approve(store)
    monkeypatch.setattr(
        queue,
        "completed_reports",
        lambda: (_ for _ in ()).throw(AssertionError("解析单个 Skill 不应全量扫描报告")),
    )

    _template, resolved_report, _pattern, _skill = resolve_template(queue, store, skill["skill_id"])

    assert resolved_report["analysis_id"] == report["analysis_id"]


def test_nonrepresentative_source_change_blocks_stale_common_skill(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "runtime" / "s3.sqlite3")
    store = ViralSkillStore(tmp_path / "analysis" / "skills.sqlite3")
    _accepted_report(queue, tmp_path / "analysis-a", "e", video_id="video_eeeeeeeeeeeeeeeeeeeeeeee")
    _accepted_report(queue, tmp_path / "analysis-b", "f", video_id="video_ffffffffffffffffffffffff")
    refresh_skill_candidates(store, queue)
    skill = _approve(store)
    representative = next(
        item for item in skill["occurrences"]
        if item["occurrence_id"] == skill["representative_occurrence_id"]
    )
    stale_analysis_id = next(
        item["analysis_id"] for item in skill["occurrences"]
        if item["analysis_id"] != representative["analysis_id"]
    )
    for task in queue.completed_reports():
        path = Path(task.result_path or "")
        report = json.loads(path.read_text(encoding="utf-8"))
        if report["analysis_id"] == stale_analysis_id:
            report["status"] = "reviewed"
            path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            break

    refresh_skill_candidates(store, queue)

    assert list_templates(queue, store) == []
    with pytest.raises(ValueError, match="证据.*变化|来源.*变化"):
        resolve_template(queue, store, skill["skill_id"])
