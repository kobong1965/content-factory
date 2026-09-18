"""Registry for the canonical JSON Schemas kept in this package's source tree."""

from pathlib import Path

SCHEMA_VERSION = "1.0.0"

SCHEMA_FILES: dict[str, str] = {
    "analysis": "analysis-report.schema.json",
    "product": "product-profile.schema.json",
    "script": "script-package.schema.json",
    "gold_case": "gold-set-case.schema.json",
    "gold_manifest": "gold-set-manifest.schema.json",
    "media_task": "media-task.schema.json",
    "media_result": "media-result.schema.json",
    "ocr_result": "ocr-result.schema.json",
    "analysis_task": "analysis-task.schema.json",
    "gateway_settings": "gateway-settings.schema.json",
    "script_task": "script-task.schema.json",
    "material": "material-asset.schema.json",
    "shooting_task": "shooting-task.schema.json",
    "material_import_task": "material-import-task.schema.json",
    "material_usage": "material-usage.schema.json",
    "edit_project": "edit-project.schema.json",
    "render_task": "render-task.schema.json",
    "render_output": "render-output.schema.json",
    "edit_audio_asset": "edit-audio-asset.schema.json",
    "publication": "publication.schema.json",
    "metric_snapshot": "metric-snapshot.schema.json",
    "metric_import_draft": "metric-import-draft.schema.json",
    "learning_report": "learning-report.schema.json",
    "viral_skill": "viral-skill.schema.json",
}


def schema_root() -> Path:
    """Return the checked-in schema directory used by editable local installs."""

    return Path(__file__).resolve().parents[2] / "schemas"


def schema_path(kind: str) -> Path:
    try:
        filename = SCHEMA_FILES[kind]
    except KeyError as exc:
        supported = ", ".join(SCHEMA_FILES)
        raise ValueError(f"未知合同类型 {kind!r}；可选类型：{supported}") from exc

    path = schema_root() / filename
    if not path.is_file():
        raise FileNotFoundError(f"找不到合同文件：{path}")
    return path
