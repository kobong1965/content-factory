export const EDIT_PROJECT_VERSION = "1.0.0" as const;

export type EditProjectStatus = "draft" | "video_review" | "approved" | "rejected";
export type RenderTaskStatus = "pending" | "running" | "retry_wait" | "completed" | "failed";
export type RenderTaskStep = "queued" | "prepare" | "render_clips" | "concat" | "subtitles" | "audio" | "package" | "completed" | "failed";
export type RenderReviewStatus = "pending" | "approved" | "rejected";

export type SubtitleStyle = Readonly<{
  enabled: boolean; font_family: string; font_size: number; primary_color: string; outline_color: string; margin_v: number;
}>;

export type EditSettings = Readonly<{
  width: 1080; height: 1920; fps: 30; video_codec: "h264"; audio_codec: "aac";
  color_preset: "natural" | "bright" | "warm" | "cool";
  normalize_voice: boolean; reduce_noise: boolean; auto_sound_effects: boolean;
  original_volume: number; bgm_asset_id: string | null; bgm_volume: number; output_clean_copy: true;
  subtitle_style: SubtitleStyle;
}>;

export type EditVisualOverlay = Readonly<{
  material_id: string; material_clip_id: string; source_start_ms: number; source_end_ms: number;
  speed: number; detail_tag: string; instruction: string; audio_mode: "retain_primary";
}>;

export type EditClip = Readonly<{
  id: string; order: number; script_shot_id: string; material_id: string; material_clip_id: string;
  source_start_ms: number; source_end_ms: number; timeline_start_ms: number; timeline_end_ms: number;
  speed: number; crop_mode: "fill" | "fit"; focus_x: number; focus_y: number;
  transition: "cut" | "fade"; transition_ms: number; subtitle: string; voiceover: string; sound_effect: string;
  has_source_audio: boolean; original_volume: number; note: string; continuous_take?: boolean;
  visual_overlay?: EditVisualOverlay;
}>;

export type EditVariant = Readonly<{
  id: string; script_version_id: string; name: string; duration_ms: number;
  clips: readonly EditClip[]; warnings: readonly string[]; latest_output_id: string | null;
}>;

export type EditProject = Readonly<{
  schema_version: typeof EDIT_PROJECT_VERSION; fixture_data: boolean; revision: number;
  project_id: string; script_id: string; script_revision: number; product_id: string;
  selected_version_id?: string;
  status: EditProjectStatus; settings: EditSettings; variants: readonly EditVariant[];
  created_at: string; updated_at: string;
}>;

export type EditAudioAsset = Readonly<{
  schema_version: "1.0.0"; fixture_data: boolean; asset_id: string; kind: "bgm" | "sound_effect";
  name: string; original_name: string; mime_type: string; sha256: string; size_bytes: number;
  duration_ms: number; sample_rate: number; channels: number; license_note: string; imported_by: string; created_at: string;
}>;

export type RenderTask = Readonly<{
  schema_version: "1.0.0"; fixture_data: boolean; task_id: string; project_id: string; project_revision: number;
  variant_id: string; status: RenderTaskStatus; progress: number; current_step: RenderTaskStep;
  attempt_count: number; max_attempts: number; output_id: string | null; error: string | null; created_at: string; updated_at: string;
}>;

export type RenderOutput = Readonly<{
  schema_version: "1.0.0"; fixture_data: boolean; output_id: string; project_id: string; project_revision: number;
  variant_id: string; status: "video_review" | "approved" | "rejected";
  resources: Readonly<{ video_ref: string; clean_video_ref: string; subtitle_ref: string; project_ref: string; jianying_package_ref: string; jianying_experimental: true }>;
  media: Readonly<{ filename: string; mime_type: "video/mp4"; sha256: string; size_bytes: number; duration_ms: number; width: 1080; height: 1920; fps: number; video_codec: "h264" | "avc1"; audio_codec: "aac"; has_audio: true; faststart: true }>;
  render: Readonly<{ task_id: string; ffmpeg_version: string; rendered_at: string; duration_ms: number }>;
  review: Readonly<{ status: RenderReviewStatus; reviewed_by: string | null; reviewed_at: string | null; note: string | null }>;
  created_at: string; updated_at: string;
}>;

export type S7Readiness = Readonly<{
  stage: "S7"; engineering_ready: boolean; ffmpeg_ready: boolean; ffprobe_ready: boolean;
  eligible_scripts: number; project_count: number; pending_renders: number; failed_renders: number;
  outputs_waiting_review: number; accepted_real_outputs: number; required_real_outputs: 1;
  max_parallel_renders: 2; business_ready: boolean; pending_reason: string | null;
  jianying_mode: "experimental_handoff_only";
}>;

export type S7EligibleScript = Readonly<{
  script_id: string; product_id: string; product_name: string; product_sku: string; revision: number; version_count: number;
  selected_version_id: string; selected_version_name: string; selected_version_duration_ms: number;
  fixture_data: boolean; shooting_status: "pending_shoot" | "materials_uploaded" | "ready_for_edit";
  missing_count: number; project_id: string | null;
}>;
