from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from content_factory_api import s3
from content_factory_api.main import app
from content_factory_api.s3_analysis import AnalysisArtifacts
from content_factory_api.s3_queue import AnalysisTaskQueue
from content_factory_api.s3_skills import SkillConflictError, ViralSkillStore, refresh_skill_candidates


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PROJECT_ROOT / "packages" / "contracts" / "fixtures"


def _json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _accepted_report(
    queue: AnalysisTaskQueue,
    root: Path,
    *,
    marker: str,
    video_id: str,
    pattern_name: str = "结果先行配合细节证明",
    mechanism: str = "先展示上身结果，再用近距离面料动作补充可信证据。",
    mechanism_key: str = "result_then_visual_proof",
    reuse_mode: str = "reuse",
    purpose: str = "analysis",
    status: str = "accepted",
) -> dict:
    report = deepcopy(_json("analysis.valid.json"))
    report["fixture_data"] = False
    report["analysis_id"] = f"analysis_{marker * 32}"
    report["video_id"] = video_id
    report["status"] = status
    report["revision"] = 2
    report["processing"]["purpose"] = purpose
    report["source"]["source_id"] = f"source_{marker * 24}"
    report["source"]["source_uri"] = str(root / f"{marker}-主播长镜头.mp4")
    report["review"] = {
        "reviewer": "分析审核人" if status == "accepted" else None,
        "reviewed_at": "2026-09-07T08:00:00Z" if status == "accepted" else None,
        "note": "逐秒证据已核对" if status == "accepted" else None,
    }
    pattern = report["pattern_candidates"][0]
    pattern["id"] = f"pattern_{marker * 24}"
    pattern["name"] = pattern_name
    pattern["mechanism"] = mechanism
    pattern["mechanism_key"] = mechanism_key
    pattern["reuse_mode"] = reuse_mode

    media_result = root / f"media-{marker}.json"
    media_result.parent.mkdir(parents=True, exist_ok=True)
    media_result.write_text("{}", encoding="utf-8")
    media_task_id = f"media_{marker * 32}"
    task = queue.enqueue(
        media_task_id=media_task_id,
        media_result_path=media_result,
        workspace_path=root / "analysis",
        input_payload={"metric_snapshots": [], "comments": []},
        fixture_data=False,
    )
    claimed = queue.claim_next()
    assert claimed is not None and claimed.task_id == task.task_id and claimed.worker_id
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


def test_candidates_count_distinct_videos_and_materialize_exact_evidence(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "runtime" / "s3.sqlite3")
    store = ViralSkillStore(tmp_path / "analysis" / "skills" / "viral-skills.sqlite3")
    video_a = "video_aaaaaaaaaaaaaaaaaaaaaaaa"
    _accepted_report(queue, tmp_path / "first", marker="a", video_id=video_a)
    _accepted_report(queue, tmp_path / "second-analysis", marker="b", video_id=video_a)

    refresh_skill_candidates(store, queue)
    candidate = store.list_candidates()[0]

    assert candidate["occurrence_count"] == 2
    assert candidate["distinct_video_count"] == 1
    assert candidate["classification"] == "single_video"
    assert candidate["is_common"] is False
    detail = store.get_candidate(candidate["candidate_id"])
    assert {item["video_id"] for item in detail["occurrences"]} == {video_a}
    assert all(item["analysis_task_id"].startswith("analysis_task_") for item in detail["occurrences"])
    evidence = detail["occurrences"][0]["steps"][0]["evidence"][0]
    assert evidence["start_ms"] is not None
    assert evidence["exact_dialogue"]
    assert evidence["action"]
    assert evidence["claim"]
    assert detail["evidence_level"] in {"content_observation", "content_inference", "metric_correlation"}
    assert detail["causality_status"] == "not_established"

    _accepted_report(
        queue,
        tmp_path / "third-video",
        marker="c",
        video_id="video_cccccccccccccccccccccccc",
    )
    refresh_skill_candidates(store, queue)
    common = store.get_candidate(candidate["candidate_id"])

    assert common["occurrence_count"] == 3
    assert common["distinct_video_count"] == 2
    assert common["classification"] == "common_candidate"
    assert common["is_common"] is True


