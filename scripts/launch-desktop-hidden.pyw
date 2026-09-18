from __future__ import annotations

import msvcrt
import hashlib
import json
import os
import subprocess
import time
import traceback
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


HOST = "127.0.0.1"
PORT = 8766
CREATE_NO_WINDOW = 0x08000000
API_COMPAT_VERSION = "0.1.0"


def project_instance_id(project_root: Path) -> str:
    normalized = os.path.normcase(str(project_root.resolve())).rstrip("\\/")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def project_build_id(project_root: Path) -> str:
    payload = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("The project package version is missing")
    return f"content-factory-{version.strip()}"


def default_data_paths(project_root: Path, environment: dict[str, str] | None = None) -> dict[str, Path]:
    env = os.environ if environment is None else environment
    local_app_data = Path(env.get("LOCALAPPDATA", project_root / "data" / "runtime"))
    return {
        "CONTENT_FACTORY_RUNTIME_ROOT": project_root / "data" / "runtime",
        "CONTENT_FACTORY_MEDIA_ROOT": project_root / "data" / "media",
        "CONTENT_FACTORY_ANALYSIS_ROOT": project_root / "data" / "analysis",
        "CONTENT_FACTORY_S3_CONFIG_PATH": local_app_data / "爆款内容工厂" / "gateway-config.json",
        "CONTENT_FACTORY_S4_DATA_DIR": project_root / "data" / "s4",
        "CONTENT_FACTORY_S5_DATA_DIR": project_root / "data" / "s5",
        "CONTENT_FACTORY_S6_DATA_DIR": project_root / "data" / "s6",
        "CONTENT_FACTORY_S7_DATA_DIR": project_root / "data" / "s7",
        "CONTENT_FACTORY_S8_DATA_DIR": project_root / "data" / "s8",
    }


def default_data_profile_id(project_root: Path, environment: dict[str, str] | None = None) -> str:
    normalized = [
        os.path.normcase(str(path.resolve())).rstrip("\\/")
        for path in default_data_paths(project_root, environment).values()
    ]
    return hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest()[:16]


def production_environment(project_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({name: str(path.resolve()) for name, path in default_data_paths(project_root, environment).items()})
    environment.pop("CONTENT_FACTORY_ALLOW_FIXTURE_UPLOADS", None)
    environment.pop("CONTENT_FACTORY_S7_INTEGRATION", None)
    return environment


def api_is_ready(project_root: Path | None = None) -> bool:
    expected_root = project_root or Path(__file__).resolve().parents[1]
    try:
        request = Request(f"http://{HOST}:{PORT}/health", headers={"Accept": "application/json"})
        with urlopen(request, timeout=0.75) as response:
            payload = json.loads(response.read(4096).decode("utf-8"))
        return (
            isinstance(payload, dict)
            and payload.get("service") == "api"
            and payload.get("status") == "ok"
            and payload.get("version") == API_COMPAT_VERSION
            and payload.get("instance_id") == project_instance_id(expected_root)
            and payload.get("data_profile_id") == default_data_profile_id(expected_root)
            and payload.get("build_id") == project_build_id(expected_root)
        )
    except (OSError, URLError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def python_path_entries(project_root: Path) -> tuple[Path, ...]:
    return (
        project_root / "packages" / "contracts" / "python",
        project_root / "workers" / "media" / "src",
        project_root / "services" / "api" / "src",
    )


def terminate(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run() -> int:
    project_root = Path(__file__).resolve().parents[1]
    application = project_root / "dist" / "windows" / "content-factory" / "content-factory-desktop.exe"
    python = project_root / ".venv" / "Scripts" / "python.exe"
    runtime_directory = project_root / "data" / "runtime"
    runtime_directory.mkdir(parents=True, exist_ok=True)

    if not application.is_file():
        raise FileNotFoundError(f"Desktop application is missing: {application}")
    if not python.is_file():
        raise FileNotFoundError(f"Local service runtime is missing: {python}")

    lock_path = runtime_directory / "desktop-launcher.lock"
    with lock_path.open("a+b") as lock_file:
        if os.fstat(lock_file.fileno()).st_size == 0:
            lock_file.write(b"1")
            lock_file.flush()
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return 0

        server: subprocess.Popen[bytes] | None = None
        try:
            if not api_is_ready(project_root):
                environment = production_environment(project_root)
                environment["PYTHONPATH"] = os.pathsep.join(str(path) for path in python_path_entries(project_root))
                server = subprocess.Popen(
                    [
                        str(python),
                        "-m",
                        "uvicorn",
                        "content_factory_api.main:app",
                        "--host",
                        HOST,
                        "--port",
                        str(PORT),
                        "--log-level",
                        "warning",
                    ],
                    cwd=project_root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW,
                )
                for _ in range(60):
                    if server.poll() is not None:
                        raise RuntimeError("Local service exited during startup")
                    if api_is_ready(project_root):
                        break
                    time.sleep(0.25)
                else:
                    raise TimeoutError("Local service startup timed out")

            desktop = subprocess.Popen(
                [str(application)],
                cwd=application.parent,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
            return desktop.wait()
        finally:
            terminate(server)


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except Exception:
        log_path = Path(__file__).resolve().parents[1] / "data" / "runtime" / "desktop-launcher-error.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(traceback.format_exc(), encoding="utf-8")
        raise SystemExit(1)
