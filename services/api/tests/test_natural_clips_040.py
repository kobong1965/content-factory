import pytest

from content_factory_api import auto_edit_worker as worker
from content_factory_api.auto_edit_store import validate_edit_plan
from test_auto_edit_projects import _settings


def test_pool_uses_complete_speech_boundaries_and_rejects_long_unbroken_speech():
    from content_factory_api.natural_clips import build_clip_pool
    words = [dict(text='完整一句。', start_ms=i * 8000 + 200, end_ms=(i + 1) * 8000 - 200) for i in range(5)]
    pool = build_clip_pool(words, source_duration_ms=40000, minimum=15000, maximum=30000)
    assert pool
    assert all(200 <= p['end_ms'] - p['start_ms'] <= 29800 for p in pool)
    assert all(p['start_ms'] in {w['start_ms'] for w in words} for p in pool)
    assert all(p['end_ms'] in {w['end_ms'] for w in words} for p in pool)
    assert build_clip_pool([dict(text='一句不能截断的话', start_ms=0, end_ms=41000)], source_duration_ms=42000, minimum=15000, maximum=30000) == []


def test_visual_pool_uses_only_complete_shot_boundaries_without_asr():
    from content_factory_api.natural_clips import build_visual_clip_pool

    shots = [
        {'start_ms': 0, 'end_ms': 7000},
        {'start_ms': 7000, 'end_ms': 15000},
        {'start_ms': 15000, 'end_ms': 24000},
        {'start_ms': 24000, 'end_ms': 36000},
    ]
    pool = build_visual_clip_pool(shots, source_duration_ms=36000, minimum=15000, maximum=30000)
    assert pool
    assert any(item['start_ms'] == 0 and item['end_ms'] == 24000 for item in pool)
    assert all(item['start_ms'] in {0, 7000, 15000, 24000} for item in pool)
    assert all(item['end_ms'] in {7000, 15000, 24000, 36000} for item in pool)
    assert all(200 <= item['end_ms'] - item['start_ms'] <= 29800 for item in pool)
    assert build_visual_clip_pool([{'start_ms': 0, 'end_ms': 40000}], source_duration_ms=40000, minimum=15000, maximum=30000) == []


def test_visual_pool_can_fall_back_to_hard_windows_for_one_continuous_shot():
    from content_factory_api.natural_clips import build_visual_clip_pool

    pool = build_visual_clip_pool([{'start_ms': 0, 'end_ms': 300000}], source_duration_ms=300000,
                                  minimum=15000, maximum=30000, allow_fixed_fallback=True)
    assert pool
    assert all(item['boundary_kind'] == 'fixed_visual_fallback' for item in pool)
    assert all(15000 <= item['end_ms'] - item['start_ms'] <= 29800 for item in pool)


def test_short_complete_clip_can_be_repeated_to_reach_output_range():
    from content_factory_api.natural_clips import build_clip_pool, pool_can_fit
    pool = build_clip_pool([dict(text='完整一句。', start_ms=0, end_ms=12000)], source_duration_ms=12000, minimum=15000, maximum=30000)
    assert pool_can_fit(pool, minimum=15000, maximum=30000)
    assert not pool_can_fit([], minimum=15000, maximum=30000)


def test_unpunctuated_asr_phrase_edges_are_candidates_without_inventing_word_times():
    from content_factory_api.natural_clips import build_clip_pool
    words = [dict(text='完整口播', start_ms=i*8000, end_ms=(i+1)*8000) for i in range(4)]
    segments = [dict(start_ms=w['start_ms'], end_ms=w['end_ms'], text=w['text']) for w in words]
    pool = build_clip_pool(words, raw_segments=segments, source_duration_ms=32000, minimum=15000, maximum=30000)
    assert any(p['start_ms']==0 and p['end_ms']==24000 for p in pool)
    assert all(p['start_ms'] % 8000 == 0 and p['end_ms'] % 8000 == 0 for p in pool)
    # A model-authored time inside an acoustic word is never promoted to a boundary.
    assert build_clip_pool(words, raw_segments=[dict(start_ms=0,end_ms=23500)],
                           source_duration_ms=32000, minimum=15000, maximum=30000) == []


