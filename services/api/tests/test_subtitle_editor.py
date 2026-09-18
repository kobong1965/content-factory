import pytest
from test_edit_batches import actual_video, client, manifest, _import
from copy import deepcopy
from pathlib import Path
from content_factory_api.subtitle_editor import validate_document, merge_sentence_cues

def test_sentence_tail_is_not_split_or_shortened():
    result=merge_sentence_cues([{'start_ms':0,'end_ms':2500,'text':'下单的时候一定要看清楚'},{'start_ms':2500,'end_ms':3100,'text':'尺码'}])
    assert result == [{'start_ms':0,'end_ms':3100,'text':'下单的时候一定要看清楚尺码'}]

def test_document_rejects_overlap_and_invalid_position():
    doc={'cues':[{'start_ms':0,'end_ms':2000,'text':'整句话'}], 'x':50,'y':80,'font':'yahei','size':68,'effect':'none'}
    assert validate_document(doc,3000)['cues'][0]['text']=='整句话'
    with pytest.raises(ValueError): validate_document({**doc,'x':101},3000)
    with pytest.raises(ValueError): validate_document({**doc,'cues':doc['cues']+[{'start_ms':1000,'end_ms':2500,'text':'重叠'}]},3000)


def test_saved_draft_conflict_invalid_input_and_reopen(client, manifest):
    batch=_import(client,manifest)
    url=f"/s7/subtitle-editor/{batch['id']}/candidate-01"
    draft=client.get(url).json()
    draft.pop('duration_ms')
    draft['document']['cues'][0]['text']='这条裤子的腰头'
    result=client.put(url,json=draft)
    assert result.status_code==200,result.text
    assert client.put(url,json=draft).status_code==409
    invalid=deepcopy(result.json());invalid.pop('duration_ms');invalid['document']['y']=110
    assert client.put(url,json=invalid).status_code==422
    assert client.get(url).json()==result.json()


@pytest.mark.parametrize('empty',[False,True])
def test_real_preview_render_new_pending_batch_preserves_original(client,manifest,empty):
    batch=_import(client,manifest)
    original=Path(batch['candidates'][0]['resources']['video']['path']).read_bytes()
    url=f"/s7/subtitle-editor/{batch['id']}/candidate-01"
    draft=client.get(url).json();draft.pop('duration_ms')
    # This fixture intentionally exercises legacy manually timed sentence captions.
    draft['document'].update(x=45,y=75,mode='sentence')
    if empty: draft['document']['cues']=[]
    saved=client.put(url,json=draft).json();saved.pop('duration_ms')
    assert client.post(url+'/prepare').status_code==200
    assert client.get(url+'/preview').headers['content-type']=='video/mp4'
    result=client.post(url+'/render',json=saved)
    assert result.status_code==200,result.text
    new=result.json()
    assert new['id']!=batch['id']
    assert new['candidates'][0]['review_status']=='pending'
    assert client.get(f"/s7/subtitle-editor/{new['id']}/candidate-01").json()['document']==saved['document']
    assert Path(new['candidates'][0]['resources']['video']['path']).stat().st_size>1000
    assert Path(batch['candidates'][0]['resources']['video']['path']).read_bytes()==original
    assert client.post(url+'/render',json=saved).json()['id']==new['id']
    if not empty:
        ass=Path(new['candidates'][0]['resources']['video']['path']).with_name('subtitles.ass').read_text(encoding='utf-8-sig')
        assert r'\pos(486,1440)' in ass


def test_context_suggestions_do_not_overwrite_draft(client,manifest,monkeypatch):
    from types import SimpleNamespace
    from content_factory_api.s3_settings import GatewaySettingsStore
    from content_factory_api import s3_gateway
    batch=_import(client,manifest)
    url=f"/s7/subtitle-editor/{batch['id']}/candidate-01"
    draft=client.get(url).json();draft.pop('duration_ms')
    gateway=SimpleNamespace(has_purpose=lambda _:True,for_purpose=lambda _:None)
    monkeypatch.setattr(GatewaySettingsStore,'load',lambda _:gateway)
    def fake(config,**kwargs):
        assert '没有听到音频' in kwargs['developer_instructions']
        assert '腰头' in kwargs['context_json']
        return SimpleNamespace(content={'suggestions':[{'index':0,'text':'看一下这条裤子的腰头。','reason':'补充句末标点','uncertain':False}]})
    monkeypatch.setattr(s3_gateway,'call_gateway',fake)
    response=client.post(url+'/suggest',json=draft)
    assert response.status_code==200,response.text
    assert response.json()['suggestions'][0]['text'].endswith('。')
    assert client.get(url).json()['document']==draft['document']


