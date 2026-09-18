import json
from pathlib import Path
import pytest


def test_cover_uses_bound_keyframe_and_rejects_escape(tmp_path):
    from content_factory_api.creator_media import bound_cover_file
    original = tmp_path / 'original.mp4'
    original.write_bytes(b'video')
    cover = tmp_path / 'shot.jpg'
    cover.write_bytes(b'jpeg')
    media = {'source': {'sha256': 'a'*64, 'managed_original_path': str(original)},
             'shots': [{'keyframe_path': str(cover)}]}
    assert bound_cover_file(media, 'video_'+'a'*24, tmp_path) == cover
    with pytest.raises(ValueError):
        bound_cover_file(media, 'video_'+'b'*24, tmp_path)
    media['shots'][0]['keyframe_path'] = str(tmp_path.parent / 'private.jpg')
    with pytest.raises(ValueError):
        bound_cover_file(media, 'video_'+'a'*24, tmp_path)


def test_playback_uses_bound_proxy_and_rejects_escape(tmp_path):
    from content_factory_api.creator_media import bound_media_file
    root = tmp_path / 'media'
    root.mkdir()
    proxy = root / 'proxy.mp4'
    proxy.write_bytes(b'video')
    original = root / 'original.mkv'
    original.write_bytes(b'original')
    media = {'source': {'sha256': 'a'*64, 'managed_original_path': str(original)}, 'artifacts': {'proxy_path': str(proxy)}}
    assert bound_media_file(media, 'video_'+'a'*24, root) == proxy
    with pytest.raises(ValueError):
        bound_media_file(media, 'video_'+'b'*24, root)
    media['artifacts']['proxy_path'] = str(tmp_path / 'secret.mp4')
    with pytest.raises(ValueError):
        bound_media_file(media, 'video_'+'a'*24, root)


def test_workspace_persists_selection_and_refuses_other_product(tmp_path):
    from content_factory_api.s4_store import ProductStore
    from content_factory_api.product_workspace import read_workspace, save_workspace
    store = ProductStore(tmp_path)
    p = store.create(name='裤子', actor='test')
    first = read_workspace(store, p['product_id'])
    saved = save_workspace(store, p['product_id'], expected_revision=first['revision'], notes='面料待确认', selected_asset_ids=[], primary_asset_id=None)
    assert read_workspace(ProductStore(tmp_path), p['product_id']) == saved
    assert saved['notes'] == '面料待确认'
    assert not store.get(p['product_id'])['facts']
    with pytest.raises(ValueError):
        save_workspace(store, p['product_id'], expected_revision=saved['revision'], notes='', selected_asset_ids=['asset_other'], primary_asset_id=None)
    with pytest.raises(RuntimeError):
        save_workspace(store, p['product_id'], expected_revision=first['revision'], notes='stale', selected_asset_ids=[], primary_asset_id=None)
