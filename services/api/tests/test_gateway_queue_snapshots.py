from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from content_factory_api import s3_queue as s3_queue_module
from content_factory_api import s5_queue as s5_queue_module
from content_factory_api import s6 as s6_module
from content_factory_api.s3_analysis import AnalysisArtifacts
from content_factory_api.s3_queue import AnalysisTaskQueue
from content_factory_api.s3_settings import GatewayConfig, GatewayModelConfig
from content_factory_api.s5_generation import ScriptArtifacts
from content_factory_api.s5_queue import ScriptTaskQueue
from content_factory_api.s6_queue import MaterialImportQueue


def _model(
    model_id: str,
    *,
    base_url: str,
    model: str,
    api_mode: str,
    api_key: str,
    provider: str = "openai_compatible",
    modalities: tuple[str, ...] = ("text", "image"),
    purposes: tuple[str, ...] = ("analysis", "script", "material", "video_review"),
) -> GatewayModelConfig:
    return GatewayModelConfig(
        model_id=model_id,
        display_name=model_id,
        base_url=base_url,
        model=model,
        api_mode=api_mode,  # type: ignore[arg-type]
        api_key=api_key,
        modalities=modalities,  # type: ignore[arg-type]
        purposes=purposes,  # type: ignore[arg-type]
        provider=provider,  # type: ignore[arg-type]
    )


def _registry(
    *,
    alpha: GatewayModelConfig | None = None,
    default_model_id: str = "model_alpha",
    routed_model_id: str = "model_alpha",
) -> GatewayConfig:
    alpha = alpha or _model(
        "model_alpha",
        base_url="https://relay-a.example.com/v1",
        model="vision-alpha",
        api_mode="responses",
        api_key="alpha-key-before",
    )
    beta = _model(
        "model_beta",
        base_url="https://relay-b.example.com/v1",
        model="vision-beta",
        api_mode="chat_completions",
        api_key="beta-key",
    )
    models = (alpha, beta)
    selected = next(item for item in models if item.model_id == default_model_id)
    return GatewayConfig(
        selected.base_url,
        selected.model,
        selected.api_mode,
        selected.api_key,
        "2026-08-31T00:00:00Z",
        model_id=selected.model_id,
        display_name=selected.display_name,
        modalities=selected.modalities,
        purposes=selected.purposes,
        provider=selected.provider,
        models=models,
        routing={
            purpose: routed_model_id
            for purpose in ("analysis", "script", "material", "video_review")
        },
    )


def _enqueue_analysis(
    queue: AnalysisTaskQueue,
    tmp_path: Path,
    snapshot: dict[str, object],
    *,
    purpose: str = "analysis",
):
    media_result = tmp_path / "media-result.json"
    media_result.write_text("{}", encoding="utf-8")
    return queue.enqueue(
        media_task_id="media_00000000000000000000000000000001",
        media_result_path=media_result,
        workspace_path=tmp_path / "analysis",
        input_payload={
            "metric_snapshots": [],
            "comments": [],
            "_gateway_model_snapshot": snapshot,
            "_gateway_purpose": purpose,
        },
        fixture_data=True,
    )


def _enqueue_script(
    queue: ScriptTaskQueue,
    tmp_path: Path,
    snapshot: dict[str, object],
):
    return queue.enqueue(
        workspace_path=tmp_path / "scripts",
        input_payload={
            "product": {"product_id": "product_demo_001", "revision": 1, "fixture_data": True},
            "analysis": {"analysis_id": "analysis_demo_001", "revision": 1, "fixture_data": True},
            "pattern": {"id": "pattern_demo_001"},
            "request": {
                "content_goal": "seeding",
                "target_audience": "关注通勤裤装的成年男性",
                "version_count": 3,
            },
            "_gateway_model_snapshot": snapshot,
        },
    )


def _enqueue_material(
    queue: MaterialImportQueue,
    tmp_path: Path,
    snapshot: dict[str, object],
):
    source = tmp_path / "material.mp4"
    source.write_bytes(b"fixture")
    return queue.enqueue(
        source,
        tmp_path / "materials",
        fixture_data=True,
        source_name="material.mp4",
        product_id="product_demo_001",
        source_script_id=None,
        input_payload={
            "product": {"product_id": "product_demo_001"},
            "archive": {"imported_by": "tester"},
            "_gateway_model_snapshot": snapshot,
        },
    )


