from __future__ import annotations

from array import array
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import wave
import zipfile

import pytest

from content_factory_api.s6_matching import build_shooting_task
from content_factory_api.s6_store import MaterialStore
from content_factory_api.s7_projects import build_edit_project
from content_factory_api.s7_queue import RenderQueue
from content_factory_api.s7_renderer import (
    _concat,
    _mux_primary_audio,
    _render_clip,
    _render_continuous_primary_audio,
    render_project,
)
from content_factory_api.s7_store import EditStore
from content_factory_media.pipeline import MediaPipeline
from content_factory_media.tools import find_tool, probe_media, run_command, write_json_atomic

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PROJECT_ROOT / "packages" / "contracts" / "fixtures"
SRT_TIMESTAMP = re.compile(r"(?P<hours>\d{2,}):(?P<minutes>[0-5]\d):(?P<seconds>[0-5]\d),(?P<millis>\d{3})")
REQUIRED_HANDOFF_MEMBERS = {
    "manifest.json",
    "timeline.json",
    "subtitles.srt",
    "README_剪映实验交接.txt",
}


def _json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _srt_milliseconds(timestamp: str) -> int:
    match = SRT_TIMESTAMP.fullmatch(timestamp)
    assert match is not None, f"无效 SRT 时间码: {timestamp}"
    parts = {name: int(value) for name, value in match.groupdict().items()}
    return (
        parts["hours"] * 3_600_000
        + parts["minutes"] * 60_000
        + parts["seconds"] * 1000
        + parts["millis"]
    )


def _parse_srt(path: Path) -> list[tuple[int, int, str]]:
    blocks = re.split(r"\r?\n\r?\n", path.read_text(encoding="utf-8-sig").strip())
    parsed: list[tuple[int, int, str]] = []
    for expected_index, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        assert len(lines) >= 3, f"SRT 第 {expected_index} 条结构不完整"
        assert lines[0] == str(expected_index), "SRT 序号必须从 1 连续递增"
        start_text, separator, end_text = lines[1].partition(" --> ")
        assert separator, f"SRT 第 {expected_index} 条缺少标准时间范围分隔符"
        start_ms = _srt_milliseconds(start_text)
        end_ms = _srt_milliseconds(end_text)
        assert 0 <= start_ms < end_ms, f"SRT 第 {expected_index} 条时间范围无效"
        subtitle = "\n".join(lines[2:]).strip()
        assert subtitle, f"SRT 第 {expected_index} 条字幕为空"
        parsed.append((start_ms, end_ms, subtitle))
    return parsed