def test_refresh_excludes_unaccepted_video_review_and_fixture_reports(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "runtime" / "s3.sqlite3")
    store = ViralSkillStore(tmp_path / "analysis" / "skills" / "viral-skills.sqlite3")
    _accepted_report(
        queue,
        tmp_path / "review",
        marker="d",
        video_id="video_dddddddddddddddddddddddd",
        purpose="video_review",
    )
    _accepted_report(
        queue,
        tmp_path / "draft",
        marker="e",
        video_id="video_eeeeeeeeeeeeeeeeeeeeeeee",
        status="draft",
    )

    refresh_skill_candidates(store, queue)

    assert store.list_candidates() == []


def test_candidate_refresh_is_idempotent_and_never_overwrites_approved_skill(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "runtime" / "s3.sqlite3")
    store = ViralSkillStore(tmp_path / "analysis" / "skills" / "viral-skills.sqlite3")
    _accepted_report(
        queue,
        tmp_path / "first",
        marker="f",
        video_id="video_ffffffffffffffffffffffff",
    )
    refresh_skill_candidates(store, queue)
    first = store.list_candidates()[0]
    refresh_skill_candidates(store, queue)
    unchanged = store.get_candidate(first["candidate_id"])
    assert unchanged["revision"] == first["revision"]

    skill = store.approve_candidate(
        first["candidate_id"],
        expected_candidate_revision=first["revision"],
        expected_skill_revision=None,
        reviewer="内容负责人",
        reuse_mode="reuse",
        name="直播间结果先行 + 细节证明",
        mechanism="先把上身结果说清，再用同机位动作或同款细节覆盖完成证明。",
        note="允许用于固定直播间脚本",
    )
    _accepted_report(
        queue,
        tmp_path / "new-video",
        marker="1",
        video_id="video_111111111111111111111111",
    )
    refresh_skill_candidates(store, queue)

    preserved = store.get_skill(skill["skill_id"])
    refreshed_candidate = store.get_candidate(first["candidate_id"])
    assert preserved["revision"] == 1
    assert preserved["name"] == "直播间结果先行 + 细节证明"
    assert preserved["review"]["note"] == "允许用于固定直播间脚本"
    assert refreshed_candidate["linked_skill"]["skill_id"] == skill["skill_id"]
    assert refreshed_candidate["linked_skill"]["update_available"] is True

    with pytest.raises(SkillConflictError):
        store.approve_candidate(
            first["candidate_id"],
            expected_candidate_revision=first["revision"],
            expected_skill_revision=skill["revision"],
            reviewer="内容负责人",
            reuse_mode="reuse",
            name="过期提交",
            mechanism="过期提交不能覆盖新候选。",
            note=None,
        )


def test_disabled_skill_remains_readable_but_is_not_script_eligible(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "runtime" / "s3.sqlite3")
    store = ViralSkillStore(tmp_path / "analysis" / "skills" / "viral-skills.sqlite3")
    _accepted_report(
        queue,
        tmp_path / "first",
        marker="2",
        video_id="video_222222222222222222222222",
    )
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
        note=None,
    )
    disabled = store.set_skill_status(
        skill["skill_id"],
        expected_revision=skill["revision"],
        status="disabled",
        reviewer="内容负责人",
        note="证据不足，暂时停用",
    )

    assert disabled["status"] == "disabled"
    assert store.get_skill(skill["skill_id"])["review"]["note"] == "证据不足，暂时停用"
    assert store.list_skills(status="approved", reuse_mode="reuse") == []


def test_refresh_scans_completed_reports_beyond_the_old_500_task_limit(tmp_path: Path) -> None:
    queue = AnalysisTaskQueue(tmp_path / "runtime" / "s3.sqlite3")
    store = ViralSkillStore(tmp_path / "analysis" / "skills" / "viral-skills.sqlite3")
    oldest = _accepted_report(
        queue,
        tmp_path / "oldest",
        marker="3",
        video_id="video_333333333333333333333333",
        mechanism_key="question_then_demonstration",
    )
    media = tmp_path / "newer.json"
    media.write_text("{}", encoding="utf-8")
    for index in range(500):
        queue.enqueue(
            media_task_id=f"media_{index:032x}",
            media_result_path=media,
            workspace_path=tmp_path / "newer",
            input_payload={"metric_snapshots": [], "comments": []},
            fixture_data=False,
        )

    refresh_skill_candidates(store, queue)

    assert any(
        occurrence["analysis_id"] == oldest["analysis_id"]
        for candidate in store.list_candidates()
        for occurrence in store.get_candidate(candidate["candidate_id"])["occurrences"]
    )


