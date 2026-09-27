"""Repetitive ASR must be re-decoded from audio, not split into fake timings."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from content_factory_api.speech_captions import split_words


@pytest.fixture
def speech_worker():
    path = Path(__file__).resolve().parents[3] / 'scripts' / 'speech-caption-worker.py'
    spec = importlib.util.spec_from_file_location('speech_worker_037', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def segment(text, compression=1.2):
    return SimpleNamespace(text=text, start=0, end=18, avg_logprob=-.05,
                           no_speech_prob=0, compression_ratio=compression)


class Decoder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return iter(self.responses.pop(0)), None


def test_repeated_high_confidence_words_redecode_same_audio_and_preserve_audit(speech_worker):
    first = segment('裤子 ' * 56, 14)
    good = segment('很多大哥担心我的裤子太小了穿不了')
    decoder = Decoder([first], [good])
    audio = object()
    segments, attempts = speech_worker.transcribe_checked(decoder, audio, hotwords='裤子 裤腰')
    assert segments == [good]
    assert len(decoder.calls) == 2
    assert all(call[0] is audio for call in decoder.calls)
    recovery = decoder.calls[1][1]
    assert not recovery['hotwords'] and recovery['condition_on_previous_text'] is False
    assert recovery['repetition_penalty'] > 1 and recovery['no_repeat_ngram_size'] > 0
    assert len(recovery['temperature']) > 1
    assert attempts[0]['accepted'] is False and attempts[1]['accepted'] is True
    assert attempts[0]['segments'][0]['text'] == first.text


def test_normal_short_repetition_is_not_silently_removed_or_retried(speech_worker):
    good = segment('看一下看一下，这条裤子有弹力')
    decoder = Decoder([good])
    result, attempts = speech_worker.transcribe_checked(decoder, object(), hotwords='裤子')
    assert result == [good] and len(decoder.calls) == 1 and len(attempts) == 1


def test_recovery_is_bounded_and_does_not_return_bad_transcript(speech_worker):
    decoder = Decoder([segment('裤子 ' * 56)], [segment('装' * 100)])
    with pytest.raises(ValueError, match='重复.*核听'):
        speech_worker.transcribe_checked(decoder, object(), hotwords='裤子')
    assert len(decoder.calls) == 2


def test_replacement_character_triggers_audio_redecode(speech_worker):
    good = segment('这条裤子穿着舒服')
    decoder = Decoder([segment('这条裤子\ufffd')], [good])
    assert speech_worker.transcribe_checked(decoder, object(), hotwords='')[0] == [good]


def test_oversized_observed_unit_still_cannot_be_evenly_split():
    with pytest.raises(ValueError, match='字词单位过长'):
        split_words([{'text': '裤子' * 11, 'start_ms': 9260, 'end_ms': 10160}], max_chars=14)
