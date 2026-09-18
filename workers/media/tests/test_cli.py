import json

from content_factory_media.cli import build_health, main


def test_worker_health_matches_shared_service_shape() -> None:
    health = build_health()

    assert health.service == "media-worker"
    assert health.status == "ok"
    assert health.version == "0.1.0"


def test_check_command_outputs_json(capsys) -> None:
    assert main(["--check"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "service": "media-worker",
        "status": "ok",
        "version": "0.1.0",
    }
