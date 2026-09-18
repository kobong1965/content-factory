"""Short captions derived from acoustic units, never evenly spaced text."""
import re
import threading
from .subtitles import simplify_chinese

MODES = {'sentence', 'reveal', 'highlight'}
HOTWORDS = '裤子 裤脚口 裤腰 裤长 弹力 尺码 腰头 裤脚 九分裤 长裤 面料 松紧 直筒 裤型'
_speech_lock=threading.Lock()


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


def recognize(audio, workdir, *, cues=None, hotwords=HOTWORDS):
    """Run the separately installed ASR environment without polluting the API."""
    import os, sys, subprocess, json, uuid
    from pathlib import Path
    folder=Path(workdir);folder.mkdir(parents=True,exist_ok=True)
    job=folder/('speech-'+uuid.uuid4().hex+'.json')
    output=job.with_suffix('.result.json')
    model=Path(os.environ.get('CONTENT_FACTORY_SPEECH_MODEL','E:/Codex工作盘/downloads/models/content-factory-large-v3-turbo'))
    if not (model/'model.bin').is_file(): raise ValueError('本地精确字幕模型未安装，无法生成语音对齐字幕')
    job.write_text(json.dumps({'audio':str(audio),'model':str(model),'output':str(output),'cues':cues,'hotwords':hotwords},ensure_ascii=False),encoding='utf-8')
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
    except subprocess.TimeoutExpired as exc:raise ValueError('音频复核超时，原字幕已保留，可缩小片段后重试') from exc
    if result.returncode or not output.is_file():
        (job.with_suffix('.error.log')).write_bytes(result.stderr)
        raise ValueError('原音频识别/对齐失败，字幕未覆盖。请检查本地模型与运行时；日志已保存。')
    payload=json.loads(output.read_text(encoding='utf-8'))
    for unit in payload.get('words',[]): unit['text']=simplify_chinese(unit['text'])
    return payload
