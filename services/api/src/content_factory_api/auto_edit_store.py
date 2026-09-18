"""Persistent projects for source-led automatic editing.

This module owns project state only. Model selection and FFmpeg work are kept
behind workers so an interrupted process can resume from committed checkpoints.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4
from .subtitles import FONTS, EFFECTS


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
    if not required.issubset(settings) or set(settings) - required - {'subtitle_font', 'subtitle_effect'}:
        raise ValueError("剪辑参数字段不完整")
    target_count = int(settings["target_count"])
    minimum = int(settings["duration_min_ms"])
    maximum = int(settings["duration_max_ms"])
    font_size = int(settings["subtitle_font_size"])
    keyword_scale = float(settings["keyword_scale"])
    color = str(settings["keyword_color"]).upper()
    font = str(settings.get('subtitle_font', 'heiti'))
    effect = str(settings.get('subtitle_effect', 'none'))
    if font not in FONTS or effect not in EFFECTS:
        raise ValueError('请选择支持的字幕字体和特效')
    if not 1 <= target_count <= 10:
        raise ValueError("成片数量必须为 1—10")
    if not 5_000 <= minimum <= 180_000 or not 5_000 <= maximum <= 180_000 or minimum > maximum:
        raise ValueError("成片时长必须为 5—180 秒，且最小时长不能大于最大时长")
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


def validate_edit_plan(
    plan: Iterable[Mapping[str, Any]], *, source_duration_ms: int, settings: Mapping[str, Any],
) -> list[dict[str, Any]]:
    normalized_settings = validate_settings(settings)
    candidates = copy.deepcopy([dict(item) for item in plan])
    if len(candidates) != normalized_settings["target_count"]:
        raise ValueError("剪辑方案数量与用户设置不一致")
    seen: set[str] = set()
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        title = " ".join(str(candidate.get("title") or "").split()).strip()
        clips = candidate.get("clips")
        if not candidate_id or candidate_id in seen or not title or not isinstance(clips, list) or not clips:
            raise ValueError("剪辑方案候选资料不完整或 ID 重复")
        seen.add(candidate_id)
        previous_end = -1
        total = 0
        normalized_clips = []
        for clip in clips:
            start = int(clip.get("start_ms", -1))
            end = int(clip.get("end_ms", -1))
            if start < 0 or end <= start or end > source_duration_ms:
                raise ValueError("剪辑选段超出源素材范围")
            if start < previous_end:
                raise ValueError("同一成片内的剪辑选段不能重叠")
            previous_end = end
            total += end - start
            normalized_clips.append({"start_ms": start, "end_ms": end})
        if not normalized_settings["duration_min_ms"] <= total <= normalized_settings["duration_max_ms"]:
            raise ValueError("剪辑方案成片时长不在用户设置范围内")
        candidate["candidate_id"] = candidate_id
        candidate["title"] = title
        candidate["clips"] = normalized_clips
        candidate["duration_ms"] = total
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

    def create_project(self, *, title: str, settings: Mapping[str, Any], source: Mapping[str, Any] | None = None, sources: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
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
            return self._decode(self._row(connection, project_id))

    def list_projects(self, *, deleted: bool = False) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM auto_edit_projects ORDER BY created_at DESC,rowid DESC",
            ).fetchall()
        return [self._decode(row) for row in rows if bool(self._decode(row).get('deleted_at')) == deleted]

    def set_deleted(self, project_id: str, *, expected_revision: int, deleted: bool) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            project = self._decode(self._row(connection, project_id))
            if project['revision'] != expected_revision:
                raise AutoEditConflictError('项目已更新，请刷新后重试')
            if project['status'] not in {'draft', 'failed', 'cancelled', 'review', 'completed'}:
                raise AutoEditConflictError('正在处理的项目不能删除，请先取消或等待处理结束')
            project.update(deleted_at=_now_iso() if deleted else None,
                           revision=project['revision'] + 1, updated_at=_now_iso())
            self._write(connection, project)
            self._event(connection, project_id, 'trashed' if deleted else 'restored', {'revision': project['revision']})
            return project

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
            project["settings"] = validate_settings(settings)
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
            project.update(
                status="queued", revision=project["revision"] + 1, progress=0, error=None,
                selected_skill=None, eligible_skill_snapshots=[], plan=[], output_batch_id=None,
                updated_at=_now_iso(),
            )
            self._write(connection, project)
            self._event(connection, project_id, "queued", {"revision": project["revision"]})
            return project

    def claim_next(self, *, available_skills: Iterable[Mapping[str, Any]], worker_id: str) -> dict[str, Any] | None:
        snapshots = reusable_skill_snapshots(available_skills)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM auto_edit_projects WHERE status='queued' ORDER BY created_at,rowid LIMIT 1",
            ).fetchone()
            if row is None:
                return None
            project = self._decode(row)
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
            for item in candidates:
                source_id = item.get('source_id') or (sources[0]['source_id'] if len(sources) == 1 else None)
                if source_id not in source_map:
                    raise ValueError('剪辑方案素材来源不在当前项目中')
                validated_plan.extend(validate_edit_plan(
                    [{**item, 'source_id': source_id}], source_duration_ms=source_map[source_id]['duration_ms'],
                    settings={**project['settings'], 'target_count': 1},
                ))
            if len({item['candidate_id'] for item in validated_plan}) != len(validated_plan):
                raise ValueError('剪辑方案候选 ID 重复')
            project.update(
                status="render_pending", revision=project["revision"] + 1, progress=45,
                analysis_summary=(analysis_summary or "").strip() or None,
                selected_skill={"snapshot": copy.deepcopy(skill), "reason": " ".join(reason.split()).strip()},
                plan=validated_plan, updated_at=_now_iso(), error=None,
            )
            self._write(connection, project)
            self._event(connection, project_id, "plan_saved", {"skill_id": selected_skill_id, "revision": skill["revision"]})
            return project

    def register_output_batch(self, project_id: str, *, batch_id: str) -> dict[str, Any]:
        clean_batch_id = str(batch_id).strip()
        if not clean_batch_id:
            raise ValueError("输出批次 ID 不能为空")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project = self._decode(self._row(connection, project_id))
            if project.get("output_batch_id"):
                if project["output_batch_id"] != clean_batch_id:
                    raise AutoEditConflictError("项目已经登记了另一个输出批次")
                return project
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
            project.update(
                status="rendering", revision=project["revision"] + 1,
                progress=55, updated_at=_now_iso(), error=None,
            )
            self._write(connection, project, worker_id=worker_id)
            self._event(connection, project_id, "rendering", {"worker_id": worker_id})
            return project

    def fail(self, project_id: str, *, message: str) -> dict[str, Any]:
        clean = " ".join(str(message).split()).strip()[:2000] or "自动剪辑失败"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project = self._decode(self._row(connection, project_id))
            if project["status"] in {"review", "completed", "cancelled"}:
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