@pytest.mark.skipif(os.environ.get("CONTENT_FACTORY_S7_INTEGRATION") != "1", reason="仅在 S7 总验收运行真实 FFmpeg 渲染")
def test_actual_ffmpeg_detail_overlay_keeps_primary_audio_continuous(tmp_path: Path) -> None:
    ffmpeg = find_tool("ffmpeg")
    primary = tmp_path / "primary.mp4"
    detail = tmp_path / "detail.mp4"
    output = tmp_path / "overlay-output.mp4"
    for color, frequency, destination in (("red", 440, primary), ("blue", 880, detail)):
        run_command([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=" + color + ":s=180x320:r=30:d=1.2",
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=1.2",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(destination),
        ], timeout=60)

    clip = {
        "source_start_ms": 0, "source_end_ms": 1000,
        "timeline_start_ms": 0, "timeline_end_ms": 1000,
        "speed": 1.0, "crop_mode": "fill", "focus_x": 0.5, "focus_y": 0.5,
        "transition": "cut", "transition_ms": 0,
        "has_source_audio": True, "original_volume": 1.0,
        "visual_overlay": {
            "material_id": "material_demo_001", "material_clip_id": "clip_detail_001",
            "source_start_ms": 0, "source_end_ms": 1000, "speed": 1.0,
            "detail_tag": "面料纹理", "instruction": "覆盖同款细节", "audio_mode": "retain_primary",
        },
    }
    settings = deepcopy(_json("edit-project.valid.json")["settings"])
    assert settings["reduce_noise"] is True
    assert settings["normalize_voice"] is True

    _render_clip(ffmpeg, primary, clip, settings, output, overlay_source=detail)

    frame = tmp_path / "frame.rgb"
    run_command([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.5", "-i", str(output),
        "-frames:v", "1", "-vf", "scale=1:1,format=rgb24", "-f", "rawvideo", str(frame),
    ], timeout=60)
    red, green, blue = frame.read_bytes()[:3]
    assert blue > 180 and blue > red * 3 and blue > green * 3, (red, green, blue)

    audio = tmp_path / "audio.wav"
    run_command([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(output),
        "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(audio),
    ], timeout=60)
    with wave.open(str(audio), "rb") as stream:
        samples = array("h", stream.readframes(stream.getnframes()))
        sample_rate = stream.getframerate()
    start, end = sample_rate // 10, min(len(samples), sample_rate * 9 // 10)
    stable = samples[start:end]
    positive_crossings = sum(left <= 0 < right for left, right in zip(stable, stable[1:], strict=False))
    measured_hz = positive_crossings / (len(stable) / sample_rate)
    window = sample_rate // 10
    rms_windows = [
        math.sqrt(sum(value * value for value in stable[index:index + window]) / len(stable[index:index + window]))
        for index in range(0, len(stable) - window + 1, window)
    ]
    assert 420 <= measured_hz <= 460, measured_hz
    assert rms_windows and min(rms_windows) > 300, rms_windows


