"""Regression: model metadata cannot strand already-rendered media."""
import copy
import hashlib
import json
import subprocess

import pytest

from content_factory_api import auto_edit_worker as worker, speech_captions, subtitle_editor
from content_factory_api.auto_edit_store import AutoEditProjectStore, validate_edit_plan
from test_auto_edit_projects import _settings, _skills, _source


@pytest.mark.parametrize('candidate_id', ['c4_banxing_zh展sh', '../escape', 'x'*101, 'a/b', 'a\\b', 'NUL', 'Con'])
def test_local_plan_rejects_unsafe_internal_ids(candidate_id):
    with pytest.raises(ValueError, match='编号'):
        validate_edit_plan([{'candidate_id': candidate_id, 'title': '正版型',
                             'clips': [{'start_ms': 0, 'end_ms': 20000}]}],
                           source_duration_ms=300000, settings=_settings(target_count=1))


@pytest.mark.parametrize('changes', [dict(title='裤'*201), dict(selection_reason='证'*20001),
                                    dict(clips=[{'start_ms': i*1000, 'end_ms': (i+1)*1000} for i in range(31)])])
def test_plan_rejects_metadata_before_render(changes):
    plan={'candidate_id':'one','title':'一条','clips':[{'start_ms':0,'end_ms':20000}], **changes}
    with pytest.raises(ValueError):
        validate_edit_plan([plan], source_duration_ms=300000, settings=_settings(target_count=1))


def test_plan_refuses_windows_case_insensitive_collision():
    plans=[{'candidate_id':value,'title':'一条','clips':[{'start_ms':0,'end_ms':20000}]} for value in ('Clip_1','clip_1')]
    with pytest.raises(ValueError,match='重复'):
        validate_edit_plan(plans,source_duration_ms=300000,settings=_settings(target_count=2))


def test_explicit_regeneration_gets_new_output_generation(tmp_path):
    store=AutoEditProjectStore(tmp_path/'projects.sqlite3')
    p=store.create_project(title='222',source=_source(tmp_path),settings=_settings(target_count=1))
    first=store.enqueue(p['project_id'],expected_revision=p['revision'])
    failed=store.fail(p['project_id'],message='planner failed')
    second=store.enqueue(p['project_id'],expected_revision=failed['revision'])
    assert first['render_generation'] != second['render_generation']


@pytest.mark.parametrize('failure_stage', ['import', 'inherit'])
def test_registration_retry_reuses_videos_and_word_timing_without_paid_planner(tmp_path,monkeypatch,failure_stage):
    monkeypatch.setenv('CONTENT_FACTORY_S7_DATA_DIR',str(tmp_path/'s7'))
    source=tmp_path/'原素材.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=96x160:r=10',
                    '-f','lavfi','-i','sine=frequency=440','-t','6','-c:v','libx264','-c:a','aac',str(source)],
                   check=True,capture_output=True)
    store=AutoEditProjectStore(tmp_path/'projects.sqlite3')
    p=store.create_project(title='222',source={'source_id':'source_01','file_name':source.name,'path':str(source),
                          'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'duration_ms':6000},
                          settings=_settings(target_count=1,duration_min_ms=5000,duration_max_ms=6000))
    store.enqueue(p['project_id'],expected_revision=p['revision'])
    words=[{'text':'裤子','start_ms':100,'end_ms':500,'probability':.99}]
    monkeypatch.setattr(speech_captions,'recognize',lambda *a,**k:{'words':words,'raw_segments':[]})
    real_import=worker.import_batch
    real_inherit=subtitle_editor.inherit_draft
    def fail(*a,**kw):
        raise RuntimeError('test registration interruption')
    monkeypatch.setattr(worker if failure_stage=='import' else subtitle_editor,
                        'import_batch' if failure_stage=='import' else 'inherit_draft',fail)
    result=worker.process_one(store,available_skills=_skills(),worker_id='first',root=tmp_path/'render',
        planner=lambda p:('skill_a','音画证据','真实渲染测试',[{'candidate_id':'c4_banxing_zh展sh','title':'版型展示',
                        'clips':[{'start_ms':0,'end_ms':5000}]}]))
    assert result['status']=='failed'
    assert result.get('registration_checkpoint'),result
    manifest=next((tmp_path/'render').rglob('manifest.json'))
    data=json.loads(manifest.read_text(encoding='utf-8'))
    candidate_id=data['candidates'][0]['id']
    assert candidate_id.isascii() and candidate_id != 'c4_banxing_zh展sh'
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in manifest.parent.iterdir() if p.is_file()}
    frozen=copy.deepcopy(result['plan'])
    retry=store.enqueue(result['project_id'],expected_revision=result['revision'])
    assert retry['plan']==frozen
    assert retry['render_generation']==result['render_generation']
    monkeypatch.setattr(worker,'import_batch',real_import)
    monkeypatch.setattr(subtitle_editor,'inherit_draft',real_inherit)
    monkeypatch.setattr(worker,'_run',lambda *a,**kw:pytest.fail('Retry must not re-encode'))
    done=worker.process_one(store,available_skills=[],worker_id='resume',root=tmp_path/'render',
                           planner=lambda p:pytest.fail('Retry must not request a paid plan'))
    assert done['status']=='review' and done['progress']==100
    assert done['output_batch_id']==data['id']
    assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in manifest.parent.iterdir() if p.is_file()}
    draft=subtitle_editor.get_draft(data['id'],candidate_id)
    assert draft['document']['cues'][0]['words']==words
    assert draft['document']['mode']=='reveal'


def test_interrupted_saved_plan_runs_without_new_model_call(tmp_path,monkeypatch):
    store=AutoEditProjectStore(tmp_path/'projects.sqlite3')
    p=store.create_project(title='恢复',source=_source(tmp_path),settings=_settings(target_count=1))
    store.enqueue(p['project_id'],expected_revision=1)
    store.claim_next(available_skills=_skills(),worker_id='old')
    store.save_plan(p['project_id'],worker_id='old',selected_skill_id='skill_a',reason='证据',
                    plan=[{'candidate_id':'one','title':'一条','clips':[{'start_ms':0,'end_ms':20000}]}])
    store.recover_interrupted()
    monkeypatch.setattr(worker,'render_and_register',lambda s,p,root:s.register_output_batch(p['project_id'],batch_id='recovered'))
    result=worker.process_one(store,available_skills=[],worker_id='new',root=tmp_path,
                              planner=lambda p:pytest.fail('Persisted plan must not be regenerated'))
    assert result['status']=='review'
