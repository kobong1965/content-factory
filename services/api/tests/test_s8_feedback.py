from __future__ import annotations

from copy import deepcopy
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from content_factory_api import s7, s8
from content_factory_api.main import app
from content_factory_api.s7_store import EditStore
from content_factory_api.s8_imports import metrics_from_ocr_text, parse_csv_candidates, recognize_metric_image
from content_factory_api.s8_learning import build_learning_report
from content_factory_api.s8_store import FeedbackConflictError, FeedbackStore

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PROJECT_ROOT / "packages" / "contracts" / "fixtures"


def _json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _add_approved_output(store: EditStore, *, variant_id: str = "edit_variant_demo_a", suffix: str = "001") -> dict:
    output = _json("render-output.valid.json")
    output["output_id"] = f"render_output_demo_{suffix}"
    output["variant_id"] = variant_id
    output["render"]["task_id"] = f"render_task_demo_{suffix}"
    output["media"]["sha256"] = ("c" if suffix == "001" else "e") * 64
    output["media"]["filename"] = f"工程样片-{suffix}.mp4"
    for key, value in list(output["resources"].items()):
        if isinstance(value, str):
            output["resources"][key] = f"render_{key}_{suffix}"
    output_directory = store.output_root / output["output_id"]
    output_directory.mkdir()
    resources = {}
    for key, resource_ref in output["resources"].items():
        if key == "jianying_experimental":
            continue
        path = output_directory / f"{key}.bin"
        path.write_bytes(b"s8-output-fixture")
        resources[resource_ref] = (path, "application/octet-stream", path.name)
    store.create_output(output, resources=resources)
    return store.review_output(output["output_id"], decision="approved", reviewer="成片审核甲", note="通过")


def _approved_project(root: Path, *, two_outputs: bool = False) -> tuple[EditStore, dict, list[dict]]:
    edit_store = EditStore(root / "s7")
    edit_store.create_project(_json("edit-project.valid.json"), actor="编辑甲")
    outputs = [_add_approved_output(edit_store)]
    if two_outputs:
        outputs.append(_add_approved_output(edit_store, variant_id="edit_variant_demo_b", suffix="002"))
    return edit_store, edit_store.get_project("edit_project_demo_001"), outputs


def _publication(store: FeedbackStore, output: dict, project: dict, suffix: str = "001") -> dict:
    publication, duplicate = store.create_publication(
        output=output, project=project, work_url=f"https://www.douyin.com/video/7600000000000000{suffix}",
        work_id=f"7600000000000000{suffix}", account_label="工程验收账号", title=f"工程样例 {suffix}",
        published_at="2026-08-30T01:00:00Z", actor="运营甲", note="工程样例",
    )
    assert duplicate is False
    return publication


def test_publication_only_accepts_current_approved_output_and_deduplicates(tmp_path: Path) -> None:
    _, project, outputs = _approved_project(tmp_path)
    store = FeedbackStore(tmp_path / "s8")
    publication = _publication(store, outputs[0], project)

    duplicate, existed = store.create_publication(
        output=outputs[0], project=project, work_url=publication["work_url"], work_id=publication["work_id"],
        account_label="工程验收账号", title="重复登记", published_at=publication["published_at"], actor="运营乙",
    )

    assert existed is True and duplicate["publication_id"] == publication["publication_id"]
    assert str(tmp_path) not in json.dumps(publication, ensure_ascii=False)


def test_snapshots_protect_cumulative_metrics_and_allow_explicit_correction(tmp_path: Path) -> None:
    _, project, outputs = _approved_project(tmp_path)
    store = FeedbackStore(tmp_path / "s8")
    publication = _publication(store, outputs[0], project)
    first = store.add_snapshot(
        publication["publication_id"], captured_at="2026-08-31T01:00:00Z", source="manual",
        confidence="confirmed", confirmed_by="运营甲", metrics={"views": 1000, "likes": 80},
    )

    with pytest.raises(Exception, match="累计指标 views"):
        store.add_snapshot(
            publication["publication_id"], captured_at="2026-09-01T01:00:00Z", source="manual",
            confidence="confirmed", confirmed_by="运营甲", metrics={"views": 900, "likes": 90},
        )
    correction = store.add_snapshot(
        publication["publication_id"], captured_at="2026-09-01T01:00:00Z", source="manual",
        confidence="confirmed", confirmed_by="运营甲", metrics={"views": 900, "likes": 90},
        is_correction=True, correction_reason="平台后台修正展示口径",
    )

    assert first["observation_minutes"] == 1440
    assert correction["is_correction"] is True
    with pytest.raises(FeedbackConflictError, match="已经有快照"):
        store.add_snapshot(
            publication["publication_id"], captured_at="2026-09-01T01:00:00Z", source="manual",
            confidence="confirmed", confirmed_by="运营甲", metrics={"views": 901},
            is_correction=True, correction_reason="重复时间",
        )


