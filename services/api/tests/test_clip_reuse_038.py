"""Source ranges are reusable assets, not consumed timeline positions."""
import copy
import json
from types import SimpleNamespace

import pytest

from content_factory_api import auto_edit_worker as worker
from content_factory_api.auto_edit_store import AutoEditProjectStore, generation_check, validate_edit_plan
from test_auto_edit_projects import _settings, _skills, _source


@pytest.mark.parametrize('actual_ms,passes', [(6000, True), (6040, True), (6800, False), (5800, False)])
def test_final_media_duration_gate_rejects_accumulated_drift(monkeypatch, tmp_path, actual_ms, passes):
    monkeypatch.setattr(worker, 'probe_media', lambda _: SimpleNamespace(duration_ms=actual_ms, fps=25), raising=False)
    item = {'clips': [{'start_ms': 0, 'end_ms': 2000}] * 3}
    if passes:
        worker._validate_rendered_duration(tmp_path / 'final.mp4', item)
    else:
        with pytest.raises(ValueError, match='成片实际时长'):
            worker._validate_rendered_duration(tmp_path / 'final.mp4', item)


@pytest.mark.parametrize('ranges', [
    [(0, 10000), (20000, 30000), (0, 10000)],
    [(0, 12000), (10000, 22000)],
    [(20000, 30000), (0, 10000)],
    [(0, 10000), (0, 10000), (0, 10000)],
])
def test_source_ranges_may_repeat_overlap_or_go_backwards_without_reordering(ranges):
    clips = [{'start_ms': a, 'end_ms': b} for a, b in ranges]
    plan = [{'candidate_id': 'reuse', 'title': '重复展示', 'clips': clips}]
    original = copy.deepcopy(plan)
    actual = validate_edit_plan(plan, source_duration_ms=30000, settings=_settings(target_count=1))
    assert actual[0]['clips'] == clips
    assert actual[0]['duration_ms'] == sum(b-a for a, b in ranges)
    assert plan == original


def test_reuse_duration_counts_each_appearance_not_union_of_source_ranges():
    plan = [{'candidate_id': 'reuse', 'title': '重复展示', 'clips': [{'start_ms': 0, 'end_ms': 10000}] * 3}]
    result = validate_edit_plan(plan, source_duration_ms=10000,
                               settings=_settings(target_count=1, duration_min_ms=30000, duration_max_ms=30000))
    assert result[0]['duration_ms'] == 30000
    with pytest.raises(ValueError, match='时长'):
        validate_edit_plan(plan, source_duration_ms=10000,
                           settings=_settings(target_count=1, duration_max_ms=29000))


def test_existing_piece_budget_is_independent_from_source_reuse():
    plan = [{'candidate_id': 'reuse', 'title': '三十段', 'clips': [{'start_ms': 0, 'end_ms': 5000}] * 30}]
    settings = _settings(target_count=1, duration_min_ms=150000, duration_max_ms=180000)
    assert validate_edit_plan(plan, source_duration_ms=5000, settings=settings)[0]['duration_ms'] == 150000
    plan[0]['clips'].append({'start_ms': 0, 'end_ms': 5000})
    with pytest.raises(ValueError, match='最多 30 段'):
        validate_edit_plan(plan, source_duration_ms=5000, settings=settings)


@pytest.mark.parametrize('clip', [{'start_ms': -1, 'end_ms': 10000},
                                  {'start_ms': 3000, 'end_ms': 3000},
                                  {'start_ms': 3000, 'end_ms': 2000},
                                  {'start_ms': 0, 'end_ms': 30001}])
def test_reuse_does_not_allow_invalid_source_bounds(clip):
    with pytest.raises(ValueError, match='范围'):
        validate_edit_plan([{'candidate_id': 'reuse', 'title': '有效范围', 'clips': [clip]}],
                           source_duration_ms=30000, settings=_settings(target_count=1))


