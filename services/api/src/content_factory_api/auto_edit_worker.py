"""Automatic Skill selection, conservative clip planning, and local rendering."""

from __future__ import annotations

import json
import base64
import hashlib
import math
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

from content_factory_media.pipeline import MediaPipeline
from content_factory_media.tools import probe_media

from .auto_edit_store import AutoEditProjectStore, MAX_CLIPS_PER_CANDIDATE, validate_edit_plan
from .candidate_contract import stable_candidate_id, valid_candidate_id
from .edit_batches import ImportRequest, Manifest, import_batch
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
                    "required": ["candidate_id", "title", "source_id", "skill_id", "selection_reason", "keywords", "clips"],
                    "properties": {
                        "source_id": {"type": "string", "enum": [item['source_id'] for item in (project.get('sources') or [project['source']])]},
                        "skill_id": {"type": "string", "enum": [item['skill_id'] for item in project['eligible_skill_snapshots'] ]},
                        "candidate_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,100}$"},
                        "title": {"type": "string", "minLength": 1, "maxLength": 200},
                        "selection_reason": {"type": "string", "minLength": 1},
                        "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                        "clips": {
                            "type": "array", "minItems": 1, "maxItems": MAX_CLIPS_PER_CANDIDATE,
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


def _semantic_subtitle_cues(words: list[dict[str, Any]], *, semantic_config: Any) -> list[dict[str, Any]]:
    """Choose meaning-complete boundaries without changing ASR words.

    A real configured text model gets the semantic pass.  Isolated unit tests
    and legacy local-only configurations retain the acoustic splitter; that
    fallback is explicit in the evidence and never pretends to understand
    meaning.  Once a semantic model is configured, its unsafe response is a
    hard failure rather than a silent random regrouping.
    """
    from .speech_captions import split_words

    if not words:
        return []
    modalities = set(getattr(semantic_config, 'modalities', ()) or ()) if semantic_config is not None else set()
    if semantic_config is not None and 'text' in modalities:
        from .semantic_captions import group_words_semantically
        return group_words_semantically(words, gateway_config=semantic_config)
    cues = split_words(words)
    for cue in cues:
        cue['alignment_source'] = 'api_word_timestamps+acoustic_boundaries'
        cue['semantic_confidence'] = None
    return cues


def _configured_speech_model(gateway: Any) -> Any | None:
    """Return only a real, audio-capable routed model.

    A few legacy/unit-test gateway doubles report every purpose as available;
    treating those placeholders as an API speech route would skip local ASR
    and then fail with ``object has no attribute model``.  Production
    ``GatewayConfig`` objects always carry these fields and the explicit audio
    modality, so this guard is both backwards-compatible and fail-closed.
    """
    if gateway is None:
        return None
    try:
        if not gateway.has_purpose("speech"):
            return None
        config = gateway.for_purpose("speech")
    except (AttributeError, KeyError, ValueError):
        return None
    if config is None or any(not getattr(config, name, None) for name in ("base_url", "model", "api_key")):
        # Older in-process test doubles represented an explicitly routed
        # speech model with a bare object.  Keep that narrow compatibility
        # path only when no script route exists; real persisted settings always
        # carry the fields above and an explicit audio modality.
        try:
            if not hasattr(config, "modalities") and not gateway.has_purpose("script"):
                return config
        except AttributeError:
            pass
        return None
    if "audio" not in set(getattr(config, "modalities", ()) or ()):
        return None
    return config


