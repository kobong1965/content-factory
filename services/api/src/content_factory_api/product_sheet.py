"""Bounded local CSV/XLSX intake. No formula evaluation, links, macros or remote reads."""
import csv
import hashlib
import io
import json
import re
import sqlite3
import zipfile
import xml.etree.ElementTree as ET
from .s4_products import new_product
from .product_workspace import _init

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def _xml(raw):
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('表格包含不支持的 XML 声明')
    return ET.fromstring(raw)


def preview_sheet(data: bytes, filename: str, sheet_name: str | None = None):
    if len(data)>8*1024*1024:
        raise ValueError('表格最大 8 MB')
    names=['CSV']
    if filename.lower().endswith('.csv'):
        try:
            text=data.decode('utf-8-sig')
        except UnicodeDecodeError:
            text=data.decode('gb18030')
        matrix=list(csv.reader(io.StringIO(text)))
    elif filename.lower().endswith('.xlsx'):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if len(z.infolist())>1000 or sum(x.file_size for x in z.infolist())>32*1024*1024:
                    raise ValueError('表格解压内容过大')
                if any('vbaProject' in x or 'externalLinks/' in x for x in z.namelist()):
                    raise ValueError('不接受宏或外部链接，请另存纯值表格')
                workbook=_xml(z.read('xl/workbook.xml'))
                rels=_xml(z.read('xl/_rels/workbook.xml.rels'))
                mapping={r.attrib['Id']:r.attrib['Target'] for r in rels}
                sheets=workbook.findall('s:sheets/s:sheet',NS)
                names=[s.attrib['name'] for s in sheets]
                selected=next((s for s in sheets if s.attrib['name']==(sheet_name or '商品资料')),sheets[0])
                if sheet_name and sheet_name not in names:
                    raise ValueError('找不到选择的工作表')
                target=mapping[selected.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
                target=target.lstrip('/') if target.startswith('/') else 'xl/'+target
                if '..' in target.split('/'):
                    raise ValueError('工作表路径不合法')
                strings=[]
                if 'xl/sharedStrings.xml' in z.namelist():
                    strings=[''.join(x.itertext()) for x in _xml(z.read('xl/sharedStrings.xml'))]
                tree=_xml(z.read(target))
                if tree.findall('.//s:f',NS):
                    raise ValueError('包含公式，请复制并粘贴为值后再上传')
                matrix=[]
                for row in tree.findall('.//s:sheetData/s:row',NS):
                    values=['']*30
                    for cell in row:
                        letters=re.match(r'[A-Z]+',cell.attrib.get('r','')).group(0)
                        column=0
                        for c in letters: column=column*26+ord(c)-64
                        if column>30: raise ValueError('最多支持 30 列')
                        kind=cell.attrib.get('t')
                        value=cell.findtext('s:v',default='',namespaces=NS)
                        if kind=='s': value=strings[int(value)]
                        elif kind=='inlineStr': value=''.join(cell.find('s:is',NS).itertext())
                        values[column-1]=value
                    matrix.append(values)
        except (KeyError, IndexError, ET.ParseError, zipfile.BadZipFile, AttributeError) as exc:
            raise ValueError('无法读取 XLSX，请使用标准 Excel 工作簿或另存 CSV') from exc
    else:
        raise ValueError('仅支持 XLSX 或 CSV')
    if len(matrix)>505 or any(len(row)>30 for row in matrix):
        raise ValueError('一次最多 500 行、30 列，请分批导入')
    header_index=next((i for i,r in enumerate(matrix[:15]) if any(str(v).replace('*','').strip() in {'商品名称','商品名','名称'} for v in r)),0)
    if not matrix: raise ValueError('表格为空')
    columns=[str(x).strip() for x in matrix[header_index]]
    width = max((i + 1 for row in matrix[header_index:] for i, value in enumerate(row) if str(value).strip()), default=0)
    columns = (columns + [''] * width)[:width]
    rows=[([str(x).strip() for x in r]+['']*len(columns))[:len(columns)] for r in matrix[header_index+1:] if any(str(x).strip() for x in r)]
    if any(len(v)>20000 for r in rows for v in r): raise ValueError('单元格文字过长')
    return {'columns':columns,'rows':rows,'sheet_names':names,'header_row':header_index+1}


def import_rows(store, rows):
    if not rows or len(rows)>200: raise ValueError('请选择 1—200 个商品')
    canonical=[{'name':str(r['name']).strip(),'sku':str(r.get('sku') or '').strip(),'notes':str(r.get('notes') or '')} for r in rows]
    if any(not r['name'] or len(r['name'])>200 or len(r['sku'])>64 or len(r['notes'])>20000 for r in canonical):
        raise ValueError('商品名必填；名称/款号/资料不能超长')
    token=hashlib.sha256(json.dumps(canonical,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    _init(store)
    with store._lock, store._connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS product_sheet_imports (token TEXT PRIMARY KEY, result TEXT NOT NULL)')
        c.execute('BEGIN IMMEDIATE')
        prior=c.execute('SELECT result FROM product_sheet_imports WHERE token=?',(token,)).fetchone()
        if prior: return json.loads(prior['result'])
        result=[]
        try:
            for row in canonical:
                p=new_product(sku=row['sku'] or None,name=row['name'])
                c.execute('INSERT INTO products VALUES(?,?,?,?,?,?,?,?)',(p['product_id'],p['sku'],p['name'],p['status'],p['revision'],json.dumps(p,ensure_ascii=False),p['created_at'],p['updated_at']))
                store._insert_version(c,p,'created','表格导入（待确认资料）')
                workspace={'revision':1,'notes':row['notes'],'selected_asset_ids':[],'primary_asset_id':None}
                c.execute('INSERT INTO product_workspaces VALUES(?,?,?)',(p['product_id'],1,json.dumps(workspace,ensure_ascii=False)))
                result.append({'product_id':p['product_id'],'name':p['name'],'sku':p['sku']})
            c.execute('INSERT INTO product_sheet_imports VALUES(?,?)',(token,json.dumps(result,ensure_ascii=False)))
        except sqlite3.IntegrityError as exc:
            raise ValueError('表内或已有商品存在重复款号，整批未导入。请打开已有商品或修正款号') from exc
        return result
