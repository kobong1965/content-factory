"""Offline faster-whisper ASR and cross-attention/DTW forced alignment."""
import argparse
import json
from pathlib import Path
import re
import os


def transcribe_checked(model, audio, *, hotwords='', audit_path=None):
    """Reject decoder loops before alignment; retry the audio locally once.

    A loop may have very high token probability, so probability is not a safe
    acceptance gate. Never repair it by deleting repeated text or inventing time.
    Manual forced alignment does not enter this automatic recognition path.
    """
    attempts = []
    for recovery in (False, True):
        options = dict(language='zh', beam_size=5, temperature=0,
                       word_timestamps=True, vad_filter=True,
                       vad_parameters={'min_silence_duration_ms': 300, 'speech_pad_ms': 200},
                       hotwords=hotwords, condition_on_previous_text=True)
        if recovery:
            options.update(hotwords='', condition_on_previous_text=False,
                           temperature=(0, .2, .4), repetition_penalty=1.1,
                           no_repeat_ngram_size=6)
        iterator, _ = model.transcribe(audio, **options)
        segments = list(iterator)
        full = re.sub(r'\W|_', '', ''.join(segment.text for segment in segments))
        repetitive = any(len(match.group()) >= 18 for match in re.finditer(r'(.{1,8})\1{5,}', full))
        invalid = repetitive or any('\ufffd' in segment.text or segment.compression_ratio > 4 for segment in segments)
        attempts.append({
            'mode': 'local_recovery' if recovery else 'standard', 'accepted': not invalid,
            'segments': [{'text': s.text, 'start_ms': round(s.start * 1000),
                          'end_ms': round(s.end * 1000), 'compression_ratio': s.compression_ratio,
                          'avg_logprob': s.avg_logprob} for s in segments],
        })
        if audit_path is not None:
            Path(audit_path).write_text(json.dumps(attempts, ensure_ascii=False, indent=2), encoding='utf-8')
        if not invalid:
            return segments, attempts
    raise ValueError('本地语音识别出现异常重复，已从原音频重识别一次仍不合格，请核听；未生成伪造字幕')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--job',required=True)
    request=json.loads(Path(parser.parse_args().job).read_text(encoding='utf-8'))
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio, pad_or_trim
    from faster_whisper.tokenizer import Tokenizer
    model=WhisperModel(request['model'],device='cpu',compute_type='int8',cpu_threads=6,num_workers=1,local_files_only=True)
    audio=decode_audio(request['audio'],sampling_rate=16000)
    duration=round(len(audio)/16)
    raw=[];words=[];attempts=[]
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
        segments,attempts=transcribe_checked(model,audio,hotwords=request.get('hotwords',''),
            audit_path=Path(request['output']).with_suffix('.attempts.json'))
        for segment in segments:
            raw.append({'text':segment.text,'start_ms':round(segment.start*1000),'end_ms':round(segment.end*1000),'avg_logprob':segment.avg_logprob,'no_speech_prob':segment.no_speech_prob})
            observed=units([{'word':w.word,'start':w.start,'end':w.end,'probability':w.probability} for w in segment.words or []])
            if request.get('word_timestamps_only'):
                # Source selection needs observed word edges, not repeated
                # character realignment of every phrase in a long recording.
                words.extend(observed)
                continue
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
    result={'engine':'faster-whisper-1.2.1/large-v3-turbo/cpu-int8','alignment':'asr-word-timestamps' if request.get('word_timestamps_only') and request.get('cues') is None else 'cross-attention-DTW','duration_ms':duration,'raw_segments':raw,'words':words,'recognition_attempts':attempts}
    Path(request['output']).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__': main()
