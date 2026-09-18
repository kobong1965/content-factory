from content_factory_api.subtitles import normalize_cues, simplify_chinese, subtitle_effect


def test_cues_never_overlap_linger_or_cover_silence():
    cues = [{'start_ms':0,'end_ms':30000,'text':'這條褲子'}, {'start_ms':2944,'end_ms':5900,'text':'看清尺碼'}]
    result = normalize_cues(cues, duration_ms=6000, silence=[(4200,6000)])
    assert result[0] == {'start_ms':0,'end_ms':2944,'text':'这条裤子'}
    assert result[1] == {'start_ms':2944,'end_ms':4200,'text':'看清尺码'}
    assert all(a['end_ms'] <= b['start_ms'] for a,b in zip(result,result[1:]))


def test_no_speech_produces_no_captions():
    assert normalize_cues([{'start_ms':0,'end_ms':2000,'text':'字幕'}], duration_ms=2000, silence=[(0,2000)]) == []


def test_chinese_and_effects():
    assert simplify_chinese('請看褲長與尺碼') == '请看裤长与尺码'
    assert simplify_chinese('什麼區別，怎麼選') == '什么区别，怎么选'
    assert subtitle_effect('none',1000) == ''
    assert '\\fad' in subtitle_effect('fade',1000)
    assert '\\t' in subtitle_effect('pop',1000)


def test_font_effect_are_burned_into_ass_and_srt_remains_plain(tmp_path):
    from content_factory_api.auto_edit_worker import _write_subtitles
    project={'settings':{'subtitle_font_size':68,'keyword_color':'#FFD400','keyword_scale':1.3,'subtitle_font':'kaiti','subtitle_effect':'fade'}}
    item={'subtitle_segments':[{'start_ms':0,'end_ms':1000,'text':'褲長與尺碼'}], 'keywords':['尺碼']}
    srt,ass=tmp_path/'a.srt',tmp_path/'a.ass'
    _write_subtitles(item,project,srt,ass)
    assert '裤长与尺码' in srt.read_text(encoding='utf-8-sig')
    style=ass.read_text(encoding='utf-8-sig')
    assert 'Default,KaiTi,68' in style
    assert '\\fad(100,100)' in style
    assert '\\c&H0000D4FF&' in style