@pytest.mark.skipif(os.environ.get("CONTENT_FACTORY_S7_INTEGRATION") != "1", reason="仅在 S7 总验收运行真实 FFmpeg 渲染")
def test_actual_ffmpeg_contiguous_host_take_segments_do_not_restart_audio(tmp_path: Path) -> None:
    ffmpeg = find_tool("ffmpeg")
    source = tmp_path / "continuous-host-take.mp4"
    detail = tmp_path / "detail-overlay.mp4"
    run_command([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=yellow:s=180x320:r=30:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=1",
        "-filter_complex", "[1:a][2:a]concat=n=2:v=0:a=1[a]",
        "-map", "0:v:0", "-map", "[a]", "-shortest",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
    ], timeout=60)
    run_command([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=blue:s=180x320:r=30:d=1.2",
        "-f", "lavfi", "-i", "sine=frequency=1760:sample_rate=48000:duration=1.2",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(detail),
    ], timeout=60)
    settings = deepcopy(_json("edit-project.valid.json")["settings"])
    assert settings["reduce_noise"] is True
    assert settings["normalize_voice"] is True
    base_clip = {
        "timeline_start_ms": 0, "timeline_end_ms": 1000,
        "speed": 1.0, "crop_mode": "fill", "focus_x": 0.5, "focus_y": 0.5,
        "transition": "cut", "transition_ms": 0, "has_source_audio": True, "original_volume": 1.0,
    }
    segments = []
    clips = []
    for index, (start_ms, end_ms) in enumerate(((0, 1000), (1000, 2000)), start=1):
        segment = tmp_path / f"segment-{index}.mp4"
        clip = {
            **base_clip, "source_start_ms": start_ms, "source_end_ms": end_ms,
            "timeline_start_ms": start_ms, "timeline_end_ms": end_ms,
            "continuous_take": True,
        }
        overlay_source = None
        if index == 2:
            clip["visual_overlay"] = {
                "material_id": "material_detail_001", "material_clip_id": "clip_detail_001",
                "source_start_ms": 0, "source_end_ms": 1000, "speed": 1.0,
                "detail_tag": "面料纹理", "instruction": "只覆盖画面",
                "audio_mode": "retain_primary",
            }
            overlay_source = detail
        _render_clip(
            ffmpeg, source, clip, settings, segment,
            overlay_source=overlay_source, include_primary_audio=False,
        )
        segments.append(segment)
        clips.append(clip)
    concatenated_video = tmp_path / "continuous-video.mp4"
    _concat(ffmpeg, segments, tmp_path, concatenated_video)
    primary_audio = tmp_path / "continuous-primary.m4a"
    _render_continuous_primary_audio(
        ffmpeg, clips, [source, source], settings, primary_audio,
    )
    output = tmp_path / "continuous-output.mp4"
    _mux_primary_audio(ffmpeg, concatenated_video, primary_audio, 2000, output)

    frame = tmp_path / "overlay-frame.rgb"
    run_command([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", "1.5", "-i", str(output),
        "-frames:v", "1", "-vf", "scale=1:1,format=rgb24", "-f", "rawvideo", str(frame),
    ], timeout=60)
    red, green, blue = frame.read_bytes()[:3]
    assert blue > 180 and blue > red * 3 and blue > green * 3, (red, green, blue)

    audio = tmp_path / "continuous.wav"
    run_command([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(output),
        "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(audio),
    ], timeout=60)
    with wave.open(str(audio), "rb") as stream:
        samples = array("h", stream.readframes(stream.getnframes()))
        sample_rate = stream.getframerate()

    def measured_frequency(start_seconds: float, end_seconds: float) -> float:
        sample_window = samples[round(start_seconds * sample_rate):round(end_seconds * sample_rate)]
        crossings = sum(
            left <= 0 < right for left, right in zip(sample_window, sample_window[1:], strict=False)
        )
        return crossings / (len(sample_window) / sample_rate)

    first_hz = measured_frequency(0.2, 0.8)
    second_hz = measured_frequency(1.2, 1.8)
    boundary = samples[round(0.9 * sample_rate):round(1.1 * sample_rate)]
    boundary_rms = math.sqrt(sum(value * value for value in boundary) / len(boundary))
    boundary_window = sample_rate // 100
    boundary_floor = min(
        math.sqrt(
            sum(value * value for value in boundary[index:index + boundary_window])
            / boundary_window
        )
        for index in range(0, len(boundary) - boundary_window + 1, boundary_window)
    )
    assert 420 <= first_hz <= 460, first_hz
    # 880 Hz proves the second half continues from the host source. The detail
    # overlay contains 1760 Hz and must never take ownership of the soundtrack.
    assert 850 <= second_hz <= 910, second_hz
    assert boundary_rms > 300, boundary_rms
    assert boundary_floor > 500, boundary_floor


