"""Real FFmpeg reuse regression; only ASR inference is a deterministic fixture."""
from __future__ import annotations

import hashlib
import json
import math
import struct
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from content_factory_api import auto_edit_worker, speech_captions, subtitle_editor
from content_factory_api.auto_edit_store import AutoEditProjectStore
from content_factory_media.tools import find_tool


def _run(*args: str) -> bytes:
    return subprocess.run(list(args), check=True, capture_output=True, timeout=120).stdout


@pytest.mark.parametrize('stage', ['clean', 'final'])
def test_duration_drift_cannot_register_and_keeps_paid_plan_for_local_retry(tmp_path, monkeypatch, stage):
    monkeypatch.setenv('CONTENT_FACTORY_S7_DATA_DIR', str(tmp_path / 's7'))
    source = tmp_path / 'source.mp4'
    _run(find_tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=96x160:r=25:d=6',
         '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=6',
         '-c:v', 'libx264', '-c:a', 'aac', str(source))
    store = AutoEditProjectStore(tmp_path / 'projects.sqlite3')
    project = store.create_project(title='禁止登记异常时长', source={
        'source_id': 'source_01', 'file_name': source.name, 'path': str(source),
        'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'duration_ms': 6000,
    }, settings={'target_count': 1, 'duration_min_ms': 5000, 'duration_max_ms': 7000,
                 'subtitle_font_size': 68, 'keyword_color': '#FFD400', 'keyword_scale': 1.3,
                 'top_title_enabled': False})
    real_probe = auto_edit_worker.probe_media
    target_name = 'one.clean.mp4' if stage == 'clean' else 'one.mp4'
    def bad_duration(path):
        info = real_probe(path)
        return replace(info, duration_ms=info.duration_ms + 1000) if path.name == target_name else info
    monkeypatch.setattr(auto_edit_worker, 'probe_media', bad_duration)
    def recognize(*args, **kwargs):
        assert stage == 'final', '底片时长异常时必须先停止，不得继续 ASR'
        return {'words': [{'text': '原声', 'start_ms': 100, 'end_ms': 500, 'probability': .99}]}
    monkeypatch.setattr(speech_captions, 'recognize', recognize)
    monkeypatch.setattr(auto_edit_worker, 'import_batch', lambda *a, **k: pytest.fail('异常成片不得登记'))
    clips = [{'start_ms': 0, 'end_ms': 3000}] * 2
    store.enqueue(project['project_id'], expected_revision=project['revision'])
    result = auto_edit_worker.process_one(store, available_skills=[{
        'skill_id': 'skill_a', 'revision': 1, 'status': 'approved', 'reuse_mode': 'reuse',
        'name': '本地模拟', 'mechanism': '重复原声',
    }], worker_id='drift-test', root=tmp_path / 'render', planner=lambda p: (
        'skill_a', '模拟方案', '本地回归', [{'candidate_id': 'one', 'title': '一条', 'clips': clips}],
    ))
    assert result['status'] == 'failed' and '成片实际时长' in result['error']
    assert not result.get('registration_checkpoint') and not result.get('output_batch_id')
    assert result['plan'][0]['clips'] == clips
    retry = store.enqueue(result['project_id'], expected_revision=result['revision'])
    assert retry['status'] == 'render_pending' and retry['plan'] == result['plan']