def test_short_source_can_queue_and_reused_plan_survives_reopen_and_retry(tmp_path):
    store = AutoEditProjectStore(tmp_path / 'reuse.sqlite3')
    project = store.create_project(title='重复不是消耗', source={**_source(tmp_path), 'duration_ms': 10000},
                                   settings=_settings(target_count=2, duration_min_ms=30000, duration_max_ms=30000))
    assert generation_check(project) == {'ready': True, 'blockers': []}
    store.enqueue(project['project_id'], expected_revision=1)
    store.claim_next(available_skills=_skills(), worker_id='first')
    clips = [{'start_ms': 0, 'end_ms': 10000}] * 3
    saved = store.save_plan(project['project_id'], worker_id='first', selected_skill_id='skill_a', reason='同一片段再次展示',
                            plan=[{'candidate_id': f'c{i}', 'title': f'成片{i}', 'clips': clips} for i in range(2)])
    assert all(c['clips'] == clips and c['duration_ms'] == 30000 for c in saved['plan'])
    store.mark_rendering(project['project_id'], worker_id='first')
    failed = store.fail(project['project_id'], message='模拟渲染中断', worker_id='first')
    reopened = AutoEditProjectStore(store.database_path)
    pending = reopened.enqueue(project['project_id'], expected_revision=failed['revision'])
    assert pending['status'] == 'render_pending'
    assert pending['plan'] == saved['plan']
    assert pending['render_generation'] != failed['render_generation']


def test_subtitle_projection_repeats_per_occurrence_on_output_timeline():
    clips = [{'start_ms': 0, 'end_ms': 1000}, {'start_ms': 2000, 'end_ms': 3000}, {'start_ms': 0, 'end_ms': 1000}]
    cues = worker._subtitle_segments({'clips': clips}, [
        {'start_ms': 0, 'end_ms': 1000, 'text': '甲'}, {'start_ms': 2000, 'end_ms': 3000, 'text': '乙'},
    ])
    assert cues == [{'start_ms': 0, 'end_ms': 1000, 'text': '甲'},
                    {'start_ms': 1000, 'end_ms': 2000, 'text': '乙'},
                    {'start_ms': 2000, 'end_ms': 3000, 'text': '甲'}]


def test_gateway_explicitly_allows_reuse_and_preserves_model_clip_order(tmp_path, monkeypatch):
    source = {**_source(tmp_path), 'duration_ms': 30000}
    clips = [{'start_ms': 10000, 'end_ms': 20000}, {'start_ms': 0, 'end_ms': 10000}, {'start_ms': 10000, 'end_ms': 20000}]
    project = {'project_id': 'auto_edit_' + 'a'*32, 'source': source,
               'settings': _settings(target_count=1), 'eligible_skill_snapshots': _skills()[:1]}
    frame = tmp_path / 'frame.jpg'
    frame.write_bytes(b'local mock frame; no external model')
    transcript = tmp_path / 'transcript.json'
    transcript.write_text(json.dumps({'segments': [{'start_ms': 0, 'end_ms': 30000, 'text': '本地模拟口播'}]}), encoding='utf-8')
    result_path = tmp_path / 'result.json'
    result_path.write_text(json.dumps({'asr': {'status': 'completed'}, 'artifacts': {'transcript_path': str(transcript)},
                                      'shots': [{'id': 'shot', 'start_ms': 0, 'end_ms': 30000, 'keyframe_path': str(frame)}]}), encoding='utf-8')
    monkeypatch.setattr(worker, 'MediaPipeline', lambda **k: SimpleNamespace(process=lambda *a, **kw: result_path))
    gateway = SimpleNamespace(has_purpose=lambda p: True, for_purpose=lambda p: object())
    monkeypatch.setattr(worker, 'GatewaySettingsStore', lambda *a: SimpleNamespace(load=lambda: gateway))
    captured = {}
    def respond(config, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(content={'selected_skill_id': 'skill_a', 'selection_reason': '重复细节', 'analysis_summary': '模拟',
                              'candidates': [{'candidate_id': 'repeat', 'title': '回放', 'source_id': source['source_id'], 'clips': clips}]})
    monkeypatch.setattr(worker, 'call_gateway', respond)
    returned = worker.analyze_and_plan_with_gateway(project, root=tmp_path / 'work')
    context = json.loads(captured['context_json'])
    assert '同一成片' in context['clip_reuse_policy'] and '重复' in context['clip_reuse_policy']
    assert '成片播放顺序' in context['clip_reuse_policy']
    assert '每次' in context['clip_reuse_policy'] and '时长' in context['clip_reuse_policy']
    assert '不得重叠' not in context['task']
    assert '重复' in captured['developer_instructions']
    assert captured['output_schema']['properties']['candidates']['items']['properties']['clips']['maxItems'] == 30
    assert returned[3][0]['clips'] == clips
