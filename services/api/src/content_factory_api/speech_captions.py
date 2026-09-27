"""Short captions derived from acoustic units, never evenly spaced text."""
import re
import threading
import json
import os
import sys
import subprocess
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from .subtitles import simplify_chinese

MODES = {'sentence', 'reveal', 'highlight'}
HOTWORDS = '裤子 裤脚口 裤腰 裤长 弹力 尺码 腰头 裤脚 九分裤 长裤 面料 松紧 直筒 裤型'
_speech_lock=threading.Lock()


class SpeechRecognitionError(ValueError):
    """A safe, user-facing speech failure with a stable diagnostic code."""

    def __init__(self, message: str, *, code: str, cause: Exception | None = None) -> None:
        self.code = code
        self.cause = cause
        super().__init__(message)


def split_words(words, max_chars=14):
    if not 4 <= max_chars <= 28:
        raise ValueError('短句字数应为4—28')
    words=[dict(w) for w in words if w.get('text')]
    full=''.join(w['text'] for w in words)
    forbidden=set()
    terms=HOTWORDS.split()+['告诉你','因为','很多','大哥','担心','太小了','穿不了','已经','最小码','买的码','越大','越宽','只有','看一下','看到没有','这个','齐平','知道吗','然后','穿到','身上','的话','好穿又好脱','好穿','好脱','不会','我们家','人家','对比一下']
    for term in terms:
        for match in re.finditer(re.escape(term),full): forbidden.update(range(match.start()+1,match.end()))
    # Sentence-final particles stay with the preceding phrase, never orphaned.
    forbidden.update(m.start() for m in re.finditer(r'[了的吗呢吧啊呀哦]',full) if m.start()>0)
    forbidden.update(m.end() for m in re.finditer(r'[他她它我你](?=[\u3400-\u9fff])',full))
    preferred={m.start() for m in re.finditer(r'因为|我的裤子|我告诉你|我这个|你看一下|然后|知道吗|不会|我拿的|你买的|我给你|裤脚口|看到没有|这个他|他好穿|它好穿',full)}
    position=0
    for word in words:
        position+=len(word['text'])
        if word.get('break_after') is False and position not in preferred:forbidden.add(position)
    groups, current = [], []
    def commit():
        if current:
            groups.append({'start_ms':current[0]['start_ms'],'end_ms':current[-1]['end_ms'],
                           'text':''.join(w['text'] for w in current),'words':current.copy()})
            current.clear()
    start=0; offset=0
    while start<len(words):
        length=0; candidates=[]
        for end in range(start,len(words)):
            length+=len(words[end]['text'])
            if length>max_chars: break
            boundary=offset+length
            if boundary not in forbidden:
                gap=words[end+1]['start_ms']-words[end]['end_ms'] if end+1<len(words) else 999
                natural=gap>=350 or bool(re.search(r'[。！？!?，,；;]$',words[end]['text']))
                candidates.append((end+1,length,natural or boundary in preferred))
                if natural or (length>=3 and boundary in preferred):break
            if length>=max_chars:break
        if not candidates:
            raise ValueError('字词单位过长，无法安全分句，请人工拆分并重新对齐')
        selected=next((c for c in reversed(candidates) if c[2] and c[1]>=3),candidates[-1])
        stop,count,_=selected
        current.extend(words[start:stop]);commit()
        start=stop;offset+=count
    return [c for c in groups if c['text']]


def alignment_valid(cue):
    words=cue.get('words')
    if not isinstance(words,list) or not words or any(not isinstance(w,dict) or not isinstance(w.get('text'),str) for w in words) or ''.join(w['text'] for w in words)!=cue['text']:
        return False
    end=cue['start_ms']
    for word in words:
        if not isinstance(word.get('text'),str) or not word['text'] or type(word.get('start_ms')) is not int or type(word.get('end_ms')) is not int:
            return False
        if not end<=word['start_ms']<word['end_ms']<=cue['end_ms']: return False
        end=word['end_ms']
    return True


def display_events(cue, mode):
    if mode not in MODES: raise ValueError('字幕显示模式无效')
    if mode=='sentence': return [{k:cue[k] for k in ('start_ms','end_ms','text')}]
    if not alignment_valid(cue): raise ValueError('字幕已修改或缺少字词时间，请先按原音频重新对齐')
    events=[]; prefix=''
    for index,word in enumerate(cue['words']):
        prefix+=word['text']
        end=cue['words'][index+1]['start_ms'] if index+1<len(cue['words']) else cue['end_ms']
        if end<=word['start_ms']: continue
        event={'start_ms':word['start_ms'],'end_ms':end,'text':prefix if mode=='reveal' else cue['text']}
        if mode=='highlight': event['highlight']=prefix
        events.append(event)
    return events


