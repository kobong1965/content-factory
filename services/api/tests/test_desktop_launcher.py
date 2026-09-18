from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load_launcher():
    path = PROJECT_ROOT / "scripts" / "launch-desktop-hidden.pyw"
    loader = importlib.machinery.SourceFileLoader("content_factory_desktop_launcher", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_powershell_launcher_uses_the_same_unicode_data_profile() -> None:
    if sys.platform != "win32":
        return

    launcher = _load_launcher()
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PROJECT_ROOT / "scripts" / "launch-desktop.ps1"),
            "-PrintDataProfileId",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.stdout.strip() == launcher.default_data_profile_id(PROJECT_ROOT)


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int = -1) -> bytes:
        return self.payload


def test_launcher_checks_the_health_payload_instead_of_only_the_tcp_port(monkeypatch) -> None:
    launcher = _load_launcher()
    monkeypatch.setattr(launcher, "urlopen", lambda *_args, **_kwargs: _Response({"service": "other", "status": "ok"}))
    assert launcher.api_is_ready() is False

    expected_id = launcher.project_instance_id(PROJECT_ROOT)
    expected_data_profile = launcher.default_data_profile_id(PROJECT_ROOT)
    expected_build = launcher.project_build_id(PROJECT_ROOT)
    monkeypatch.setattr(launcher, "urlopen", lambda *_args, **_kwargs: _Response({
        "service": "api",
        "status": "ok",
        "version": launcher.API_COMPAT_VERSION,
        "instance_id": expected_id,
        "data_profile_id": expected_data_profile,
        "build_id": expected_build,
    }))
    assert launcher.api_is_ready() is True


def test_launcher_rejects_wrong_instance_incompatible_version_and_non_object_json(monkeypatch) -> None:
    launcher = _load_launcher()
    expected_id = launcher.project_instance_id(PROJECT_ROOT)
    expected_data_profile = launcher.default_data_profile_id(PROJECT_ROOT)
    expected_build = launcher.project_build_id(PROJECT_ROOT)

    monkeypatch.setattr(launcher, "urlopen", lambda *_args, **_kwargs: _Response({
        "service": "api",
        "status": "ok",
        "version": launcher.API_COMPAT_VERSION,
        "instance_id": "another-checkout",
        "data_profile_id": expected_data_profile,
        "build_id": expected_build,
    }))
    assert launcher.api_is_ready(PROJECT_ROOT) is False

    monkeypatch.setattr(launcher, "urlopen", lambda *_args, **_kwargs: _Response({
        "service": "api",
        "status": "ok",
        "version": "999.0.0",
        "instance_id": expected_id,
        "data_profile_id": expected_data_profile,
        "build_id": expected_build,
    }))
    assert launcher.api_is_ready(PROJECT_ROOT) is False


def test_launcher_rejects_same_checkout_using_an_isolated_data_profile(monkeypatch) -> None:
    launcher = _load_launcher()
    monkeypatch.setattr(launcher, "urlopen", lambda *_args, **_kwargs: _Response({
        "service": "api",
        "status": "ok",
        "version": launcher.API_COMPAT_VERSION,
        "instance_id": launcher.project_instance_id(PROJECT_ROOT),
        "data_profile_id": "isolated-test-data",
        "build_id": launcher.project_build_id(PROJECT_ROOT),
    }))

    assert launcher.api_is_ready(PROJECT_ROOT) is False


def test_launcher_rejects_a_stale_build_from_the_same_checkout(monkeypatch) -> None:
    launcher = _load_launcher()
    monkeypatch.setattr(launcher, "urlopen", lambda *_args, **_kwargs: _Response({
        "service": "api",
        "status": "ok",
        "version": launcher.API_COMPAT_VERSION,
        "instance_id": launcher.project_instance_id(PROJECT_ROOT),
        "data_profile_id": launcher.default_data_profile_id(PROJECT_ROOT),
        "build_id": "content-factory-0.1.7",
    }))

    assert launcher.api_is_ready(PROJECT_ROOT) is False

    monkeypatch.setattr(launcher, "urlopen", lambda *_args, **_kwargs: _Response([]))
    assert launcher.api_is_ready(PROJECT_ROOT) is False


def test_launcher_python_path_includes_the_real_media_package() -> None:
    launcher = _load_launcher()

    entries = launcher.python_path_entries(PROJECT_ROOT)

    assert PROJECT_ROOT / "workers" / "media" / "src" in entries
    assert PROJECT_ROOT / "packages" / "media" / "python" not in entries


def test_production_launcher_replaces_test_data_overrides(monkeypatch, tmp_path: Path) -> None:
    launcher = _load_launcher()
    monkeypatch.setenv("CONTENT_FACTORY_RUNTIME_ROOT", str(tmp_path / "test-runtime"))
    monkeypatch.setenv("CONTENT_FACTORY_S7_DATA_DIR", str(tmp_path / "test-s7"))
    monkeypatch.setenv("CONTENT_FACTORY_ALLOW_FIXTURE_UPLOADS", "1")
    monkeypatch.setenv("CONTENT_FACTORY_S7_INTEGRATION", "1")

    environment = launcher.production_environment(PROJECT_ROOT)
    expected = launcher.default_data_paths(PROJECT_ROOT, environment)

    for name, path in expected.items():
        assert Path(environment[name]) == path.resolve()
    assert "CONTENT_FACTORY_ALLOW_FIXTURE_UPLOADS" not in environment
    assert "CONTENT_FACTORY_S7_INTEGRATION" not in environment
