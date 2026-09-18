"""Second real-audio pass with source context, plus manual-text acoustic realignment."""
import json
import subprocess
from pathlib import Path
from content_factory_api.speech_captions import recognize

root=Path('E:/Codex工作盘/artifacts/test-builds/subtitle-0.1.28')
source=Path('E:/Codex项目盘/男装编剪器/data/s7/auto-edit/sources/ba8415c00128464d88169b8ac5892e7f/00.mp4')
audio=root/'context-review.wav'
subprocess.run(['ffmpeg','-v','error','-y','-ss','226.388','-i',str(source),'-t','26.908','-vn','-ar','16000','-ac','1',str(audio)],check=True)
result=recognize(audio,root/'context-review',hotwords='裤子 裤脚口 裤腰 裤长 尺码 腰头')
(root/'context-review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result['raw_segments'],ensure_ascii=True))
