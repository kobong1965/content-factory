"""Versioned subtitle drafts, clean preview and non-destructive render."""
import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import shutil
import copy
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from . import edit_batches
from .subtitles import FONTS, EFFECTS, simplify_chinese
from .speech_captions import MODES, HOTWORDS, alignment_valid, caption_events, recognize, split_words

router=APIRouter(prefix='/s7/subtitle-editor',tags=['subtitle editor'])
_render_lock=threading.Lock()


def merge_sentence_cues(cues, silence=()):
    result=[]
    for cue in cues:
        cue={**cue,'text':simplify_chinese(cue['text'].strip())}
        if result and 0 <= cue['start_ms']-result[-1]['end_ms'] <= 350 and not any(a < cue['start_ms'] and b > result[-1]['end_ms'] for a,b in silence) and not re.search(r'[。！？!?]$',result[-1]['text']) and len(result[-1]['text']+cue['text'])<=48 and cue['end_ms']-result[-1]['start_ms']<=12000:
            result[-1]['text']+=cue['text']
            result[-1]['end_ms']=cue['end_ms']
        else: result.append(cue)
    return result


def validate_document(doc,duration):
    doc=copy.deepcopy(doc)
    required={'cues','x','y','font','size','effect'}
    if not isinstance(doc,dict) or not required.issubset(doc) or set(doc)-required-{'keywords','keyword_color','keyword_scale','mode','hotwords'}:
        raise ValueError('字幕参数不完整')
    if doc.get('mode','sentence') not in MODES: raise ValueError('字幕显示模式无效')
    if not isinstance(doc.get('hotwords',HOTWORDS),str) or len(doc.get('hotwords',HOTWORDS))>1000: raise ValueError('热词不能超过1000字')
    if not isinstance(doc.get('keywords',[]),list) or len(doc.get('keywords',[]))>50 or any(not isinstance(word,str) or len(word)>40 for word in doc.get('keywords',[])):
        raise ValueError('重点词需为最多50个短词')
    if not isinstance(doc.get('keyword_color','#FFD400'),str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}',doc.get('keyword_color','#FFD400')):
        raise ValueError('重点词颜色无效')
    if not isinstance(doc.get('keyword_scale',1.3),(int,float)) or not 1<=doc.get('keyword_scale',1.3)<=2:
        raise ValueError('重点词倍率无效')
    if doc['font'] not in FONTS or doc['effect'] not in EFFECTS:
        raise ValueError('字幕字体或特效无效')
    for key,low,high in [('x',10,90),('y',10,94),('size',32,120)]:
        if not isinstance(doc[key],(int,float)) or not math.isfinite(doc[key]) or not low<=doc[key]<=high:
            raise ValueError('字幕位置或字号超出安全范围')
    if not isinstance(doc['cues'],list) or len(doc['cues'])>1000:
        raise ValueError('字幕最多1000句')
    end=0
    for cue in doc['cues']:
        if not isinstance(cue,dict) or not {'start_ms','end_ms','text'}.issubset(cue) or set(cue)-{'start_ms','end_ms','text','words','uncertain','original_text'} or not isinstance(cue['text'],str) or not 1<=len(cue['text'].strip())<=200:
            raise ValueError('每句字幕需为1—200字，不能留空')
        if type(cue['start_ms']) is not int or type(cue['end_ms']) is not int or not end<=cue['start_ms']<cue['end_ms']<=duration:
            raise ValueError('字幕时间不能重叠、逆序或超出视频，请调整时间后保存')
        end=cue['end_ms']
        cue['text']=simplify_chinese(cue['text'].strip())
        if 'words' in cue and not alignment_valid(cue):
            cue.pop('words',None)  # Keep the user's text, but stale timing cannot render karaoke.
    return {**doc,'cues':[{**cue,'text':simplify_chinese(cue['text'].strip())} for cue in doc['cues']]}


def root():
    path=Path(os.environ.get('CONTENT_FACTORY_S7_DATA_DIR',Path(__file__).resolve().parents[4]/'data/s7'))/'subtitle-editor'
    path.mkdir(parents=True,exist_ok=True)
    return path


def identity(batch,candidate):
    return hashlib.sha256(f'{batch}/{candidate}'.encode()).hexdigest()[:32]


