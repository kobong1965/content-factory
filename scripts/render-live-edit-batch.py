"""Reproducible, offline edit decisions for the user-authorized 2026-09-15 batch.

Never modifies source footage; outputs review drafts, not approved advertisements.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/api/src'))
from content_factory_api.subtitle_design import caption_text

ROOT = Path('E:/Codex工作盘/artifacts/latest/千川对标剪辑-20260915-字幕新版')
EVIDENCE = Path('E:/Codex工作盘/artifacts/latest/千川对标剪辑-20260915/evidence')
TEMP = Path('E:/Codex工作盘/temp/live-edit-20260915-subtitle-v2/render')
CASES = Path('E:/Codex工作盘/artifacts/latest/大平视频-Skill-20260913')
FONT = Path('C:/Windows/Fonts/simhei.ttf')
DECISIONS = [
    ('01', '黑灰色怎么搭', '039', [(12.66,24.72),(240.26,243.68)], ['4.20 J85','5.13 J83'],
     '腰头近景先认款，退后露出整体；从可见颜色引出搭配，再保留报身高体重的选码入口。',
     ['原话含“一眼富贵”等主观修辞，是否保留由审核人判断。']),
    ('02', '穿不好看，先看版型', '040', [(151.44,159.10),(123.78,128.82),(132.80,140.30),(147.70,151.44)], ['5.9 J72','7.29 J85'],
     '先用身材焦虑的反转留人，再给完整裤型和九分长度选择；避开原片中获奖和身高保证表达。',
     ['这是口播共鸣版，未实拍男模上身；显高是主播主观描述，不能当作实际效果证明。']),
    ('03', '裤脚怕太小？直接看展示', '040', [(233.20,250.74)], ['5.9 J82','6.13 J96'],
     '疑问与展示连续发生，保留裤脚对齐腰头的完整动作；在另一条灰色裤子入镜之前结束。',
     ['主播称最小码、码数越大裤口越宽；上架前与该款尺码表核对。']),
    ('04', '有弹力，也要看裤型', '041', [(222.78,244.58)], ['4.20 J85','5.10 J82','5.12 J85'],
     '连续保留季节场景、横向拉伸、释放和裤口展示；动作结果没有被切走。',
     ['原话“八面环弹”“冬天套秋裤不显臃肿”需要商品事实确认；画面只能证明本次手拉演示。']),
    ('05', '衣柜里的上衣怎么配', '041', [(2.30,8.30),(120.22,133.26),(234.62,244.58)], ['5.13 J83','6.3 J96'],
     '开头直接露出全裤搭白色上衣，主体列举衣橱搭配，结尾用裤脚细节落回商品。',
     ['搭配建议来自原口播；未展示所有列举的鞋服组合。“不挑人”需人工判断是否删去。']),
    ('06', '长裤还是九分，先选对', '039', [(98.72,100.04),(101.22,108.18),(228.84,234.72),(240.26,243.68)], ['6.16 J99','7.29 J85'],
     '按真实购买疑问组织：长裤和九分选择、裤腿轮廓、报身高体重选码；不保留具体观众尺码。',
     ['裤长与尺码推荐仍应以该款尺码表为准，不能只按身高决定。']),
]
# Obvious homophone substitutions only. The unmodified ASR remains alongside them.
CORRECTIONS = {'小肢痛':'小直筒','小肢通':'小直筒','小枝筒':'小直筒','不谨慎':'不紧身',
               '纸上纸下':'直上直下','衣锅':'衣柜','毛泥':'毛呢','逗逗鞋':'豆豆鞋',
               '没有悬好':'没有选好','暴身高点钟':'报身高体重','长库':'长裤',
               '9分':'九分','卡马':'卡码','有孕肺险':'有运费险','和铁':'合体','腰长万贯':'腰缠万贯','小球你':'小瞧你'}

def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def stamp(seconds, ass=False):
    value = round(seconds * (100 if ass else 1000))
    base = 100 if ass else 1000
    s, frac = divmod(value, base); m, s = divmod(s, 60); h, m = divmod(m, 60)
    return f'{h}:{m:02}:{s:02}.{frac:02}' if ass else f'{h:02}:{m:02}:{s:02},{frac:03}'

def run(args, logfile):
    with logfile.open('wb') as log:
        subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, check=True)

def render_one(decision):
    number, title, source_id, windows, refs, hook, notes = decision
    folder = ROOT / 'videos' / number
    folder.mkdir(parents=True, exist_ok=True)
    work = TEMP / number; work.mkdir(parents=True, exist_ok=True)
    source = Path(f'D:/桌面/ZENGZHI增致牛仔内衣旗舰店_2026-08-31_17-05-33_{source_id}.mp4')
    raw = json.loads((EVIDENCE / source_id / 'transcript.json').read_text(encoding='utf-8'))
    subtitles, offset = [], 0.0
    for start, end in windows:
        for seg in raw['segments']:
            if seg['end'] <= start or seg['start'] >= end: continue
            a,b=seg['start'],seg['end']
            if a < start-.03 or b > end+.03:
                words=[w for w in seg['words'] if w['start'] >= start-.01 and w['end'] <= end+.01]
                if not words: continue
                text=''.join(w['word'] for w in words).strip(); a=words[0]['start']; b=words[-1]['end']
            else:
                text = seg['text'].strip()
            for before, after in CORRECTIONS.items(): text = text.replace(before, after)
            subtitles.append((offset+a-start, offset+b-start, text))
        offset += end-start
    merged=[]
    for a,b,t in subtitles:
        if merged and a-merged[-1][1] < .12 and (b-a < .85 or merged[-1][1]-merged[-1][0] < .85) and len(merged[-1][2])+len(t) <= 25:
            pa,pb,pt=merged.pop(); merged.append((pa,b,pt+'，'+t))
        else: merged.append((a,b,t))
    subtitles=[]
    for a,b,t in merged:
        midpoint=(len(t)+1)//2
        subtitles.append((a,b,t if len(t)<=15 else t[:midpoint]+'\n'+t[midpoint:]))
    srt = '\n\n'.join(f'{i+1}\n{stamp(a)} --> {stamp(b)}\n{t}' for i,(a,b,t) in enumerate(subtitles))+'\n'
    (folder/'subtitles.srt').write_text(srt, encoding='utf-8-sig')
    header = '''[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,SimHei,68,&H00FFFFFF,&H00FFFFFF,&H00181818,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,90,120,390,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    ass = header
    for a,b,t in subtitles:
        safe=caption_text(t)
        ass += f'Dialogue: 0,{stamp(a,True)},{stamp(b,True)},Default,,0,0,0,,{safe}\n'
    (folder/'subtitles.ass').write_text(ass, encoding='utf-8-sig')
    shutil.copyfile(folder/'subtitles.ass',work/'subtitles.ass')
    video=folder/'review.mp4'
    args=['ffmpeg','-hide_banner','-y','-threads','2']
    filters=[]
    for i,(a,b) in enumerate(windows):
        args += ['-ss',str(a),'-t',str(b-a),'-i',str(source)]
        filters += [f'[{i}:v]setpts=PTS-STARTPTS,setsar=1[v{i}]',f'[{i}:a]asetpts=PTS-STARTPTS[a{i}]']
    joined=''.join(f'[v{i}][a{i}]' for i in range(len(windows)))
    filters += [f'{joined}concat=n={len(windows)}:v=1:a=1[v][a]', '[v]ass=subtitles.ass:fontsdir=../fonts[outv]']
    args += ['-filter_complex_threads','2','-filter_complex',';'.join(filters),'-map','[outv]','-map','[a]',
             '-c:v','libx264','-preset','veryfast','-crf','19','-threads','4','-pix_fmt','yuv420p',
             '-r','30','-c:a','aac','-b:a','192k','-movflags','+faststart',str(video)]
    with (work/'render.log').open('wb') as log:
        subprocess.run(args,cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True)
    run(['ffmpeg','-hide_banner','-y','-ss','1','-i',str(video),'-frames:v','1','-update','1',str(folder/'cover.jpg')],work/'cover.log')
    run(['ffmpeg','-v','error','-i',str(video),'-f','null','-'],work/'decode.log')
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(video)]))
    duration=float(probe['format']['duration'])
    assert abs(duration-offset)<.16, (duration,offset)
    assert any(s['codec_type']=='audio' for s in probe['streams'])
    assert any(s['codec_type']=='video' and s['width']==1080 and s['height']==1920 for s in probe['streams'])
    (folder/'technical-qa.json').write_text(json.dumps({'full_decode_pass':True,'duration':duration,'expected_duration':offset,'sha256':sha(video),'probe':probe},ensure_ascii=False,indent=2),encoding='utf-8')
    result={'id':number,'title':title,'source_path':str(source),'source_start_ms':round(windows[0][0]*1000),
            'source_end_ms':round(windows[0][1]*1000),'clips':[{'start_ms':round(a*1000),'end_ms':round(b*1000)} for a,b in windows],
            'video_path':f'videos/{number}/review.mp4','subtitle_path':f'videos/{number}/subtitles.srt','cover_path':f'videos/{number}/cover.jpg',
            'hook':hook,'benchmark_refs':refs,'review_notes':['字幕来自本地自动转写，已修正常见同音词，尚未逐句听审。']+notes}
    (folder/'edit-decision.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Finished {number}: {duration:.2f}s',flush=True)
    return result

def main():
    if ROOT.exists() and any(ROOT.iterdir()):
        raise SystemExit('结果目录已有完整批次，请另设输出目录，禁止覆盖已审核文件。')
    (TEMP/'fonts').mkdir(parents=True,exist_ok=True)
    shutil.copyfile(FONT,TEMP/'fonts'/FONT.name)
    with ThreadPoolExecutor(max_workers=2) as pool: candidates=list(pool.map(render_one,DECISIONS))
    batch={'schema_version':1,'id':'qianchuan-20260915-subtitle-v2','title':'千川对标 · 六版字幕新版（无顶部字）',
           'analysis_summary':'12 条千川验证对标的共性是商品先入眼、疑问紧接实物动作、展示结果留屏。本批从 3 条各 5 分钟实拍中选取完整口播段，保留对应原声与动作，不混入对标商品画面。6 条是不同剪辑角度的待审核候选；暂无逐秒投放数据，不能认定某句话触发算法。',
           'candidates':candidates}
    (ROOT/'batch.json').write_text(json.dumps(batch,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__': main()
