from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from content_factory_api import auto_edit_worker
from content_factory_media.tools import find_tool


def test_installed_profile_depth_finishes_media_preprocessing_without_paid_model(tmp_path_factory, monkeypatch):
    """Real FFmpeg I/O under an even longer root than the failing installation."""
    tmp_path = tmp_path_factory.mktemp("ap")
    source = tmp_path / "录播.mp4"
    subprocess.run([
        find_tool("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=96x160:r=10", "-t", "1", "-c:v", "libx264", str(source),
    ], check=True, capture_output=True)
    root = tmp_path / ("p" * (100 - len(str(tmp_path)) - 1))
    assert len(str(root)) == 100
    gateway = SimpleNamespace(has_purpose=lambda purpose: True, for_purpose=lambda purpose: None)
    monkeypatch.setattr(auto_edit_worker, "GatewaySettingsStore", lambda *args: SimpleNamespace(load=lambda: gateway))
    monkeypatch.setattr(auto_edit_worker, "call_gateway", lambda *args, **kwargs: pytest.fail("No paid model in path regression"))
    project = {
        "project_id": "auto_edit_" + "a" * 32,
        "source": {"source_id": "source_" + "b" * 32 + "_0", "path": str(source), "file_name": source.name},
    }
    # A no-audio result proves probe/copy/proxy/scenes/keyframes/result completed;
    # only then does the existing guard correctly refuse a model call.
    with pytest.raises(ValueError, match="原素材语音转写未完成"):
        auto_edit_worker.analyze_and_plan_with_gateway(project, root=root)
    results = list((root / "analysis").rglob("result.json"))
    assert len(results) == 1
    result = json.loads(results[0].read_text(encoding="utf-8"))
    assert result["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert Path(result["artifacts"]["proxy_path"]).is_file()
    assert result["shots"] and Path(result["shots"][0]["keyframe_path"]).is_file()
    assert len(str(results[0])) < 260
