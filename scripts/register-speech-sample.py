"""Register the requested real sample as a NEW pending review, never overwrite."""
import json
import subprocess
from pathlib import Path
from urllib.request import Request,urlopen

root=Path('E:/Codex工作盘/artifacts/test-builds/subtitle-0.1.28')
base='http://127.0.0.1:8766'
def api(path,value=None,method=None):
    req=Request(base+path,data=json.dumps(value,ensure_ascii=False).encode() if value is not None else None,headers={'Content-Type':'application/json'},method=method)
    with urlopen(req,timeout=180) as response:return json.load(response)
assert api('/health')['build_id']=='content-factory-0.1.28'
original=json.loads(Path('data/s7/auto-edit/outputs/auto_c590779fb4c643d890d7863e9cb3a118/manifest.json').read_text(encoding='utf-8'))
candidate={**original['candidates'][0],'video_path':'reveal.mp4','subtitle_path':'reveal.srt','cover_path':'cover.jpg','review_notes':['真实原音频重新识别与逐字对齐；0—2.32秒、11.08—13.04秒及片尾仍需人工核听。','本样片未批准，不会自动进入已审核成片。']}
subprocess.run(['ffmpeg','-v','error','-y','-ss','2','-i',str(root/'reveal.mp4'),'-frames:v','1',str(root/'cover.jpg')],check=True)
manifest={**original,'id':'subtitle_028_audio_review','title':'字幕修复样片 · 逐字跟随原声 · 待核听','candidates':[candidate]}
path=root/'manifest.json';path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
result=api('/s7/footage-batches/import',{'manifest_path':str(path)})
assert result['candidates'][0]['review_status']=='pending'
endpoint='/s7/subtitle-editor/'+result['id']+'/candidate_001'
draft=api(endpoint)
if draft['revision']==0:
    document=json.loads((root/'document.json').read_text(encoding='utf-8'))
    draft=api(endpoint,{'revision':0,'document':document},'PUT')
    assert api(endpoint)['document']==document
    preview=api(endpoint+'/events',{'revision':draft['revision'],'document':document})
    assert preview['events'][0]['text']!=document['cues'][0]['text']
print(json.dumps({'batch':result['id'],'review':'pending','draft_revision':draft['revision']},ensure_ascii=True))
