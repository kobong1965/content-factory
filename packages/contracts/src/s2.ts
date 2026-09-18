export const S2_SCHEMA_VERSION = "1.0.0" as const;

export const mediaTaskStatuses = ["pending", "running", "retry_wait", "completed", "failed"] as const;
export type MediaTaskStatus = (typeof mediaTaskStatuses)[number];

export const mediaTaskSteps = [
  "queued", "probe", "copy", "proxy", "audio", "asr", "scenes", "keyframes", "finalize", "recovered", "completed", "failed",
] as const;
export type MediaTaskStep = (typeof mediaTaskSteps)[number];

export type MediaTask = Readonly<{
  schema_version: typeof S2_SCHEMA_VERSION;
  fixture_data: boolean;
  task_id: string;
  status: MediaTaskStatus;
  progress: number;
  current_step: MediaTaskStep;
  source_name: string;
  source_path: string;
  workspace_path: string;
  attempt_count: number;
  max_attempts: number;
  error: string | null;
  result_path: string | null;
  created_at: string;
  updated_at: string;
}>;

export type MediaResult = Readonly<{
  schema_version: typeof S2_SCHEMA_VERSION;
  fixture_data: boolean;
  task_id: string;
  source: Readonly<{ original_name: string; sha256: string; size_bytes: number; managed_original_path: string }>;
  media: Readonly<{
    duration_ms: number; width: number; height: number; fps: number; video_codec: string;
    audio_codec: string | null; has_audio: boolean; format_name: string;
  }>;
  artifacts: Readonly<{ proxy_path: string; audio_path: string | null; transcript_path: string | null; scene_manifest_path: string }>;
  asr: Readonly<{ status: "completed" | "no_audio" | "model_missing"; model_name: string | null; language: string | null; segment_count: number }>;
  shots: readonly Readonly<{ id: string; start_ms: number; end_ms: number; scene_score: number; keyframe_path: string }>[];
  timings_ms: Readonly<Record<string, number>>;
  created_at: string;
}>;

export type S2Readiness = Readonly<{
  stage: "S2";
  engineering_ready: boolean;
  ffmpeg_ready: boolean;
  ffprobe_ready: boolean;
  whisper_filter_ready: boolean;
  whisper_model_ready: boolean;
  queue_ready: boolean;
  max_local_workers: 4;
  pending_tasks: number;
  running_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  accepted_real_videos: number;
  required_real_videos: number;
  business_ready: boolean;
  pending_reason: string | null;
}>;
