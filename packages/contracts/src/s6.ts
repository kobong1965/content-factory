export const MATERIAL_ASSET_VERSION = "1.0.0" as const;

export type MaterialPurpose = "hook" | "proof" | "detail" | "comfort" | "cta" | "transition" | "broll";
export type RepeatRisk = "low" | "medium" | "high";
export type MaterialImportStatus = "pending" | "running" | "retry_wait" | "completed" | "failed";
export type ShootingStatus = "pending_shoot" | "materials_uploaded" | "ready_for_edit";
export type MaterialCaptureRole = "host_take" | "detail" | "standard";

export type MaterialClip = Readonly<{
  id: string;
  source_shot_id: string;
  order: number;
  start_ms: number;
  end_ms: number;
  keyframe_ref: string;
  transcript: string;
  purpose_tags: readonly MaterialPurpose[];
  visual_tags: readonly string[];
  garment_views: readonly ("front" | "side" | "back" | "detail" | "full_body" | "unknown")[];
  action_tags: readonly string[];
  scene_tags: readonly string[];
  shot_size: string;
  people_count: number;
  standalone_usable: boolean;
  reusable: boolean;
  quality: Readonly<{ clarity: number; stability: number; audio: number; exposure: number; overall: number }>;
  note: string;
  capture_scope?: "scene" | "full_take";
}>;

export type MaterialAsset = Readonly<{
  schema_version: typeof MATERIAL_ASSET_VERSION;
  fixture_data: boolean;
  revision: number;
  material_id: string;
  product_id: string;
  product_revision: number;
  source_script_id: string | null;
  capture_role?: MaterialCaptureRole;
  file: Readonly<{
    original_name: string; mime_type: string; sha256: string; size_bytes: number; duration_ms: number;
    width: number; height: number; fps: number; has_audio: boolean; original_ref: string; proxy_ref: string;
  }>;
  archive: Readonly<{
    model_name: string; scene: string; shot_date: string; batch: string; imported_by: string; note: string;
  }>;
  processing: Readonly<{
    media_task_id: string; local_status: "completed"; asr_status: "completed" | "no_audio" | "model_missing";
    recognition_status: "pending" | "completed" | "not_configured" | "failed";
    provider: string | null; model_profile_id?: string | null; model: string | null; recognized_at: string | null; original_video_uploaded: false;
  }>;
  clips: readonly MaterialClip[];
  created_at: string;
  updated_at: string;
}>;

export type MaterialSummary = Readonly<{
  material_id: string; product_id: string; product_name: string; product_sku: string; revision: number;
  original_name: string; model_name: string; scene: string; shot_date: string; batch: string;
  clip_count: number; reusable_clip_count: number; recognition_status: MaterialAsset["processing"]["recognition_status"];
  updated_at: string;
}>;

export type MaterialImportTask = Readonly<{
  schema_version: "1.0.0"; fixture_data: boolean; task_id: string; product_id: string; source_script_id: string | null;
  source_name: string; status: MaterialImportStatus; progress: number;
  current_step: "queued" | "probe" | "copy" | "proxy" | "audio" | "asr" | "scenes" | "keyframes" | "archive" | "recognize" | "completed" | "failed";
  attempt_count: number; max_attempts: number; material_id: string | null; error: string | null; created_at: string; updated_at: string;
}>;

export type MaterialSuggestion = Readonly<{
  material_id: string; clip_id: string; score: number; repeat_risk: RepeatRisk; reason: string;
  continuous_take_reusable?: boolean;
}>;

export type ShootingRequirement = Readonly<{
  script_shot_id: string; version_id: string; order: number;
  requirement_kind?: "primary" | "detail_overlay";
  source_script_shot_id?: string | null;
  detail_tag?: string | null;
  requirement_status: "required" | "reusable" | "reshoot" | "optional";
  purpose: MaterialPurpose; visual: string; voiceover: string;
  match_status: "missing" | "suggested" | "confirmed" | "optional";
  suggestions: readonly MaterialSuggestion[];
  confirmed_match: MaterialSuggestion | null;
}>;

export type ShootingTask = Readonly<{
  schema_version: "1.0.0"; fixture_data: boolean; task_id: string; script_id: string; script_revision: number;
  selected_version_id?: string; product_id: string; status: ShootingStatus; requirements: readonly ShootingRequirement[];
  missing_count: number; matched_count: number; created_at: string; updated_at: string;
}>;

export type MaterialUsage = Readonly<{
  schema_version: "1.0.0"; event_id: string; action: "confirmed" | "released"; material_id: string;
  clip_id: string; script_id: string; script_shot_id: string; actor: string; created_at: string;
}>;

export type S6Readiness = Readonly<{
  stage: "S6"; engineering_ready: boolean; ffmpeg_ready: boolean; ffprobe_ready: boolean; whisper_ready: boolean;
  gateway_configured: boolean; material_count: number; clip_count: number; pending_imports: number; failed_imports: number;
  pending_shoot_tasks: number; ready_for_edit_tasks: number; accepted_real_ready_tasks: number; required_real_ready_tasks: 1;
  business_ready: boolean; pending_reason: string | null;
}>;

export type S6ScriptOption = Readonly<{
  script_id: string; product_id: string; product_name: string; product_sku: string; revision: number; version_count: number;
  selected_version_id: string; selected_version_name: string; selected_version_duration_ms: number;
}>;
