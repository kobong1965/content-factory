"""Automatic Skill selection, conservative clip planning, and local rendering."""

from __future__ import annotations

import json
import base64
import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

from content_factory_media.pipeline import MediaPipeline

from .auto_edit_store import AutoEditProjectStore
from .edit_batches import ImportRequest, import_batch
from .s3_gateway import call_gateway
from .s3_settings import GatewaySettingsStore
from .subtitles import FONTS, normalize_cues, simplify_chinese, detect_silence, subtitle_effect


def _terms(value: object) -> set[str]:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "")).lower()
    terms = {text[index:index + 2] for index in range(max(0, len(text) - 1))}
    return terms | ({text} if text else set())


def choose_skill(project: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    skills = list(project.get("eligible_skill_snapshots") or [])
    if not skills:
        raise ValueError("项目没有冻结可用的剪辑 Skill")
    source_terms = _terms(project["source"].get("file_name"))
    ranked = []
    for skill in skills:
        skill_terms = _terms(skill.get("name")) | _terms(skill.get("mechanism"))
        score = len(source_terms & skill_terms)
        ranked.append((score, int(skill.get("revision") or 0), str(skill["skill_id"]), skill))
    score, _, _, selected = max(ranked, key=lambda item: (item[0], item[1], item[2]))
    reason = (
        f"原素材名称与“{selected.get('name', '剪辑方法')}”存在 {score} 个文本特征匹配；"
        "当前为基础匹配，系统已冻结该 Skill 版本，剪辑结果仍需人工审核。"
    )
    return selected, reason


def build_conservative_plan(project: Mapping[str, Any]) -> list[dict[str, Any]]:
    settings = project["settings"]
    source_duration = int(project["source"]["duration_ms"])
    count = int(settings["target_count"])
    minimum = int(settings["duration_min_ms"])
    maximum = int(settings["duration_max_ms"])
    if source_duration < minimum:
        raise ValueError("原素材短于成片最小时长")
    duration = min(maximum, max(minimum, source_duration // max(count, 1)))
    latest_start = source_duration - duration
    starts = [round(latest_start * index / max(count - 1, 1)) for index in range(count)]
    return [
        {
            "candidate_id": f"candidate_{index + 1:02d}",
            "title": f"自动剪辑版本 {index + 1}",
            "clips": [{"start_ms": start, "end_ms": start + duration}],
            "selection_reason": "在原素材时间轴上分散取样，保留连续原声并交由人工审核。",
            "keywords": [],
        }
        for index, start in enumerate(starts)
    ]


def _image_data_url(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _transcript_text(media_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    transcript_path = media_result.get("artifacts", {}).get("transcript_path")
    if not transcript_path:
        return []
    try:
        payload = json.loads(Path(transcript_path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    segments = payload.get("segments", []) if isinstance(payload, dict) else []
    result = []
    for item in segments:
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text") or item.get("transcript") or "").strip()
        if not text:
            continue
        result.append({
            # FFmpeg's whisper JSON filter reports integer milliseconds.
            "start_ms": round(float(item.get("start_ms", item.get("start", 0)))),
            "end_ms": round(float(item.get("end_ms", item.get("end", 0)))),
            "text": text,
        })
    return result


def _planner_schema(project: Mapping[str, Any]) -> dict[str, Any]:
    count = int(project["settings"]["target_count"])
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["analysis_summary", "selected_skill_id", "selection_reason", "candidates"],
        "properties": {
            "analysis_summary": {"type": "string", "minLength": 1},
            "selected_skill_id": {"type": "string", "minLength": 1},
            "selection_reason": {"type": "string", "minLength": 1},
            "candidates": {
                "type": "array", "minItems": count, "maxItems": count,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["candidate_id", "title", "source_id", "selection_reason", "keywords", "clips"],
                    "properties": {
                        "source_id": {"type": "string", "enum": [item['source_id'] for item in (project.get('sources') or [project['source']])]},
                        "candidate_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,100}$"},
                        "title": {"type": "string", "minLength": 1},
                        "selection_reason": {"type": "string", "minLength": 1},
                        "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                        "clips": {
                            "type": "array", "minItems": 1, "maxItems": 12,
                            "items": {
                                "type": "object", "additionalProperties": False,
                                "required": ["start_ms", "end_ms"],
                                "properties": {"start_ms": {"type": "integer", "minimum": 0}, "end_ms": {"type": "integer", "minimum": 1}},
                            },
                        },
                    },
                },
            },
        },
    }


def _subtitle_segments(candidate: Mapping[str, Any], transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    transcript = normalize_cues(transcript, duration_ms=max((int(s['end_ms']) for s in transcript), default=0))
    timeline_offset = 0
    result: list[dict[str, Any]] = []
    for clip in candidate.get("clips", []):
        clip_start = int(clip["start_ms"])
        clip_end = int(clip["end_ms"])
        for segment in transcript:
            overlap_start = max(clip_start, int(segment["start_ms"]))
            overlap_end = min(clip_end, int(segment["end_ms"]))
            if overlap_end <= overlap_start:
                continue
            result.append({
                "start_ms": timeline_offset + overlap_start - clip_start,
                "end_ms": timeline_offset + overlap_end - clip_start,
                "text": str(segment["text"]).strip(),
            })
        timeline_offset += clip_end - clip_start
    return result


def analyze_and_plan_with_gateway(project: Mapping[str, Any], *, root: Path) -> tuple[str, str, str, list[dict[str, Any]]]:
    settings_path = os.environ.get("CONTENT_FACTORY_S3_CONFIG_PATH")
    gateway = GatewaySettingsStore(settings_path).load() if settings_path else GatewaySettingsStore().load()
    if gateway is None or not gateway.has_purpose("video_review"):
        raise ValueError("没有可用于视频审核的多模态模型，请先在模型连接设置中完成验证。")
    config = gateway.for_purpose("video_review")
    task_root = root / "analysis" / str(project["project_id"])
    model_path = Path(os.environ.get(
        "CONTENT_FACTORY_WHISPER_MODEL",
        Path(__file__).resolve().parents[4] / ".models" / "whisper" / "ggml-tiny.bin",
    ))
    evidence, keyframes, transcripts = [], [], {}
    for source in project.get('sources') or [project['source']]:
        # Stable per project/source so retries reuse their own media workspace.
        identity = json.dumps([project['project_id'], source['source_id']], ensure_ascii=False)
        media_task_id = 'media_' + hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]
        media_result_path = MediaPipeline(asr_model_path=model_path).process(
            source['path'], task_root / source['source_id'], media_task_id, original_name=source['file_name'],
        )
        media_result = json.loads(media_result_path.read_text(encoding='utf-8'))
        transcript = _transcript_text(media_result)
        if media_result.get('asr', {}).get('status') != 'completed' or not transcript:
            raise ValueError(f"原素材语音转写未完成：{source['file_name']}，不能在没有话术证据时生成智能剪辑方案。")
        shots = media_result.get('shots', [])
        frames = [item for item in shots if isinstance(item, Mapping) and item.get('keyframe_path') and Path(item['keyframe_path']).is_file()][:12]
        if not frames:
            raise ValueError(f"原素材没有可用关键帧：{source['file_name']}")
        image_start = len(keyframes)
        keyframes.extend(_image_data_url(Path(item['keyframe_path'])) for item in frames)
        transcripts[source['source_id']] = transcript
        evidence.append({
            'source_id': source['source_id'], 'file_name': source['file_name'], 'duration_ms': source['duration_ms'],
            'transcript': transcript,
            'shots': [{key: item.get(key) for key in ('id', 'start_ms', 'end_ms')} for item in shots[:30] if isinstance(item, Mapping)],
            'keyframe_images': [{'image_index': image_start + i, 'start_ms': item.get('start_ms'), 'end_ms': item.get('end_ms')} for i, item in enumerate(frames)],
        })
    compact_skills = [
        {
            "skill_id": item["skill_id"], "revision": item["revision"],
            "name": item.get("name"), "mechanism": item.get("mechanism"),
            "steps": item.get("steps", []), "necessary_conditions": item.get("necessary_conditions", []),
            "failure_signals": item.get("failure_signals", []),
        }
        for item in project["eligible_skill_snapshots"]
    ]
    context = {
        "task": "分析待剪录播，自动选择一个最适合的已审核剪辑 Skill，并返回可直接执行的选段方案。只依据转写、镜头关键帧和 Skill，不得编造商品信息或平台算法因果。每条成片保留原声，选段按时间排序且不得重叠。",
        "sources": evidence,
        "source_policy": "读取全部素材，成片数量是整个项目总数。每条候选必须指定一个 source_id，仅从该源片取段，禁止跨文件拼接商品和原声。图片索引从0开始，对应上传图片顺序。",
        "settings": project["settings"],
        "skills": compact_skills,
    }
    result = call_gateway(
        config, context_json=json.dumps(context, ensure_ascii=False), keyframe_data_urls=keyframes,
        output_schema=_planner_schema(project), timeout_seconds=540,
        developer_instructions="你是抖音国内男装录播剪辑规划器。先依据真实 ASR 和关键帧理解素材，再从给定已审核 Skill 中选择一个方法。不得虚构话术、商品参数、数据或平台算法因果。所有时间码必须落在源视频内，数量和时长必须严格满足用户设置。只返回指定 JSON。",
        schema_name="auto_edit_plan",
    ).content
    selected_skill_id = str(result.get("selected_skill_id") or "")
    if selected_skill_id not in {item["skill_id"] for item in project["eligible_skill_snapshots"]}:
        raise ValueError("模型选择了项目冻结范围之外的 Skill")
    candidates = list(result.get("candidates") or [])
    for candidate in candidates:
        source_id = candidate.get('source_id')
        if source_id not in transcripts:
            raise ValueError('模型返回了项目之外的素材来源')
        candidate["subtitle_segments"] = _subtitle_segments(candidate, transcripts[source_id])
    return (
        selected_skill_id,
        str(result.get("selection_reason") or "").strip(),
        str(result.get("analysis_summary") or "").strip(),
        candidates,
    )


def _run(command: list[str], *, timeout: int = 1800) -> None:
    completed = subprocess.run(command, capture_output=True, timeout=timeout)
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[-1200:]
        raise RuntimeError(f"FFmpeg 剪辑失败：{detail}")


def _srt_time(milliseconds: int) -> str:
    hours, remaining = divmod(max(0, milliseconds), 3_600_000)
    minutes, remaining = divmod(remaining, 60_000)
    seconds, millis = divmod(remaining, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _ass_time(milliseconds: int) -> str:
    hours, remaining = divmod(max(0, milliseconds), 3_600_000)
    minutes, remaining = divmod(remaining, 60_000)
    seconds, millis = divmod(remaining, 1_000)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{millis // 10:02d}"


def _ass_color(rgb: str) -> str:
    red, green, blue = rgb[1:3], rgb[3:5], rgb[5:7]
    return f"&H00{blue}{green}{red}&"


def _highlight_text(text: str, keywords: list[str], color: str, scale: float) -> str:
    escaped = text.replace("{", "（").replace("}", "）").replace("\\", "／")
    if not keywords:
        return escaped
    tag = f"{{\\c{color}\\fscx{round(scale * 100)}\\fscy{round(scale * 100)}}}"
    reset = "{\\c&H00FFFFFF&\\fscx100\\fscy100}"
    for keyword in sorted({item.strip() for item in keywords if item.strip()}, key=len, reverse=True):
        escaped = escaped.replace(keyword, f"{tag}{keyword}{reset}")
    return escaped


def _write_subtitles(item: Mapping[str, Any], project: Mapping[str, Any], subtitle: Path, ass: Path) -> bool:
    if item.get('preserve_sentence_timing'):
        segments=[{**cue,'text':simplify_chinese(cue['text'])} for cue in item.get('subtitle_segments', [])]
    else:
        segments = normalize_cues(item.get('subtitle_segments', []), duration_ms=sum(int(c['end_ms'])-int(c['start_ms']) for c in item.get('clips', [])) or max((int(s['end_ms']) for s in item.get('subtitle_segments', [])),default=0), silence=item.get('silence_intervals', []))
    lines = []
    for index, segment in enumerate(segments, start=1):
        lines.extend([str(index), f"{_srt_time(int(segment['start_ms']))} --> {_srt_time(int(segment['end_ms']))}", str(segment["text"]).strip(), ""])
    subtitle.write_text("\n".join(lines), encoding="utf-8-sig")
    if not segments:
        return False
    settings = project["settings"]
    yellow = _ass_color(str(settings["keyword_color"]))
    size = int(settings["subtitle_font_size"])
    font = FONTS[settings.get('subtitle_font', 'heiti')]
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Default,{font},{size},&H00FFFFFF&,&H00FFFFFF&,&H00000000&,&H64000000&,0,0,0,0,100,100,0,0,1,4,0,2,54,54,150,1\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    events = []
    keywords = [simplify_chinese(str(word)) for word in item.get("keywords", [])]
    from .speech_captions import caption_events
    for segment in caption_events(segments,item.get('caption_mode','sentence')):
        margin=0
        text = _highlight_text(str(segment["text"]), keywords, yellow, float(settings["keyword_scale"]))
        if 'highlight' in segment:
            prefix=segment['highlight']
            text=_highlight_text(prefix,[],yellow,1)
            text=f'{{\\c{yellow}}}'+text+'{\\c&H00FFFFFF&}'+_highlight_text(segment['text'][len(prefix):],[],yellow,1)
        # Reapplying a fade/pop on every acoustic unit makes karaoke blink.
        if item.get('caption_mode','sentence')=='sentence':
            text = subtitle_effect(settings.get('subtitle_effect', 'none'), segment['end_ms']-segment['start_ms']) + text
        if 'subtitle_x' in settings and 'subtitle_y' in settings:
            text = f"{{\\an2\\pos({round(float(settings['subtitle_x'])*10.8)},{round(float(settings['subtitle_y'])*19.2)})}}" + text
            available_width=(min(float(settings['subtitle_x']),100-float(settings['subtitle_x']))*2-10)*10.8
            margin=round((1080-available_width)/2)
        events.append(f"Dialogue: 0,{_ass_time(int(segment['start_ms']))},{_ass_time(int(segment['end_ms']))},Default,,{margin},{margin},0,,{text}")
    ass.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")
    return True


def _ass_filter_path(path: Path) -> str:
    return path.resolve().as_posix().replace(":", "\\:").replace("'", "\\'")


def render_and_register(store: AutoEditProjectStore, project: Mapping[str, Any], *, root: Path) -> dict[str, Any]:
    project_id = str(project["project_id"])
    batch_id = "auto_" + project_id.removeprefix("auto_edit_")
    output_root = (root / "outputs" / batch_id).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    sources = {item['source_id']: item for item in (project.get('sources') or [project['source']])}
    candidates = []
    for item in project["plan"]:
        source_id = item.get('source_id') or (next(iter(sources)) if len(sources) == 1 else None)
        if source_id not in sources:
            raise ValueError('剪辑方案素材来源不在当前项目中')
        source = Path(sources[source_id]['path'])
        candidate_id = item["candidate_id"]
        video = output_root / f"{candidate_id}.mp4"
        clean = output_root / f"{candidate_id}.clean.mp4"
        subtitle = output_root / f"{candidate_id}.srt"
        ass = output_root / f"{candidate_id}.ass"
        cover = output_root / f"{candidate_id}.jpg"
        parts = []
        for part_index, clip in enumerate(item["clips"], start=1):
            part = output_root / f"{candidate_id}.part-{part_index:02d}.mp4"
            _run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{clip['start_ms'] / 1000:.3f}", "-t", f"{(clip['end_ms'] - clip['start_ms']) / 1000:.3f}", "-i", str(source),
                "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "21", "-c:a", "aac", "-movflags", "+faststart", str(part),
            ])
            parts.append(part)
        if len(parts) == 1:
            parts[0].replace(clean)
        else:
            concat = output_root / f"{candidate_id}.concat.txt"
            concat.write_text("\n".join(f"file '{part.name}'" for part in parts), encoding="utf-8")
            _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(clean)])
            concat.unlink(missing_ok=True)
            for part in parts:
                part.unlink(missing_ok=True)
        from .speech_captions import recognize, split_words, HOTWORDS
        settings=project['settings']
        speech=recognize(clean,output_root/f'{candidate_id}.speech')
        cues=split_words(speech['words'],max_chars=max(8,min(14,int(1944/settings['subtitle_font_size']/max(1,settings['keyword_scale'])))))
        for cue in cues:
            cue['uncertain']=any(w.get('probability',0)<.65 for w in cue['words']) or bool(re.search(r'[0-9]|尺码|价格|款号|品牌|面料',cue['text']))
        document={'cues':cues,'mode':'reveal','hotwords':HOTWORDS,'x':50,'y':86,'font':settings.get('subtitle_font','heiti'),'size':settings['subtitle_font_size'],'effect':settings.get('subtitle_effect','none'),'keywords':item.get('keywords',[]),'keyword_color':settings['keyword_color'],'keyword_scale':settings['keyword_scale']}
        (output_root/f'{candidate_id}.captions.json').write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
        caption_item = {**item,'subtitle_segments':cues,'preserve_sentence_timing':True,'caption_mode':'reveal'}
        caption_project={**project,'settings':{**settings,'subtitle_x':50,'subtitle_y':86}}
        has_subtitles = _write_subtitles(caption_item, caption_project, subtitle, ass)
        if has_subtitles:
            _run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(clean),
                "-vf", f"ass='{_ass_filter_path(ass)}'", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "21", "-c:a", "copy", "-movflags", "+faststart", str(video),
            ])
            clean.unlink(missing_ok=True)
        else:
            clean.replace(video)
        ass.unlink(missing_ok=True)
        _run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.2",
            "-i", str(video), "-frames:v", "1", "-q:v", "2", str(cover),
        ], timeout=120)
        candidates.append({
            "id": candidate_id,
            "title": item["title"],
            "source_path": str(source),
            "source_start_ms": min(clip["start_ms"] for clip in item["clips"]),
            "source_end_ms": max(clip["end_ms"] for clip in item["clips"]),
            "clips": item["clips"],
            "video_path": video.name,
            "subtitle_path": subtitle.name,
            "cover_path": cover.name,
            "hook": item.get("selection_reason") or "自动选段",
            "benchmark_refs": [project["selected_skill"]["snapshot"].get("name", "已审核剪辑 Skill")],
            "review_notes": [
                "保留原素材连续原声；系统自动选择 Skill 与选段，必须人工复核。",
                "字幕来自本条原素材 ASR；黄色重点词由本项目规划结果生成，必须人工听审。",
            ],
        })
    manifest = {
        "schema_version": 1,
        "id": batch_id,
        "title": project["title"],
        "analysis_summary": project.get("analysis_summary") or project["selected_skill"]["reason"],
        "candidates": candidates,
    }
    manifest_path = output_root / "manifest.json"
    temporary = output_root / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(manifest_path)
    imported = import_batch(ImportRequest(manifest_path=str(manifest_path)))
    from .subtitle_editor import inherit_draft
    for item in project['plan']:
        document=json.loads((output_root/f"{item['candidate_id']}.captions.json").read_text(encoding='utf-8'))
        inherit_draft(batch_id,item['candidate_id'],document)
    return store.register_output_batch(project_id, batch_id=imported["id"])


def process_one(
    store: AutoEditProjectStore, *, available_skills: list[dict[str, Any]], worker_id: str, root: Path,
    planner=None,
) -> dict[str, Any] | None:
    project = store.claim_next(available_skills=available_skills, worker_id=worker_id)
    if project is None:
        return None
    try:
        if planner is None:
            selected_skill_id, reason, analysis_summary, plan = analyze_and_plan_with_gateway(project, root=root)
        else:
            selected_skill_id, reason, analysis_summary, plan = planner(project)
        project = store.save_plan(
            project["project_id"], worker_id=worker_id, selected_skill_id=selected_skill_id,
            reason=reason, analysis_summary=analysis_summary, plan=plan,
        )
        project = store.mark_rendering(project["project_id"], worker_id=worker_id)
        return render_and_register(store, project, root=root)
    except Exception as exc:
        return store.fail(project["project_id"], message=str(exc))
