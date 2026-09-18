"""Exercise the installed API using the real manually corrected sample draft."""
import hashlib,json
from pathlib import Path
from urllib.request import Request,urlopen

root=Path('E:/Codex工作盘/artifacts/test-builds/subtitle-0.1.28')
base='http://127.0.0.1:8766'
endpoint='/s7/subtitle-editor/subtitle_028_audio_review/candidate_001'
def api(path,value=None):
    with urlopen(Request(base+path,data=json.dumps(value,ensure_ascii=False).encode() if value is not None else None,headers={'Content-Type':'application/json'}),timeout=300) as response:return json.load(response)
draft=api(endpoint)
assert draft['document']['cues'][-1]['text']=='不会勒'
document=draft['document'];events=api(endpoint+'/events',{'revision':draft['revision'],'document':document})['events']
assert events[0]['text']=='你'
old_hash=hashlib.sha256((root/'reveal.mp4').read_bytes()).hexdigest()
new=api(endpoint+'/render',{'revision':draft['revision'],'document':document})
candidate=new['candidates'][0]
assert candidate['review_status']=='pending'
video=Path(candidate['resources']['video']['path'])
ass=video.with_name('subtitles.ass').read_text(encoding='utf-8-sig')
assert ass.count('Dialogue:')==len(events)
assert '不会勒' in video.with_name('subtitles.srt').read_text(encoding='utf-8-sig')
assert api('/s7/subtitle-editor/'+new['id']+'/candidate_001')['document']==document
assert hashlib.sha256((root/'reveal.mp4').read_bytes()).hexdigest()==old_hash
report={'batch':new['id'],'video':str(video),'cues':len(document['cues']),'events':len(events),'original_unchanged':True,'manual_text':'不会勒','review':'pending','saved_reopened_document_equal':True}
(root/'real-api-export-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True))