def caption_events(cues,mode):
    return [event for cue in cues for event in display_events(cue,mode)]


def _recognize_local(audio, workdir, *, cues=None, hotwords=HOTWORDS, word_timestamps_only=False):
    """Run the separately installed ASR environment without polluting the API."""
    folder=Path(workdir);folder.mkdir(parents=True,exist_ok=True)
    job=folder/('speech-'+uuid.uuid4().hex+'.json')
    output=job.with_suffix('.result.json')
    model=Path(os.environ.get('CONTENT_FACTORY_SPEECH_MODEL','E:/Codex工作盘/downloads/models/content-factory-large-v3-turbo'))
    if not (model/'model.bin').is_file():
        raise SpeechRecognitionError('本地精确字幕模型未安装，无法生成语音对齐字幕', code='local_model_missing')
    job.write_text(json.dumps({'audio':str(audio),'model':str(model),'output':str(output),'cues':cues,'hotwords':hotwords,'word_timestamps_only':word_timestamps_only},ensure_ascii=False),encoding='utf-8')
    env=os.environ.copy()
    from .deployment_paths import cache_root
    speech_temp = cache_root('speech')
    speech_temp.mkdir(parents=True, exist_ok=True)
    env.update(PYTHONPATH=os.environ.get('CONTENT_FACTORY_SPEECH_RUNTIME','E:/Codex工作盘/runtimes/content-factory-asr'),PYTHONDONTWRITEBYTECODE='1',HF_HUB_OFFLINE='1',HF_HOME=str(cache_root('huggingface')),TEMP=str(speech_temp),TMP=str(speech_temp),TMPDIR=str(speech_temp))
    script=Path(__file__).resolve().parents[4]/'scripts'/'speech-caption-worker.py'
    try:
        with _speech_lock:
            interpreter=os.environ.get('CONTENT_FACTORY_SPEECH_PYTHON',sys.executable)
            result=subprocess.run([interpreter,'-B',str(script),'--job',str(job)],env=env,capture_output=True,timeout=1800)
    except subprocess.TimeoutExpired as exc:
        raise SpeechRecognitionError('音频复核超时，原字幕已保留，可缩小片段后重试', code='local_asr_timeout', cause=exc) from exc
    if result.returncode or not output.is_file():
        (job.with_suffix('.error.log')).write_bytes(result.stderr)
        detail = result.stderr.decode('utf-8', errors='replace').strip()[-300:]
        lower_detail = detail.lower()
        if any(marker in lower_detail for marker in (
            'mkl_malloc', 'failed to allocate memory', 'out of memory',
            'cannot allocate memory',
        )):
            code = 'local_asr_memory'
            message = '本地语音识别模型内存不足，无法完成字幕对齐；请关闭占内存程序、改用较小本地模型，或配置真正支持 /audio/transcriptions 的 API。'
        else:
            code = 'local_asr_repetition' if '异常重复' in detail else 'local_asr_runtime'
            message = '原音频识别/对齐失败，字幕未覆盖。请检查本地模型与运行时；日志已保存。'
        raise SpeechRecognitionError(
            message,
            code=code,
        )
    payload=json.loads(output.read_text(encoding='utf-8'))
    for unit in payload.get('words',[]): unit['text']=simplify_chinese(unit['text'])
    return payload


def _speech_endpoint(config) -> str:
    return _speech_endpoint_candidates(config)[0]


def _speech_endpoint_candidates(config) -> list[str]:
    """Return compatible transcription endpoints without exposing credentials.

    Relays are commonly saved as either ``https://host`` or ``https://host/v1``;
    OpenAI-compatible transcription routes therefore need both forms when the
    configured URL does not already include a version prefix.  Keep the exact
    configured endpoint first so an explicit provider path always wins.
    """
    base = str(config.base_url).rstrip('/')
    base = re.sub(r'/(?:responses|chat/completions)$', '', base, flags=re.IGNORECASE)
    if re.search(r'/audio/transcriptions$', base, flags=re.IGNORECASE):
        return [base]
    candidates = [f'{base}/audio/transcriptions']
    if not re.search(r'/v\d+(?:\.\d+)?$', base, flags=re.IGNORECASE):
        candidates.append(f'{base}/v1/audio/transcriptions')
    return list(dict.fromkeys(candidates))


