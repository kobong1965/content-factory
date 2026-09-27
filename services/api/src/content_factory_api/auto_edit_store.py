"""Persistent projects for source-led automatic editing.

This module owns project state only. Model selection and FFmpeg work are kept
behind workers so an interrupted process can resume from committed checkpoints.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4
from .subtitles import FONTS, EFFECTS
from .candidate_contract import artifact_safe_candidate_id

MAX_CLIPS_PER_CANDIDATE = 30
DURATION_POLICIES = {'custom', 'bounded_15_30'}

class AutoEditNotFoundError(LookupError):
    pass


class AutoEditConflictError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_title(value: str) -> str:
    title = " ".join(str(value).split()).strip()
    if not 1 <= len(title) <= 100:
        raise ValueError("项目名称应为 1—100 个字符")
    return title


def validate_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "target_count", "duration_min_ms", "duration_max_ms", "subtitle_font_size",
        "keyword_color", "keyword_scale", "top_title_enabled",
    }
    if not required.issubset(settings) or set(settings) - required - {'subtitle_font', 'subtitle_effect', 'subtitle_mode', 'duration_policy'}:
        raise ValueError("剪辑参数字段不完整")
    target_count = int(settings["target_count"])
    minimum = int(settings["duration_min_ms"])
    maximum = int(settings["duration_max_ms"])
    font_size = int(settings["subtitle_font_size"])
    keyword_scale = float(settings["keyword_scale"])
    color = str(settings["keyword_color"]).upper()
    font = str(settings.get('subtitle_font', 'heiti'))
    effect = str(settings.get('subtitle_effect', 'none'))
    mode = str(settings.get('subtitle_mode', 'reveal'))
    duration_policy = str(settings.get('duration_policy', 'custom'))
    if duration_policy not in DURATION_POLICIES:
        raise ValueError('请选择支持的时长策略')
    if mode not in {'sentence', 'reveal', 'highlight', 'auto', 'none'}:
        raise ValueError('请选择支持的字幕显示模式')
    if font not in FONTS or effect not in EFFECTS:
        raise ValueError('请选择支持的字幕字体和特效')
    if not 1 <= target_count <= 20:
        raise ValueError("成片数量必须为 1—20")
    if not 5_000 <= minimum <= 180_000 or not 5_000 <= maximum <= 180_000 or minimum > maximum:
        raise ValueError("成片时长必须为 5—180 秒，且最小时长不能大于最大时长")
    if duration_policy == 'bounded_15_30' and (minimum < 15_000 or maximum > 30_000):
        raise ValueError("完整片段模式的成片硬边界必须为 15—30 秒")
    if not 32 <= font_size <= 120:
        raise ValueError("字幕字号必须为 32—120")
    if not 1.0 <= keyword_scale <= 2.0:
        raise ValueError("重点词字号倍率必须为 1.0—2.0")
    if len(color) != 7 or color[0] != "#" or any(character not in "0123456789ABCDEF" for character in color[1:]):
        raise ValueError("重点词颜色必须是 #RRGGBB")
    return {
        "target_count": target_count,
        "duration_min_ms": minimum,
        "duration_max_ms": maximum,
        "subtitle_font_size": font_size,
        "keyword_color": color,
        "keyword_scale": keyword_scale,
        "top_title_enabled": bool(settings["top_title_enabled"]),
        'subtitle_font': font,
        'subtitle_effect': effect,
        'subtitle_mode': mode,
        'duration_policy': duration_policy,
    }


def validate_source(source: Mapping[str, Any]) -> dict[str, Any]:
    source_id = str(source.get("source_id") or "").strip()
    file_name = str(source.get("file_name") or "").strip()
    path = Path(str(source.get("path") or "")).expanduser().resolve()
    sha256 = str(source.get("sha256") or "").lower()
    duration_ms = int(source.get("duration_ms") or 0)
    if not source_id or not file_name or not path.is_file():
        raise ValueError("源素材文件不存在或资料不完整")
    if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
        raise ValueError("源素材指纹无效")
    if duration_ms < 5_000:
        raise ValueError("源素材不足 5 秒，无法建立剪辑项目")
    return {
        "source_id": source_id,
        "file_name": file_name,
        "path": str(path),
        "sha256": sha256,
        "duration_ms": duration_ms,
    }


def reusable_skill_snapshots(skills: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for skill in skills:
        if skill.get("status") != "approved" or skill.get("reuse_mode") != "reuse":
            continue
        if not skill.get("skill_id") or int(skill.get("revision") or 0) < 1:
            continue
        result.append(copy.deepcopy(dict(skill)))
    return sorted(result, key=lambda item: str(item["skill_id"]))


def generation_check(project: Mapping[str, Any]) -> dict[str, Any]:
    """Cheap local prerequisites only; never claims model/ASR quality is known.

    Drafts stay saveable. Each output uses one source and can reuse any range
    in playback order, up to the existing per-candidate piece limit.
    Registration recovery must not depend on new planning prerequisites.
    """
    if project['status'] == 'failed' and project.get('registration_checkpoint'):
        return {'ready': True, 'blockers': []}
    blockers = []
    sources = project.get('sources') or [project['source']]
    minimum = project['settings']['duration_min_ms']
    longest = max((source['duration_ms'] for source in sources), default=0)
    if longest * MAX_CLIPS_PER_CANDIDATE < minimum:
        blockers.append(f'每条成片最多 {MAX_CLIPS_PER_CANDIDATE} 段；最长原片 {longest / 1000:.3f} 秒重复使用后，'
                        f'仍不足最短成片 {minimum / 1000:.3f} 秒。请降低最短秒数或上传更长的素材。')
    for index, source in enumerate(sources, start=1):
        try:
            path = Path(source['path'])
            if not path.is_file() or path.stat().st_size == 0:
                raise OSError('missing or empty source')
            with path.open('rb') as handle:
                handle.read(1)
        except OSError:
            blockers.append(f'第 {index} 条素材无法读取，请恢复原文件访问或重新上传。')
    return {'ready': not blockers, 'blockers': blockers}


def require_generation_ready(project: Mapping[str, Any]) -> None:
    check = generation_check(project)
    if not check['ready']:
        raise ValueError('暂不支持生成：' + '；'.join(check['blockers']))


def validate_edit_plan(
    plan: Iterable[Mapping[str, Any]], *, source_duration_ms: int, settings: Mapping[str, Any],
    candidate_offset: int = 0,
) -> list[dict[str, Any]]:
    normalized_settings = validate_settings(settings)
    candidates = copy.deepcopy([dict(item) for item in plan])
    if len(candidates) != normalized_settings["target_count"]:
        raise ValueError("剪辑方案数量与用户设置不一致")
    seen: set[str] = set()
    for index, candidate in enumerate(candidates, start=candidate_offset + 1):
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        title = " ".join(str(candidate.get("title") or "").split()).strip()
        clips = candidate.get("clips")
        if not candidate_id or candidate_id.casefold() in seen or not title or not isinstance(clips, list) or not clips:
            raise ValueError("剪辑方案候选资料不完整或 ID 重复")
        if not artifact_safe_candidate_id(candidate_id):
            raise ValueError('成片内部编号只能使用 1—100 个英文字母、数字、下划线和短横线')
        if len(title) > 200 or len(clips) > MAX_CLIPS_PER_CANDIDATE or len(str(candidate.get('selection_reason') or '')) > 20000:
            raise ValueError('成片标题最多 200 字、选段最多 30 段、选段说明最多 20000 字')
        seen.add(candidate_id.casefold())
        total = 0
        normalized_clips = []
        for clip in clips:
            start = int(clip.get("start_ms", -1))
            end = int(clip.get("end_ms", -1))
            if start < 0 or end <= start or end > source_duration_ms:
                raise ValueError("剪辑选段超出源素材范围")
            # Source ranges are reusable. Preserve playback order (including
            # A -> B -> A); count every occurrence on the output timeline.
            total += end - start
            normalized_clips.append({"start_ms": start, "end_ms": end})
        if not normalized_settings["duration_min_ms"] <= total <= normalized_settings["duration_max_ms"]:
            minimum, maximum = normalized_settings['duration_min_ms'], normalized_settings['duration_max_ms']
            difference = (f'低于下限 {(minimum - total) / 1000:.3f}' if total < minimum
                          else f'超过上限 {(total - maximum) / 1000:.3f}')
            raise ValueError(
                f"第 {index} 条剪辑方案 {candidate_id} 成片时长不在用户设置范围内："
                f"实际 {total / 1000:.3f} 秒，"
                f"允许 {normalized_settings['duration_min_ms'] / 1000:.3f}—"
                f"{normalized_settings['duration_max_ms'] / 1000:.3f} 秒；{difference} 秒。未进入成片渲染。"
            )
        candidate["candidate_id"] = candidate_id
        candidate["title"] = title
        candidate["clips"] = normalized_clips
        candidate["duration_ms"] = total
        if normalized_settings['duration_policy'] == 'bounded_15_30':
            from .natural_clips import validate_natural_evidence
            validate_natural_evidence(candidate)
    return candidates


class AutoEditProjectStore:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS auto_edit_projects (
                    project_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    worker_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_auto_edit_status
                    ON auto_edit_projects(status, created_at);
                CREATE TABLE IF NOT EXISTS auto_edit_events (
                    event_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(project_id, event_type, payload_json),
                    FOREIGN KEY(project_id) REFERENCES auto_edit_projects(project_id)
                );
                CREATE TABLE IF NOT EXISTS auto_edit_purge_events (
                    purge_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        return json.loads(row["payload_json"])

    def _row(self, connection: sqlite3.Connection, project_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM auto_edit_projects WHERE project_id=?", (project_id,),
        ).fetchone()
        if row is None:
            raise AutoEditNotFoundError("找不到这个自动剪辑项目")
        return row

    def _write(self, connection: sqlite3.Connection, project: Mapping[str, Any], *, worker_id: str | None = None) -> None:
        connection.execute(
            """UPDATE auto_edit_projects
               SET status=?,revision=?,payload_json=?,updated_at=?,worker_id=? WHERE project_id=?""",
            (
                project["status"], project["revision"],
                json.dumps(project, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                project["updated_at"], worker_id, project["project_id"],
            ),
        )

    def _event(self, connection: sqlite3.Connection, project_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        connection.execute(
            "INSERT OR IGNORE INTO auto_edit_events VALUES(?,?,?,?,?)",
            (f"event_{uuid4().hex}", project_id, event_type, _now_iso(), encoded),
        )

    def create_project(self, *, title: str, settings: Mapping[str, Any], source: Mapping[str, Any] | None = None, sources: list[Mapping[str, Any]] | None = None, sku: str = '') -> dict[str, Any]:
        sku = str(sku).strip()
        if len(sku) > 100:
            raise ValueError('款号最多 100 个字符')
        inputs = sources if sources is not None else ([source] if source else [])
        if not 1 <= len(inputs) <= 20:
            raise ValueError('每个项目请选择 1—20 条录播视频')
        validated_sources = [validate_source(item) for item in inputs]
        if len({item['source_id'] for item in validated_sources}) != len(validated_sources):
            raise ValueError('素材来源 ID 重复')
        created_at = _now_iso()
        project = {
            "schema_version": "1.0.0",
            "project_id": f"auto_edit_{uuid4().hex}",
            "title": _clean_title(title),
            "sku": sku,
            "status": "draft",
            "revision": 1,
            "source": validated_sources[0],
            "sources": validated_sources,
            "settings": validate_settings(settings),
            "analysis_summary": None,
            "eligible_skill_snapshots": [],
            "selected_skill": None,
            "plan": [],
            "progress": 0,
            "error": None,
            "output_batch_id": None,
            "created_at": created_at,
            "updated_at": created_at,
        }
        encoded = json.dumps(project, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO auto_edit_projects VALUES(?,?,?,?,?,?,NULL)",
                (project["project_id"], "draft", 1, encoded, created_at, created_at),
            )
            self._event(connection, project["project_id"], "created", {"revision": 1})
        return copy.deepcopy(project)

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            project = self._decode(self._row(connection, project_id))
        if project['status'] == 'purged':
            raise AutoEditNotFoundError('项目已永久删除；审计记录已保留')
        return project

    def list_projects(self, *, deleted: bool = False) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM auto_edit_projects ORDER BY created_at DESC,rowid DESC",
            ).fetchall()
        projects = [self._decode(row) for row in rows]
        return [project for project in projects if project['status'] != 'purged' and bool(project.get('deleted_at')) == deleted]

    def set_deleted(self, project_id: str, *, expected_revision: int, deleted: bool) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            project = self._decode(self._row(connection, project_id))
            if project['revision'] != expected_revision:
                raise AutoEditConflictError('项目已更新，请刷新后重试')
            if project.get('purge') or project['status'] in {'purging', 'purge_failed', 'purged'}:
                raise AutoEditConflictError('永久删除已经开始，不能恢复可能已缺失原片的项目；请查看删除清单并重试')
            if project['status'] not in {'draft', 'failed', 'cancelled', 'review', 'completed'}:
                raise AutoEditConflictError('正在处理的项目不能删除，请先取消或等待处理结束')
            project.update(deleted_at=_now_iso() if deleted else None,
                           revision=project['revision'] + 1, updated_at=_now_iso())
            self._write(connection, project)
            self._event(connection, project_id, 'trashed' if deleted else 'restored', {'revision': project['revision']})
            return project

    def preview_purge(self, project_id: str, *, expected_revision: int) -> dict[str, Any]:
        from .project_purge import preview
        return preview(self, project_id, expected_revision=expected_revision)

    def purge_project(self, project_id: str, *, expected_revision: int, confirmation_token: str | None = None) -> dict[str, Any]:
        from .project_purge import purge
        return purge(self, project_id, expected_revision=expected_revision, confirmation_token=confirmation_token)

    def update_project(
        self, project_id: str, *, expected_revision: int, settings: Mapping[str, Any], title: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project = self._decode(self._row(connection, project_id))
            if project["revision"] != expected_revision:
                raise AutoEditConflictError("项目已在其他窗口更新，请刷新后重试")
            if project["status"] not in {"draft", "failed", "cancelled"}:
                raise AutoEditConflictError("运行中的项目不能修改参数")
            if project.get('deleted_at'):
                raise AutoEditConflictError('请先从回收站恢复项目')
            normalized_settings = validate_settings(settings)
            if normalized_settings != validate_settings(project['settings']):
                project.update(plan=[], selected_skill=None, eligible_skill_snapshots=[], analysis_summary=None)
            project["settings"] = normalized_settings
            # A changed draft must not reuse rendered files with old settings.
            project.pop('registration_checkpoint', None)
            if title is not None:
                project["title"] = _clean_title(title)
            project.update(revision=project["revision"] + 1, updated_at=_now_iso(), error=None)
            self._write(connection, project)
            self._event(connection, project_id, "updated", {"revision": project["revision"]})
            return project

    def enqueue(self, project_id: str, *, expected_revision: int) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project = self._decode(self._row(connection, project_id))
            if project["revision"] != expected_revision:
                raise AutoEditConflictError("项目已在其他窗口更新，请刷新后重试")
            if project["status"] not in {"draft", "failed"}:
                raise AutoEditConflictError("当前项目状态不能开始剪辑")
            if project.get('deleted_at'):
                raise AutoEditConflictError('请先从回收站恢复项目')
            if project['status'] == 'failed' and project.get('registration_checkpoint'):
                project.update(status='render_pending', revision=project['revision'] + 1,
                               progress=90, error=None, updated_at=_now_iso())
                self._write(connection, project)
                self._event(connection, project_id, 'registration_retry', {'revision': project['revision']})
                return project
            require_generation_ready(project)
            if project['status'] == 'failed' and project.get('plan'):
                # A committed, unchanged plan survived a local render failure.
                # Revalidate it, keep its frozen methods and use a fresh output
                # directory so partial files from the previous attempt survive.
                sources = project.get('sources') or [project['source']]
                source_map = {item['source_id']: item for item in sources}
                if len(project['plan']) != project['settings']['target_count']:
                    raise ValueError('剪辑方案数量与用户设置不一致')
                selected = (project.get('selected_skill') or {}).get('snapshot')
                skills = {skill['skill_id']: skill for skill in project.get('eligible_skill_snapshots', [])}
                if not selected or skills.get(selected['skill_id']) != selected:
                    raise ValueError('已保存方案的剪辑方法快照不完整，不能恢复渲染')
                seen = set()
                for index, item in enumerate(project['plan']):
                    source_id = item.get('source_id') or (sources[0]['source_id'] if len(sources) == 1 else None)
                    if source_id not in source_map:
                        raise ValueError('剪辑方案素材来源不在当前项目中')
                    skill_id = item.get('skill_id') or selected['skill_id']
                    if skill_id not in skills or item.get('skill_snapshot', selected) != skills[skill_id]:
                        raise ValueError('已保存方案的剪辑方法快照不一致，不能恢复渲染')
                    validate_edit_plan([item], source_duration_ms=source_map[source_id]['duration_ms'],
                                       settings={**project['settings'], 'target_count': 1}, candidate_offset=index)
                    identity = item['candidate_id'].casefold()
                    if identity in seen:
                        raise ValueError('剪辑方案候选 ID 重复')
                    seen.add(identity)
                project.update(status='render_pending', revision=project['revision'] + 1,
                               progress=45, error=None, updated_at=_now_iso(),
                               render_generation=uuid4().hex[:12], registration_checkpoint=None)
                self._write(connection, project)
                self._event(connection, project_id, 'render_retry', {'revision': project['revision']})
                return project
            project.update(
                status="queued", revision=project["revision"] + 1, progress=0, error=None,
                selected_skill=None, eligible_skill_snapshots=[], plan=[], output_batch_id=None,
                updated_at=_now_iso(),
                render_generation=uuid4().hex[:12], registration_checkpoint=None,
            )
            self._write(connection, project)
            self._event(connection, project_id, "queued", {"revision": project["revision"]})
            return project

    def claim_next(self, *, available_skills: Iterable[Mapping[str, Any]], worker_id: str) -> dict[str, Any] | None:
        snapshots = reusable_skill_snapshots(available_skills)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM auto_edit_projects WHERE status='queued' OR (status='render_pending' AND worker_id IS NULL) "
                "ORDER BY CASE status WHEN 'render_pending' THEN 0 ELSE 1 END,created_at,rowid LIMIT 1",
            ).fetchone()
            if row is None:
                return None
            project = self._decode(row)
            if project['status'] == 'render_pending':
                project.update(status='rendering', revision=project['revision'] + 1,
                               error=None, updated_at=_now_iso())
                self._write(connection, project, worker_id=worker_id)
                self._event(connection, project['project_id'], 'render_resumed', {'revision': project['revision']})
                return project
            if not snapshots:
                project.update(
                    status="failed", revision=project["revision"] + 1, progress=0,
                    error="没有已审核且允许复用的剪辑 Skill，请先在素材分析中审核 Skill。",
                    updated_at=_now_iso(),
                )
                self._write(connection, project)
                self._event(connection, project["project_id"], "failed", {"reason": "no_reusable_skill"})
                return None
            project.update(
                status="analyzing", revision=project["revision"] + 1, progress=10,
                eligible_skill_snapshots=snapshots, error=None, updated_at=_now_iso(),
            )
            self._write(connection, project, worker_id=worker_id)
            self._event(connection, project["project_id"], "claimed", {"worker_id": worker_id})
            return project

    def save_plan(
        self, project_id: str, *, worker_id: str, selected_skill_id: str, reason: str,
        plan: Iterable[Mapping[str, Any]], analysis_summary: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row(connection, project_id)
            if row["worker_id"] != worker_id:
                raise AutoEditConflictError("任务租约已变化，旧工作器不能写入结果")
            project = self._decode(row)
            skill = next(
                (item for item in project["eligible_skill_snapshots"] if item["skill_id"] == selected_skill_id), None,
            )
            if skill is None:
                raise ValueError("所选 Skill 不在项目冻结的可用快照中")
            candidates = list(plan)
            if len(candidates) != project['settings']['target_count']:
                raise ValueError('剪辑方案数量与用户设置不一致')
            sources = project.get('sources') or [project['source']]
            source_map = {item['source_id']: item for item in sources}
            validated_plan = []
            for index, item in enumerate(candidates):
                candidate_skill_id = item.get('skill_id') or selected_skill_id
                candidate_skill = next((s for s in project['eligible_skill_snapshots'] if s['skill_id'] == candidate_skill_id), None)
                if candidate_skill is None:
                    raise ValueError('成片 Skill 不在项目冻结的可用快照中')
                source_id = item.get('source_id') or (sources[0]['source_id'] if len(sources) == 1 else None)
                if source_id not in source_map:
                    raise ValueError('剪辑方案素材来源不在当前项目中')
                validated_plan.extend(validate_edit_plan(
                    [{**item, 'source_id': source_id, 'skill_id': candidate_skill_id, 'skill_snapshot': copy.deepcopy(candidate_skill)}], source_duration_ms=source_map[source_id]['duration_ms'],
                    settings={**project['settings'], 'target_count': 1},
                    candidate_offset=index,
                ))
            if len({item['candidate_id'].casefold() for item in validated_plan}) != len(validated_plan):
                raise ValueError('剪辑方案候选 ID 重复')
            if len(analysis_summary or '') > 20000 or not reason.strip() or len(reason) > 20000:
                raise ValueError('剪辑分析摘要及选择理由须为有效文本且不超过 20000 字')
            project.update(
                status="render_pending", revision=project["revision"] + 1, progress=45,
                analysis_summary=(analysis_summary or "").strip() or None,
                selected_skill={"snapshot": copy.deepcopy(skill), "reason": " ".join(reason.split()).strip()},
                plan=validated_plan, updated_at=_now_iso(), error=None,
            )
            self._write(connection, project, worker_id=worker_id)
            self._event(connection, project_id, "plan_saved", {"skill_id": selected_skill_id, "revision": skill["revision"]})
            return project

    def save_registration_checkpoint(self, project_id: str, *, expected_revision: int, checkpoint: Mapping[str, Any]) -> dict[str, Any]:
        """Commit only after every video and acoustic subtitle document exists."""
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = self._row(connection, project_id)
            project = self._decode(row)
            if project['status'] != 'rendering' or project['revision'] != expected_revision or project.get('deleted_at'):
                raise AutoEditConflictError('项目状态已改变，不能提交过期的成片登记检查点')
            project.update(registration_checkpoint=copy.deepcopy(dict(checkpoint)), progress=90,
                           revision=project['revision'] + 1, updated_at=_now_iso())
            self._write(connection, project, worker_id=row['worker_id'])
            self._event(connection, project_id, 'registration_ready', {'revision': project['revision']})
            return project

    def register_output_batch(self, project_id: str, *, batch_id: str, expected_revision: int | None = None,
                              render_generation: str | None = None) -> dict[str, Any]:
        clean_batch_id = str(batch_id).strip()
        if not clean_batch_id:
            raise ValueError("输出批次 ID 不能为空")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project = self._decode(self._row(connection, project_id))
            if project.get('deleted_at') or project.get('purge') or project['status'] in {'purging', 'purge_failed', 'purged'}:
                raise AutoEditConflictError('项目已进入回收站或永久删除，旧任务不能登记成片')
            if project.get("output_batch_id"):
                if project["output_batch_id"] != clean_batch_id:
                    raise AutoEditConflictError("项目已经登记了另一个输出批次")
                return project
            if expected_revision is not None and (
                project['revision'] != expected_revision or project['status'] != 'rendering'
                or project.get('render_generation') != render_generation
            ):
                raise AutoEditConflictError('项目已取消或重新生成，旧工作器不能登记过期成片')
            project.update(
                status="review", output_batch_id=clean_batch_id, progress=100,
                revision=project["revision"] + 1, updated_at=_now_iso(), error=None,
            )
            self._write(connection, project)
            self._event(connection, project_id, "output_registered", {"batch_id": clean_batch_id})
            return project

    def mark_rendering(self, project_id: str, *, worker_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row(connection, project_id)
            project = self._decode(row)
            if project["status"] != "render_pending":
                raise AutoEditConflictError("项目尚未形成可渲染方案")
            if row['worker_id'] not in (None, worker_id):
                raise AutoEditConflictError('任务已被另一工作器接管')
            project.update(
                status="rendering", revision=project["revision"] + 1,
                progress=55, updated_at=_now_iso(), error=None,
            )
            self._write(connection, project, worker_id=worker_id)
            self._event(connection, project_id, "rendering", {"worker_id": worker_id})
            return project

    def fail(self, project_id: str, *, message: str, worker_id: str | None = None) -> dict[str, Any]:
        clean = " ".join(str(message).split()).strip()[:2000] or "自动剪辑失败"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._row(connection, project_id)
            project = self._decode(row)
            if worker_id is not None and row['worker_id'] != worker_id:
                return project
            if project.get('deleted_at') or project.get('purge') or project["status"] in {"review", "completed", "cancelled", "purging", "purge_failed", "purged"}:
                return project
            project.update(
                status="failed", revision=project["revision"] + 1,
                error=clean, updated_at=_now_iso(),
            )
            self._write(connection, project)
            self._event(connection, project_id, "failed", {"message": clean})
            return project

    def cancel(self, project_id: str, *, expected_revision: int) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project = self._decode(self._row(connection, project_id))
            if project["revision"] != expected_revision:
                raise AutoEditConflictError("项目已在其他窗口更新，请刷新后重试")
            if project.get('deleted_at') or project.get('purge') or project['status'] in {'purging', 'purge_failed', 'purged'}:
                raise AutoEditConflictError('项目已进入回收站或永久删除，不能更改任务状态')
            if project["status"] in {"review", "completed"}:
                raise AutoEditConflictError("已生成成片的项目不能取消")
            project.update(
                status="cancelled", revision=project["revision"] + 1,
                error=None, updated_at=_now_iso(),
            )
            self._write(connection, project)
            self._event(connection, project_id, "cancelled", {"revision": project["revision"]})
            return project

    def recover_interrupted(self) -> list[str]:
        recovered: list[str] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # A committed plan survives restart; release only its dead lease.
            connection.execute("UPDATE auto_edit_projects SET worker_id=NULL WHERE status='render_pending'")
            rows = connection.execute(
                "SELECT * FROM auto_edit_projects WHERE status IN ('analyzing','planning','rendering')",
            ).fetchall()
            for row in rows:
                project = self._decode(row)
                project.update(
                    status="render_pending" if project.get("plan") else "queued",
                    revision=project["revision"] + 1,
                    error=None,
                    updated_at=_now_iso(),
                )
                self._write(connection, project)
                self._event(connection, project["project_id"], "recovered", {"status": project["status"]})
                recovered.append(project["project_id"])
        return recovered