def test_skill_api_requires_explicit_approval_and_uses_optimistic_locking(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("CONTENT_FACTORY_ANALYSIS_ROOT", str(tmp_path / "analysis"))
    s3._queue_instances.clear()
    s3._skill_store_instances.clear()
    _accepted_report(
        s3.get_analysis_queue(),
        tmp_path / "source",
        marker="4",
        video_id="video_444444444444444444444444",
    )
    client = TestClient(app)

    candidates = client.get("/s3/skill-candidates").json()
    assert len(candidates) == 1
    refreshed = client.post("/s3/skill-candidates/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["candidate_count"] == 1
    assert client.get("/s3/skills").json() == []

    candidate = candidates[0]
    approved = client.post(
        f"/s3/skill-candidates/{candidate['candidate_id']}/approve",
        json={
            "expected_candidate_revision": candidate["revision"],
            "expected_skill_revision": None,
            "reviewer": "内容负责人",
            "reuse_mode": "reuse",
            "name": candidate["suggested_name"],
            "mechanism": candidate["suggested_mechanism"],
            "note": "允许用于脚本",
        },
    )
    assert approved.status_code == 200, approved.text
    skill = approved.json()
    assert skill["status"] == "approved"
    assert client.get("/s3/skills?status=approved&reuse_mode=reuse").json()[0]["skill_id"] == skill["skill_id"]

    stale = client.post(
        f"/s3/skills/{skill['skill_id']}/status",
        json={
            "expected_revision": skill["revision"] + 1,
            "status": "disabled",
            "reviewer": "内容负责人",
            "note": "并发旧请求",
        },
    )
    assert stale.status_code == 409

    disabled = client.post(
        f"/s3/skills/{skill['skill_id']}/status",
        json={
            "expected_revision": skill["revision"],
            "status": "disabled",
            "reviewer": "内容负责人",
            "note": "暂时停用",
        },
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["status"] == "disabled"


def test_management_api_keeps_a_formal_skill_visible_when_its_source_becomes_inactive(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("CONTENT_FACTORY_ANALYSIS_ROOT", str(tmp_path / "analysis"))
    s3._queue_instances.clear()
    s3._skill_store_instances.clear()
    _accepted_report(
        s3.get_analysis_queue(),
        tmp_path / "source",
        marker="5",
        video_id="video_555555555555555555555555",
    )
    client = TestClient(app)
    candidate = client.get("/s3/skill-candidates").json()[0]
    approved = client.post(
        f"/s3/skill-candidates/{candidate['candidate_id']}/approve",
        json={
            "expected_candidate_revision": candidate["revision"],
            "expected_skill_revision": None,
            "reviewer": "内容负责人",
            "reuse_mode": "reuse",
            "name": candidate["suggested_name"],
            "mechanism": candidate["suggested_mechanism"],
            "note": "来源失效后仍需管理",
        },
    ).json()
    task = s3.get_analysis_queue().completed_reports()[0]
    report_path = Path(task.result_path or "")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["status"] = "reviewed"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    refreshed = client.post("/s3/skill-candidates/refresh")

    assert refreshed.status_code == 200, refreshed.text
    assert client.get("/s3/skill-candidates").json() == []
    management_candidates = client.get(
        "/s3/skill-candidates?include_inactive_linked=true",
    ).json()
    assert len(management_candidates) == 1
    assert management_candidates[0]["active"] is False
    assert management_candidates[0]["linked_skill"]["source_status"] == "inactive"
    assert management_candidates[0]["linked_skill"]["update_available"] is True

    skill = client.get(f"/s3/skills/{approved['skill_id']}").json()
    assert skill["source_status"] == "inactive"
    assert skill["update_available"] is True
    assert skill["eligibility"] == {
        "s5_eligible": False,
        "reason_code": "source_inactive",
        "reason": "Skill 的来源证据已失效，请重新确认候选",
    }
    listed = client.get("/s3/skills?status=approved&reuse_mode=reuse").json()
    assert [item["skill_id"] for item in listed] == [approved["skill_id"]]
    assert listed[0]["eligibility"] == skill["eligibility"]