def _multipart_audio(audio: Path, *, model: str, hotwords: str) -> tuple[bytes, str]:
    boundary = f'----ContentFactory{uuid.uuid4().hex}'
    chunks: list[bytes] = []

    def field(name: str, value: str) -> None:
        chunks.extend([
            f'--{boundary}\r\n'.encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode('utf-8'), b'\r\n',
        ])

    field('model', model)
    field('language', 'zh')
    field('response_format', 'verbose_json')
    field('timestamp_granularities[]', 'word')
    if hotwords:
        prompt = hotwords if isinstance(hotwords, str) else ','.join(str(word) for word in hotwords)
        field('prompt', prompt)
    content_type = 'audio/mp4' if audio.suffix.lower() in {'.mp4', '.m4v', '.mov'} else 'audio/mpeg'
    chunks.extend([
        f'--{boundary}\r\n'.encode(),
        f'Content-Disposition: form-data; name="file"; filename="{audio.name}"\r\n'.encode(),
        f'Content-Type: {content_type}\r\n\r\n'.encode(),
        audio.read_bytes(), b'\r\n', f'--{boundary}--\r\n'.encode(),
    ])
    return b''.join(chunks), boundary


def _api_timing(payload: dict[str, object], *, require_timestamps: bool = True) -> dict[str, object]:
    """Normalize OpenAI-compatible verbose transcription timing."""
    raw_segments = payload.get('segments') if isinstance(payload.get('segments'), list) else []
    raw_words = payload.get('words') if isinstance(payload.get('words'), list) else []

    def ms(value: object) -> int:
        try:
            number = float(value or 0)
        except (TypeError, ValueError):
            return 0
        return round(number * 1000) if number < 10000 else round(number)

    words: list[dict[str, object]] = []
    for item in raw_words:
        if not isinstance(item, dict):
            continue
        text = simplify_chinese(str(item.get('word') or item.get('text') or '').strip())
        start, end = ms(item.get('start')), ms(item.get('end'))
        if text and 0 <= start < end:
            words.append({'text': text, 'start_ms': start, 'end_ms': end, 'probability': float(item.get('probability') or 0.8)})
    if not words:
        # Some compatible services return segment timestamps only. Treat each
        # complete segment as one observed unit; never invent per-character time.
        for item in raw_segments:
            if not isinstance(item, dict):
                continue
            text = simplify_chinese(str(item.get('text') or '').strip())
            start, end = ms(item.get('start')), ms(item.get('end'))
            if text and 0 <= start < end:
                words.append({'text': text, 'start_ms': start, 'end_ms': end, 'probability': 0.75})
    segments = []
    for item in raw_segments:
        if isinstance(item, dict):
            text = simplify_chinese(str(item.get('text') or '').strip())
            start, end = ms(item.get('start')), ms(item.get('end'))
            if text and 0 <= start < end:
                segments.append({'text': text, 'start_ms': start, 'end_ms': end})
    if not segments and words:
        segments = [{'text': ''.join(str(item['text']) for item in words), 'start_ms': words[0]['start_ms'], 'end_ms': words[-1]['end_ms']}]
    if not words and require_timestamps:
        raise SpeechRecognitionError('API 没有返回可用的语音时间信息', code='speech_api_missing_timestamps')
    duration_ms = ms(payload.get('duration')) or (max(int(item['end_ms']) for item in words) if words else 0)
    return {'engine': 'openai-compatible-transcription', 'alignment': 'api-word-timestamps',
            'duration_ms': duration_ms, 'raw_segments': segments, 'words': words, 'recognition_attempts': []}


