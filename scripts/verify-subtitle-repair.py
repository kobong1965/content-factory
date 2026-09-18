"""Render an isolated replacement, preserving the user's approved old files."""
import json
import subprocess
from pathlib import Path
from content_factory_media.pipeline import MediaPipeline
from content_factory_api.auto_edit_worker import _transcript_text, _write_subtitles, _ass_filter_path
from content_factory_api.subtitles import detect_silence

repo = Path(__file__).resolve().parents[1]
out = Path('E:/Codex工作盘/artifacts/test-builds/subtitle-0.1.26')
out.mkdir(parents=True, exist_ok=False)
manifest = json.loads((repo/'data/s7/auto-edit/outputs/auto_62eeba2f40374d4481ccbef310a56b41/manifest.json').read_text(encoding='utf-8'))
candidate = next(c for c in manifest['candidates'] if c['id']=='candidate_02')
duration = candidate['source_end_ms']-candidate['source_start_ms']
clean = out/'clean.mp4'
def run(args):
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin',*args],check=True)
run(['-ss',str(candidate['source_start_ms']/1000),'-i',candidate['source_path'],'-t',str(duration/1000),'-map','0:v:0','-map','0:a:0','-c:v','libx264','-preset','veryfast','-crf','21','-c:a','aac',str(clean)])
audio = out/'audio.wav'
run(['-i',str(clean),'-vn','-ar','16000','-ac','1',str(audio)])
pipeline = MediaPipeline(asr_model_path=repo/'.models/whisper/ggml-tiny.bin')
transcript, status = pipeline._transcribe(audio,out)
cues = _transcript_text({'artifacts':{'transcript_path':str(transcript)}})
item = {'clips':[{'start_ms':0,'end_ms':duration}],'subtitle_segments':cues,'keywords':['尺码','下单'],'silence_intervals':detect_silence(clean,duration)}
settings={'subtitle_font_size':68,'keyword_color':'#FFD400','keyword_scale':1.3,'subtitle_font':'yahei','subtitle_effect':'none'}
srt, ass = out/'修复字幕.srt',out/'修复字幕.ass'
_write_subtitles(item,{'settings':settings},srt,ass)
video=out/'下单前看清裤长与尺码-字幕修复待审核.mp4'
run(['-i',str(clean),'-vf',f"ass='{_ass_filter_path(ass)}'",'-c:v','libx264','-preset','veryfast','-crf','21','-c:a','copy','-movflags','+faststart',str(video)])
run(['-ss','8','-i',str(video),'-frames:v','1',str(out/'preview.jpg')])
print(json.dumps({'video':str(video),'subtitle':srt.read_text(encoding='utf-8-sig'),'silence':item['silence_intervals']},ensure_ascii=False))
