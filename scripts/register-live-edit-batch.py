"""Explicit local import; only the footage-batch store is opened."""
import argparse
import json
import os
from pathlib import Path
import sys

parser=argparse.ArgumentParser()
parser.add_argument('--data-root',required=True)
parser.add_argument('--manifest',required=True)
parser.add_argument('--receipt',required=True)
args=parser.parse_args()
repo=Path(__file__).resolve().parents[1]
for rel in ['services/api/src','workers/media/src','packages/contracts/python']:
    sys.path.insert(0,str(repo/rel))
os.environ['CONTENT_FACTORY_S7_DATA_DIR']=str(Path(args.data_root).resolve())
from content_factory_api.edit_batches import import_batch, ImportRequest
batch=import_batch(ImportRequest(manifest_path=str(Path(args.manifest).resolve())))
receipt=Path(args.receipt); receipt.parent.mkdir(parents=True,exist_ok=True)
receipt.write_text(json.dumps({'batch_id':batch['id'],'revision':batch['revision'],'candidates':len(batch['candidates']),
                               'statuses':[c['review_status'] for c in batch['candidates']]},ensure_ascii=False,indent=2),encoding='utf-8')
print(receipt.read_text(encoding='utf-8'))