def _prepare_material_success(monkeypatch, tmp_path: Path) -> None:
    result_path = tmp_path / "material-result.json"
    result_path.write_text("{}", encoding="utf-8")

    class FakePipeline:
        def __init__(self, **_kwargs):
            pass

        def process(self, *_args, **_kwargs):
            return result_path

    class DuplicateStore:
        @staticmethod
        def create(*_args, **_kwargs):
            return {
                "material_id": "material_demo_001",
                "processing": {"recognition_status": "completed"},
            }, True

    monkeypatch.setattr(s6_module, "MediaPipeline", FakePipeline)
    monkeypatch.setattr(s6_module, "_model_path", lambda: None)
    monkeypatch.setattr(s6_module, "build_material_profile", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(s6_module, "get_material_store", lambda: DuplicateStore())


def _analysis_artifacts(task_directory: str | Path) -> AnalysisArtifacts:
    root = Path(task_directory)
    root.mkdir(parents=True, exist_ok=True)
    report = root / "analysis-report.json"
    ocr = root / "ocr-result.json"
    report.write_text("{}", encoding="utf-8")
    ocr.write_text("{}", encoding="utf-8")
    return AnalysisArtifacts("analysis_demo_001", report, ocr)


def _script_artifacts(task_id: str, task_directory: str | Path) -> ScriptArtifacts:
    root = Path(task_directory)
    root.mkdir(parents=True, exist_ok=True)
    result = root / "script-package.json"
    result.write_text("{}", encoding="utf-8")
    return ScriptArtifacts(f"script_{task_id[-32:]}", result)


def test_execution_snapshot_tracks_provider_modalities_and_purposes() -> None:
    selected = _registry().for_purpose("analysis")
    snapshot = selected.execution_snapshot()

    assert snapshot["provider"] == "openai_compatible"
    assert snapshot["modalities"] == ["text", "image"]
    assert snapshot["purposes"] == ["analysis", "script", "material", "video_review"]
    assert not selected.matches_snapshot({**snapshot, "provider": "qwen"})
    assert not selected.matches_snapshot({**snapshot, "modalities": ["text"]})
    assert not selected.matches_snapshot({**snapshot, "purposes": ["analysis"]})
    assert selected.matches_snapshot({
        **snapshot,
        "modalities": ["image", "text"],
        "purposes": ["video_review", "material", "script", "analysis"],
    })


def test_legacy_four_field_snapshot_can_finish_after_current_purpose_validation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    current = _registry()
    complete_snapshot = current.for_purpose("analysis").execution_snapshot()
    legacy_snapshot = {
        key: complete_snapshot[key]
        for key in ("model_id", "base_url", "model", "api_mode")
    }
    queue = AnalysisTaskQueue(tmp_path / "s3-legacy.sqlite3")
    task = _enqueue_analysis(queue, tmp_path, legacy_snapshot)
    seen: list[str] = []

    def process(**kwargs):
        seen.append(kwargs["config"].model_id)
        return _analysis_artifacts(kwargs["task_directory"])

    monkeypatch.setattr(s3_queue_module, "process_analysis", process)
    queue.run_pending(gateway_config=current)

    completed = queue.get(task.task_id)
    assert completed is not None and completed.status == "succeeded"
    assert seen == ["model_alpha"]


def test_legacy_snapshot_rejects_provider_semantics_change(monkeypatch, tmp_path: Path) -> None:
    original = _registry()
    complete_snapshot = original.for_purpose("analysis").execution_snapshot()
    legacy_snapshot = {
        key: complete_snapshot[key]
        for key in ("model_id", "base_url", "model", "api_mode")
    }
    changed = _registry(alpha=_model(
        "model_alpha",
        base_url="https://relay-a.example.com/v1",
        model="vision-alpha",
        api_mode="responses",
        api_key="alpha-key-after",
        provider="qwen",
    ))
    queue = AnalysisTaskQueue(tmp_path / "s3-legacy-provider.sqlite3")
    task = _enqueue_analysis(queue, tmp_path, legacy_snapshot)
    calls = 0

    def must_not_process(**_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("legacy provider mismatch must fail before processing")

    monkeypatch.setattr(s3_queue_module, "process_analysis", must_not_process)
    queue.run_pending(gateway_config=changed)

    failed = queue.get(task.task_id)
    assert failed is not None and failed.status == "failed"
    assert failed.error == "任务绑定的模型配置已变更，请明确重新创建任务后再运行"
    assert calls == 0


def test_provider_change_invalidates_a_pinned_analysis_snapshot(monkeypatch, tmp_path: Path) -> None:
    original = _registry(alpha=_model(
        "model_alpha",
        base_url="https://relay-a.example.com/v1",
        model="vision-alpha",
        api_mode="responses",
        api_key="alpha-key-before",
        provider="openai",
    ))
    changed = _registry(alpha=_model(
        "model_alpha",
        base_url="https://relay-a.example.com/v1",
        model="vision-alpha",
        api_mode="responses",
        api_key="alpha-key-after",
        provider="qwen",
    ))
    queue = AnalysisTaskQueue(tmp_path / "s3-provider.sqlite3")
    task = _enqueue_analysis(queue, tmp_path, original.for_purpose("analysis").execution_snapshot())
    calls = 0

    def must_not_process(**_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("provider mismatch must fail before processing")

    monkeypatch.setattr(s3_queue_module, "process_analysis", must_not_process)
    queue.run_pending(gateway_config=changed)

    failed = queue.get(task.task_id)
    assert failed is not None and failed.status == "failed"
    assert failed.error == "任务绑定的模型配置已变更，请明确重新创建任务后再运行"
    assert calls == 0


def test_s3_pinned_model_must_still_support_analysis(monkeypatch, tmp_path: Path) -> None:
    original = _registry()
    changed = _registry(alpha=_model(
        "model_alpha",
        base_url="https://relay-a.example.com/v1",
        model="vision-alpha",
        api_mode="responses",
        api_key="alpha-key-after",
        purposes=("script", "material", "video_review"),
    ))
    queue = AnalysisTaskQueue(tmp_path / "s3-purpose.sqlite3")
    task = _enqueue_analysis(queue, tmp_path, original.for_purpose("analysis").execution_snapshot())
    monkeypatch.setattr(
        s3_queue_module,
        "process_analysis",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not process")),
    )

    queue.run_pending(gateway_config=changed)

    failed = queue.get(task.task_id)
    assert failed is not None and failed.status == "failed"
    assert "不再支持深度分析" in failed.error


def test_s5_pinned_model_must_still_support_script(monkeypatch, tmp_path: Path) -> None:
    original = _registry()
    changed = _registry(alpha=_model(
        "model_alpha",
        base_url="https://relay-a.example.com/v1",
        model="vision-alpha",
        api_mode="responses",
        api_key="alpha-key-after",
        purposes=("analysis", "material", "video_review"),
    ))
    queue = ScriptTaskQueue(tmp_path / "s5-purpose.sqlite3")
    task = _enqueue_script(queue, tmp_path, original.for_purpose("script").execution_snapshot())
    monkeypatch.setattr(
        s5_queue_module,
        "process_script_generation",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not process")),
    )

    queue.run_pending(gateway_config=changed)

    failed = queue.get(task.task_id)
    assert failed is not None and failed.status == "failed"
    assert "不再支持脚本生成" in failed.error


def test_s6_pinned_model_must_still_support_material(monkeypatch, tmp_path: Path) -> None:
    original = _registry()
    changed = _registry(alpha=_model(
        "model_alpha",
        base_url="https://relay-a.example.com/v1",
        model="vision-alpha",
        api_mode="responses",
        api_key="alpha-key-after",
        purposes=("analysis", "script", "video_review"),
    ))
    queue = MaterialImportQueue(tmp_path / "s6-purpose.sqlite3")
    task = _enqueue_material(queue, tmp_path, original.for_purpose("material").execution_snapshot())
    monkeypatch.setattr(
        s6_module,
        "get_gateway_settings_store",
        lambda: SimpleNamespace(load=lambda: changed),
    )

    class MustNotRunPipeline:
        def __init__(self, **_kwargs):
            pass

        def process(self, *_args, **_kwargs):
            raise AssertionError("must not process")

    monkeypatch.setattr(s6_module, "MediaPipeline", MustNotRunPipeline)
    queue.run_pending(s6_module._process_import)

    failed = queue.get(task.task_id)
    assert failed is not None and failed.status == "failed"
    assert failed.error == "素材任务绑定的模型配置已变更，请明确重新导入后再识别"


def test_video_review_queue_uses_video_review_capability(monkeypatch, tmp_path: Path) -> None:
    current = _registry()
    queue = AnalysisTaskQueue(tmp_path / "s3-video-review.sqlite3")
    task = _enqueue_analysis(
        queue,
        tmp_path,
        current.for_purpose("video_review").execution_snapshot(),
        purpose="video_review",
    )
    seen: list[tuple[GatewayConfig, str]] = []

    def process(**kwargs):
        seen.append((kwargs["config"], kwargs["purpose"]))
        return _analysis_artifacts(kwargs["task_directory"])

    monkeypatch.setattr(s3_queue_module, "process_analysis", process)
    queue.run_pending(gateway_config=current)

    completed = queue.get(task.task_id)
    assert completed is not None and completed.status == "succeeded"
    assert completed.model_purpose == "video_review"
    assert len(seen) == 1
    assert "video_review" in seen[0][0].purposes
    assert seen[0][1] == "video_review"


def test_s3_default_route_change_does_not_retarget_queued_task(monkeypatch, tmp_path: Path) -> None:
    original = _registry()
    queue = AnalysisTaskQueue(tmp_path / "s3.sqlite3")
    task = _enqueue_analysis(queue, tmp_path, original.for_purpose("analysis").execution_snapshot())
    rerouted = _registry(default_model_id="model_beta", routed_model_id="model_beta")
    seen: list[GatewayConfig] = []

    def process(**kwargs):
        seen.append(kwargs["config"])
        return _analysis_artifacts(kwargs["task_directory"])

    monkeypatch.setattr(s3_queue_module, "process_analysis", process)
    queue.run_pending(gateway_config=rerouted)

    completed = queue.get(task.task_id)
    assert rerouted.for_purpose("analysis").model_id == "model_beta"
    assert completed is not None and completed.status == "succeeded"
    assert [config.model_id for config in seen] == ["model_alpha"]


def test_s5_default_route_change_does_not_retarget_queued_task(monkeypatch, tmp_path: Path) -> None:
    original = _registry()
    queue = ScriptTaskQueue(tmp_path / "s5.sqlite3")
    task = _enqueue_script(queue, tmp_path, original.for_purpose("script").execution_snapshot())
    rerouted = _registry(default_model_id="model_beta", routed_model_id="model_beta")
    seen: list[GatewayConfig] = []

    def process(**kwargs):
        seen.append(kwargs["config"])
        return _script_artifacts(kwargs["task_id"], kwargs["task_directory"])

    monkeypatch.setattr(s5_queue_module, "process_script_generation", process)
    queue.run_pending(gateway_config=rerouted)

    completed = queue.get(task.task_id)
    assert rerouted.for_purpose("script").model_id == "model_beta"
    assert completed is not None and completed.status == "completed"
    assert [config.model_id for config in seen] == ["model_alpha"]


def test_s6_default_route_change_does_not_retarget_queued_task(monkeypatch, tmp_path: Path) -> None:
    original = _registry()
    queue = MaterialImportQueue(tmp_path / "s6.sqlite3")
    task = _enqueue_material(queue, tmp_path, original.for_purpose("material").execution_snapshot())
    rerouted = _registry(default_model_id="model_beta", routed_model_id="model_beta")
    selected_ids: list[str | None] = []

    def material_gateway(model_id: str | None = None):
        selected_ids.append(model_id)
        return rerouted.for_model_purpose(model_id, "material") if model_id else rerouted.for_purpose("material")

    _prepare_material_success(monkeypatch, tmp_path)
    monkeypatch.setattr(s6_module, "_material_gateway", material_gateway)
    queue.run_pending(s6_module._process_import)

    completed = queue.get(task.task_id)
    assert rerouted.for_purpose("material").model_id == "model_beta"
    assert completed is not None and completed.status == "completed"
    assert selected_ids == ["model_alpha"]


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("base_url", "https://relay-a.example.com/v2"),
        ("model", "vision-alpha-v2"),
        ("api_mode", "chat_completions"),
    ],
)
def test_s3_changed_model_identity_fails_old_task_once_without_processing(
    monkeypatch,
    tmp_path: Path,
    changed_field: str,
    changed_value: str,
) -> None:
    original = _registry()
    queue = AnalysisTaskQueue(tmp_path / "s3.sqlite3", retry_base_seconds=0.01)
    task = _enqueue_analysis(queue, tmp_path, original.for_purpose("analysis").execution_snapshot())
    values = {
        "base_url": "https://relay-a.example.com/v1",
        "model": "vision-alpha",
        "api_mode": "responses",
    }
    values[changed_field] = changed_value
    changed = _registry(alpha=_model(
        "model_alpha",
        base_url=values["base_url"],
        model=values["model"],
        api_mode=values["api_mode"],
        api_key="alpha-key-after",
    ))
    calls = 0

    def must_not_process(**_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("snapshot mismatch must be rejected before model processing")

    monkeypatch.setattr(s3_queue_module, "process_analysis", must_not_process)
    queue.run_pending(gateway_config=changed)
    assert queue.run_pending(gateway_config=changed) == []

    failed = queue.get(task.task_id)
    assert failed is not None and failed.status == "failed"
    assert failed.attempt_count == 1
    assert failed.error == "任务绑定的模型配置已变更，请明确重新创建任务后再运行"
    assert queue.counts()["retry_wait"] == 0
    assert calls == 0


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("base_url", "https://relay-a.example.com/v2"),
        ("model", "vision-alpha-v2"),
        ("api_mode", "chat_completions"),
    ],
)
def test_s5_changed_model_identity_fails_old_task_once_without_processing(
    monkeypatch,
    tmp_path: Path,
    changed_field: str,
    changed_value: str,
) -> None:
    original = _registry()
    queue = ScriptTaskQueue(tmp_path / "s5.sqlite3", retry_base_seconds=0.01)
    task = _enqueue_script(queue, tmp_path, original.for_purpose("script").execution_snapshot())
    values = {
        "base_url": "https://relay-a.example.com/v1",
        "model": "vision-alpha",
        "api_mode": "responses",
    }
    values[changed_field] = changed_value
    changed = _registry(alpha=_model(
        "model_alpha",
        base_url=values["base_url"],
        model=values["model"],
        api_mode=values["api_mode"],
        api_key="alpha-key-after",
    ))
    calls = 0

    def must_not_process(**_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("snapshot mismatch must be rejected before model processing")

    monkeypatch.setattr(s5_queue_module, "process_script_generation", must_not_process)
    queue.run_pending(gateway_config=changed)
    assert queue.run_pending(gateway_config=changed) == []

    failed = queue.get(task.task_id)
    assert failed is not None and failed.status == "failed"
    assert failed.attempt_count == 1
    assert failed.error == "任务绑定的模型配置已变更，请明确重新创建任务后再运行"
    assert queue.counts()["retry_wait"] == 0
    assert calls == 0


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("base_url", "https://relay-a.example.com/v2"),
        ("model", "vision-alpha-v2"),
        ("api_mode", "chat_completions"),
    ],
)
def test_s6_changed_model_identity_fails_old_task_once_before_media_processing(
    monkeypatch,
    tmp_path: Path,
    changed_field: str,
    changed_value: str,
) -> None:
    original = _registry()
    queue = MaterialImportQueue(tmp_path / "s6.sqlite3", retry_base_seconds=0.01)
    task = _enqueue_material(queue, tmp_path, original.for_purpose("material").execution_snapshot())
    values = {
        "base_url": "https://relay-a.example.com/v1",
        "model": "vision-alpha",
        "api_mode": "responses",
    }
    values[changed_field] = changed_value
    changed = _registry(alpha=_model(
        "model_alpha",
        base_url=values["base_url"],
        model=values["model"],
        api_mode=values["api_mode"],
        api_key="alpha-key-after",
    ))
    media_calls = 0

    class MustNotRunPipeline:
        def __init__(self, **_kwargs):
            pass

        def process(self, *_args, **_kwargs):
            nonlocal media_calls
            media_calls += 1
            raise AssertionError("snapshot mismatch must be rejected before local media processing")

    monkeypatch.setattr(s6_module, "MediaPipeline", MustNotRunPipeline)
    monkeypatch.setattr(
        s6_module,
        "_material_gateway",
        lambda model_id=None: (
            changed.for_model_purpose(model_id, "material")
            if model_id else changed.for_purpose("material")
        ),
    )
    queue.run_pending(s6_module._process_import)
    assert queue.run_pending(s6_module._process_import) == []

    failed = queue.get(task.task_id)
    assert failed is not None and failed.status == "failed"
    assert failed.attempt_count == 1
    assert failed.error == "素材任务绑定的模型配置已变更，请明确重新导入后再识别"
    assert media_calls == 0