def analyze_and_plan_with_gateway(project: Mapping[str, Any], *, root: Path) -> tuple[str, str, str, list[dict[str, Any]]]:
    settings_path = os.environ.get("CONTENT_FACTORY_S3_CONFIG_PATH")
    gateway = GatewaySettingsStore(settings_path).load() if settings_path else GatewaySettingsStore().load()
    if gateway is None or not gateway.has_purpose("video_review"):
        raise ValueError("没有可用于视频审核的多模态模型，请先在模型连接设置中完成验证。")
    config = gateway.for_purpose("video_review")
    # Video review and speech transcription are separate capabilities.  The
    # former is a text/image chat request and must never be passed to the
    # audio fallback, otherwise a local-ASR failure is misreported as a relay
    # outage (or sends an audio multipart request to a chat endpoint).
    speech_config = _configured_speech_model(gateway)
    semantic_config = gateway.for_purpose("script") if gateway.has_purpose("script") else config
    task_root = root / "analysis"
    model_path = Path(os.environ.get(
        "CONTENT_FACTORY_WHISPER_MODEL",
        Path(__file__).resolve().parents[4] / ".models" / "whisper" / "ggml-tiny.bin",
    ))
    settings = project.get('settings', {})
    original_audio_only = settings.get('subtitle_mode') == 'none'
    evidence, keyframes, transcripts = [], [], {}
    natural_pools = {}
    natural = settings.get('duration_policy') == 'bounded_15_30'
    for source in project.get('sources') or [project['source']]:
        # Stable per project/source so retries reuse their own media workspace.
        # Do not repeat long project/source IDs in nested directory names:
        # installed %LOCALAPPDATA% profiles otherwise exceed Windows MAX_PATH
        # once media hashes, tasks and atomic output suffixes are appended.
        identity = json.dumps([project['project_id'], source['source_id']], ensure_ascii=False)
        workspace_id = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]
        media_task_id = 'media_' + workspace_id
        # When an explicit audio route exists, the media pass only needs
        # proxy/scenes/keyframes; the audio route below becomes the authority
        # for word timestamps instead of spending a local ASR run first.
        media_result_path = MediaPipeline(asr_model_path=(
            None if original_audio_only or speech_config is not None else model_path
        )).process(
            source['path'], task_root / workspace_id, media_task_id, original_name=source['file_name'],
        )
        media_result = json.loads(media_result_path.read_text(encoding='utf-8'))
        transcript = _transcript_text(media_result)
        speech_words: list[dict[str, Any]] = []
        if not original_audio_only:
            from .speech_captions import recognize
            # An explicit audio relay is authoritative.  Without one, prefer
            # local word timestamps, but retain an already verified media
            # transcript as planning evidence if the optional word pass is not
            # available (legacy fixtures and local-only installations).
            try:
                speech = recognize(
                    source['path'], task_root / workspace_id / 'source-speech',
                    word_timestamps_only=True, gateway_config=speech_config,
                    prefer_api=speech_config is not None,
                )
                speech_words = list(speech.get('words') or [])
            except Exception as exc:
                if transcript and speech_config is None:
                    speech_words = []
                else:
                    raise ValueError(
                        f"原素材语音转写未完成：{source['file_name']}；{exc}"
                    ) from exc
            if speech_words:
                transcript = _semantic_subtitle_cues(speech_words, semantic_config=semantic_config)
            elif transcript and 'audio' not in set(getattr(speech_config, 'modalities', ()) or ()):
                # A legacy/local media fixture may already contain complete
                # segment timings but no per-word fixture. Keep that
                # diagnostic path usable; real API audio profiles still
                # require word timestamps before subtitle output.
                transcript = transcript
            else:
                transcript = []
            if not transcript:
                raise ValueError(f"原素材语音转写未完成：{source['file_name']}，不能在没有话术证据时生成智能剪辑方案。")
        shots = media_result.get('shots', [])
        frames = [item for item in shots if isinstance(item, Mapping) and item.get('keyframe_path') and Path(item['keyframe_path']).is_file()][:12]
        if not frames:
            raise ValueError(f"原素材没有可用关键帧：{source['file_name']}")
        image_start = len(keyframes)
        keyframes.extend(_image_data_url(Path(item['keyframe_path'])) for item in frames)
        transcripts[source['source_id']] = transcript
        if natural:
            if original_audio_only:
                from .natural_clips import build_visual_clip_pool
                natural_pools[source['source_id']] = build_visual_clip_pool(
                    shots, source_duration_ms=source['duration_ms'],
                    minimum=settings['duration_min_ms'], maximum=settings['duration_max_ms'],
                    allow_fixed_fallback=True)
            else:
                from .natural_clips import build_clip_pool
                natural_pools[source['source_id']] = build_clip_pool(
                    speech_words, raw_segments=[], source_duration_ms=source['duration_ms'],
                    minimum=settings['duration_min_ms'], maximum=settings['duration_max_ms'])
        evidence.append({
            'source_id': source['source_id'], 'file_name': source['file_name'], 'duration_ms': source['duration_ms'],
            'transcript': transcript,
            **({'natural_clip_pool': natural_pools[source['source_id']]} if natural else {}),
            'shots': [{key: item.get(key) for key in ('id', 'start_ms', 'end_ms')} for item in shots[:30] if isinstance(item, Mapping)],
            'keyframe_images': [{'image_index': image_start + i, 'start_ms': item.get('start_ms'), 'end_ms': item.get('end_ms')} for i, item in enumerate(frames)],
        })
    if natural:
        from .natural_clips import pool_can_fit
        if not any(pool_can_fit(pool, minimum=settings['duration_min_ms'],
                                maximum=settings['duration_max_ms']) for pool in natural_pools.values()):
            if original_audio_only:
                raise ValueError('暂不支持生成：未找到 15—30 秒的完整画面片段；请调整时长或补充素材。仅保留原声模式不使用 ASR 句子边界。')
            raise ValueError('暂不支持生成：没有找到能组合为所需时长的自然片段。请调整条件或补充素材；不会截断口播凑时长。')
    compact_skills = [
        {
            "skill_id": item["skill_id"], "revision": item["revision"],
            "name": item.get("name"), "mechanism": item.get("mechanism"),
            "steps": item.get("steps", []), "necessary_conditions": item.get("necessary_conditions", []),
            "failure_signals": item.get("failure_signals", []),
        }
        for item in project["eligible_skill_snapshots"]
    ]
    boundary_policy = "按用户自定义范围规划，保留完整口播。"
    if natural:
        boundary_policy = (
            "每个 clip 的 start_ms/end_ms 必须原样选择同一条 natural_clip_pool 记录。"
            + ("这是本机根据镜头检测边界建立的画面完整候选；它不代表口播句子边界，不得虚构转写内容。"
               if original_audio_only else
               "这是本机根据原声句末、停顿或ASR语句边界建立的候选；候选不等于语义完整，结合文字和关键帧确认语义、动作完整，再选择。")
            + "允许重复选择，不能自行伸缩时间或拼出候选池之外的边界。优先20—25秒。"
            + "所有选段累计应在下限+100ms到上限-200ms之间，留出整条视频的编码余量。"
        )
    context = {
        "task": "分析待剪录播，为每条成片从已审核剪辑 Skill 中选择最合适的方法，并返回可直接执行的选段方案。允许同一项目混用多个 Skill；每条候选用 skill_id 指定方法，未指定时才回退到 selected_skill_id。只依据转写、镜头关键帧和 Skill，不得编造商品信息或平台算法因果。每个选段保留对应原声，clips 数组按成片播放顺序排列。时长采用自然完整片段优先：不要求固定秒数，优先接近 20—25 秒，但必须严格落在用户设置的硬边界内；不要把话术、动作或镜头从中间截断来凑时长。",
        "clip_reuse_policy": f"同一成片内及不同成片之间，均允许重复使用同一片段或同一帧，允许源时间区间部分重叠或完全相同。可按 A→B→A 等成片播放顺序编排，不要求源时间码递增，不把已用片段视为耗尽。每次使用的时长均计入成片总时长：sum(end_ms-start_ms)，不得去重后计算；每条成片最多 {MAX_CLIPS_PER_CANDIDATE} 段。单段必须满足 0≤start_ms<end_ms≤原片时长；重复必须服务于内容表达，不得伪造话术或跨商品复用。",
        "sources": evidence,
        "source_policy": "读取全部素材，成片数量是整个项目总数。每条候选必须指定一个 source_id，仅从该源片取段，禁止跨文件拼接商品和原声。图片索引从0开始，对应上传图片顺序。",
        "settings": project["settings"],
        "audio_policy": {
            "preserve_original_audio": True,
            "speech_recognition": (
                "not_requested" if original_audio_only
                else "api_word_timestamps_plus_semantic_boundaries" if speech_config is not None
                else "local_word_timestamps_plus_acoustic_boundaries"
            ),
            "subtitle_output": "disabled" if original_audio_only else "audio_aligned_semantic_cues",
        },
        "natural_boundary_policy": boundary_policy,
        "skills": compact_skills,
    }
    result = call_gateway(
        config, context_json=json.dumps(context, ensure_ascii=False), keyframe_data_urls=keyframes,
        output_schema=_planner_schema(project), timeout_seconds=540,
        developer_instructions=(
            "你是抖音国内男装录播剪辑规划器。"
            + ("当前为仅保留原声模式：只能依据镜头关键帧和本机镜头边界规划，不得声称听到了口播，不得虚构 transcript/words；输出仍必须保留原声。"
               if original_audio_only else
               "先依据真实 ASR 和关键帧理解素材；字幕文字必须原样来自带时间戳的主播口播，"
               "语义模型只能选择连续字词边界，不能改词、乱组词或虚构内容。")
            + "再为每条候选从给定已审核剪辑 Skill 中选择方法，尽量形成有依据的多风格组合。不得虚构话术、商品参数、数据或平台算法因果。同一片段可在同一成片和不同成片内重复使用，源区间可重叠；clips 按成片播放顺序排列，不按源时间排序或去重。每次出现都计入成片时长。时长不要求固定值，优先 20—25 秒，但必须严格满足用户设置的硬边界；只在自然句子、镜头或动作边界结束，禁止为了凑时长截断口播或动作。所有时间码必须落在源视频内，数量和时长必须严格满足用户设置。只返回指定 JSON。"),
        schema_name="auto_edit_plan",
    ).content
    selected_skill_id = str(result.get("selected_skill_id") or "")
    if selected_skill_id not in {item["skill_id"] for item in project["eligible_skill_snapshots"]}:
        raise ValueError("模型选择了项目冻结范围之外的 Skill")
    candidates = list(result.get("candidates") or [])
    for candidate in candidates:
        candidate_skill_id = str(candidate.get("skill_id") or selected_skill_id)
        candidate_skill = next((skill for skill in project["eligible_skill_snapshots"] if skill["skill_id"] == candidate_skill_id), None)
        if candidate_skill is None:
            raise ValueError("模型为成片选择了项目冻结范围之外的 Skill")
        candidate["skill_id"] = candidate_skill_id
        candidate["skill_snapshot"] = json.loads(json.dumps(candidate_skill, ensure_ascii=False))
        source_id = candidate.get('source_id')
        if source_id not in transcripts:
            raise ValueError('模型返回了项目之外的素材来源')
        if natural:
            from .natural_clips import attach_natural_evidence
            attach_natural_evidence(candidate, natural_pools[source_id], boundary_kind='visual' if original_audio_only else 'speech')
        candidate["subtitle_segments"] = [] if original_audio_only else _subtitle_segments(candidate, transcripts[source_id])
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
    if item.get('caption_mode') == 'sentence' and project['settings'].get('subtitle_mode') in {'sentence', 'auto'}:
        keywords = []  # Ordinary dialogue captions retain a single text style.
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