def test_sentence_merge_does_not_bridge_detected_silence():
    cues=[{'start_ms':0,'end_ms':1000,'text':'看这条裤子'},{'start_ms':1300,'end_ms':2000,'text':'尺码'}]
    assert merge_sentence_cues(cues,silence=[(1000,1300)])==cues


def test_history_preserves_original_and_each_manual_revision(client,manifest):
    batch=_import(client,manifest)
    url=f"/s7/subtitle-editor/{batch['id']}/candidate-01"
    draft=client.get(url).json(); draft.pop('duration_ms')
    original=deepcopy(draft['document']['cues'])
    first=client.put(url,json=draft).json();first.pop('duration_ms')
    edited=deepcopy(first)
    edited['document']['cues'][0]['text']='人工确认的裤腰口播'
    second=client.put(url,json=edited)
    assert second.status_code==200,second.text
    history=client.get(url+'/history').json()
    assert history['original']==original
    assert [v['revision'] for v in history['versions']]==[2,1]
    assert history['versions'][0]['document']['cues'][0]['text']=='人工确认的裤腰口播'
    assert history['versions'][1]['document']['cues']==original
    assert client.get(url).json()==second.json()


def test_audio_recognition_returns_timed_preview_without_overwriting_manual_draft(client,manifest,monkeypatch):
    from content_factory_api import subtitle_editor
    batch=_import(client,manifest)
    url=f"/s7/subtitle-editor/{batch['id']}/candidate-01"
    draft=client.get(url).json();draft.pop('duration_ms')
    draft['document']['hotwords']='裤腰 裤脚口'
    clean=Path(batch['candidates'][0]['resources']['video']['path'])
    monkeypatch.setattr(subtitle_editor,'prepare_clean',lambda *_:clean)
    calls=[]
    def recognize(audio,workdir,**kwargs):
        assert audio==clean and audio.is_file()
        assert kwargs['hotwords']=='裤腰 裤脚口'
        calls.append(kwargs)
        return {'engine':'test-acoustic-fixture','raw_segments':[{'text':'看裤腰'}], 'words':[
            {'text':'看','start_ms':100,'end_ms':300,'probability':.98},
            {'text':'裤腰','start_ms':400,'end_ms':900,'probability':.9},
        ]}
    monkeypatch.setattr(subtitle_editor,'recognize',recognize)
    result=client.post(url+'/speech',json=draft)
    assert result.status_code==200,result.text
    assert calls[0]['cues'] is None
    proposed=result.json()['document']
    assert proposed['mode']=='reveal'
    assert proposed['cues'][0]['text']=='看裤腰'
    preview=client.post(url+'/events',json={'revision':draft['revision'],'document':proposed})
    assert preview.status_code==200,preview.text
    assert [(e['start_ms'],e['text']) for e in preview.json()['events']]==[(100,'看'),(400,'看裤腰')]
    assert client.get(url).json()['document']['cues']==draft['document']['cues']
    aligned=client.post(url+'/speech?align=true',json={'revision':0,'document':proposed})
    assert aligned.status_code==200,aligned.text
    assert calls[1]['cues']==proposed['cues']


