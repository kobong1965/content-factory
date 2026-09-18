"""Local FFmpeg renderer for editable S7 vertical-video projects."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from content_factory_contracts import validate_or_raise
from content_factory_media.tools import (
    MediaToolError, find_tool, probe_media, run_command, sha256_file, write_json_atomic,
)

from .s6_materials import load_media_result_for_material
from .s6_store import MaterialStore
from .s7_queue import RenderTaskRecord
from .s7_store import EditStore, now_iso
from .subtitle_design import caption_text


def _seconds(milliseconds: int | float) -> str:
    return f"{milliseconds / 1000:.3f}"


def _safe_label(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip(" ._")
    return cleaned[:80] or "成片"


def _render_source_path(media_result: Mapping[str, Any], *, allowed_root: str | Path) -> Path:
    """Use the managed original for delivery; proxies are reserved for preview and analysis."""
    root = Path(allowed_root).resolve()
    candidate = media_result.get("source", {}).get("managed_original_path")
    if not isinstance(candidate, str) or not candidate:
        raise ValueError("素材缺少托管原片，无法生成高清成片")
    source = Path(candidate).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise ValueError("素材托管原片不存在或不在受控目录内")
    return source


def _srt_time(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _ass_time(milliseconds: int) -> str:
    centiseconds = round(milliseconds / 10)
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, centis = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}"


def _ass_color(hex_value: str) -> str:
    red, green, blue = hex_value[1:3], hex_value[3:5], hex_value[5:7]
    return f"&H00{blue}{green}{red}".upper()


def _subtitle_files(variant: Mapping[str, Any], settings: Mapping[str, Any], workspace: Path) -> tuple[Path, Path]:
    subtitle_clips = [item for item in variant["clips"] if str(item.get("subtitle", "")).strip()]
    srt_path = workspace / "subtitles.srt"
    srt_lines: list[str] = []
    for index, clip in enumerate(subtitle_clips, start=1):
        text = str(clip["subtitle"]).replace("\r", " ").replace("\n", " ").strip()
        srt_lines.extend([
            str(index), f"{_srt_time(clip['timeline_start_ms'])} --> {_srt_time(clip['timeline_end_ms'])}", text, "",
        ])
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8-sig")

    style = settings["subtitle_style"]
    ass_path = workspace / "subtitles.ass"
    header = "\n".join([
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{style['font_family']},{style['font_size']},{_ass_color(style['primary_color'])},&H000000FF,{_ass_color(style['outline_color'])},&H66000000,-1,0,0,0,100,100,0,0,1,4,1,2,70,70,{style['margin_v']},1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])
    events = []
    for clip in subtitle_clips:
        text = caption_text(str(clip["subtitle"]))
        events.append(
            f"Dialogue: 0,{_ass_time(clip['timeline_start_ms'])},{_ass_time(clip['timeline_end_ms'])},Default,,0,0,0,,{text}"
        )
    ass_path.write_text(header + "\n" + "\n".join(events) + "\n", encoding="utf-8-sig")
    return srt_path, ass_path


def _crop_filter(clip: Mapping[str, Any]) -> str:
    if clip["crop_mode"] == "fit":
        return "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
    focus_x = float(clip["focus_x"])
    focus_y = float(clip["focus_y"])
    return (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920:(iw-ow)*{focus_x:.3f}:(ih-oh)*{focus_y:.3f}"
    )


def _color_filter(preset: str) -> str:
    return {
        "natural": "eq=contrast=1.02:saturation=1.03",
        "bright": "eq=brightness=0.025:contrast=1.04:saturation=1.05",
        "warm": "eq=contrast=1.02:saturation=1.04,colorbalance=rs=0.035:bs=-0.025",
        "cool": "eq=contrast=1.02:saturation=1.03,colorbalance=rs=-0.025:bs=0.035",
    }[preset]


def _silence_bounds(log: str, duration_ms: int) -> tuple[int, int]:
    starts = [float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", log)]
    ends = [float(value) for value in re.findall(r"silence_end:\s*([0-9.]+)", log)]
    leading = min(800, round(ends[0] * 1000)) if starts and ends and starts[0] <= 0.08 else 0
    trailing = 0
    if starts:
        last_start = starts[-1]
        last_end = ends[-1] if len(ends) >= len(starts) else duration_ms / 1000
        if last_end >= duration_ms / 1000 - 0.08:
            trailing = min(800, max(0, duration_ms - round(last_start * 1000)))
    if duration_ms - leading - trailing < 300:
        return 0, 0
    return leading, trailing


def _trim_obvious_edge_silence(ffmpeg: str, source: Path, clip: Mapping[str, Any]) -> dict[str, Any]:
    if not clip["has_source_audio"]:
        return dict(clip)
    duration_ms = int(clip["source_end_ms"]) - int(clip["source_start_ms"])
    completed = run_command([
        ffmpeg, "-hide_banner", "-nostats", "-ss", _seconds(clip["source_start_ms"]), "-t", _seconds(duration_ms),
        "-i", str(source), "-map", "0:a:0", "-af", "silencedetect=noise=-45dB:d=0.25", "-f", "null", os.devnull,
    ], timeout=60)
    leading, trailing = _silence_bounds(completed.stderr, duration_ms)
    return {
        **clip, "source_start_ms": int(clip["source_start_ms"]) + leading,
        "source_end_ms": int(clip["source_end_ms"]) - trailing,
    }


def _render_clip(
    ffmpeg: str, source: Path, clip: Mapping[str, Any], settings: Mapping[str, Any], destination: Path,
    *, overlay_source: Path | None = None, include_primary_audio: bool = True,
) -> None:
    target_ms = int(clip["timeline_end_ms"]) - int(clip["timeline_start_ms"])
    source_ms = int(clip["source_end_ms"]) - int(clip["source_start_ms"])
    primary_speed = float(clip["speed"])
    overlay = clip.get("visual_overlay")
    if isinstance(overlay, Mapping) and overlay_source is None:
        raise ValueError("剪辑片段声明了细节覆盖，但缺少对应的受控素材原片")
    visual_speed = float(overlay["speed"]) if isinstance(overlay, Mapping) else primary_speed
    filters = [
        _crop_filter(clip), _color_filter(str(settings["color_preset"])), "setsar=1", "format=yuv420p",
        f"setpts=(PTS-STARTPTS)/{visual_speed:.6f}", f"tpad=stop_mode=clone:stop_duration={target_ms / 1000:.3f}",
        f"trim=duration={target_ms / 1000:.3f}", "setpts=PTS-STARTPTS", "fps=30", "format=yuv420p",
    ]
    transition_ms = int(clip["transition_ms"])
    if clip["transition"] == "fade" and transition_ms > 0 and target_ms > transition_ms * 2:
        duration = transition_ms / 1000
        filters.extend([f"fade=t=in:st=0:d={duration:.3f}", f"fade=t=out:st={(target_ms - transition_ms) / 1000:.3f}:d={duration:.3f}"])
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", _seconds(clip["source_start_ms"]),
        "-t", _seconds(source_ms), "-i", str(source),
    ]
    video_map = "0:v:0"
    input_count = 1
    if isinstance(overlay, Mapping):
        overlay_ms = int(overlay["source_end_ms"]) - int(overlay["source_start_ms"])
        command.extend([
            "-ss", _seconds(overlay["source_start_ms"]), "-t", _seconds(overlay_ms),
            "-i", str(overlay_source),
        ])
        video_map = "1:v:0"
        input_count += 1
    volume = float(settings["original_volume"]) * float(clip["original_volume"])
    if clip["has_source_audio"] and include_primary_audio:
        audio_filters = [f"atempo={primary_speed:.6f}", f"volume={volume:.4f}"]
        if settings["reduce_noise"]:
            audio_filters.append("afftdn=nf=-25")
        if settings["normalize_voice"]:
            audio_filters.append("dynaudnorm=f=150:g=9")
        audio_filters.extend(["apad", f"atrim=duration={target_ms / 1000:.3f}", "asetpts=PTS-STARTPTS"])
        command.extend(["-map", video_map, "-map", "0:a:0", "-vf", ",".join(filters), "-af", ",".join(audio_filters)])
    else:
        command.extend([
            "-f", "lavfi", "-t", _seconds(target_ms), "-i", "anullsrc=r=48000:cl=stereo",
            "-map", video_map, "-map", f"{input_count}:a:0", "-vf", ",".join(filters),
        ])
    command.extend([
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-profile:v", "high", "-level", "4.1",
        "-r", "30", "-fps_mode", "cfr", "-g", "60", "-keyint_min", "60", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-t", _seconds(target_ms), "-movflags", "+faststart", str(destination),
    ])
    run_command(command, timeout=600)


def _concat(ffmpeg: str, segments: list[Path], workspace: Path, destination: Path) -> None:
    concat_path = workspace / "concat.txt"
    lines = []
    for path in segments:
        escaped = path.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run_command([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_path), "-c", "copy", "-movflags", "+faststart", str(destination),
    ], timeout=300)


def _render_continuous_primary_audio(
    ffmpeg: str, clips: list[Mapping[str, Any]], primary_sources: list[Path],
    settings: Mapping[str, Any], destination: Path,
) -> None:
    """Build one primary soundtrack and run stateful voice filters only once.

    Reusing a livestream take across several timeline clips must not create one
    AAC encoder or one noise/normalisation filter state per visual segment.  A
    single filter graph decodes every distinct primary source once, trims the
    requested ranges as PCM, concatenates them, then applies voice processing
    across the complete timeline.  Visual-overlay sources are deliberately not
    accepted here, so their audio can never replace the host soundtrack.
    """

    if not clips or len(clips) != len(primary_sources):
        raise ValueError("连续主音轨需要与剪辑片段一一对应的主素材")

    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    source_uses: dict[Path, list[int]] = {}
    for clip_index, (clip, source) in enumerate(zip(clips, primary_sources, strict=True)):
        if bool(clip["has_source_audio"]):
            source_uses.setdefault(source.resolve(), []).append(clip_index)

    source_input_indices: dict[Path, int] = {}
    for source in source_uses:
        source_input_indices[source] = len(source_input_indices)
        command.extend(["-i", str(source)])

    filters: list[str] = []
    clip_input_labels: dict[int, str] = {}
    for source_number, (source, clip_indices) in enumerate(source_uses.items()):
        input_index = source_input_indices[source]
        if len(clip_indices) == 1:
            clip_input_labels[clip_indices[0]] = f"{input_index}:a:0"
            continue
        split_labels = [f"primary_source_{source_number}_{branch}" for branch in range(len(clip_indices))]
        filters.append(
            f"[{input_index}:a:0]asplit={len(split_labels)}"
            + "".join(f"[{label}]" for label in split_labels)
        )
        for clip_index, label in zip(clip_indices, split_labels, strict=True):
            clip_input_labels[clip_index] = label

    clip_labels: list[str] = []
    total_duration_ms = 0
    for clip_index, clip in enumerate(clips):
        target_ms = int(clip["timeline_end_ms"]) - int(clip["timeline_start_ms"])
        if target_ms <= 0:
            raise ValueError("连续主音轨包含无效的时间范围")
        total_duration_ms += target_ms
        output_label = f"primary_clip_{clip_index}"
        clip_labels.append(output_label)
        if not bool(clip["has_source_audio"]):
            filters.append(
                f"anullsrc=r=48000:cl=stereo,atrim=duration={target_ms / 1000:.3f},"
                f"asetpts=PTS-STARTPTS[{output_label}]"
            )
            continue
        source_start = int(clip["source_start_ms"]) / 1000
        source_end = int(clip["source_end_ms"]) / 1000
        speed = float(clip["speed"])
        volume = float(settings["original_volume"]) * float(clip["original_volume"])
        filters.append(
            f"[{clip_input_labels[clip_index]}]"
            f"atrim=start={source_start:.3f}:end={source_end:.3f},asetpts=PTS-STARTPTS,"
            f"atempo={speed:.6f},volume={volume:.4f},apad,"
            f"atrim=duration={target_ms / 1000:.3f},asetpts=PTS-STARTPTS[{output_label}]"
        )

    joined_inputs = "".join(f"[{label}]" for label in clip_labels)
    filters.append(f"{joined_inputs}concat=n={len(clip_labels)}:v=0:a=1[primary_joined]")
    voice_filters: list[str] = []
    if settings["reduce_noise"]:
        voice_filters.append("afftdn=nf=-25")
    if settings["normalize_voice"]:
        voice_filters.append("dynaudnorm=f=150:g=9")
    voice_filters.extend([
        "apad", f"atrim=duration={total_duration_ms / 1000:.3f}", "asetpts=PTS-STARTPTS",
    ])
    filters.append(f"[primary_joined]{','.join(voice_filters)}[primary]")
    command.extend([
        "-filter_complex", ";".join(filters), "-map", "[primary]", "-vn",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-t", _seconds(total_duration_ms), "-movflags", "+faststart", str(destination),
    ])
    run_command(command, timeout=600)


def _mux_primary_audio(
    ffmpeg: str, video: Path, primary_audio: Path, duration_ms: int, destination: Path,
) -> None:
    """Replace temporary segment audio without re-encoding the continuous track."""

    run_command([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-i", str(primary_audio),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "copy",
        "-t", _seconds(duration_ms), "-movflags", "+faststart", str(destination),
    ], timeout=300)


def _mix_audio(
    ffmpeg: str, concatenated: Path, variant: Mapping[str, Any], settings: Mapping[str, Any],
    edit_store: EditStore, destination: Path,
) -> None:
    duration = int(variant["duration_ms"]) / 1000
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(concatenated)]
    labels = ["base"]
    filters = ["[0:a]anull[base]"]
    input_index = 1
    bgm_asset_id = settings.get("bgm_asset_id")
    if bgm_asset_id:
        asset = edit_store.get_audio(str(bgm_asset_id))
        if asset["kind"] != "bgm":
            raise ValueError("所选音频不是背景音乐")
        if bool(asset["fixture_data"]) != bool(variant["fixture_data"]):
            raise ValueError("工程样例音频和正式剪辑工程不能混用")
        command.extend(["-stream_loop", "-1", "-i", str(edit_store.audio_path(str(bgm_asset_id)))])
        filters.append(
            f"[{input_index}:a]volume={float(settings['bgm_volume']):.4f},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[bgm]"
        )
        labels.append("bgm")
        input_index += 1
    if settings["auto_sound_effects"]:
        for effect_index, clip in enumerate(variant["clips"]):
            effect = str(clip.get("sound_effect", "")).strip()
            if not effect or effect == "无":
                continue
            frequency = 720 + (effect_index % 3) * 120
            command.extend(["-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=0.07"])
            label = f"sfx{effect_index}"
            delay = int(clip["timeline_start_ms"])
            filters.append(f"[{input_index}:a]adelay={delay}:all=1,volume=0.045[{label}]")
            labels.append(label)
            input_index += 1
    if len(labels) == 1:
        run_command([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(concatenated), "-c", "copy",
            "-movflags", "+faststart", str(destination),
        ], timeout=300)
        return
    inputs = "".join(f"[{label}]" for label in labels)
    filters.append(f"{inputs}amix=inputs={len(labels)}:duration=first:normalize=0[mixed]")
    command.extend([
        "-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", "[mixed]", "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-t", f"{duration:.3f}",
        "-movflags", "+faststart", str(destination),
    ])
    run_command(command, timeout=300)


def _burn_subtitles(ffmpeg: str, clean_video: Path, ass_path: Path, enabled: bool, workspace: Path, destination: Path) -> None:
    if not enabled:
        shutil.copy2(clean_video, destination)
        return
    run_command([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(clean_video),
        "-vf", f"ass={ass_path.name}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-profile:v", "high", "-level", "4.1", "-r", "30", "-fps_mode", "cfr", "-g", "60", "-keyint_min", "60",
        "-c:a", "copy", "-movflags", "+faststart", str(destination),
    ], cwd=workspace, timeout=900)


def _faststart(path: Path) -> bool:
    with path.open("rb") as handle:
        head = handle.read(min(path.stat().st_size, 8 * 1024 * 1024))
    moov = head.find(b"moov")
    mdat = head.find(b"mdat")
    return moov >= 0 and mdat >= 0 and moov < mdat


def _ffmpeg_version(ffmpeg: str) -> str:
    completed = run_command([ffmpeg, "-version"], timeout=30)
    return completed.stdout.splitlines()[0][:500]


def _package_handoff(
    project: Mapping[str, Any], variant: Mapping[str, Any], srt_path: Path, project_path: Path,
    package_path: Path,
) -> None:
    manifest = {
        "schema_version": "1.0.0", "project_id": project["project_id"], "project_revision": project["revision"],
        "variant_id": variant["id"], "jianying_experimental": True,
        "files": ["timeline.json", "subtitles.srt", "README_剪映实验交接.txt"],
    }
    readme = (
        "这是剪映实验交接包，不是剪映原生草稿。\n"
        "稳定可编辑源是 timeline.json，字幕源是 subtitles.srt。\n"
        "剪映版本升级可能改变草稿结构，因此本包只用于人工交接和后续适配。\n"
    )
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(project_path, "timeline.json")
        archive.write(srt_path, "subtitles.srt")
        archive.writestr("README_剪映实验交接.txt", readme)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))


def render_project(
    task: RenderTaskRecord, project: Mapping[str, Any], *, material_store: MaterialStore,
    edit_store: EditStore, s6_media_root: str | Path, progress: Callable[[str, int], None],
) -> str:
    """Render and register one project variant, returning its output ID."""

    validate_or_raise("edit_project", project)
    if task.project_id != project["project_id"] or task.project_revision != project["revision"]:
        raise ValueError("渲染任务与剪辑工程快照不一致")
    variant = next((item for item in project["variants"] if item["id"] == task.variant_id), None)
    if variant is None:
        raise ValueError("找不到要渲染的剪辑版本")
    existing_output = next(
        (
            item for item in edit_store.list_outputs(project_id=task.project_id, include_fixtures=True)
            if item.get("render", {}).get("task_id") == task.task_id
        ),
        None,
    )
    if existing_output is not None:
        try:
            if (
                existing_output["project_revision"] != task.project_revision
                or existing_output["variant_id"] != task.variant_id
                or bool(existing_output["fixture_data"]) != bool(task.fixture_data)
            ):
                raise ValueError("已登记成片与渲染任务不一致")
            for key, resource_ref in existing_output["resources"].items():
                if key != "jianying_experimental":
                    edit_store.resource(existing_output["output_id"], resource_ref)
        except Exception:
            # Older builds could mark the queue failed while leaving this
            # unreviewed record behind.  Archive it before a manual retry so
            # the same task id can produce one new, valid output.
            edit_store.discard_incomplete_output(
                existing_output["output_id"], reason="重试前发现历史成片血缘或资源不完整",
            )
        else:
            return str(existing_output["output_id"])
    # Keep fixture lineage explicit through rendering and audio selection.
    variant = {**variant, "fixture_data": bool(project["fixture_data"])}
    ffmpeg = find_tool("ffmpeg")
    find_tool("ffprobe")
    workspace = Path(task.workspace_path).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    output_id = f"render_output_{uuid4().hex}"
    output_directory = (edit_store.output_root / output_id).resolve()
    if not output_directory.is_relative_to(edit_store.output_root):
        raise ValueError("渲染输出目录不安全")
    output_directory.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    try:
        progress("prepare", 5)
        segments: list[Path] = []
        primary_sources: list[Path] = []
        primary_audio_clips: list[Mapping[str, Any]] = []
        source_cache: dict[str, Path] = {}
        continuous_take = any(bool(clip.get("continuous_take")) for clip in variant["clips"])

        def source_for(material_id: str) -> Path:
            if material_id not in source_cache:
                material = material_store.get(material_id)
                if (
                    material["product_id"] != project["product_id"]
                    or bool(material["fixture_data"]) != bool(project["fixture_data"])
                ):
                    raise ValueError("剪辑片段与工程商品或数据环境不一致")
                media_result = load_media_result_for_material(
                    material_store.media_result_path(material_id), allowed_root=s6_media_root,
                )
                source_cache[material_id] = _render_source_path(media_result, allowed_root=s6_media_root)
            return source_cache[material_id]

        for index, clip in enumerate(variant["clips"], start=1):
            material_id = str(clip["material_id"])
            primary_source = source_for(material_id)
            primary_sources.append(primary_source)
            overlay = clip.get("visual_overlay")
            overlay_source = None
            if isinstance(overlay, Mapping):
                overlay_source = source_for(str(overlay["material_id"]))
            segment = workspace / f"segment-{index:03d}.mp4"
            render_clip = dict(clip) if clip.get("continuous_take") else _trim_obvious_edge_silence(
                ffmpeg, primary_source, clip,
            )
            primary_audio_clips.append(render_clip)
            _render_clip(
                ffmpeg, primary_source, render_clip, project["settings"], segment,
                overlay_source=overlay_source, include_primary_audio=not continuous_take,
            )
            segments.append(segment)
            progress("render_clips", 10 + round(index / len(variant["clips"]) * 45))

        concatenated = workspace / "concatenated.mp4"
        _concat(ffmpeg, segments, workspace, concatenated)
        progress("concat", 62)
        audio_base = concatenated
        if continuous_take:
            primary_audio = workspace / "continuous-primary.m4a"
            _render_continuous_primary_audio(
                ffmpeg, primary_audio_clips, primary_sources, project["settings"], primary_audio,
            )
            audio_base = workspace / "continuous-primary.mp4"
            _mux_primary_audio(
                ffmpeg, concatenated, primary_audio, int(variant["duration_ms"]), audio_base,
            )
        clean_video = output_directory / "clean.mp4"
        _mix_audio(ffmpeg, audio_base, variant, project["settings"], edit_store, clean_video)
        progress("audio", 74)
        srt_path, ass_path = _subtitle_files(variant, project["settings"], workspace)
        final_video = output_directory / "video.mp4"
        _burn_subtitles(
            ffmpeg, clean_video, ass_path, bool(project["settings"]["subtitle_style"]["enabled"]), workspace, final_video,
        )
        progress("subtitles", 86)

        info = probe_media(final_video)
        if (
            info.width != 1080 or info.height != 1920 or not 29 <= info.fps <= 31
            or info.video_codec not in {"h264", "avc1"} or info.audio_codec != "aac" or not info.has_audio
        ):
            raise MediaToolError(
                "成片的编码、尺寸、帧率或音轨不符合抖音交付要求"
                f"（{info.width}x{info.height}, {info.fps}fps, {info.video_codec}/{info.audio_codec}, audio={info.has_audio}）"
            )
        if not _faststart(final_video) or not _faststart(clean_video):
            raise MediaToolError("成片没有通过快速起播检查")

        public_project = {key: copy_value for key, copy_value in project.items()}
        project_path = output_directory / "timeline.json"
        write_json_atomic(project_path, public_project)
        final_srt = output_directory / "subtitles.srt"
        shutil.copy2(srt_path, final_srt)
        package_path = output_directory / "jianying-experimental.zip"
        _package_handoff(project, variant, final_srt, project_path, package_path)
        progress("package", 96)

        token = output_id.removeprefix("render_output_")
        refs = {
            "video_ref": f"render_video_{token}", "clean_video_ref": f"render_clean_{token}",
            "subtitle_ref": f"render_subtitle_{token}", "project_ref": f"render_project_{token}",
            "jianying_package_ref": f"render_jianying_{token}", "jianying_experimental": True,
        }
        created = now_iso()
        output = {
            "schema_version": "1.0.0", "fixture_data": bool(project["fixture_data"]), "output_id": output_id,
            "project_id": project["project_id"], "project_revision": project["revision"], "variant_id": variant["id"],
            "status": "video_review", "resources": refs,
            "media": {
                "filename": f"{_safe_label(str(variant['name']))}.mp4", "mime_type": "video/mp4",
                "sha256": sha256_file(final_video), "size_bytes": final_video.stat().st_size,
                "duration_ms": info.duration_ms, "width": info.width, "height": info.height, "fps": info.fps,
                "video_codec": info.video_codec, "audio_codec": info.audio_codec, "has_audio": info.has_audio,
                "faststart": True,
            },
            "render": {
                "task_id": task.task_id, "ffmpeg_version": _ffmpeg_version(ffmpeg), "rendered_at": created,
                "duration_ms": max(1, round((time.monotonic() - started) * 1000)),
            },
            "review": {"status": "pending", "reviewed_by": None, "reviewed_at": None, "note": None},
            "created_at": created, "updated_at": created,
        }
        validate_or_raise("render_output", output)
        edit_store.create_output(output, resources={
            refs["video_ref"]: (final_video, "video/mp4", output["media"]["filename"]),
            refs["clean_video_ref"]: (clean_video, "video/mp4", f"{_safe_label(str(variant['name']))}-无字幕.mp4"),
            refs["subtitle_ref"]: (final_srt, "application/x-subrip", f"{_safe_label(str(variant['name']))}.srt"),
            refs["project_ref"]: (project_path, "application/json", f"{_safe_label(str(variant['name']))}-剪辑工程.json"),
            refs["jianying_package_ref"]: (package_path, "application/zip", f"{_safe_label(str(variant['name']))}-剪映实验交接.zip"),
        })
        return output_id
    except Exception:
        # Never leave an unregistered partial output tree behind. The queue
        # workspace remains available for a safe retry.
        shutil.rmtree(output_directory, ignore_errors=True)
        raise
