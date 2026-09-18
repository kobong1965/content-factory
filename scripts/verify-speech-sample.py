"""User-requested exact 0--20.908s regression sample; outputs never replace originals."""
import json
import subprocess
from pathlib import Path
from content_factory_api.speech_captions import recognize,split_words,caption_events
from content_factory_api.auto_edit_worker import _write_subtitles,_ass_filter_path

ROOT=Path('E:/Codex工作盘/artifacts/test-builds/subtitle-0.1.28')
ROOT.mkdir(parents=True,exist_ok=True)
SOURCE=Path('E:/Codex项目盘/男装编剪器/data/s7/auto-edit/sources/ba8415c00128464d88169b8ac5892e7f/00.mp4')
clean=ROOT/'clean.mp4'
if not clean.exists():
    subprocess.run(['ffmpeg','-v','error','-y','-ss','229.388','-i',str(SOURCE),'-t','20.908','-c:v','libx264','-preset','veryfast','-crf','20','-c:a','aac',str(clean)],check=True)
result_path=ROOT/'recognition-aligned.json'
if result_path.exists(): result=json.loads(result_path.read_text(encoding='utf-8'))
else:
    result=recognize(clean,ROOT/'asr')
    result_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
manual_path=ROOT/'manual-realignment.json'
words=result['words']
if manual_path.exists():
    correction=json.loads(manual_path.read_text(encoding='utf-8'))['words']
    words=[w for w in words if w['end_ms']<=20280]+correction
cues=split_words(words,max_chars=14)
for cue in cues:
    cue['uncertain']=cue['start_ms']<2320 or (11080<=cue['start_ms']<13040) or cue['end_ms']>20280 or any(w.get('probability',0)<.65 for w in cue['words'])
doc={'cues':cues,'x':50,'y':84,'font':'yahei','size':68,'effect':'none','mode':'reveal','keywords':['裤子','裤脚口','尺码'],'keyword_color':'#FFD400','keyword_scale':1.3}
(ROOT/'document.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8')
settings={'subtitle_font':'yahei','subtitle_font_size':68,'subtitle_effect':'none','subtitle_x':50,'subtitle_y':84,'keyword_color':'#FFD400','keyword_scale':1.3}
for mode in ['reveal','sentence','highlight']:
    _write_subtitles({'subtitle_segments':cues,'preserve_sentence_timing':True,'caption_mode':mode,'keywords':doc['keywords']},{'settings':settings},ROOT/f'{mode}.srt',ROOT/f'{mode}.ass')
    subprocess.run(['ffmpeg','-v','error','-y','-i',str(clean),'-vf',f"ass='{_ass_filter_path(ROOT/f'{mode}.ass')}'",'-c:v','libx264','-preset','veryfast','-crf','20','-c:a','copy',str(ROOT/f'{mode}.mp4')],check=True)
original=Path('E:/Codex工作盘/artifacts/latest/男装编剪器下载/candidate_001-裤子会不会太小？听主播讲裤脚口-e359f887.mp4')
subprocess.run(['ffmpeg','-v','error','-y','-i',str(original),'-i',str(ROOT/'reveal.mp4'),'-filter_complex','[0:v]scale=540:960,setsar=1[l];[1:v]scale=540:960,setsar=1[r];[l][r]hstack[v]','-map','[v]','-map','1:a','-c:v','libx264','-preset','veryfast','-crf','21','-c:a','copy','-shortest',str(ROOT/'comparison-left-old-right-new.mp4')],check=True)
subprocess.run(['ffmpeg','-v','error','-y','-i',str(ROOT/'comparison-left-old-right-new.mp4'),'-vf','fps=1/3,scale=540:480,tile=3x2','-frames:v','1',str(ROOT/'comparison-contact.jpg')],check=True)
print(json.dumps({'output':str(ROOT),'cues':[{k:c[k] for k in ('start_ms','end_ms','text')} for c in cues]},ensure_ascii=True))
