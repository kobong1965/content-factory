"""Versioned product notes and non-destructive material selection, separate from confirmed facts."""
import json
from .s4_store import ProductConflictError


def _init(store):
    with store._connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS product_workspaces (product_id TEXT PRIMARY KEY REFERENCES products(product_id), revision INTEGER NOT NULL, payload TEXT NOT NULL)')


def read_workspace(store, product_id):
    store.get(product_id)
    _init(store)
    with store._connect() as c:
        row = c.execute('SELECT payload FROM product_workspaces WHERE product_id=?', (product_id,)).fetchone()
    return json.loads(row['payload']) if row else {'revision': 0, 'notes': '', 'selected_asset_ids': [], 'primary_asset_id': None}


def save_workspace(store, product_id, *, expected_revision, notes, selected_asset_ids, primary_asset_id):
    _init(store)
    with store._lock, store._connect() as c:
        c.execute('BEGIN IMMEDIATE')
        profile = store._profile(c.execute('SELECT profile_json FROM products WHERE product_id=?', (product_id,)).fetchone())
        sources = {s['asset_id']: s for s in profile['sources'] if s.get('asset_id')}
        ids = list(dict.fromkeys(selected_asset_ids))
        if any(x not in sources for x in ids):
            raise ValueError('不能选择其他商品或不存在的素材')
        if primary_asset_id and (primary_asset_id not in sources or sources[primary_asset_id]['kind'] != 'image'):
            raise ValueError('主图必须是当前商品的图片')
        if len(notes) > 20000:
            raise ValueError('商品资料不能超过 20000 字')
        row = c.execute('SELECT revision FROM product_workspaces WHERE product_id=?', (product_id,)).fetchone()
        revision = row['revision'] if row else 0
        if revision != expected_revision:
            raise ProductConflictError('资料已在其他窗口更新，请重新打开后再保存')
        value = {'revision': revision+1, 'notes': notes, 'selected_asset_ids': ids, 'primary_asset_id': primary_asset_id}
        c.execute('INSERT INTO product_workspaces VALUES(?,?,?) ON CONFLICT(product_id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload', (product_id, revision+1, json.dumps(value, ensure_ascii=False)))
        return value