def database():
    db=sqlite3.connect(root()/'drafts.sqlite3',timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS history (id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(id,revision))')
    return db


def source_candidate(batch_id,candidate_id,verify=True):
    with edit_batches._db() as db: batch=edit_batches._read(db,batch_id)
    candidate=next((c for c in batch['candidates'] if c['id']==candidate_id),None)
    if not candidate: raise HTTPException(404,'找不到成片')
    if verify:edit_batches._verify_resources([candidate])
    return batch,candidate


def validate_layout(doc):
    width=(min(doc['x'],100-doc['x'])*2-10)*10.8
    capacity=max(1,int(width/doc['size']/doc.get('keyword_scale',1.3)))
    if any(len(c['text'])>capacity*2 for c in doc['cues']):
        raise ValueError('当前位置和字号会让字幕超过两行，请把字幕移近水平中央或拆分短句')


def read_srt(path):
    value=Path(path).read_text(encoding='utf-8-sig')
    cues=[]
    def ms(parts): return ((int(parts[0])*60+int(parts[1]))*60+int(parts[2]))*1000+int(parts[3])
    for block in re.split(r'\r?\n\s*\r?\n',value.strip()):
        match=re.search(r'(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)\r?\n([\s\S]+)',block)
        if match: cues.append({'start_ms':ms(match.groups()[:4]),'end_ms':ms(match.groups()[4:8]),'text':match[9].replace('\n',' ').strip()})
    return cues


def inherit_draft(batch_id,candidate_id,document):
    """Seed the rendered revision without overwriting subsequent user edits."""
    db=database()
    try:
        with db:
            db.execute('INSERT OR IGNORE INTO drafts VALUES (?,?,?)',(identity(batch_id,candidate_id),1,json.dumps(document,ensure_ascii=False)))
    finally: db.close()


@router.get('/{batch_id}/{candidate_id}')
def get_draft(batch_id:str,candidate_id:str):
    _,candidate=source_candidate(batch_id,candidate_id)
    key=identity(batch_id,candidate_id)
    db=database()
    try:
        row=db.execute('SELECT revision,payload FROM drafts WHERE id=?',(key,)).fetchone()
    finally: db.close()
    if row: document=json.loads(row[1]); revision=row[0]
    else:
        from .subtitles import normalize_cues
        cues=normalize_cues(read_srt(candidate['resources']['subtitle']['path']),duration_ms=candidate['duration_ms'], max_duration_ms=None)
        document={'cues':cues,'x':50,'y':86,'font':'yahei','size':68,'effect':'none','mode':'reveal','hotwords':HOTWORDS}
        if batch_id.startswith('auto_'):
            from .auto_edit import get_auto_edit_store
            from .auto_edit_store import AutoEditNotFoundError
            try: project=get_auto_edit_store().get_project('auto_edit_'+batch_id.removeprefix('auto_'))
            except AutoEditNotFoundError: project=None
            if project:
                settings=project['settings']
                item=next((p for p in project['plan'] if p['candidate_id']==candidate_id),{})
                document.update(font=settings.get('subtitle_font','heiti'),size=settings['subtitle_font_size'],effect=settings.get('subtitle_effect','none'),keywords=[simplify_chinese(word) for word in item.get('keywords',[])],keyword_color=settings['keyword_color'],keyword_scale=settings['keyword_scale'])
        revision=0
    return {'revision':revision,'document':document,'duration_ms':candidate['duration_ms']}


class DraftRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int
    document:dict


@router.put('/{batch_id}/{candidate_id}')
def save_draft(batch_id:str,candidate_id:str,request:DraftRequest):
    _,candidate=source_candidate(batch_id,candidate_id)
    try: document=validate_document(request.document,candidate['duration_ms'])
    except (ValueError,TypeError,KeyError) as exc: raise HTTPException(422,str(exc)) from exc
    db=database()
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            key=identity(batch_id,candidate_id)
            row=db.execute('SELECT revision,payload FROM drafts WHERE id=?',(key,)).fetchone()
            revision=row[0] if row else 0
            if revision!=request.revision: raise HTTPException(409,'字幕已在其他窗口更新，当前修改已保留，请重新打开后核对')
            if row: db.execute('INSERT OR IGNORE INTO history VALUES (?,?,?)',(key,row[0],row[1]))
            db.execute('INSERT OR REPLACE INTO drafts VALUES (?,?,?)',(key,revision+1,json.dumps(document,ensure_ascii=False)))
            db.execute('INSERT OR IGNORE INTO history VALUES (?,?,?)',(key,revision+1,json.dumps(document,ensure_ascii=False)))
    finally: db.close()
    return {'revision':revision+1,'document':document,'duration_ms':candidate['duration_ms']}


@router.get('/{batch_id}/{candidate_id}/history')
def history(batch_id:str,candidate_id:str):
    _,candidate=source_candidate(batch_id,candidate_id)
    db=database()
    try: rows=db.execute('SELECT revision,payload FROM history WHERE id=? ORDER BY revision DESC',(identity(batch_id,candidate_id),)).fetchall()
    finally:db.close()
    return {'original':read_srt(candidate['resources']['subtitle']['path']),'versions':[{'revision':r,'document':json.loads(p)} for r,p in rows]}


@router.post('/{batch_id}/{candidate_id}/speech')
def speech(batch_id:str,candidate_id:str,request:DraftRequest,align:bool=False,cue_index:int|None=None):
    _,candidate=source_candidate(batch_id,candidate_id)
    try: doc=validate_document(request.document,candidate['duration_ms'])
    except (ValueError,TypeError,KeyError) as exc: raise HTTPException(422,str(exc)) from exc
    with _render_lock:
        clean=prepare_clean(batch_id,candidate_id)
        folder=root()/identity(batch_id,candidate_id)/'speech'
        offset=0
        if cue_index is not None:
            if align or not 0<=cue_index<len(doc['cues']):raise HTTPException(422,'复核片段无效')
            from .auto_edit_worker import _run
            folder.mkdir(parents=True,exist_ok=True)
            cue=doc['cues'][cue_index]
            offset=max(0,cue['start_ms']-1200)
            end=min(candidate['duration_ms'],cue['end_ms']+1200)
            excerpt=folder/'review-context.wav'
            _run(['ffmpeg','-v','error','-y','-ss',str(offset/1000),'-i',str(clean),'-t',str((end-offset)/1000),'-vn','-ar','16000','-ac','1',str(excerpt)])
            clean=excerpt
        try: result=recognize(clean,folder,cues=doc['cues'] if align else None,hotwords=doc.get('hotwords',HOTWORDS))
        except (ValueError,RuntimeError) as exc:raise HTTPException(422,str(exc)) from exc
    # Read the final edited audio, so cut/concat offsets are already resolved.
    max_chars=max(8,min(14,int(1944/doc['size']/max(1,doc.get('keyword_scale',1.3)))))
    words=result['words']
    if cue_index is not None:
        words=[{**w,'start_ms':max(cue['start_ms'],w['start_ms']+offset),'end_ms':min(cue['end_ms'],w['end_ms']+offset)} for w in words if cue['start_ms']<w['end_ms']+offset and w['start_ms']+offset<cue['end_ms']]
    def safe_split(units):
        try:return split_words(units,max_chars=max_chars)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    cues=safe_split(words)
    if align:
        # Keep user split/merge decisions when they already fit short captions.
        cues=[]
        for original in doc['cues']:
            aligned=[w for w in words if original['start_ms']<=w['start_ms'] and w['end_ms']<=original['end_ms']]
            if ''.join(w['text'] for w in aligned)!=original['text']:raise HTTPException(422,'对齐后的文字不完整，当前草稿未覆盖')
            cues.extend(safe_split(aligned) if len(original['text'])>max_chars else [{**original,'words':aligned}])
    for cue in cues:
        cue['uncertain']=any(w.get('probability',0)<.65 for w in cue['words']) or bool(re.search(r'[\d一二三四五六七八九十百千两]+|尺码|价格|款号|品牌|面料',cue['text']))
    if cue_index is not None:
        if not cues:raise HTTPException(422,'该片段没有可靠识别结果，原字幕保留，请核听')
        cues=doc['cues'][:cue_index]+cues+doc['cues'][cue_index+1:]
    return {'document':{**doc,'cues':cues,'mode':doc.get('mode','reveal')},'engine':result['engine'],'original':doc['cues'],'raw_segments':result['raw_segments']}


@router.post('/{batch_id}/{candidate_id}/events')
def events(batch_id:str,candidate_id:str,request:DraftRequest):
    # Preview updates on every edit; hashing multi-GB sources here blocks typing.
    # Save, audio review and render still verify the actual source resources.
    _,candidate=source_candidate(batch_id,candidate_id,verify=False)
    try:
        doc=validate_document(request.document,candidate['duration_ms'])
        validate_layout(doc)
        return {'events':caption_events(doc['cues'],doc.get('mode','sentence'))}
    except (ValueError,TypeError,KeyError) as exc: raise HTTPException(422,str(exc)) from exc


def prepare_clean(batch_id,candidate_id):
    from .auto_edit_worker import _run
    _,candidate=source_candidate(batch_id,candidate_id)
    folder=root()/identity(batch_id,candidate_id)
    folder.mkdir(exist_ok=True)
    target=folder/'clean.mp4'
    if target.is_file(): return target
    parts=[]
    for i,clip in enumerate(candidate['clips']):
        part=folder/f'part-{i}.mp4'
        _run(['ffmpeg','-v','error','-y','-ss',str(clip['start_ms']/1000),'-i',candidate['source_path'],'-t',str((clip['end_ms']-clip['start_ms'])/1000),'-map','0:v:0','-map','0:a:0','-c:v','libx264','-preset','veryfast','-crf','21','-c:a','aac',str(part)])
        parts.append(part)
    concat=folder/'concat.txt'
    concat.write_text('\n'.join(f"file '{p.name}'" for p in parts),encoding='utf-8')
    temporary=folder/'clean.partial.mp4'
    _run(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(concat),'-c','copy','-movflags','+faststart',str(temporary)])
    temporary.replace(target)
    return target


