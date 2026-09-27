"""Invalid model output remains inspectable without rendering or paid retries."""
import json
from content_factory_api import auto_edit_worker as worker
from content_factory_api.auto_edit_store import AutoEditProjectStore
from test_auto_edit_projects import _settings, _skills, _source


def test_invalid_duration_preserves_exact_plan_before_validation(tmp_path,monkeypatch):
    store=AutoEditProjectStore(tmp_path/'projects.sqlite3')
    p=store.create_project(title='222',source=_source(tmp_path),settings=_settings(target_count=1))
    queued=store.enqueue(p['project_id'],expected_revision=p['revision'])
    plan=[{'candidate_id':'one','title':'真实计划','clips':[{'start_ms':0,'end_ms':41000}]}]
    monkeypatch.setattr(worker,'render_and_register',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('invalid plan must not render')))
    result=worker.process_one(store,available_skills=_skills(),worker_id='one',root=tmp_path/'work',
                             planner=lambda p:('skill_a','真实来源','摘要',plan))
    assert result['status']=='failed'
    snapshots=list((tmp_path/'work'/'plan-snapshots').rglob('*.json'))
    assert len(snapshots)==1
    snapshot=json.loads(snapshots[0].read_text(encoding='utf-8'))
    assert snapshot['plan']==plan
    assert snapshot['render_generation']==queued['render_generation']
    assert snapshot['project_id']==p['project_id']
    assert snapshot['settings']['duration_max_ms']==40000
    assert not result.get('output_batch_id')