@pytest.mark.parametrize(
    ("ranges", "samples"),
    [
        ([(0, 2000), (4000, 6000), (0, 2000)], [(1, "red"), (3, "blue"), (5, "red")]),
        ([(0, 3000), (2000, 5000)], [(1, "red"), (3.5, "red"), (5.5, "blue")]),
        ([(4000, 7000), (0, 3000)], [(1, "blue"), (4, "red")]),
        ([(0, 1333)] * 30, [(0.5, "red"), (20, "red"), (39.5, "red")]),
    ],
    ids=["a_b_a_repeated_range", "partially_overlapping_source_ranges", "reverse_source_order", "thirty_fractional_repeats"],
)
def test_reused_ranges_render_in_requested_order_with_audio_and_caption_inheritance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ranges, samples,
) -> None:
    ffmpeg = find_tool("ffmpeg")
    ffprobe = find_tool("ffprobe")
    monkeypatch.setenv("CONTENT_FACTORY_S7_DATA_DIR", str(tmp_path / "s7"))
    source = tmp_path / "red_then_blue.mp4"
    _run(ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=180x320:r=25:d=4",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
         "-f", "lavfi", "-i", "color=c=blue:s=180x320:r=25:d=4",
         "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=4",
         "-filter_complex", "[0:v][1:a][2:v][3:a]concat=n=2:v=1:a=1[v][a]",
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-c:a", "aac", str(source))
    expected_seconds = sum(end - start for start, end in ranges) / 1000
    settings = {"target_count": 1, "duration_min_ms": 5000, "duration_max_ms": 40000,
                "subtitle_font_size": 68, "keyword_color": "#FFD400", "keyword_scale": 1.3,
                "top_title_enabled": False}
    store = AutoEditProjectStore(tmp_path / "projects.sqlite3")
    project = store.create_project(title="重复片段真实渲染", source={
        "source_id": "source_01", "file_name": source.name, "path": str(source),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "duration_ms": 8000,
    }, settings=settings)
    clips = [{"start_ms": start, "end_ms": end} for start, end in ranges]
    # The recognition stub is on the final concatenated audio timeline. It is
    # not evidence of real acoustic recognition or forced-alignment accuracy.
    acoustic_words = [{"text": "红" if color == "red" else "蓝",
                       "start_ms": round(time * 1000), "end_ms": round(time * 1000) + 200,
                       "probability": .99} for time, color in samples]
    recognized = []

    def recognize(clean: Path, workdir: Path, **kwargs):
        assert clean.is_file()
        probe = json.loads(_run(ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(clean)))
        assert float(probe["format"]["duration"]) == pytest.approx(expected_seconds, abs=.1)
        recognized.append(clean)
        return {"engine": "synthetic-final-timeline-fixture", "words": acoustic_words}

    monkeypatch.setattr(speech_captions, "recognize", recognize)
    store.enqueue(project["project_id"], expected_revision=project["revision"])
    result = auto_edit_worker.process_one(store, available_skills=[{
        "skill_id": "skill_a", "revision": 1, "status": "approved", "reuse_mode": "reuse",
        "name": "合成测试方法", "mechanism": "按指定顺序重用区间",
    }], worker_id="reuse-render", root=tmp_path / "render", planner=lambda project: (
        "skill_a", "合成选段", "仅用于回归验收", [{
            "candidate_id": "reuse_result", "title": "重复画面与原声", "source_id": "source_01",
            "clips": clips, "subtitle_segments": [],
        }],
    ))
    assert result["status"] == "review", result.get("error")
    assert result["progress"] == 100
    assert len(recognized) == 1
    manifest_path = next((tmp_path / "render").rglob("manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["candidates"][0]["clips"] == clips
    assert result["plan"][0]["clips"] == clips
    output = manifest_path.parent / "reuse_result.mp4"
    probe = json.loads(_run(ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(output)))
    assert float(probe["format"]["duration"]) == pytest.approx(expected_seconds, abs=.1)
    # Decode raw PCM too: individually encoded AAC packets must not accumulate
    # one priming/padding window per clip despite a plausible container duration.
    decoded_pcm = _run(ffmpeg, "-v", "error", "-i", str(output), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-")
    assert len(decoded_pcm) / 2 / 8000 == pytest.approx(expected_seconds, abs=.1)
    assert {stream["codec_type"] for stream in probe["streams"]} == {"video", "audio"}
    _run(ffmpeg, "-v", "error", "-xerror", "-i", str(output), "-f", "null", "-")
    for time, color in samples:
        # A top-left crop excludes burnt captions from the color check.
        pixel = _run(ffmpeg, "-v", "error", "-ss", str(time), "-i", str(output),
                     "-vf", "crop=40:40:0:0,scale=1:1", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-")
        channel = 0 if color == "red" else 2
        other = 2 if color == "red" else 0
        assert pixel[channel] > pixel[other] + 150, (time, color, pixel)
        audio = _run(ffmpeg, "-v", "error", "-ss", str(time), "-i", str(output),
                     "-t", "0.1", "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-")
        wave = struct.unpack("<" + "h" * (len(audio) // 2), audio)
        def energy(frequency):
            real = sum(value * math.cos(2 * math.pi * frequency * index / 8000) for index, value in enumerate(wave))
            imaginary = sum(value * math.sin(2 * math.pi * frequency * index / 8000) for index, value in enumerate(wave))
            return real * real + imaginary * imaginary
        expected, other_frequency = (440, 880) if color == "red" else (880, 440)
        assert energy(expected) > 100 * energy(other_frequency), (time, color)
    captions = json.loads(output.with_suffix(".captions.json").read_text(encoding="utf-8"))
    assert [word for cue in captions["cues"] for word in cue["words"]] == acoustic_words
    assert all(speech_captions.alignment_valid(cue) for cue in captions["cues"])
    events = speech_captions.caption_events(captions["cues"], captions["mode"])
    assert all(left["end_ms"] <= right["start_ms"] for left, right in zip(events, events[1:]))
    inherited = subtitle_editor.get_draft(manifest["id"], "reuse_result")
    assert inherited["document"] == captions
    assert inherited["revision"] == 1
    assert AutoEditProjectStore(store.database_path).get_project(project["project_id"])["plan"][0]["clips"] == clips
    assert hashlib.sha256(source.read_bytes()).hexdigest() == project["source"]["sha256"]


@pytest.mark.parametrize(
    ("ranges", "expected"),
    [
        ([(0, 2000), (4000, 6000), (0, 2000)],
         [(500, 1500, "红"), (2500, 3500, "蓝"), (4500, 5500, "红")]),
        ([(0, 3000), (2000, 5000)],
         [(500, 1500, "红"), (2500, 3000, "橙"), (3500, 4500, "橙"), (5500, 6000, "蓝")]),
        ([(4000, 7000), (0, 3000)],
         [(500, 1500, "蓝"), (2500, 3000, "紫"), (3500, 4500, "红"), (5500, 6000, "橙")]),
    ],
    ids=["repeat_maps_each_occurrence", "overlap_maps_both_occurrences", "reverse_maps_output_order"],
)
def test_source_transcript_maps_each_reused_occurrence_to_separate_output_time(ranges, expected):
    transcript = [
        {"start_ms": start, "end_ms": end, "text": text}
        for start, end, text in [(500, 1500, "红"), (2500, 3500, "橙"), (4500, 5500, "蓝"), (6500, 7500, "紫")]
    ]
    candidate = {"clips": [{"start_ms": start, "end_ms": end} for start, end in ranges]}
    mapped = auto_edit_worker._subtitle_segments(candidate, transcript)
    assert [(cue["start_ms"], cue["end_ms"], cue["text"]) for cue in mapped] == expected
    assert all(left["end_ms"] <= right["start_ms"] for left, right in zip(mapped, mapped[1:]))