def test_manual_correction_persists_but_stale_word_timings_cannot_preview_or_render(client,manifest):
    batch=_import(client,manifest)
    url=f"/s7/subtitle-editor/{batch['id']}/candidate-01"
    draft=client.get(url).json();draft.pop('duration_ms')
    draft['document']['mode']='reveal'
    draft['document']['cues']=[{'start_ms':100,'end_ms':900,'text':'看裤腰','words':[
        {'text':'看','start_ms':100,'end_ms':300},
        {'text':'库腰','start_ms':400,'end_ms':900},
    ]}]
    saved=client.put(url,json=draft)
    assert saved.status_code==200,saved.text
    request=saved.json();request.pop('duration_ms')
    assert request['document']['cues'][0]['text']=='看裤腰'
    assert 'words' not in request['document']['cues'][0]
    assert client.post(url+'/events',json=request).status_code==422
    assert client.post(url+'/render',json=request).status_code==422
    assert client.get(url).json()['document']==request['document']


def test_per_cue_audio_review_uses_context_and_preserves_other_manual_cues(client,manifest,monkeypatch):
    from content_factory_api import subtitle_editor
    import wave
    batch=_import(client,manifest)
    url=f"/s7/subtitle-editor/{batch['id']}/candidate-01"
    draft=client.get(url).json();draft.pop('duration_ms')
    draft['document']['cues']=[
        {'start_ms':0,'end_ms':200,'text':'保留前句'},
        {'start_ms':1500,'end_ms':1700,'text':'库腰'},
        {'start_ms':1800,'end_ms':1950,'text':'保留后句'},
    ]
    saved=client.put(url,json=draft).json();saved.pop('duration_ms')
    def recognize(audio,folder,**kwargs):
        assert audio.name=='review-context.wav' and audio.is_file()
        with wave.open(str(audio),'rb') as wav:
            assert wav.getframerate()==16000 and wav.getnchannels()==1
            # Starts 1200ms before target; context extends to source end.
            assert 1.65<=wav.getnframes()/wav.getframerate()<=1.75
        assert kwargs['cues'] is None
        return {'engine':'test-audio-context','raw_segments':[], 'words':[
            {'text':'前文','start_ms':100,'end_ms':300,'probability':.99},
            {'text':'裤','start_ms':1200,'end_ms':1290,'probability':.99},
            {'text':'腰','start_ms':1300,'end_ms':1400,'probability':.99},
            {'text':'后文','start_ms':1500,'end_ms':1600,'probability':.99},
        ]}
    monkeypatch.setattr(subtitle_editor,'recognize',recognize)
    response=client.post(url+'/speech?cue_index=1',json=saved)
    assert response.status_code==200,response.text
    cues=response.json()['document']['cues']
    assert cues[0]==saved['document']['cues'][0]
    assert cues[-1]==saved['document']['cues'][-1]
    assert cues[1]['text']=='裤腰'
    assert (cues[1]['start_ms'],cues[1]['end_ms'])==(1500,1700)
    assert [w['start_ms'] for w in cues[1]['words']]==[1500,1600]
    assert client.get(url).json()['document']==saved['document']


def test_audio_realign_preserves_manually_split_short_cues(client,manifest,monkeypatch):
    from content_factory_api import subtitle_editor
    batch=_import(client,manifest)
    url=f"/s7/subtitle-editor/{batch['id']}/candidate-01"
    draft=client.get(url).json();draft.pop('duration_ms')
    draft['document']['cues']=[{'start_ms':100,'end_ms':900,'text':'看裤腰'}, {'start_ms':900,'end_ms':1800,'text':'有弹力'}]
    def recognize(audio,folder,**kwargs):
        assert kwargs['cues']==draft['document']['cues']
        return {'engine':'test-align','raw_segments':[], 'words':[
            {'text':'看','start_ms':100,'end_ms':300,'probability':.99},
            {'text':'裤腰','start_ms':400,'end_ms':900,'probability':.99},
            {'text':'有','start_ms':900,'end_ms':1100,'probability':.99},
            {'text':'弹力','start_ms':1200,'end_ms':1800,'probability':.99},
        ]}
    monkeypatch.setattr(subtitle_editor,'recognize',recognize)
    response=client.post(url+'/speech?align=true',json=draft)
    assert response.status_code==200,response.text
    cues=response.json()['document']['cues']
    assert [(c['text'],c['start_ms'],c['end_ms']) for c in cues]==[('看裤腰',100,900),('有弹力',900,1800)]
    assert [len(c['words']) for c in cues]==[2,2]