def _caption_mode(settings: Mapping[str, Any], candidate_index: int) -> str:
    """Resolve the persisted subtitle choice without changing acoustic timing."""
    configured = str(settings.get('subtitle_mode', 'reveal'))
    if configured != 'auto':
        return configured
    return ('sentence', 'highlight', 'reveal')[candidate_index % 3]


def _ass_filter_path(path: Path) -> str:
    return path.resolve().as_posix().replace(":", "\\:").replace("'", "\\'")


def _output_identity(project: Mapping[str, Any], root: Path) -> tuple[str, Path]:
    batch_id = 'auto_' + str(project['project_id']).removeprefix('auto_edit_')
    generation = project.get('render_generation')
    if generation:
        batch_id += '_' + str(generation)
    if not valid_candidate_id(batch_id):
        raise ValueError('输出批次内部编号无效')
    output_root = (root / "outputs" / batch_id).resolve()
    return batch_id, output_root


def _manifest_for_project(project: Mapping[str, Any], batch_id: str) -> dict[str, Any]:
    """Apply the final library contract before spending time encoding videos."""
    if len(project['plan']) != project['settings']['target_count']:
        raise ValueError('剪辑方案数量与用户设置不一致')
    sources = {s['source_id']: s for s in (project.get('sources') or [project['source']])}
    candidates = []
    for item in project['plan']:
        source_id = item.get('source_id') or (next(iter(sources)) if len(sources) == 1 else None)
        if source_id not in sources:
            raise ValueError('剪辑方案素材来源不在当前项目中')
        validate_edit_plan([item], source_duration_ms=sources[source_id]['duration_ms'],
                           settings={**project['settings'], 'target_count': 1})
        candidate_id = item['candidate_id']
        review_notes = ['保留原素材连续原声；系统自动选择 Skill 与选段，必须人工复核。']
        if project['settings'].get('subtitle_mode') == 'none':
            review_notes.append('本次关闭字幕并跳过 ASR；仅保证画面与原声音轨，不保证口播句子边界。')
        else:
            review_notes.append('字幕来自最终成片原声 ASR，并由语义模型只做连续字词断句；黄色重点词必须人工听审。')
        candidates.append({
            'id': candidate_id, 'title': item['title'], 'source_path': sources[source_id]['path'],
            'source_start_ms': min(c['start_ms'] for c in item['clips']),
            'source_end_ms': max(c['end_ms'] for c in item['clips']), 'clips': item['clips'],
            'video_path': f'{candidate_id}.mp4', 'subtitle_path': f'{candidate_id}.srt',
            'cover_path': f'{candidate_id}.jpg', 'hook': item.get('selection_reason') or '自动选段',
            'benchmark_refs': [item.get('skill_snapshot', project['selected_skill']['snapshot']).get('name', '已审核剪辑 Skill')],
            'review_notes': review_notes,
        })
    if len({c['id'] for c in candidates}) != len(candidates):
        raise ValueError('剪辑方案候选 ID 重复')
    return Manifest.model_validate({
        'schema_version': 1, 'id': batch_id, 'title': project['title'], 'sku': project.get('sku', ''),
        'analysis_summary': project.get('analysis_summary') or project['selected_skill']['reason'],
        'candidates': candidates,
    }).model_dump()