def test_csv_is_atomic_and_ocr_stays_unconfirmed() -> None:
    valid = "发布记录ID,采集时间,播放量,完播率,成交额(元)\npublication_demo_001,2026-08-31T01:00:00Z,1.2万,31%,9280\n".encode()
    _, candidates = parse_csv_candidates(valid)

    assert candidates[0]["metrics"] == {"views": 12000, "completion_rate": 0.31, "gmv_cents": 928000}
    with pytest.raises(ValueError, match="第 2 行"):
        parse_csv_candidates("发布记录ID,采集时间,播放量\npublication_demo_001,2026-08-31T01:00:00Z,错误\n".encode())
    metrics, confidence = metrics_from_ocr_text("播放量 12,800 点赞 920 完播率 31%", confidence=0.88)
    assert metrics == {"views": 12800, "likes": 920, "completion_rate": 0.31}
    assert confidence["views"] == 0.88


def test_real_ocr_adapter_builds_a_pending_candidate(tmp_path: Path) -> None:
    class Result:
        txts = ["播放量 12800", "商品点击率 4.2%"]
        scores = [0.95, 0.85]

    raw, candidates = recognize_metric_image(
        tmp_path / "screen.png", publication_id="publication_demo_001", captured_at="2026-08-31T01:00:00Z",
        engine=lambda _path: Result(),
    )

    assert "播放量" in raw
    assert candidates[0]["confidence"] == "unconfirmed"
    assert candidates[0]["metrics"]["product_ctr"] == 0.042


def test_import_confirmation_is_atomic_and_creates_formal_snapshots(tmp_path: Path) -> None:
    _, project, outputs = _approved_project(tmp_path, two_outputs=True)
    store = FeedbackStore(tmp_path / "s8")
    publications = [_publication(store, output, project, f"00{index}") for index, output in enumerate(outputs, start=1)]
    csv_text = (
        "发布记录ID,采集时间,播放量,商品点击率\n"
        f"{publications[0]['publication_id']},2026-08-31T01:00:00Z,10000,4.2%\n"
        f"{publications[1]['publication_id']},2026-08-31T01:00:00Z,9000,3.1%\n"
    ).encode()
    raw, candidates = parse_csv_candidates(csv_text)
    draft, _ = store.create_import_draft(
        import_kind="csv", fixture_data=True, source_name="metrics.csv", source_sha256="a" * 64,
        raw_text=raw, candidates=candidates, errors=[], created_by="运营甲",
    )
    result = store.confirm_import(draft["draft_id"], confirmed_by="运营甲")

    assert result["draft"]["status"] == "confirmed"
    assert len(result["snapshots"]) == 2
    assert all(item["confidence"] == "confirmed" for item in result["snapshots"])


def test_corrected_import_candidate_is_persisted_and_survives_store_reopen(tmp_path: Path) -> None:
    _, project, outputs = _approved_project(tmp_path)
    store_root = tmp_path / "s8"
    store = FeedbackStore(store_root)
    publication = _publication(store, outputs[0], project)
    raw, candidates = parse_csv_candidates((
        "发布记录ID,采集时间,播放量,商品点击率\n"
        f"{publication['publication_id']},2026-08-31T01:00:00Z,10000,4.2%\n"
    ).encode())
    draft, _ = store.create_import_draft(
        import_kind="csv", fixture_data=True, source_name="corrected.csv", source_sha256="b" * 64,
        raw_text=raw, candidates=candidates, errors=[], created_by="运营甲",
    )
    candidate = draft["candidates"][0]

    confirmed = store.confirm_import(
        draft["draft_id"], confirmed_by="运营乙", candidates=[{
            "candidate_id": candidate["candidate_id"],
            "captured_at": "2026-09-01T02:30:00Z",
            "confidence": "confirmed",
            "metrics": {"views": 18801, "product_ctr": 0.055},
        }],
    )

    assert confirmed["snapshots"][0]["captured_at"] == "2026-09-01T02:30:00Z"
    assert confirmed["snapshots"][0]["metrics"] == {"views": 18801, "product_ctr": 0.055}
    reopened = FeedbackStore(store_root)
    persisted = reopened.list_snapshots(
        publication_id=publication["publication_id"], include_fixtures=True,
    )
    assert len(persisted) == 1
    assert persisted[0]["snapshot_id"] == confirmed["snapshots"][0]["snapshot_id"]
    assert persisted[0]["captured_at"] == "2026-09-01T02:30:00Z"
    assert persisted[0]["metrics"] == {"views": 18801, "product_ctr": 0.055}