@router.post('/{batch_id}/{candidate_id}/prepare')
def prepare(batch_id:str,candidate_id:str):
    with _render_lock: prepare_clean(batch_id,candidate_id)
    return {'ready':True}


@router.get('/{batch_id}/{candidate_id}/preview')
def preview(batch_id:str,candidate_id:str):
    source_candidate(batch_id,candidate_id)
    file=root()/identity(batch_id,candidate_id)/'clean.mp4'
    if not file.is_file(): raise HTTPException(409,'请先准备预览')
    return FileResponse(file,media_type='video/mp4')


@router.post('/{batch_id}/{candidate_id}/suggest')
def suggest(batch_id:str,candidate_id:str,request:DraftRequest):
    from .s3_settings import GatewaySettingsStore
    from .s3_gateway import call_gateway
    _,candidate=source_candidate(batch_id,candidate_id)
    try: document=validate_document(request.document,candidate['duration_ms'])
    except (ValueError,TypeError,KeyError) as exc: raise HTTPException(422,str(exc)) from exc
    gateway=GatewaySettingsStore(os.environ['CONTENT_FACTORY_S3_CONFIG_PATH']).load() if os.environ.get('CONTENT_FACTORY_S3_CONFIG_PATH') else GatewaySettingsStore().load()
    if not gateway or not gateway.has_purpose('video_review'): raise HTTPException(422,'请先配置模型连接')
    cues=document['cues']
    if not cues: raise HTTPException(422,'没有可校对的字幕')
    schema={'type':'object','additionalProperties':False,'required':['suggestions'],'properties':{'suggestions':{'type':'array','minItems':len(cues),'maxItems':len(cues),'items':{'type':'object','additionalProperties':False,'required':['index','text','reason','uncertain'],'properties':{'index':{'type':'integer','minimum':0,'maximum':len(cues)-1},'text':{'type':'string','minLength':1,'maxLength':200},'reason':{'type':'string'},'uncertain':{'type':'boolean'}}}}}}
    result=call_gateway(gateway.for_purpose('video_review'),context_json=json.dumps({'subtitles':list(enumerate(cues)),'context':candidate['hook']},ensure_ascii=False),keyframe_data_urls=[],output_schema=schema,timeout_seconds=180,schema_name='subtitle_proofread',developer_instructions='你是中文口播字幕校对员。你只看到了ASR文本和上下文，没有听到音频，不能声称已经核听。逐句思考谐音、服装语境与前后文，给出最保守的简体修正建议。禁止新增卖点或补猜价格尺码款号品牌数字；含糊处保留原文并标uncertain=true、说明需核听。不得改写为广告文案，不改变句子数量和索引。每个index恰好一次。').content
    suggestions=result.get('suggestions',[])
    if not isinstance(suggestions,list) or any(not isinstance(s,dict) or type(s.get('index')) is not int or not isinstance(s.get('text'),str) or not 1<=len(s['text'].strip())<=200 or not isinstance(s.get('reason'),str) or type(s.get('uncertain')) is not bool for s in suggestions):
        raise HTTPException(422,'模型返回的校对建议无效，原字幕未修改')
    if sorted(s['index'] for s in suggestions)!=list(range(len(cues))): raise HTTPException(422,'模型返回的字幕索引不完整，原字幕未修改')
    for suggestion in suggestions:
        suggestion['text']=simplify_chinese(suggestion['text'])
    return {'suggestions':suggestions}


