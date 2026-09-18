import pytest
from io import BytesIO
import zipfile


def test_csv_preview_preserves_identifier_and_no_write(tmp_path):
    from content_factory_api.product_sheet import preview_sheet, import_rows
    from content_factory_api.s4_store import ProductStore
    data='商品名称,款号,颜色\n裤子,0012,黑色\n'.encode('utf-8-sig')
    preview=preview_sheet(data, '商品.csv')
    assert preview['rows'][0][1]=='0012'
    store=ProductStore(tmp_path)
    assert not store.list()
    rows=[{'name':'裤子','sku':'0012','notes':'商品.csv 第2行：颜色=黑色，待确认'}]
    first=import_rows(store,rows)
    assert import_rows(store,rows)==first
    assert len(store.list())==1
    assert store.get(first[0]['product_id'])['facts']==[]


def test_batch_conflict_rolls_back_all_rows(tmp_path):
    from content_factory_api.product_sheet import import_rows
    from content_factory_api.s4_store import ProductStore
    store=ProductStore(tmp_path)
    store.create(name='旧款',sku='J85',actor='test')
    with pytest.raises(ValueError):
        import_rows(store,[{'name':'新款','sku':'J72','notes':''},{'name':'冲突','sku':'J85','notes':''}])
    assert len(store.list())==1


def test_xlsx_rejects_formula_and_zip_expansion():
    from content_factory_api.product_sheet import preview_sheet
    stream=BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        z.writestr('xl/worksheets/sheet1.xml','<worksheet><f>WEBSERVICE("https://bad")</f></worksheet>')
    with pytest.raises(ValueError):
        preview_sheet(stream.getvalue(),'data.xlsx')