def test_learning_report_is_explainable_and_refuses_unfair_window(tmp_path: Path) -> None:
    _, project, outputs = _approved_project(tmp_path, two_outputs=True)
    store = FeedbackStore(tmp_path / "s8")
    publications = [_publication(store, output, project, f"00{index}") for index, output in enumerate(outputs, start=1)]
    snapshots = [
        store.add_snapshot(publications[0]["publication_id"], captured_at="2026-08-31T01:00:00Z", source="manual", confidence="confirmed", confirmed_by="运营甲", metrics={"views": 12000, "likes": 900, "product_ctr": 0.042}),
        store.add_snapshot(publications[1]["publication_id"], captured_at="2026-08-31T01:10:00Z", source="manual", confidence="confirmed", confirmed_by="运营甲", metrics={"views": 11000, "likes": 700, "product_ctr": 0.031}),
    ]
    report = build_learning_report(
        product_id="product_demo_001", publications=publications, snapshots=snapshots,
        primary_metric="product_ctr", target_window_minutes=1440,
    )
    insufficient = build_learning_report(
        product_id="product_demo_001", publications=publications, snapshots=snapshots,
        primary_metric="product_ctr", target_window_minutes=60,
    )

    assert report["status"] == "ready" and report["winner_publication_id"] == publications[0]["publication_id"]
    assert report["evidence"] and report["limitations"]
    assert insufficient["status"] == "insufficient_data" and insufficient["winner_publication_id"] is None


def test_business_review_requires_two_snapshots_and_explicit_human_confirmation(tmp_path: Path) -> None:
    _, project, outputs = _approved_project(tmp_path)
    store = FeedbackStore(tmp_path / "s8")
    output = deepcopy(outputs[0])
    output["fixture_data"] = False
    publication = _publication(store, output, project)

    with pytest.raises(ValueError, match="至少需要两个"):
        store.confirm_business_review(
            publication["publication_id"], expected_revision=1, reviewed_by="业务负责人", note="确认复盘",
        )
    store.add_snapshot(
        publication["publication_id"], captured_at="2026-08-31T01:00:00Z", source="manual",
        confidence="confirmed", confirmed_by="运营甲", metrics={"views": 1000},
    )
    store.add_snapshot(
        publication["publication_id"], captured_at="2026-09-01T01:00:00Z", source="manual",
        confidence="confirmed", confirmed_by="运营甲", metrics={"views": 1800},
    )
    confirmed = store.confirm_business_review(
        publication["publication_id"], expected_revision=1, reviewed_by="业务负责人", note="已核对数据和下一步测试",
    )

    assert confirmed["business_review"]["status"] == "confirmed"
    assert confirmed["history"][-1]["action"] == "loop_confirmed"
    assert store.counts()["closed_real_loops"] == 1

    corrected = store.update_publication(
        publication["publication_id"], expected_revision=2, actor="运营甲", action="corrected",
        title="更正后的作品标题", note="更正发布信息后需要重新确认闭环",
    )
    assert corrected["status"] == "published"
    assert corrected["business_review"]["status"] == "pending"
    assert store.counts()["closed_real_loops"] == 0


def test_s8_api_flow_exposes_no_private_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_S7_DATA_DIR", str(tmp_path / "s7"))
    monkeypatch.setenv("CONTENT_FACTORY_S8_DATA_DIR", str(tmp_path / "s8"))
    monkeypatch.setenv("CONTENT_FACTORY_ALLOW_FIXTURE_UPLOADS", "1")
    s7._stores.clear()
    s7._queues.clear()
    s8._stores.clear()
    edit_store = s7.get_edit_store()
    edit_store.create_project(_json("edit-project.valid.json"), actor="编辑甲")
    output = _add_approved_output(edit_store)
    client = TestClient(app)

    eligible = client.get("/s8/eligible-outputs")
    created = client.post("/s8/publications", json={
        "output_id": output["output_id"], "work_url": "https://www.douyin.com/video/7600000000000000001",
        "work_id": "7600000000000000001", "account_label": "工程验收账号", "title": "工程样片",
        "published_at": "2026-08-30T01:00:00Z", "actor": "运营甲", "note": "样例",
    })
    publication_id = created.json()["publication"]["publication_id"]
    snapshot = client.post(f"/s8/publications/{publication_id}/metric-snapshots", json={
        "captured_at": "2026-08-31T01:00:00Z", "confirmed_by": "运营甲", "metrics": {"views": 1000},
    })

    assert eligible.status_code == 200 and len(eligible.json()) == 1
    assert created.status_code == 201 and snapshot.status_code == 201
    assert str(tmp_path) not in created.text + snapshot.text
    assert "/s8/imports/ocr" in app.openapi()["paths"]
    assert f"/s8/publications/{{publication_id}}/confirm-business-review" in app.openapi()["paths"]
