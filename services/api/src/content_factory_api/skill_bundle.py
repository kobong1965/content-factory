"""Internal Skill snapshots, without API settings or execution of bundled code.

This package preserves embedded evidence, not source video files. A checksum
detects damage; it is not a publisher signature. Only import trusted team files.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile
from content_factory_contracts import validate_or_raise
from .s3_skills import ViralSkillStore


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _envelope(payload: dict) -> tuple[dict, str]:
    checksum = hashlib.sha256(_canonical(payload)).hexdigest()
    return {'sha256': checksum, 'payload': payload}, checksum


def write_bundle(payload: dict, destination: Path) -> str:
    """Write one canonical, inert ``.cfskills`` file and return its checksum."""
    envelope, checksum = _envelope(payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf-8') as output:
        json.dump(envelope, output, ensure_ascii=False, indent=2)
    return checksum


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
    checksum = write_bundle(payload, destination)
    return {'skills': len(skills), 'sha256': checksum}


_EVIDENCE_UI_FIELDS = {'eligibility', 'source_status', 'update_available'}
_MAX_EVIDENCE_MEMBERS = 10_000
_MAX_EVIDENCE_METADATA_BYTES = 64 * 1024**2


def _safe_archive_name(name: str) -> str:
    normalized = name.replace('\\', '/')
    trimmed = normalized.rstrip('/')
    parts = trimmed.split('/') if trimmed else []
    if not parts or normalized.startswith('/') or ':' in normalized or any(part in ('', '..') for part in parts):
        raise ValueError('压缩包包含不安全路径')
    return normalized


def _normalise_evidence_skill(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError('证据包中的 Skill 不是对象')
    skill = dict(value)
    # The desktop export includes these view-only fields. They are not part of
    # the signed/imported Skill contract and must not be persisted as runtime data.
    for field in _EVIDENCE_UI_FIELDS:
        skill.pop(field, None)
    validate_or_raise('viral_skill', skill)
    if skill['status'] != 'approved' or skill['reuse_mode'] != 'reuse':
        raise ValueError(f"Skill {skill.get('name', '')} 尚未审核通过，不能导入")
    return skill


def evidence_archive_payload(archive: zipfile.ZipFile) -> dict:
    """Convert a Codex evidence ZIP into a compact runtime Skill payload.

    Only small JSON method snapshots are read. Videos, images, scripts and
    every other archive member remain outside the resulting package; nothing
    is extracted or executed. This lets users submit the original evidence
    archive without turning the application database into a media store.
    """
    members = archive.infolist()
    if len(members) > _MAX_EVIDENCE_MEMBERS:
        raise ValueError('证据包文件数量过多，请只保留 Skill 证据包')
    choices: dict[str, list[tuple[int, zipfile.ZipInfo]]] = {}
    metadata_bytes = 0
    for member in members:
        name = _safe_archive_name(member.filename)
        lower = name.lower()
        if not (lower.endswith('/software-skill-current.json') or lower.endswith('/software-skill.json')):
            continue
        if member.is_dir() or member.file_size > _MAX_EVIDENCE_METADATA_BYTES:
            raise ValueError('证据包中的 Skill 元数据过大')
        metadata_bytes += member.file_size
        if metadata_bytes > _MAX_EVIDENCE_METADATA_BYTES:
            raise ValueError('证据包中的 Skill 元数据总量过大')
        parent = name.rsplit('/', 1)[0]
        priority = 0 if lower.endswith('/software-skill-current.json') else 1
        choices.setdefault(parent, []).append((priority, member))
    if not choices:
        raise ValueError('ZIP 中未找到 references/**/software-skill.json')

    skills: list[dict] = []
    seen_ids: set[str] = set()
    seen_candidates: set[str] = set()
    candidates: list[dict] = []
    for parent in sorted(choices):
        selected = sorted(choices[parent], key=lambda item: item[0])
        skill = None
        last_error: Exception | None = None
        for _, member in selected:
            try:
                skill = _normalise_evidence_skill(json.loads(archive.read(member)))
                break
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                last_error = exc
        if skill is None:
            raise ValueError(f'证据包中的 Skill 无效: {last_error}')
        if skill['skill_id'] in seen_ids or skill['origin_candidate_id'] in seen_candidates:
            raise ValueError('证据包包含重复的 Skill 或来源候选')
        seen_ids.add(skill['skill_id'])
        seen_candidates.add(skill['origin_candidate_id'])
        candidate_id = skill['origin_candidate_id']
        candidate_body = {
            'candidate_id': candidate_id,
            'title': skill['name'],
            'pattern_id': 'pattern_' + hashlib.sha256(candidate_id.encode('utf-8')).hexdigest()[:32],
            'pattern_name': skill['name'],
            'pattern_mechanism': skill['mechanism'],
            'classification': skill['classification'],
            'evidence_level': skill['evidence_level'],
            'distinct_video_count': skill['distinct_video_count'],
            'occurrence_count': skill['occurrence_count'],
            'steps': skill['steps'],
            'necessary_conditions': skill['necessary_conditions'],
            'failure_signals': skill['failure_signals'],
            'occurrences': skill['occurrences'],
        }
        candidate_payload = _canonical(candidate_body).decode('utf-8')
        candidates.append({
            'candidate_id': candidate_id,
            'cluster_key': hashlib.sha256(_canonical({'skill_id': skill['skill_id'], 'candidate_id': candidate_id})).hexdigest(),
            'revision': int(skill['based_on_candidate_revision']),
            'active': 1,
            'content_hash': hashlib.sha256(candidate_payload.encode('utf-8')).hexdigest(),
            'payload_json': candidate_payload,
            'refreshed_at': skill['updated_at'],
        })
        skills.append(skill)
    return {'schema': 1, 'skills': skills, 'candidates': candidates, 'source_videos_included': False}


def inspect_bundle(package: Path) -> tuple[dict, str]:
    if package.stat().st_size > 32 * 1024**2:
        raise ValueError('Skill 包过大')
    envelope = json.loads(package.read_text('utf-8'))
    if not isinstance(envelope, dict) or not isinstance(envelope.get('payload'), dict):
        raise ValueError('Skill 包结构无效，需要结构化对象')
    payload = envelope['payload']
    checksum = hashlib.sha256(_canonical(payload)).hexdigest()
    if checksum != envelope['sha256'] or payload.get('schema') != 1:
        raise ValueError('Skill 包已损坏或版本不受支持')
    skills = payload['skills']
    if not isinstance(skills, list) or len(skills) > 1000:
        raise ValueError('Skill 清单无效')
    rows = payload.get('candidates')
    if not isinstance(rows, list) or len(rows) > 1000 or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Skill 来源清单无效')
    candidates = {item['candidate_id']: item for item in rows}
    if len(candidates) != len(rows):
        raise ValueError('Skill 来源标识重复')
    for skill in skills:
        validate_or_raise('viral_skill', skill)
        if skill['status'] != 'approved' or skill['origin_candidate_id'] not in candidates:
            raise ValueError('Skill 未批准或来源不完整')
        candidate = candidates[skill['origin_candidate_id']]
        body = json.loads(candidate['payload_json'])
        if not isinstance(body, dict) or body.get('candidate_id') != skill['origin_candidate_id']:
            raise ValueError('Skill 来源标识不一致')
    return payload, checksum


def import_bundle(package: Path, database: Path) -> dict:
    payload, checksum = inspect_bundle(package)
    skills = payload['skills']
    candidates = {item['candidate_id']: item for item in payload['candidates']}
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
