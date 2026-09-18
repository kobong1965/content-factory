"""Offline evidence helpers for the user supplied September 15 editing batch."""
import dataclasses
import json
import os
from pathlib import Path
import sys

ROOT = Path('E:/Codex工作盘/artifacts/latest/千川对标剪辑-20260915')
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['HF_HOME'] = 'E:/Codex工作盘/caches/huggingface'
os.environ['TEMP'] = os.environ['TMP'] = 'E:/Codex工作盘/temp/live-edit-20260915'
Path(os.environ['TEMP']).mkdir(parents=True, exist_ok=True)

def transcribe():
    from faster_whisper import WhisperModel
    snap = next(Path('E:/Codex工作盘/caches/huggingface/hub/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots').iterdir())
    model = WhisperModel(str(snap), device='cpu', compute_type='int8', cpu_threads=8, local_files_only=True)
    for number in ['039', '040', '041']:
        out = ROOT / 'evidence' / number / 'transcript.json'
        if out.exists():
            continue
        source = Path(f'D:/桌面/ZENGZHI增致牛仔内衣旗舰店_2026-08-31_17-05-33_{number}.mp4')
        segments, info = model.transcribe(str(source), language='zh', beam_size=5, word_timestamps=True, condition_on_previous_text=False, vad_filter=True)
        items = []
        for segment in segments:
            items.append(dataclasses.asdict(segment))
            print(f'{number} {segment.start:.2f}-{segment.end:.2f}: {segment.text}', flush=True)
        out.write_text(json.dumps({'model': str(snap), 'status': 'machine_transcript_pending_listening_review', 'source': str(source), 'segments': items}, ensure_ascii=False, indent=2), encoding='utf-8')

def overview():
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 20)
    for number in ['039', '040', '041']:
        folder = ROOT / 'evidence' / number
        frames = json.loads((folder / 'frame_index.json').read_text(encoding='utf-8'))
        for start in range(0, len(frames), 48):
            sheet = Image.new('RGB', (8*194, 6*370), '#222222')
            draw = ImageDraw.Draw(sheet)
            for i, frame in enumerate(frames[start:start+48]):
                x, y = (i % 8)*194, (i // 8)*370
                with Image.open(frame['file']) as im:
                    im.thumbnail((190, 338))
                    sheet.paste(im, (x, y+30))
                draw.text((x+2, y+3), f"{frame['time_seconds']:.2f}s", font=font, fill='white')
            sheet.save(folder / f'review_{start//48+1:02}.jpg', quality=92)

if __name__ == '__main__':
    (transcribe if sys.argv[1] == 'transcribe' else overview)()
