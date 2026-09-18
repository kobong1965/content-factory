import { runtimeApiBaseUrl } from './runtimeApi';
import type { MediaTask, MediaTaskStatus, MediaTaskStep, S2Readiness } from "@content-factory/contracts";

const API_BASE_URL = runtimeApiBaseUrl;

export const DEFAULT_S2_READINESS: S2Readiness = {
  stage: "S2",
  engineering_ready: false,
  ffmpeg_ready: false,
  ffprobe_ready: false,
  whisper_filter_ready: false,
  whisper_model_ready: false,
  queue_ready: false,
  max_local_workers: 4,
  pending_tasks: 0,
  running_tasks: 0,
  completed_tasks: 0,
  failed_tasks: 0,
  accepted_real_videos: 0,
  required_real_videos: 10,
  business_ready: false,
  pending_reason: "本地接口未连接，暂时无法读取验收状态",
};

const statusLabels: Record<MediaTaskStatus, string> = {
  pending: "等待处理",
  running: "正在处理",
  retry_wait: "准备重试",
  completed: "处理完成",
  failed: "处理失败",
};

const stepLabels: Record<MediaTaskStep, string> = {
  queued: "已加入队列",
  probe: "读取视频信息",
  copy: "保存本地原片",
  proxy: "生成流畅预览",
  audio: "提取音频",
  asr: "识别语音",
  scenes: "查找镜头边界",
  keyframes: "提取关键画面",
  finalize: "整理结果",
  recovered: "已从中断恢复",
  completed: "全部完成",
  failed: "已停止",
};

export function taskStatusLabel(status: MediaTaskStatus): string {
  return statusLabels[status];
}

export function taskStepLabel(step: MediaTaskStep): string {
  return stepLabels[step];
}

export function completionPercent(completed: number, required: number): number {
  if (required <= 0) return 0;
  return Math.min(100, Math.max(0, Math.round((completed / required) * 100)));
}

async function responseJson<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;
  let message = `本地接口返回 ${response.status}`;
  try {
    const payload = (await response.json()) as { detail?: string };
    if (payload.detail) message = payload.detail;
  } catch {
    // Keep the stable fallback when the server did not return JSON.
  }
  throw new Error(message);
}

export async function fetchS2Readiness(signal?: AbortSignal): Promise<S2Readiness> {
  return responseJson(await fetch(`${API_BASE_URL}/s2/readiness`, { signal }));
}

export async function fetchMediaTasks(signal?: AbortSignal): Promise<MediaTask[]> {
  return responseJson(await fetch(`${API_BASE_URL}/s2/tasks`, { signal }));
}

export async function uploadMedia(file: File): Promise<MediaTask> {
  const body = new FormData();
  body.append("video", file);
  return responseJson(await fetch(`${API_BASE_URL}/s2/import/file`, { method: "POST", body }));
}

export async function submitDouyinLink(url: string): Promise<string> {
  const payload = await responseJson<{ message: string }>(
    await fetch(`${API_BASE_URL}/s2/import/link`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    }),
  );
  return payload.message;
}