def test_s3_api_key_rotation_does_not_invalidate_queued_task(monkeypatch, tmp_path: Path) -> None:
    original = _registry()
    queue = AnalysisTaskQueue(tmp_path / "s3.sqlite3")
    task = _enqueue_analysis(queue, tmp_path, original.for_purpose("analysis").execution_snapshot())
    rotated = _registry(alpha=_model(
        "model_alpha",
        base_url="https://relay-a.example.com/v1",
        model="vision-alpha",
        api_mode="responses",
        api_key="alpha-key-rotated",
    ))
    seen_keys: list[str] = []

    def process(**kwargs):
        seen_keys.append(kwargs["config"].api_key)
        return _analysis_artifacts(kwargs["task_directory"])

    monkeypatch.setattr(s3_queue_module, "process_analysis", process)
    queue.run_pending(gateway_config=rotated)

    completed = queue.get(task.task_id)
    assert completed is not None and completed.status == "succeeded"
    assert completed.attempt_count == 1
    assert seen_keys == ["alpha-key-rotated"]


def test_s5_api_key_rotation_does_not_invalidate_queued_task(monkeypatch, tmp_path: Path) -> None:
    original = _registry()
    queue = ScriptTaskQueue(tmp_path / "s5.sqlite3")
    task = _enqueue_script(queue, tmp_path, original.for_purpose("script").execution_snapshot())
    rotated = _registry(alpha=_model(
        "model_alpha",
        base_url="https://relay-a.example.com/v1",
        model="vision-alpha",
        api_mode="responses",
        api_key="alpha-key-rotated",
    ))
    seen_keys: list[str] = []

    def process(**kwargs):
        seen_keys.append(kwargs["config"].api_key)
        return _script_artifacts(kwargs["task_id"], kwargs["task_directory"])

    monkeypatch.setattr(s5_queue_module, "process_script_generation", process)
    queue.run_pending(gateway_config=rotated)

    completed = queue.get(task.task_id)
    assert completed is not None and completed.status == "completed"
    assert completed.attempt_count == 1
    assert seen_keys == ["alpha-key-rotated"]


def test_s6_api_key_rotation_does_not_invalidate_queued_task(monkeypatch, tmp_path: Path) -> None:
    original = _registry()
    queue = MaterialImportQueue(tmp_path / "s6.sqlite3")
    task = _enqueue_material(queue, tmp_path, original.for_purpose("material").execution_snapshot())
    rotated = _registry(alpha=_model(
        "model_alpha",
        base_url="https://relay-a.example.com/v1",
        model="vision-alpha",
        api_mode="responses",
        api_key="alpha-key-rotated",
    ))
    seen_keys: list[str] = []

    def material_gateway(model_id: str | None = None):
        config = rotated.for_model_purpose(model_id, "material") if model_id else rotated.for_purpose("material")
        seen_keys.append(config.api_key)
        return config

    _prepare_material_success(monkeypatch, tmp_path)
    monkeypatch.setattr(s6_module, "_material_gateway", material_gateway)
    queue.run_pending(s6_module._process_import)

    completed = queue.get(task.task_id)
    assert completed is not None and completed.status == "completed"
    assert completed.attempt_count == 1
    assert seen_keys == ["alpha-key-rotated"]