def _sha256_file(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _validate_rendered_duration(path: Path, item: Mapping[str, Any], *, settings=None) -> None:
    info = probe_media(path)
    if settings and settings.get('duration_policy') == 'bounded_15_30':
        if not settings['duration_min_ms'] <= info.duration_ms <= settings['duration_max_ms']:
            raise ValueError(f'成片实际时长 {info.duration_ms / 1000:.3f} 秒超出硬边界，已阻止登记。请重新规划完整片段。')
    expected_ms = sum(clip['end_ms'] - clip['start_ms'] for clip in item['clips'])
    # One output frame plus an AAC packet can round at the edge, but never
    # multiply this allowance by the number of reused pieces.
    tolerance_ms = max(100, math.ceil(1000 / info.fps) + 25)
    if abs(info.duration_ms - expected_ms) > tolerance_ms:
        raise ValueError(f'成片实际时长 {info.duration_ms / 1000:.3f} 秒与计划 '
                         f'{expected_ms / 1000:.3f} 秒不一致，已阻止登记；'
                         '保留原方案，不会截断口播来凑时长。')


def _register_checkpoint(store: AutoEditProjectStore, project: Mapping[str, Any], *, root: Path) -> dict[str, Any]:
    current = store.get_project(project['project_id'])
    if current['revision'] != project['revision'] or current['status'] != 'rendering':
        raise ValueError('项目状态已改变，不能登记过期成片')
    batch_id, output_root = _output_identity(project, root)
    checkpoint = project['registration_checkpoint']
    manifest_path = output_root / 'manifest.json'
    if Path(checkpoint['manifest_path']).resolve() != manifest_path or _sha256_file(manifest_path) != checkpoint['manifest_sha256']:
        raise ValueError('已生成批次清单发生变化；已保留成片，请检查后重试登记')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest != _manifest_for_project(project, batch_id):
        raise ValueError('已生成成片与项目方案不一致；不能覆盖旧结果')
    documents = {}
    expected_files = {f"{item['candidate_id']}{suffix}" for item in project['plan']
                      for suffix in ('.mp4', '.srt', '.jpg', '.captions.json')}
    if set(checkpoint['artifacts']) != expected_files:
        raise ValueError('成片登记检查点缺少资源指纹')
    for name, fingerprint in checkpoint['artifacts'].items():
        path = (output_root / name).resolve()
        if not path.is_relative_to(output_root) or _sha256_file(path) != fingerprint:
            raise ValueError('已生成的成片、字幕或封面发生变化；不能沿用旧登记检查点')
    for item in project['plan']:
        candidate_id = item['candidate_id']
        _validate_rendered_duration(output_root / f'{candidate_id}.mp4', item, settings=project['settings'])
        documents[candidate_id] = json.loads((output_root / f'{candidate_id}.captions.json').read_text(encoding='utf-8'))
    # Import is idempotent and inherits existing manual subtitle edits untouched.
    imported = import_batch(ImportRequest(manifest_path=str(manifest_path)))
    from .subtitle_editor import inherit_draft
    for candidate_id, document in documents.items():
        inherit_draft(batch_id, candidate_id, document)
    return store.register_output_batch(project['project_id'], batch_id=imported['id'],
                                       expected_revision=project['revision'], render_generation=project.get('render_generation'))


def render_and_register(store: AutoEditProjectStore, project: Mapping[str, Any], *, root: Path) -> dict[str, Any]:
    if project.get('registration_checkpoint'):
        return _register_checkpoint(store, project, root=root)
    project_id = str(project['project_id'])
    batch_id, output_root = _output_identity(project, root)
    manifest = _manifest_for_project(project, batch_id)
    output_root.mkdir(parents=True, exist_ok=True)
    sources = {item['source_id']: item for item in (project.get('sources') or [project['source']])}
    settings_path = os.environ.get("CONTENT_FACTORY_S3_CONFIG_PATH")
    gateway = GatewaySettingsStore(settings_path).load() if settings_path else GatewaySettingsStore().load()
    speech_config = _configured_speech_model(gateway)
    semantic_config = gateway.for_purpose("script") if gateway is not None and gateway.has_purpose("script") else None
    if semantic_config is None and gateway is not None and gateway.has_purpose("video_review"):
        semantic_config = gateway.for_purpose("video_review")
    for candidate_index, item in enumerate(project["plan"]):
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
        multiple_parts = len(item['clips']) > 1
        for part_index, clip in enumerate(item["clips"], start=1):
            part = output_root / f"{candidate_id}.part-{part_index:02d}{'.mkv' if multiple_parts else '.mp4'}"
            _run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{clip['start_ms'] / 1000:.3f}", "-t", f"{(clip['end_ms'] - clip['start_ms']) / 1000:.3f}", "-i", str(source),
                "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "21",
                *(["-bf", "0", "-c:a", "pcm_s16le"] if multiple_parts else ["-c:a", "aac", "-movflags", "+faststart"]),
                str(part),
            ])
            parts.append(part)
        if len(parts) == 1:
            parts[0].replace(clean)
        else:
            concat = output_root / f"{candidate_id}.concat.txt"
            # Exact offsets avoid accumulating per-part video frame rounding.
            # PCM intermediates avoid adding AAC priming at every occurrence.
            concat.write_text("ffconcat version 1.0\n" + "\n".join(
                f"file '{part.name}'\nduration {(clip['end_ms'] - clip['start_ms']) / 1000:.3f}"
                for part, clip in zip(parts, item['clips'])
            ) + "\n", encoding="utf-8")
            fps = probe_media(source).fps
            _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
                  "-map", "0:v:0", "-map", "0:a?", "-vf", f"fps={fps}", "-af", "aresample=async=1:first_pts=0",
                  "-c:v", "libx264", "-bf", "0", "-preset", "veryfast", "-crf", "21", "-c:a", "aac",
                  "-movflags", "+faststart", str(clean)])
            concat.unlink(missing_ok=True)
            for part in parts:
                part.unlink(missing_ok=True)
        _validate_rendered_duration(clean, item, settings=project['settings'])
        settings=project['settings']
        original_audio_only = settings.get('subtitle_mode') == 'none'
        if original_audio_only:
            # FFmpeg already maps the source audio into the clean video.  In
            # this explicit mode ASR is not requested and subtitles stay empty.
            cues = []
            # The subtitle-editor contract only accepts renderable display
            # modes. Empty cues plus the project setting/manifest note express
            # that subtitles were disabled while keeping the draft editable.
            caption_mode = 'sentence'
            caption_keywords = []
            document={'cues':cues,'mode':caption_mode,'hotwords':[],'x':50,'y':86,
                      'font':settings.get('subtitle_font','heiti'),'size':settings['subtitle_font_size'],
                      'effect':'none','keywords':caption_keywords,
                      'keyword_color':settings['keyword_color'],'keyword_scale':settings['keyword_scale']}
            subtitle.write_text('', encoding='utf-8-sig')
            has_subtitles = False
        else:
            from .speech_captions import recognize, alignment_valid, HOTWORDS
            try:
                speech=recognize(
                    clean, output_root/f'{candidate_id}.speech', gateway_config=speech_config,
                    prefer_api=speech_config is not None,
                )
            except Exception as exc:
                raise ValueError(f"成片原声转写未完成：{candidate_id}；{exc}") from exc
            cues=_semantic_subtitle_cues(speech['words'], semantic_config=semantic_config)
            actual_duration = probe_media(clean).duration_ms
            if (not cues and settings.get('duration_policy') == 'bounded_15_30') or any(not alignment_valid(c) or c['start_ms'] < 0 or c['end_ms'] > actual_duration for c in cues):
                raise ValueError('成片字幕缺少有效原声对齐，或时间超出视频范围，请核听后重试')
            for cue in cues:
                cue['uncertain']=any(w.get('probability',0)<.65 for w in cue['words']) or bool(re.search(r'[0-9]|尺码|价格|款号|品牌|面料',cue['text']))
            caption_mode = _caption_mode(settings, candidate_index)
            caption_keywords = [] if caption_mode == 'sentence' else item.get('keywords', [])
            document={'cues':cues,'mode':caption_mode,'hotwords':HOTWORDS,'x':50,'y':86,'font':settings.get('subtitle_font','heiti'),'size':settings['subtitle_font_size'],'effect':settings.get('subtitle_effect','none'),'keywords':caption_keywords,'keyword_color':settings['keyword_color'],'keyword_scale':settings['keyword_scale']}
            caption_item = {**item,'keywords':caption_keywords,'subtitle_segments':cues,'preserve_sentence_timing':True,'caption_mode':caption_mode}
            caption_project={**project,'settings':{**settings,'subtitle_x':50,'subtitle_y':86}}
            has_subtitles = _write_subtitles(caption_item, caption_project, subtitle, ass)
        (output_root/f'{candidate_id}.captions.json').write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
        if has_subtitles:
            _run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(clean),
                "-vf", f"ass='{_ass_filter_path(ass)}'", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "21", "-c:a", "copy", "-movflags", "+faststart", str(video),
            ])
            clean.unlink(missing_ok=True)
        else:
            clean.replace(video)
        _validate_rendered_duration(video, item, settings=project['settings'])
        ass.unlink(missing_ok=True)
        _run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.2",
            "-i", str(video), "-frames:v", "1", "-q:v", "2", str(cover),
        ], timeout=120)
    manifest_path = output_root / "manifest.json"
    temporary = output_root / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(manifest_path)
    artifacts = {f"{item['candidate_id']}{suffix}": _sha256_file(output_root / f"{item['candidate_id']}{suffix}")
                 for item in project['plan'] for suffix in ('.mp4', '.srt', '.jpg', '.captions.json')}
    project = store.save_registration_checkpoint(project_id, expected_revision=project['revision'], checkpoint={
        'manifest_path': str(manifest_path), 'manifest_sha256': _sha256_file(manifest_path), 'artifacts': artifacts,
    })
    return _register_checkpoint(store, project, root=root)


