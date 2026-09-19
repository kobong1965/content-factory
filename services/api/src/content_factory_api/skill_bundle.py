"""Internal Skill snapshots, without API settings or execution of bundled code.

This package preserves embedded evidence, not source video files. A checksum
detects damage; it is not a publisher signature. Only import trusted team files.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sqlite3
from content_factory_contracts import validate_or_raise
from .s3_skills import ViralSkillStore


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def export_bundle(database: Path, destination: Path) -> dict:
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('BEGIN')
        skills = [json.loads(row[0]) for row in connection.execute("SELECT payload_json FROM viral_skills WHERE status='approved' ORDER BY skill_id")]
        candidates = []
        for skill in skills:
            validate_or_raise('viral_skill', skill)
            row = connection.execute('SELECT * FROM viral_skill_candidates WHERE candidate_id=?', (skill['origin_candidate_id'],)).fetchone()
            if row is None:
                raise ValueError('Skill 来源候选缺失，停止导出')
            candidates.append(dict(row))
    payload = {'schema': 1, 'skills': skills, 'candidates': candidates, 'source_videos_included': False}
    envelope = {'sha256': hashlib.sha256(_canonical(payload)).hexdigest(), 'payload': payload}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf-8') as output:
        json.dump(envelope, output, ensure_ascii=False, indent=2)
    return {'skills': len(skills), 'sha256': envelope['sha256']}


def import_bundle(package: Path, database: Path) -> dict:
    if package.stat().st_size > 32 * 1024**2:
        raise ValueError('Skill 包过大')
    envelope = json.loads(package.read_text('utf-8'))
    payload = envelope['payload']
    checksum = hashlib.sha256(_canonical(payload)).hexdigest()
    if checksum != envelope['sha256'] or payload.get('schema') != 1:
        raise ValueError('Skill 包已损坏或版本不受支持')
    skills = payload['skills']
    if not isinstance(skills, list) or len(skills) > 1000:
        raise ValueError('Skill 清单无效')
    candidates = {item['candidate_id']: item for item in payload['candidates']}
    for skill in skills:
        validate_or_raise('viral_skill', skill)
        if skill['status'] != 'approved' or skill['origin_candidate_id'] not in candidates:
            raise ValueError('Skill 未批准或来源不完整')
        candidate = candidates[skill['origin_candidate_id']]
        body = json.loads(candidate['payload_json'])
        if body['candidate_id'] != skill['origin_candidate_id']:
            raise ValueError('Skill 来源标识不一致')
    store = ViralSkillStore(database)
    result = {'imported': 0, 'preserved': [], 'package_id': checksum, 'source_videos_included': False}
    with store._connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('CREATE TABLE IF NOT EXISTS skill_package_imports (package_id TEXT PRIMARY KEY, result_json TEXT NOT NULL)')
        connection.execute('CREATE TABLE IF NOT EXISTS bundled_skill_sources (candidate_id TEXT PRIMARY KEY, package_id TEXT NOT NULL)')
        if connection.execute('SELECT 1 FROM skill_package_imports WHERE package_id=?', (checksum,)).fetchone():
            return result
        for skill in skills:
            candidate = candidates[skill['origin_candidate_id']]
            # Never replace user-owned records, including disabled or edited Skills.
            collision = connection.execute('SELECT 1 FROM viral_skills WHERE skill_id=? OR origin_candidate_id=?', (skill['skill_id'], skill['origin_candidate_id'])).fetchone()
            candidate_collision = connection.execute('SELECT 1 FROM viral_skill_candidates WHERE candidate_id=? OR cluster_key=?', (candidate['candidate_id'], candidate['cluster_key'])).fetchone()
            if collision or candidate_collision:
                result['preserved'].append(skill['skill_id'])
                continue
            columns = ('candidate_id', 'cluster_key', 'revision', 'active', 'content_hash', 'payload_json', 'refreshed_at')
            connection.execute('INSERT INTO viral_skill_candidates VALUES (?,?,?,?,?,?,?)', tuple(candidate[key] for key in columns))
            text = _canonical(skill).decode('utf-8')
            connection.execute('INSERT INTO viral_skills VALUES (?,?,?,?,?,?,?,?)', (skill['skill_id'], skill['origin_candidate_id'], skill['revision'], skill['status'], skill['reuse_mode'], text, skill['created_at'], skill['updated_at']))
            connection.execute('INSERT INTO viral_skill_versions VALUES (?,?,?,?,?,?)', (skill['skill_id'], skill['revision'], 'internal_package_import', 'internal-delivery', skill['updated_at'], text))
            result['imported'] += 1
            connection.execute('INSERT INTO bundled_skill_sources VALUES (?,?)', (candidate['candidate_id'], checksum))
        connection.execute('INSERT INTO skill_package_imports VALUES (?,?)', (checksum, json.dumps(result)))
    return result