def test_natural_plan_rejects_unverified_edges_and_short_or_long_output():
    settings = _settings(target_count=1, duration_min_ms=15000, duration_max_ms=30000, duration_policy='bounded_15_30')
    candidate = dict(candidate_id='one', title='完整句子', clips=[dict(start_ms=1000, end_ms=21000)])
    with pytest.raises(ValueError, match='自然片段'):
        validate_edit_plan([candidate], source_duration_ms=60000, settings=settings)
    candidate['natural_clip_evidence'] = dict(version=1, clips=[dict(start_ms=1000, end_ms=21000)])
    assert validate_edit_plan([candidate], source_duration_ms=60000, settings=settings)[0]['duration_ms'] == 20000
    for end in (6000, 42000, 20900):
        changed = {**candidate, 'clips': [dict(start_ms=1000, end_ms=end)]}
        with pytest.raises(ValueError):
            validate_edit_plan([changed], source_duration_ms=60000, settings=settings)


def test_real_output_outside_hard_bounds_is_rejected_even_with_small_codec_drift(monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr(worker, 'probe_media', lambda path: SimpleNamespace(duration_ms=30033, fps=30))
    with pytest.raises(ValueError, match='硬边界'):
        worker._validate_rendered_duration(tmp_path / 'x.mp4', {'clips': [{'start_ms': 0, 'end_ms': 30000}]},
                                           settings=_settings(duration_min_ms=15000, duration_max_ms=30000, duration_policy='bounded_15_30'))


def test_auto_caption_modes_depend_on_output_order_not_model_ids():
    assert [worker._caption_mode({'subtitle_mode': 'auto'}, index) for index in range(5)] == [
        'sentence', 'highlight', 'reveal', 'sentence', 'highlight']


@pytest.mark.parametrize('bad_boundary', [False, True])
def test_gateway_uses_local_pool_and_never_trusts_model_boundary_evidence(tmp_path, monkeypatch, bad_boundary):
    import json
    from types import SimpleNamespace
    from content_factory_api import speech_captions
    from test_auto_edit_projects import _skills
    transcript = tmp_path / 'transcript.json'
    transcript.write_text(json.dumps({'segments':[dict(start_ms=0, end_ms=20000, text='裤型展示。')]}), encoding='utf-8')
    frame = tmp_path / 'frame.jpg'
    frame.write_bytes(b'image-fixture')
    result = tmp_path / 'result.json'
    result.write_text(json.dumps({'artifacts':{'transcript_path':str(transcript)},'asr':{'status':'completed'},
                                 'shots':[{'keyframe_path':str(frame),'start_ms':0,'end_ms':20000}]}), encoding='utf-8')
    gateway = SimpleNamespace(has_purpose=lambda _:True, for_purpose=lambda _:None)
    monkeypatch.setattr(worker, 'GatewaySettingsStore', lambda *a:SimpleNamespace(load=lambda:gateway))
    monkeypatch.setattr(worker, 'MediaPipeline', lambda **kw:SimpleNamespace(process=lambda *a, **k:result))
    def source_recognize(*a, **kw):
        assert kw.get('word_timestamps_only') is True
        return {'words':[dict(text='裤型展示。',start_ms=200,end_ms=22000)]}
    monkeypatch.setattr(speech_captions, 'recognize', source_recognize)
    def gateway_call(*a, **kw):
        context = json.loads(kw['context_json'])
        pool = context['sources'][0]['natural_clip_pool']
        assert pool == [dict(start_ms=200,end_ms=22000,text='裤型展示。')]
        return SimpleNamespace(content={'selected_skill_id':'skill_a','selection_reason':'原声证据','analysis_summary':'完整口播',
            'candidates':[dict(candidate_id='free_model_name',title='裤型',source_id='s1',skill_id='skill_a',
                               clips=[dict(start_ms=300 if bad_boundary else 200,end_ms=22000)],
                               natural_clip_evidence={'version':1,'clips':[]})]})
    monkeypatch.setattr(worker, 'call_gateway', gateway_call)
    project = dict(project_id='test',source=dict(source_id='s1',path=str(tmp_path/'video.mp4'),file_name='video.mp4',duration_ms=30000),
                   eligible_skill_snapshots=[_skills()[0]], settings=_settings(target_count=1,duration_min_ms=15000,duration_max_ms=30000,duration_policy='bounded_15_30'))
    if bad_boundary:
        with pytest.raises(ValueError,match='自然片段'):
            worker.analyze_and_plan_with_gateway(project,root=tmp_path)
    else:
        plan = worker.analyze_and_plan_with_gateway(project,root=tmp_path)[3]
        assert plan[0]['natural_clip_evidence']['clips'] == plan[0]['clips']
        assert validate_edit_plan(plan,source_duration_ms=30000,settings=project['settings'])[0]['duration_ms']==21800
