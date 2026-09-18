"""HTTP regression tests with isolated stores and real multipart OOXML containers."""
import hashlib
import io
import json
import zipfile
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def api(tmp_path, monkeypatch):
    from content_factory_api import s4, s5
    from content_factory_api.s4_store import ProductStore
    root = tmp_path / 's4'
    monkeypatch.setenv('CONTENT_FACTORY_S4_DATA_DIR', str(root))
    app = FastAPI()
    app.include_router(s4.router)
    app.include_router(s5.router)
    with TestClient(app) as client:
        yield client, root, s4


def create(client, name='裤子', sku='J85'):
    response = client.post('/s4/products', json={'name': name, 'sku': sku, 'actor': 'HTTP验收'})
    assert response.status_code == 201, response.text
    return response.json()


def xlsx(formula=False, blank_last_header=False):
    """A complete OOXML workbook; formula is in an otherwise readable valid sheet."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="商品资料" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        rows=[]
        for n, values in enumerate([['商品名称', '款号', '' if blank_last_header else '颜色'], ['裤子', '0012', '黑色']], 1):
            cells = ''.join(f'<c r="{chr(65+i)}{n}" t="inlineStr"><is><t>{escape(v)}</t></is></c>' for i,v in enumerate(values))
            if n == 2 and formula:
                cells += '<c r="D2"><f>1+1</f><v>2</v></c>'
            rows.append(f'<row r="{n}">{cells}</row>')
        z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+''.join(rows)+'</sheetData></worksheet>')
    return out.getvalue()


@pytest.mark.parametrize('kind', ['csv', 'xlsx'])
def test_blank_trailing_header_preserves_real_data_in_http_preview(api, kind):
    client, _, _ = api
    data = xlsx(blank_last_header=True) if kind == 'xlsx' else '商品名称,款号,\n裤子,0012,黑色\n'.encode('utf-8-sig')
    response = client.post('/s4/product-sheet/preview', files={'upload': ('商品.' + kind, data)})
    assert response.status_code == 200, response.text
    assert response.json()['columns'] == ['商品名称', '款号', '']
    assert response.json()['rows'] == [['裤子', '0012', '黑色']]


@pytest.mark.parametrize('kind', ['csv', 'xlsx'])
def test_sheet_http_preview_import_reopen_and_idempotency(api, kind):
    client, root, s4 = api
    from content_factory_api.s4_store import ProductStore
    data = xlsx() if kind == 'xlsx' else '商品名称,款号,颜色\n裤子,0012,黑色\n'.encode('utf-8-sig')
    response = client.post('/s4/product-sheet/preview', files={'upload': ('商品.'+kind, data)})
    assert response.status_code == 200, response.text
    assert response.json()['rows'][0] == ['裤子', '0012', '黑色']
    assert ProductStore(root).list() == []
    payload = {'rows': [{'name': '裤子', 'sku': '0012', 'notes': '颜色：黑色（表格待确认）'}]}
    first = client.post('/s4/product-sheet/import', json=payload)
    assert first.status_code == 200, first.text
    assert client.post('/s4/product-sheet/import', json=payload).json() == first.json()
    store = ProductStore(root)
    assert len(store.list()) == 1
    product = store.get(first.json()[0]['product_id'])
    assert product['sku'] == '0012'
    assert product['facts'] == []
    s4._stores.pop(root.resolve(), None)
    state = client.get(f"/s4/products/{product['product_id']}/workspace").json()
    assert state['notes'] == payload['rows'][0]['notes']


def test_valid_xlsx_formula_is_explicitly_rejected(api):
    client, root, _ = api
    from content_factory_api.s4_store import ProductStore
    response = client.post('/s4/product-sheet/preview', files={'upload': ('含公式.xlsx', xlsx(formula=True))})
    assert response.status_code == 422, response.text
    assert '公式' in response.json()['detail']
    assert ProductStore(root).list() == []


@pytest.mark.parametrize('existing', [False, True])
def test_duplicate_sku_rolls_back_entire_http_batch(api, existing):
    client, root, _ = api
    from content_factory_api.s4_store import ProductStore
    if existing:
        create(client)
    before = ProductStore(root).list()
    rows = [{'name': '新款', 'sku': 'J72'}, {'name': '冲突', 'sku': 'J85' if existing else 'J72'}]
    response = client.post('/s4/product-sheet/import', json={'rows': rows})
    assert response.status_code == 422, response.text
    assert ProductStore(root).list() == before


def test_workspace_http_preserves_saved_notes_on_conflict_and_reopens(api):
    client, root, s4 = api
    product = create(client)
    url = f"/s4/products/{product['product_id']}/workspace"
    data = {'expected_revision': 0, 'notes': '成分未知\n仅确认后袋', 'selected_asset_ids': [], 'primary_asset_id': None}
    first = client.put(url, json=data)
    assert first.status_code == 200, first.text
    conflict = client.put(url, json={**data, 'notes': '不应覆盖'})
    assert conflict.status_code == 409
    s4._stores.pop(root.resolve(), None)
    assert client.get(url).json() == first.json()


def test_workspace_rejects_actual_other_product_asset_without_changes(api):
    from PIL import Image
    client, _, _ = api
    p1, p2 = create(client), create(client, sku='J72')
    image = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(image, format='PNG')
    asset = client.post(f"/s4/products/{p2['product_id']}/assets", data={'actor': 'HTTP验收', 'expected_revision': p2['revision']}, files={'upload': ('商品.png', image.getvalue(), 'image/png')})
    assert asset.status_code == 200, asset.text
    asset_id = asset.json()['source']['asset_id']
    url = f"/s4/products/{p1['product_id']}/workspace"
    before = client.get(url).json()
    response = client.put(url, json={'expected_revision': 0, 'notes': '不能串款', 'selected_asset_ids': [asset_id], 'primary_asset_id': asset_id})
    assert response.status_code == 422, response.text
    assert client.get(url).json() == before
    own_url = f"/s4/products/{p2['product_id']}/workspace"
    accepted = client.put(own_url, json={'expected_revision': 0, 'notes': '同款细节可复用', 'selected_asset_ids': [asset_id], 'primary_asset_id': asset_id})
    assert accepted.status_code == 200, accepted.text
    api[2]._stores.pop(api[1].resolve(), None)
    assert client.get(own_url).json() == accepted.json()
    assert client.get(f"/s4/products/{p2['product_id']}/assets/{asset_id}").content == image.getvalue()


def test_unknown_product_workspace_http_is_not_found(api):
    client, _, _ = api
    url = '/s4/products/product_does_not_exist/workspace'
    assert client.get(url).status_code == 404
    assert client.put(url, json={'expected_revision': 0, 'notes': '不创建幽灵商品'}).status_code == 404


def test_skill_media_http_range_and_unbound_refusal(api, tmp_path, monkeypatch):
    from content_factory_api import s2, s5, s5_sources
    client, _, _ = api
    root = tmp_path / 'media'
    root.mkdir()
    original = root / 'original.mp4'
    content = b'0123456789abcdef'
    original.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    video_id = 'video_' + digest[:24]
    result = root / 'result.json'
    result.write_text(json.dumps({'source': {'sha256': digest, 'managed_original_path': str(original)}, 'artifacts': {}}))
    skill = {'occurrences': [{'video_id': video_id, 'analysis_task_id': 'analysis_a'}]}
    monkeypatch.setattr(s5_sources, 'resolve_template', lambda *args: (None, None, None, skill))
    monkeypatch.setattr(s5, 'get_analysis_queue', lambda: SimpleNamespace(get=lambda _: SimpleNamespace(media_task_id='media_a')))
    monkeypatch.setattr(s5, 'get_viral_skill_store', lambda: object())
    monkeypatch.setattr(s2, '_queue', lambda: SimpleNamespace(get=lambda _: SimpleNamespace(result_path=str(result))))
    monkeypatch.setattr(s2, '_media_root', lambda: root)
    url = f'/s5/skills/skill_test/sources/{video_id}/media'
    response = client.get(url, headers={'Range': 'bytes=3-7'})
    assert response.status_code == 206
    assert response.content == content[3:8]
    assert response.headers['content-range'] == 'bytes 3-7/16'
    invalid = client.get('/s5/skills/skill_test/sources/video_unbound/media')
    assert invalid.status_code == 404
    assert str(root) not in invalid.text
    unsatisfied = client.get(url, headers={'Range': 'bytes=100-200'})
    assert unsatisfied.status_code == 416