def _recognize_api(audio, workdir, *, config, hotwords=HOTWORDS, require_timestamps=True):
    del workdir
    source = Path(audio)
    body, boundary = _multipart_audio(source, model=str(config.model), hotwords=hotwords)
    last_endpoint_error: HTTPError | None = None
    last_network_error: BaseException | None = None
    for endpoint in _speech_endpoint_candidates(config):
        request = Request(endpoint, data=body, method='POST', headers={
            'Authorization': f'Bearer {config.api_key}',
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Accept': 'application/json',
        })
        try:
            with urlopen(request, timeout=600) as response:
                payload = json.loads(response.read().decode('utf-8'))
            if not isinstance(payload, dict):
                raise SpeechRecognitionError('API 语音识别返回格式不正确', code='speech_api_invalid_response')
            return _api_timing(payload, require_timestamps=require_timestamps)
        except HTTPError as exc:
            if exc.code in {404, 405}:
                last_endpoint_error = exc
                continue
            raise SpeechRecognitionError(f'API 语音识别请求失败（HTTP {exc.code}）', code='speech_api_http_error', cause=exc) from exc
        except (URLError, TimeoutError, OSError) as exc:
            last_network_error = exc
            continue
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SpeechRecognitionError('API 语音识别返回不是有效 JSON', code='speech_api_invalid_response', cause=exc) from exc
    if last_endpoint_error is not None:
        raise SpeechRecognitionError(
            'API 未提供 /audio/transcriptions 音频转写接口，当前模型不能作为语音识别备用',
            code='speech_api_endpoint_missing', cause=last_endpoint_error,
        ) from last_endpoint_error
    if last_network_error is not None:
        raise SpeechRecognitionError('API 语音识别网络或接口不可用', code='speech_api_unavailable', cause=last_network_error) from last_network_error
    raise SpeechRecognitionError('API 语音识别网络或接口不可用', code='speech_api_unavailable')


def _fallback_gateway_config():
    """Use only an explicitly routed speech model for audio fallback.

    A text/image video-review model is not an ASR model. Sending audio to it
    produced misleading 404/network errors, so legacy routes are deliberately
    no longer promoted to speech capability.
    """
    from .s3_settings import GatewaySettingsStore
    settings_path = os.environ.get('CONTENT_FACTORY_S3_CONFIG_PATH')
    gateway = GatewaySettingsStore(settings_path).load() if settings_path else GatewaySettingsStore().load()
    if gateway is None:
        return None
    for purpose in ('speech',):
        if gateway.has_purpose(purpose):
            return gateway.for_purpose(purpose)
    return None


def recognize(audio, workdir, *, cues=None, hotwords=HOTWORDS, word_timestamps_only=False, gateway_config=None, prefer_api=False):
    """Recognize speech with a configured API or the local ASR fallback.

    The API path is OpenAI-compatible ``/audio/transcriptions``. Only an
    explicitly routed audio-capable model is eligible for this fallback;
    saved text/image video-review credentials are never reused for audio.

    ``prefer_api`` is used by automatic subtitle generation when the user has
    deliberately configured an audio relay. It makes the chosen relay the
    source of the word timestamps, while retaining local ASR as a bounded
    fallback if that request is temporarily unavailable.
    """
    local_error = None
    api_first_error = None
    config = gateway_config or _fallback_gateway_config()
    if prefer_api and config is not None and cues is None:
        try:
            return _recognize_api(audio, workdir, config=config, hotwords=hotwords)
        except SpeechRecognitionError as exc:
            api_first_error = exc
    try:
        return _recognize_local(audio, workdir, cues=cues, hotwords=hotwords, word_timestamps_only=word_timestamps_only)
    except SpeechRecognitionError as exc:
        local_error = exc
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        # A missing Python/FFmpeg runtime or malformed worker response is also
        # a local-model capability failure. Keep the original exception out of
        # the UI while allowing the configured API fallback to run.
        local_error = SpeechRecognitionError(
            '本地语音识别运行时不可用，无法完成字幕对齐',
            code='local_asr_runtime', cause=exc,
        )
    if config is not None and cues is None:
        try:
            return _recognize_api(audio, workdir, config=config, hotwords=hotwords)
        except SpeechRecognitionError as api_error:
            local_message = str(local_error) if local_error else '本地语音识别失败'
            if api_first_error is not None:
                api_error = api_first_error
            raise SpeechRecognitionError(
                f'本地语音识别失败：{local_message}；API 语音识别也不可用：{api_error}',
                code='speech_recognition_failed', cause=api_error,
            ) from api_error
    if api_first_error is not None:
        local_message = str(local_error) if local_error else '本地语音识别失败'
        raise SpeechRecognitionError(
            f'API 语音识别失败：{api_first_error}；本地语音识别也不可用：{local_message}',
            code='speech_recognition_failed', cause=api_first_error,
        ) from api_first_error
    if local_error is not None and local_error.code == 'local_asr_memory':
        raise SpeechRecognitionError(
            str(local_error), code='speech_recognition_failed', cause=local_error,
        ) from local_error
    raise SpeechRecognitionError(
        '本地语音识别失败，且未配置可用的 API 语音识别模型；字幕未覆盖。请安装本地语音模型，或在模型连接中配置支持 /audio/transcriptions 的 API。',
        code='speech_recognition_failed', cause=local_error,
    ) from local_error
