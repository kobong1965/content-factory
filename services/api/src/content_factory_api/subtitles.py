"""Speech-only, non-overlapping captions and allowlisted ASS presentation."""
from __future__ import annotations

import ctypes
import os
import re
import subprocess

FONTS = {'heiti': 'SimHei', 'yahei': 'Microsoft YaHei', 'songti': 'SimSun', 'kaiti': 'KaiTi'}
EFFECTS = {'none', 'fade', 'pop'}


def simplify_chinese(text: str) -> str:
    if not text:
        return text
    if os.name != 'nt':
        raise RuntimeError('当前简体字幕转换需要 Windows 中文转换支持')
    mapper = ctypes.windll.kernel32.LCMapStringEx
    mapper.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_longlong]
    mapper.restype = ctypes.c_int
    needed = mapper('zh-CN', 0x02000000, text, -1, None, 0, None, None, 0)
    if not needed:
        raise RuntimeError('字幕简体转换失败')
    result = ctypes.create_unicode_buffer(needed)
    if not mapper('zh-CN', 0x02000000, text, -1, result, needed, None, None, 0):
        raise RuntimeError('字幕简体转换失败')
    # Windows NLS retains this common spoken-language variant despite the
    # simplified-Chinese flag (e.g. 什麼 / 怎麼 in Whisper transcripts).
    return result.value.replace('麼', '么').replace('麽', '么')


def normalize_cues(cues, *, duration_ms: int, silence=(), max_duration_ms=3000):
    ordered = sorted((dict(cue) for cue in cues if str(cue.get('text') or '').strip()), key=lambda cue: int(cue['start_ms']))
    result = []
    for index, cue in enumerate(ordered):
        start = max(0, int(cue['start_ms']))
        # FFmpeg Whisper uses 3-second audio queues; erroneous 30s endings
        # must not keep captions alive over subsequent speech or silence.
        end = min(int(cue['end_ms']), duration_ms)
        if max_duration_ms is not None:
            end = min(end, start + max_duration_ms)
        if index + 1 < len(ordered):
            end = min(end, int(ordered[index + 1]['start_ms']))
        text = simplify_chinese(str(cue['text']).strip())
        if re.fullmatch(r'[\[【（(].*[\]】）)]', text):
            continue  # ASR sound annotations are not spoken dialogue.
        spans = [(start, end)] if end > start else []
        for quiet_start, quiet_end in silence:
            spans = [(a,b) for left,right in spans for a,b in ((left,min(right,quiet_start)),(max(left,quiet_end),right)) if b > a]
        for left, right in spans:
            if right - left >= 80:
                result.append({'start_ms':left, 'end_ms':right, 'text':text})
    return result


def detect_silence(video, duration_ms):
    completed = subprocess.run(['ffmpeg','-hide_banner','-nostdin','-i',str(video),'-vn','-af','silencedetect=noise=-38dB:d=0.25','-f','null','-'],capture_output=True,timeout=600)
    if completed.returncode:
        raise RuntimeError('字幕静音区间检测失败，未生成可能残留文字的成片')
    intervals, start = [], None
    for kind, value in re.findall(r'silence_(start|end):\s*([0-9.]+)', completed.stderr.decode('utf-8',errors='replace')):
        if kind == 'start': start = round(float(value)*1000)
        elif start is not None:
            intervals.append((start,round(float(value)*1000))); start = None
    if start is not None: intervals.append((start,duration_ms))
    return intervals


def subtitle_effect(effect, duration_ms):
    if effect == 'fade':
        amount = min(100,max(0,duration_ms//4))
        return f'{{\\fad({amount},{amount})}}'
    if effect == 'pop':
        amount = min(120,max(0,duration_ms//3))
        return f'{{\\fscx92\\fscy92\\t(0,{amount},\\fscx100\\fscy100)}}'
    return ''
