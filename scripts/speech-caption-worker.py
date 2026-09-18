"""Offline faster-whisper ASR and cross-attention/DTW forced alignment."""
import argparse
import json
from pathlib import Path
import re
import os


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--job',required=True)
    request=json.loads(Path(parser.parse_args().job).read_text(encoding='utf-8'))
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio, pad_or_trim
    from faster_whisper.tokenizer import Tokenizer
    model=WhisperModel(request['model'],device='cpu',compute_type='int8',cpu_threads=6,num_workers=1,local_files_only=True)
    audio=decode_audio(request['audio'],sampling_rate=16000)
    duration=round(len(audio)/16)
    raw=[];words=[]
    tokenizer=Tokenizer(model.hf_tokenizer,True,task='transcribe',language='zh')
    def units(items,offset=0):
        out=[];pending=''
        for item in items:
            text=item.get('word','').strip()
            if not text:continue
            start=max(0,round(item['start']*1000)+offset)
            end=min(duration,round(item['end']*1000)+offset)
            if end<=start:
                # Punctuation/no-duration tokens belong to the preceding observed unit;
                # never invent per-character times.
                if out: out[-1]['text']+=text
                else: pending+=text
                continue
            if out and start<out[-1]['end_ms']:start=out[-1]['end_ms']
            if end>start:
                out.append({'text':pending+text,'start_ms':start,'end_ms':end,'probability':round(float(item.get('probability',0)),4)})
                pending=''
        if pending:raise ValueError('音频没有可支持这些文字的有效时间，请核听')
        return out
    def align_text(text,start,end):
        features=model.feature_extractor(audio[start*16:end*16])
        num_frames=min(features.shape[-1],model.feature_extractor.nb_max_frames)
        encoded=model.encode(pad_or_trim(features))
        # Encode Chinese characters separately so a multi-character BPE token
        # doesn't force several spoken syllables to appear at the same instant.
        tokens=[token for part in re.findall(r'[\u3400-\u9fff]|[^\u3400-\u9fff]+',text) for token in tokenizer.encode(part)]
        aligned=model.find_alignment(tokenizer,[tokens],encoded,num_frames)[0]
        aligned_words=units(aligned,start)
        if ''.join(w['text'] for w in aligned_words)!=text:
            raise ValueError('存在无法对齐的字词，请核听或缩小修改范围')
        return aligned_words
    if request.get('cues') is None:
        segments,info=model.transcribe(audio,language='zh',beam_size=5,temperature=0,word_timestamps=True,vad_filter=True,
            vad_parameters={'min_silence_duration_ms':300,'speech_pad_ms':200},hotwords=request.get('hotwords',''),condition_on_previous_text=True)
        for segment in segments:
            raw.append({'text':segment.text,'start_ms':round(segment.start*1000),'end_ms':round(segment.end*1000),'avg_logprob':segment.avg_logprob,'no_speech_prob':segment.no_speech_prob})
            observed=units([{'word':w.word,'start':w.start,'end':w.end,'probability':w.probability} for w in segment.words or []])
            group=[]
            for word in observed:
                if group and word['end_ms']-group[0]['start_ms']>27000:
                    words.extend(align_text(''.join(w['text'] for w in group),group[0]['start_ms'],group[-1]['end_ms']));group=[]
                group.append(word)
            if group:words.extend(align_text(''.join(w['text'] for w in group),group[0]['start_ms'],group[-1]['end_ms']))
    else:
        for cue in request['cues']:
            start=max(0,cue['start_ms']);end=min(duration,cue['end_ms'])
            if end-start>29000:raise ValueError('请先把超过29秒的段落拆分再对齐')
            aligned_words=align_text(cue['text'],start,end)
            raw.append({'text':cue['text'],'start_ms':start,'end_ms':end})
            words.extend(aligned_words)
    # Lexical boundaries constrain wrapping; they do not create or average timings.
    import jieba
    import tempfile
    jieba.dt.tmp_dir=tempfile.gettempdir()
    for term in request.get('hotwords','').split():jieba.add_word(term)
    boundaries=set(); position=0
    for token in jieba.cut(''.join(w['text'] for w in words),HMM=False):
        position+=len(token);boundaries.add(position)
    position=0
    for word in words:
        position+=len(word['text']);word['break_after']=position in boundaries
    result={'engine':'faster-whisper-1.2.1/large-v3-turbo/cpu-int8','alignment':'cross-attention-DTW','duration_ms':duration,'raw_segments':raw,'words':words}
    Path(request['output']).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__': main()