def _save_plan_snapshot(root: Path, project: Mapping[str, Any], *, skill_id: str,
                        reason: str, summary: str, plan: list[dict[str, Any]]) -> None:
    """Keep a local diagnostic draft, including rejected plans, without credentials.

    This is evidence only: neither loading a snapshot nor saving one authorizes
    execution. The store and manifest still enforce every source/time constraint.
    """
    identity = json.dumps([project['project_id'], project.get('render_generation'), project['revision']])
    directory = root / 'plan-snapshots'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(identity.encode('utf-8')).hexdigest() + '.json')
    payload = {'project_id': project['project_id'], 'render_generation': project.get('render_generation'),
               'revision': project['revision'], 'settings': project['settings'],
               'selected_skill_id': skill_id, 'selection_reason': reason,
               'analysis_summary': summary, 'plan': plan}
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def process_one(
    store: AutoEditProjectStore, *, available_skills: list[dict[str, Any]], worker_id: str, root: Path,
    planner=None,
) -> dict[str, Any] | None:
    project = store.claim_next(available_skills=available_skills, worker_id=worker_id)
    if project is None:
        return None
    try:
        if project['status'] == 'rendering':
            return render_and_register(store, project, root=root)
        # Recheck after queue wait/recovery: sources can disappear after start.
        from .auto_edit_store import require_generation_ready
        require_generation_ready(project)
        if planner is None:
            selected_skill_id, reason, analysis_summary, plan = analyze_and_plan_with_gateway(project, root=root)
        else:
            selected_skill_id, reason, analysis_summary, plan = planner(project)
        _save_plan_snapshot(root, project, skill_id=selected_skill_id, reason=reason,
                            summary=analysis_summary, plan=plan)
        # Model labels are not trusted filesystem keys. Repair malformed labels
        # deterministically; valid legacy IDs remain compatible with saved data.
        plan = [{**item, 'candidate_id': stable_candidate_id(index, item.get('candidate_id'))}
                for index, item in enumerate(plan, start=1)]
        project = store.save_plan(
            project["project_id"], worker_id=worker_id, selected_skill_id=selected_skill_id,
            reason=reason, analysis_summary=analysis_summary, plan=plan,
        )
        project = store.mark_rendering(project["project_id"], worker_id=worker_id)
        return render_and_register(store, project, root=root)
    except Exception as exc:
        return store.fail(project["project_id"], message=str(exc), worker_id=worker_id)
