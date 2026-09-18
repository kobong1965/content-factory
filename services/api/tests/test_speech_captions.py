"""Speech-timed caption requirements, independent of renderer implementation."""
import pytest

from content_factory_api.speech_captions import alignment_valid, display_events, split_words


def test_long_speech_is_short_cues_without_losing_or_splitting_words():
    tokens = ['大家', '看一下', '这条', '裤子', '它的', '裤脚口', '非常', '好看', '而且', '裤腰', '弹力', '很大', '穿着', '舒服']
    words = [{'text': text, 'start_ms': index * 430, 'end_ms': index * 430 + 400} for index, text in enumerate(tokens)]
    cues = split_words(words, max_chars=14)
    assert len(cues) > 1
    assert ''.join(cue['text'] for cue in cues) == ''.join(tokens)
    assert all(1 <= len(cue['text']) <= 14 for cue in cues)
    assert [word for cue in cues for word in cue['words']] == words
    assert any('裤脚口' in cue['text'] for cue in cues)
    for cue in cues:
        assert cue['start_ms'] == cue['words'][0]['start_ms']
        assert cue['end_ms'] == cue['words'][-1]['end_ms']
    assert all(left['end_ms'] <= right['start_ms'] for left, right in zip(cues, cues[1:]))


def test_reveal_uses_uneven_acoustic_onsets_not_equal_character_durations():
    cue = {'start_ms': 100, 'end_ms': 2100, 'text': '裤腰有弹力', 'words': [
        {'text': '裤', 'start_ms': 100, 'end_ms': 250},
        {'text': '腰', 'start_ms': 260, 'end_ms': 620},
        {'text': '有', 'start_ms': 640, 'end_ms': 760},
        {'text': '弹', 'start_ms': 990, 'end_ms': 1420},
        {'text': '力', 'start_ms': 1500, 'end_ms': 2100},
    ]}
    events = display_events(cue, 'reveal')
    assert [event['start_ms'] for event in events] == [100, 260, 640, 990, 1500]
    assert [event['text'] for event in events] == ['裤', '裤腰', '裤腰有', '裤腰有弹', '裤腰有弹力']
    assert events[-1]['end_ms'] == 2100
    assert all(left['end_ms'] <= right['start_ms'] for left, right in zip(events, events[1:]))


def test_display_modes_distinguish_full_sentence_from_reveal_and_highlight():
    cue = {'start_ms': 200, 'end_ms': 1800, 'text': '看裤脚口', 'words': [
        {'text': '看', 'start_ms': 200, 'end_ms': 450},
        {'text': '裤脚口', 'start_ms': 700, 'end_ms': 1800},
    ]}
    sentence = display_events(cue, 'sentence')
    assert len(sentence) == 1
    assert sentence[0]['text'] == '看裤脚口'
    assert sentence[0]['start_ms'] == 200
    assert sentence[0]['end_ms'] == 1800
    reveal = display_events(cue, 'reveal')
    assert [event['text'] for event in reveal] == ['看', '看裤脚口']
    highlight = display_events(cue, 'highlight')
    assert all(event['text'] == '看裤脚口' for event in highlight)
    assert [event['highlight'] for event in highlight] == ['看', '看裤脚口']
    assert [event['start_ms'] for event in highlight] == [200, 700]


def test_new_short_sentence_replaces_previous_and_silence_stays_empty():
    words = [
        {'text': '看裤腰', 'start_ms': 100, 'end_ms': 900},
        {'text': '有弹力', 'start_ms': 2000, 'end_ms': 2800},
    ]
    cues = split_words(words, max_chars=14)
    assert len(cues) == 2
    first = display_events(cues[0], 'reveal')
    second = display_events(cues[1], 'reveal')
    assert first[-1]['end_ms'] == 900
    assert second[0]['start_ms'] == 2000
    assert second[0]['text'] == '有弹力'
    assert '看裤腰' not in second[0]['text']


@pytest.mark.parametrize('mode', ['reveal', 'highlight'])
def test_manual_text_correction_rejects_stale_alignment_until_realignment(mode):
    cue = {'start_ms': 100, 'end_ms': 1800, 'text': '看裤腰', 'words': [
        {'text': '看', 'start_ms': 100, 'end_ms': 450},
        {'text': '库腰', 'start_ms': 700, 'end_ms': 1800},
    ]}
    assert not alignment_valid(cue)
    with pytest.raises(ValueError, match='对齐'):
        display_events(cue, mode)
    # Manual sentence captions remain usable while acoustic realignment is pending.
    assert display_events(cue, 'sentence')[0]['text'] == '看裤腰'


@pytest.mark.parametrize('start_ms,end_ms', [(300, 1800), (100, 1500)])
@pytest.mark.parametrize('mode', ['reveal', 'highlight'])
def test_manual_time_trim_cannot_reuse_words_outside_the_new_cue(start_ms, end_ms, mode):
    cue = {'start_ms': start_ms, 'end_ms': end_ms, 'text': '看裤腰', 'words': [
        {'text': '看', 'start_ms': 100, 'end_ms': 450},
        {'text': '裤腰', 'start_ms': 700, 'end_ms': 1800},
    ]}
    assert not alignment_valid(cue)
    with pytest.raises(ValueError, match='对齐'):
        display_events(cue, mode)


def test_corrected_text_with_fresh_acoustic_alignment_is_usable():
    cue = {'start_ms': 100, 'end_ms': 1800, 'text': '看裤腰', 'words': [
        {'text': '看', 'start_ms': 100, 'end_ms': 450},
        {'text': '裤腰', 'start_ms': 720, 'end_ms': 1800},
    ]}
    assert alignment_valid(cue)
    assert [(event['start_ms'], event['text']) for event in display_events(cue, 'reveal')] == [(100, '看'), (720, '看裤腰')]


def test_full_neighboring_lines_still_preserve_apparel_word_boundary():
    text = '大家可以仔细看一下这条长裤脚口这里的设计穿起来非常好看'
    words = [{'text': char, 'start_ms': index * 100, 'end_ms': index * 100 + 90} for index, char in enumerate(text)]
    cues = split_words(words, max_chars=14)
    assert ''.join(cue['text'] for cue in cues) == text
    assert all(len(cue['text']) <= 14 for cue in cues)
    assert any('裤脚口' in cue['text'] for cue in cues)


@pytest.mark.parametrize('unit',[None,7,'裤腰',{'text':None,'start_ms':100,'end_ms':900}])
def test_malformed_alignment_units_are_invalid_instead_of_crashing(unit):
    cue={'start_ms':100,'end_ms':900,'text':'裤腰','words':[unit]}
    assert alignment_valid(cue) is False
    with pytest.raises(ValueError,match='对齐'):
        display_events(cue,'reveal')


def test_sentence_final_particle_is_not_orphaned_at_fourteen_char_boundary():
    text='这条裤子现在穿起来就非常舒服了'
    words=[{'text':char,'start_ms':index*100,'end_ms':index*100+90} for index,char in enumerate(text)]
    cues=split_words(words,max_chars=14)
    assert ''.join(c['text'] for c in cues)==text
    assert all(len(c['text'])<=14 for c in cues)
    assert cues[-1]['text'].endswith('了')
    assert len(cues[-1]['text'])>1
    assert not any(c['text'].startswith('了') for c in cues)