@pytest.mark.skipif(os.environ.get("CONTENT_FACTORY_S7_INTEGRATION") != "1", reason="仅在 S7 总验收运行真实 FFmpeg 渲染")
def test_actual_ffmpeg_delivers_vertical_editable_review_package(tmp_path: Path) -> None:
    fixture = PROJECT_ROOT / "output" / "s2" / "fixture" / "s2-fixture.mp4"
    assert fixture.is_file(), "请先运行 scripts/create-s2-fixture.ps1"
    media_root = tmp_path / "s6" / "media"
    result_path = MediaPipeline(asr_model_path=PROJECT_ROOT / ".models" / "ggml-base.bin").process(
        fixture, media_root, "media_77777777777777777777777777777777", fixture_data=True, original_name="S7渲染验收.mp4",
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    duration = result["media"]["duration_ms"]
    managed_original = Path(result["source"]["managed_original_path"]).resolve()
    assert managed_original.is_relative_to(media_root.resolve())
    assert managed_original.is_file()
    assert managed_original.stat().st_size == result["source"]["size_bytes"]

    material = _json("material.valid.json")
    material["file"].update({
        "original_name": result["source"]["original_name"], "sha256": result["source"]["sha256"],
        "size_bytes": result["source"]["size_bytes"], "duration_ms": duration,
        "width": result["media"]["width"], "height": result["media"]["height"], "fps": result["media"]["fps"],
        "has_audio": result["media"]["has_audio"],
    })
    material["processing"].update({
        "media_task_id": result["task_id"], "asr_status": result["asr"]["status"],
        "recognition_status": "not_configured", "provider": None, "model": None, "recognized_at": None,
    })
    template = material["clips"][0]
    boundaries = [0, duration // 4, duration // 2, (duration * 3) // 4, duration]
    clips = []
    for index in range(3):
        clip = deepcopy(template)
        clip.update({
            "id": f"clip_demo_00{index + 1}", "source_shot_id": f"shot_s7_{index + 1:03d}", "order": index + 1,
            "start_ms": boundaries[index], "end_ms": boundaries[index + 1],
            "keyframe_ref": f"keyframe_s7_{index + 1:03d}", "purpose_tags": ["broll"],
        })
        clips.append(clip)
    detail_clip = deepcopy(template)
    detail_clip.update({
        "id": "clip_detail_render_001", "source_shot_id": "shot_detail_render_001", "order": 4,
        "start_ms": boundaries[3], "end_ms": boundaries[4],
        "keyframe_ref": "keyframe_detail_render_001", "transcript": "同款面料细节",
        "purpose_tags": ["detail"], "visual_tags": ["面料纹理"], "garment_views": ["detail"],
        "action_tags": ["展示面料"], "shot_size": "特写", "people_count": 0,
    })
    clips.append(detail_clip)
    material["clips"] = clips
    material["capture_role"] = "host_take"

    material_store = MaterialStore(tmp_path / "s6" / "materials.sqlite3")
    material_store.create(material, media_result_path=result_path, actor="工程验收")
    stored_material = material_store.get(material["material_id"])
    full_take = next(item for item in stored_material["clips"] if item.get("capture_scope") == "full_take")
    script = _json("script.valid.json")
    selected_version = script["versions"][0]
    first_shot = deepcopy(selected_version["shots"][0])
    first_shot["end_ms"] = 3000
    second_shot = deepcopy(first_shot)
    second_shot.update({
        "id": "script_shot_demo_a2", "order": 2, "start_ms": 3000, "end_ms": 6000,
        "voiceover": "主播继续说第二段，原声不要重头。", "subtitle": "主播原声连续",
        "sound_effect": "无",
        "detail_overlay": {"mode": "none", "detail_tag": None, "instruction": "保留主播画面"},
    })
    selected_version["shots"] = [first_shot, second_shot]
    script["shooting_order"][0]["shot_ids"].insert(1, second_shot["id"])
    script["material_checklist"].insert(1, {
        "shot_id": second_shot["id"], "status": "required", "notes": "同一条主播长镜头继续录制",
    })
    for shot in selected_version["shots"]:
        material_store.confirm_match(
            script_id=script["script_id"], script_shot_id=shot["id"],
            suggestion={
                "material_id": material["material_id"], "clip_id": full_take["id"],
                "score": 90, "repeat_risk": "low", "reason": "实际渲染验收",
            }, actor="工程验收", allow_reuse=True,
        )
    shooting = build_shooting_task(material_store, script)
    detail_requirement = next(
        item for item in shooting["requirements"]
        if item.get("requirement_kind") == "detail_overlay"
        and item.get("source_script_shot_id") == script["versions"][0]["shots"][0]["id"]
    )
    material_store.confirm_match(
        script_id=script["script_id"], script_shot_id=detail_requirement["script_shot_id"],
        suggestion={
            "material_id": material["material_id"], "clip_id": detail_clip["id"],
            "score": 95, "repeat_risk": "low", "reason": "同款细节只覆盖画面",
        }, actor="工程验收", allow_reuse=True,
    )
    shooting = build_shooting_task(material_store, script)
    project = build_edit_project(script, shooting, material_store)
    assert len(project["variants"][0]["clips"]) == 2
    assert all(clip["continuous_take"] is True for clip in project["variants"][0]["clips"])
    assert project["variants"][0]["clips"][0]["visual_overlay"]["audio_mode"] == "retain_primary"
    assert project["settings"]["reduce_noise"] is True
    assert project["settings"]["normalize_voice"] is True
    edit_store = EditStore(tmp_path / "s7")
    edit_store.create_project(project, actor="工程验收")
    workspace = tmp_path / "s7" / "tasks" / "acceptance"
    snapshot = workspace / "project.json"
    write_json_atomic(snapshot, project)
    queue = RenderQueue(tmp_path / "s7" / "queue.sqlite3", retry_base_seconds=0.01)
    task = queue.enqueue(
        project, project["variants"][0]["id"], snapshot_path=snapshot, workspace_path=workspace, max_attempts=1,
    )

    completed = queue.run_pending(
        lambda record, progress: render_project(
            record, record.snapshot(), material_store=material_store, edit_store=edit_store,
            s6_media_root=media_root, progress=progress,
        ), max_workers=2,
    )[0]

    assert completed.status == "completed", completed.error
    output = edit_store.get_output(completed.output_id or "")
    video_path, _, _ = edit_store.resource(output["output_id"], output["resources"]["video_ref"])
    info = probe_media(video_path)
    assert (info.width, info.height) == (1080, 1920)
    assert 29 <= info.fps <= 31 and info.video_codec == "h264" and info.audio_codec == "aac" and info.has_audio
    assert output["media"]["faststart"] is True and output["status"] == "video_review"
    resource_paths = {
        field: edit_store.resource(output["output_id"], output["resources"][field])[0]
        for field in ("video_ref", "clean_video_ref", "subtitle_ref", "project_ref", "jianying_package_ref")
    }
    assert all(path.is_file() for path in resource_paths.values())
    assert output["resources"]["jianying_experimental"] is True

    timeline = json.loads(resource_paths["project_ref"].read_text(encoding="utf-8"))
    assert timeline == project
    assert (timeline["project_id"], timeline["revision"]) == (output["project_id"], output["project_revision"])
    selected_variant = next(item for item in timeline["variants"] if item["id"] == output["variant_id"])
    expected_subtitles = [
        (
            clip["timeline_start_ms"],
            clip["timeline_end_ms"],
            str(clip["subtitle"]).replace("\r", " ").replace("\n", " ").strip(),
        )
        for clip in selected_variant["clips"]
        if str(clip.get("subtitle", "")).strip()
    ]
    parsed_subtitles = _parse_srt(resource_paths["subtitle_ref"])
    assert parsed_subtitles == expected_subtitles
    assert all(end_ms <= selected_variant["duration_ms"] for _, end_ms, _ in parsed_subtitles)
    assert all(
        previous[1] <= following[0]
        for previous, following in zip(parsed_subtitles, parsed_subtitles[1:], strict=False)
    )

    assert zipfile.is_zipfile(resource_paths["jianying_package_ref"])
    with zipfile.ZipFile(resource_paths["jianying_package_ref"]) as archive:
        assert archive.testzip() is None, "剪映交接包存在 CRC 校验失败的成员"
        assert REQUIRED_HANDOFF_MEMBERS <= set(archive.namelist())
        package_manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        assert set(package_manifest["files"]) == REQUIRED_HANDOFF_MEMBERS - {"manifest.json"}
        assert (
            package_manifest["project_id"],
            package_manifest["project_revision"],
            package_manifest["variant_id"],
        ) == (output["project_id"], output["project_revision"], output["variant_id"])
        assert json.loads(archive.read("timeline.json").decode("utf-8")) == timeline
        assert archive.read("subtitles.srt") == resource_paths["subtitle_ref"].read_bytes()

    assert str(tmp_path) not in json.dumps(output, ensure_ascii=False)
