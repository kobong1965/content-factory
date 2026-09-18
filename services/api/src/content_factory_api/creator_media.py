"""Resolve media only from persisted Skill provenance; never accept a file path from HTTP."""
import json
from pathlib import Path


def bound_media_file(media: dict, video_id: str, root: Path) -> Path:
    if video_id != 'video_' + str(media['source']['sha256'])[:24]:
        raise ValueError('来源视频身份不匹配')
    root = root.resolve()
    original = Path(media['source']['managed_original_path']).resolve()
    path = Path(media.get('artifacts', {}).get('proxy_path') or original).resolve()
    if not path.is_relative_to(root) or not original.is_relative_to(root):
        raise ValueError('来源媒体不在托管目录中')
    if not path.is_file():
        raise ValueError('播放文件缺失，请重新处理这条原素材')
    if path.suffix.lower() not in {'.mp4', '.webm', '.mov'}:
        raise ValueError('该格式需要先生成本地播放代理')
    return path


def bound_cover_file(media: dict, video_id: str, root: Path) -> Path:
    root = root.resolve()
    if video_id != 'video_' + str(media['source']['sha256'])[:24]:
        raise ValueError('来源视频身份不匹配')
    original = Path(media['source']['managed_original_path']).resolve()
    if not original.is_relative_to(root):
        raise ValueError('来源不在托管目录')
    for shot in media.get('shots', []):
        path = Path(shot['keyframe_path']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('封面不在托管目录')
        if path.is_file() and path.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'}:
            return path
    raise ValueError('来源封面缺失')


def skill_media_file(queue, store, media_queue, skill_id: str, video_id: str, root: Path, *, cover=False):
    from .s5_sources import resolve_template
    _, _, _, skill = resolve_template(queue, store, skill_id)
    for occurrence in skill['occurrences']:
        if occurrence['video_id'] != video_id:
            continue
        analysis_task = queue.get(occurrence['analysis_task_id'])
        media_task = media_queue.get(analysis_task.media_task_id) if analysis_task else None
        if media_task and media_task.result_path:
            media = json.loads(Path(media_task.result_path).read_text(encoding='utf-8'))
            return bound_cover_file(media, video_id, root) if cover else bound_media_file(media, video_id, root)
    raise ValueError('这个 Skill 没有可播放的对应来源，请检查原素材是否已处理')
