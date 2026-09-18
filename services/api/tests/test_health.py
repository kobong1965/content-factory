from content_factory_api import main
from content_factory_api.main import (
    app,
    data_profile_id,
    get_health,
    get_s1_readiness,
    project_build_id,
    project_instance_id,
)


def test_health_endpoint_reports_service_version() -> None:
    health = get_health()

    assert health.model_dump() == {
        "service": "api",
        "status": "ok",
        "version": "0.1.0",
        "instance_id": project_instance_id(),
        "data_profile_id": data_profile_id(),
        "build_id": project_build_id(),
    }


def test_data_profile_fingerprint_changes_for_an_isolated_runtime(tmp_path, monkeypatch) -> None:
    baseline = data_profile_id()
    monkeypatch.setenv("CONTENT_FACTORY_RUNTIME_ROOT", str(tmp_path / "runtime"))

    assert data_profile_id() != baseline


def test_openapi_exposes_health_endpoint() -> None:
    schema = app.openapi()

    assert "/health" in schema["paths"]
    assert "/s1/readiness" in schema["paths"]


def test_s1_readiness_is_honest_about_missing_real_data() -> None:
    readiness = get_s1_readiness()

    assert readiness.model_dump() == {
        "stage": "S1",
        "schema_version": "1.0.0",
        "schema_count": 5,
        "engineering_ready": True,
        "business_ready": False,
        "accepted_videos": 0,
        "required_videos": 20,
        "accepted_products": 0,
        "required_products": 3,
        "pending_reason": "待补 20 条真实视频和 3 款真实商品",
    }


def test_desktop_dev_origin_is_allowed() -> None:
    cors = next(middleware for middleware in app.user_middleware if middleware.cls.__name__ == "CORSMiddleware")

    assert "http://127.0.0.1:1420" in cors.kwargs["allow_origins"]
    assert cors.kwargs["allow_methods"] == ["GET", "POST", "PUT", "PATCH", "DELETE"]


def test_startup_launches_recovery_consumers_for_every_local_queue(monkeypatch) -> None:
    started: list[tuple[str, object]] = []

    class ImmediateThread:
        def __init__(self, *, target, name, daemon):
            started.append((name, target))
            self.target = target
            assert daemon is True

        def start(self) -> None:
            self.target()

    calls: list[str] = []
    monkeypatch.setattr(main, "_RECOVERY_RUNNERS", tuple(
        (stage, lambda current=stage: calls.append(current))
        for stage in ("s2", "s5", "s6", "s7")
    ))
    monkeypatch.setattr(main.threading, "Thread", ImmediateThread)

    workers = main.start_recovered_queue_workers()

    assert calls == ["s2", "s5", "s6", "s7"]
    assert [name for name, _target in started] == [
        "recover-s2", "recover-s5", "recover-s6", "recover-s7",
    ]
    assert len(workers) == 4
