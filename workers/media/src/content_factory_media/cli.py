import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Sequence

from . import __version__
from .pipeline import MediaPipeline
from .queue import MediaTaskQueue
from .tools import find_tool, run_command, whisper_filter_available


@dataclass(frozen=True)
class WorkerHealth:
    service: Literal["media-worker"] = "media-worker"
    status: Literal["ok"] = "ok"
    version: str = __version__


def build_health() -> WorkerHealth:
    return WorkerHealth()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="爆款内容工厂本地媒体处理工作器")
    parser.add_argument("--check", action="store_true", help="输出 S0 兼容健康状态后退出")
    commands = parser.add_subparsers(dest="command")

    s2_check = commands.add_parser("s2-check", help="检查 S2 本地媒体能力")
    s2_check.add_argument("--asr-model")

    process = commands.add_parser("process", help="同步处理一个本地视频")
    process.add_argument("--input", required=True)
    process.add_argument("--workspace", required=True)
    process.add_argument("--task-id")
    process.add_argument("--asr-model")
    process.add_argument("--fixture-data", action="store_true")

    enqueue = commands.add_parser("enqueue", help="把本地视频加入 SQLite 队列")
    enqueue.add_argument("--database", required=True)
    enqueue.add_argument("--input", required=True)
    enqueue.add_argument("--workspace", required=True)
    enqueue.add_argument("--fixture-data", action="store_true")

    run_queue = commands.add_parser("run-queue", help="执行待处理媒体任务")
    run_queue.add_argument("--database", required=True)
    run_queue.add_argument("--asr-model")
    run_queue.add_argument("--workers", type=int, default=4)

    list_queue = commands.add_parser("list-queue", help="查看媒体任务")
    list_queue.add_argument("--database", required=True)
    return parser


def _s2_health(model_path: str | None) -> dict:
    ffmpeg = find_tool("ffmpeg")
    ffprobe = find_tool("ffprobe")
    ffmpeg_version = run_command([ffmpeg, "-version"], timeout=30).stdout.splitlines()[0]
    ffprobe_version = run_command([ffprobe, "-version"], timeout=30).stdout.splitlines()[0]
    model = Path(model_path).expanduser().resolve() if model_path else None
    return {
        "service": "media-worker",
        "status": "ok",
        "version": __version__,
        "ffmpeg_ready": True,
        "ffprobe_ready": True,
        "whisper_filter_ready": whisper_filter_available(ffmpeg),
        "whisper_model_ready": bool(model and model.is_file()),
        "ffmpeg_version": ffmpeg_version,
        "ffprobe_version": ffprobe_version,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.check:
        print(json.dumps(asdict(build_health()), ensure_ascii=False))
        return 0
    if args.command == "s2-check":
        print(json.dumps(_s2_health(args.asr_model), ensure_ascii=False, indent=2))
        return 0
    if args.command == "process":
        from uuid import uuid4

        task_id = args.task_id or f"media_{uuid4().hex}"
        pipeline = MediaPipeline(asr_model_path=args.asr_model)
        result_path = pipeline.process(
            args.input,
            args.workspace,
            task_id,
            fixture_data=args.fixture_data,
        )
        print(result_path.read_text(encoding="utf-8"))
        return 0
    if args.command == "enqueue":
        task = MediaTaskQueue(args.database).enqueue(
            args.input,
            args.workspace,
            fixture_data=args.fixture_data,
        )
        print(json.dumps(task.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "run-queue":
        queue = MediaTaskQueue(args.database)
        queue.recover_interrupted()
        tasks = queue.run_pending(
            max_workers=args.workers,
            asr_model_path=args.asr_model,
        )
        print(json.dumps([task.to_dict() for task in tasks], ensure_ascii=False, indent=2))
        return 0
    if args.command == "list-queue":
        tasks = MediaTaskQueue(args.database).list()
        print(json.dumps([task.to_dict() for task in tasks], ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 0
