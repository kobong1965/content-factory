"""Script renders share the finished library without bypassing media identity."""

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from content_factory_api import s4, s7
from content_factory_api.main import app
from content_factory_api.s7_store import EditStore


@pytest.mark.parametrize('fixture_data', [False, True])
def test_script_output_library_uses_real_product_and_rejects_changed_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture_data: bool,
) -> None:
    fixtures = Path(__file__).resolve().parents[3] / 'packages/contracts/fixtures'
    project = json.loads((fixtures / 'edit-project.valid.json').read_text(encoding='utf-8'))
    output = json.loads((fixtures / 'render-output.valid.json').read_text(encoding='utf-8'))
    project['fixture_data'] = output['fixture_data'] = fixture_data
    store = EditStore(tmp_path / 'script-store')
    store.create_project(project, actor='测试编辑')
    folder = store.output_root / output['output_id']
    folder.mkdir()
    # This integration verifies resource identity and HTTP bytes, not decoding.
    video_bytes = b'local-script-render-resource-for-library-test'
    resources = {}
    for kind, reference in output['resources'].items():
        if kind == 'jianying_experimental':
            continue
        path = folder / f'{kind}.bin'
        content = video_bytes if kind == 'video_ref' else f'local-{kind}'.encode()
        path.write_bytes(content)
        resources[reference] = (path, 'video/mp4' if kind == 'video_ref' else 'application/octet-stream', path.name)
    output['media']['sha256'] = hashlib.sha256(video_bytes).hexdigest()
    output['media']['size_bytes'] = len(video_bytes)
    store.create_output(output, resources=resources)
    monkeypatch.setenv('CONTENT_FACTORY_S7_DATA_DIR', str(tmp_path / 'footage-store'))
    monkeypatch.setattr(s7, 'get_edit_store', lambda: store)

    def get_product(product_id):
        assert product_id == project['product_id']
        return {'id': product_id, 'sku': '实测裤款-2026', 'name': '真实测试裤款名称'}

    monkeypatch.setattr(s4, 'get_product_store', lambda: SimpleNamespace(get=get_product))
    client = TestClient(app)  # No lifespan: never initialize unrelated production queues.
    initial = client.get('/s7/footage-batches/library')
    assert initial.status_code == 200, initial.text
    assert initial.json()['total'] == 0, '未审核脚本成片不得提前入库'
    store.review_output(output['output_id'], decision='approved', reviewer='测试审核', note='核对完成')
    response = client.get('/s7/footage-batches/library')
    assert response.status_code == 200, response.text
    library = response.json()
    if fixture_data:
        assert library['total'] == 0 and library['groups'] == [], '演示成片不得进入真实成片素材库'
        return
    assert library['total'] == 1 and len(library['groups']) == 1
    group = library['groups'][0]
    assert group['sku'] == '实测裤款-2026' and group['count'] == 1
    item = group['items'][0]
    assert item['batch_title'] == '真实测试裤款名称'
    assert item['candidate']['id'] == output['output_id']
    assert item['candidate']['review_status'] == 'approved'
    url = f"/s7/outputs/{output['output_id']}/resources/{output['resources']['video_ref']}"
    assert item['video_url'] == url
    downloaded = client.get(url, params={'download': 'true'})
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == video_bytes
    assert 'attachment' in downloaded.headers['content-disposition']
    assert TestClient(app).get('/s7/footage-batches/library').json() == library
    video_path = resources[output['resources']['video_ref']][0]
    original_stat = video_path.stat()
    video_path.write_bytes(bytes([video_bytes[0] ^ 1]) + video_bytes[1:])
    os.utime(video_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    unavailable = client.get('/s7/footage-batches/library')
    assert unavailable.status_code == 200, unavailable.text
    assert unavailable.json()['total'] == 0 and unavailable.json()['groups'] == []
    assert client.get(url, params={'download': 'true'}).status_code == 409