@router.post('/{batch_id}/{candidate_id}/render')
def render(batch_id:str,candidate_id:str,request:DraftRequest):
    from .auto_edit_worker import _run,_write_subtitles,_ass_filter_path
    current=get_draft(batch_id,candidate_id)
    if current['revision']!=request.revision or current['document']!=request.document or not request.revision:
        raise HTTPException(409,'请先保存字幕草稿再生成新版')
    batch,candidate=source_candidate(batch_id,candidate_id)
    doc=validate_document(current['document'],candidate['duration_ms'])
    try:
        validate_layout(doc)
        caption_events(doc['cues'],doc.get('mode','sentence'))
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    if any(len(c['text'])>28 for c in doc['cues']): raise HTTPException(422,'仍有长段字幕，请先按原音频重新识别分段或拆分，再导出')
    with _render_lock:
        clean=prepare_clean(batch_id,candidate_id)
        new_id='sub_'+identity(batch_id,candidate_id)+'_'+str(request.revision)
        folder=root()/new_id; folder.mkdir(exist_ok=True)
        manifest_path=folder/'manifest.json'
        if manifest_path.is_file():
            result=edit_batches.import_batch(edit_batches.ImportRequest(manifest_path=str(manifest_path.resolve())))
            inherit_draft(new_id,candidate_id,doc)
            return result
        settings={'subtitle_font_size':doc['size'],'subtitle_font':doc['font'],'subtitle_effect':doc['effect'],'subtitle_x':doc['x'],'subtitle_y':doc['y'],'keyword_color':doc.get('keyword_color','#FFD400'),'keyword_scale':doc.get('keyword_scale',1.3)}
        has_subtitles=_write_subtitles({'subtitle_segments':doc['cues'],'preserve_sentence_timing':True,'caption_mode':doc.get('mode','sentence'),'keywords':doc.get('keywords',[])}, {'settings':settings},folder/'subtitles.srt',folder/'subtitles.ass')
        if has_subtitles:
            _run(['ffmpeg','-v','error','-y','-i',str(clean),'-vf',f"ass='{_ass_filter_path(folder/'subtitles.ass')}'",'-c:v','libx264','-preset','veryfast','-crf','21','-c:a','copy','-movflags','+faststart',str(folder/'video.mp4')])
        else:
            shutil.copyfile(clean,folder/'video.mp4')
        _run(['ffmpeg','-v','error','-y','-ss','0.2','-i',str(folder/'video.mp4'),'-frames:v','1',str(folder/'cover.jpg')])
        item={key:candidate[key] for key in edit_batches.Candidate.model_fields}
        item.update(video_path='video.mp4',subtitle_path='subtitles.srt',cover_path='cover.jpg',review_notes=['字幕人工编辑新版，需重新审核。'])
        payload={'schema_version':1,'id':new_id,'title':batch['title']+' · 字幕修改','analysis_summary':batch['analysis_summary'],'candidates':[item]}
        temporary=folder/'manifest.tmp';temporary.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8');temporary.replace(manifest_path)
        result=edit_batches.import_batch(edit_batches.ImportRequest(manifest_path=str(manifest_path.resolve())))
        inherit_draft(new_id,candidate_id,doc)
        if batch.get('sku'):
            result=edit_batches.classify_batch(result['id'],edit_batches.ProductRequest(revision=result['revision'],sku=batch['sku']))
        return result
