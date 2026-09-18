export const S3_SCHEMA_VERSION = "1.0.0" as const;
export const ANALYSIS_REPORT_VERSION = "1.1.0" as const;

export const analysisTaskStatuses = ["pending", "running", "retry_wait", "succeeded", "completed", "failed", "cancelled"] as const;
export type AnalysisTaskStatus = (typeof analysisTaskStatuses)[number];

export const analysisTaskSteps = [
  "queued", "ocr", "prepare", "segmenting", "segment", "gateway", "summarizing", "validate", "persist",
  "recovered", "cancelling", "cancelled", "completed", "failed",
] as const;
export type AnalysisTaskStep = (typeof analysisTaskSteps)[number];

export type GatewayApiMode = "responses" | "chat_completions";

export const gatewayInputModalities = ["text", "image"] as const;
export type GatewayInputModality = (typeof gatewayInputModalities)[number];

/**
 * The provider only describes the upstream API family. Model capabilities are
 * still declared explicitly through `modalities` and `purposes`, so a relay or
 * custom OpenAI-compatible endpoint is not mistaken for a particular model.
 */
export const gatewayProviders = ["openai", "qwen", "openai_compatible", "custom"] as const;
export type GatewayProvider = (typeof gatewayProviders)[number];

export const gatewayPurposes = ["analysis", "script", "material", "video_review"] as const;
export type GatewayPurpose = (typeof gatewayPurposes)[number];

export const gatewayPurposeRequirements = {
  analysis: ["text", "image"],
  script: ["text"],
  material: ["text", "image"],
  video_review: ["text", "image"],
} as const satisfies Readonly<Record<GatewayPurpose, readonly GatewayInputModality[]>>;

type GatewayModelSettingsBase = Readonly<{
  model_id: string;
  display_name: string;
  base_url: string;
  model: string;
  api_mode: GatewayApiMode;
  modalities: readonly GatewayInputModality[];
  purposes: readonly GatewayPurpose[];
  enabled: boolean;
  api_key_configured: boolean;
}>;

export type GatewayModelSettingsV20 = GatewayModelSettingsBase & Readonly<{
  provider?: GatewayProvider;
}>;

export type GatewayModelSettingsV21 = GatewayModelSettingsBase & Readonly<{
  provider: GatewayProvider;
}>;

export type GatewayModelSettings = GatewayModelSettingsV20 | GatewayModelSettingsV21;

type GatewayRoutingBase = Readonly<{
  analysis: string | null;
  script: string | null;
  material: string | null;
}>;

export type GatewayRoutingV20 = GatewayRoutingBase & Readonly<{
  video_review?: string | null;
}>;

export type GatewayRoutingV21 = GatewayRoutingBase & Readonly<{
  video_review: string | null;
}>;

export type GatewayRouting = GatewayRoutingV20 | GatewayRoutingV21;

type GatewaySettingsBase = Readonly<{
  base_url: string;
  model: string;
  api_mode: GatewayApiMode;
  api_key_configured: boolean;
  default_model_id: string | null;
  updated_at: string | null;
}>;

/** Persisted legacy settings remain readable while all new writes use 2.1.0. */
export type GatewaySettingsV20 = GatewaySettingsBase & Readonly<{
  schema_version: "2.0.0";
  models: readonly GatewayModelSettingsV20[];
  routing: GatewayRoutingV20;
}>;

export type GatewaySettingsV21 = GatewaySettingsBase & Readonly<{
  schema_version: "2.1.0";
  models: readonly GatewayModelSettingsV21[];
  routing: GatewayRoutingV21;
}>;

export type GatewaySettings = GatewaySettingsV20 | GatewaySettingsV21;

export type AnalysisTask = Readonly<{
  schema_version: typeof S3_SCHEMA_VERSION;
  fixture_data: boolean;
  task_id: string;
  media_task_id: string;
  /** Optional for persisted 1.0.0 tasks created before dedicated video review routing. */
  model_purpose?: Extract<GatewayPurpose, "analysis" | "video_review">;
  status: AnalysisTaskStatus;
  progress: number;
  current_step: AnalysisTaskStep;
  attempt_count: number;
  max_attempts: number;
  metric_count: number;
  comment_count: number;
  ocr_result_path: string | null;
  result_path: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  /** Durable job metadata. Optional only for records created before checkpoint support. */
  trace_id?: string;
  idempotency_key?: string;
  segment_total?: number;
  segment_completed?: number;
  current_segment?: number | null;
  last_completed_segment?: number | null;
  last_checkpoint_at?: string | null;
  next_retry_at?: string | null;
  recovering?: boolean;
  cancel_requested?: boolean;
  diagnostic_code?: string | null;
  retry_count?: number;
}>;

export type Evidence = Readonly<{
  id: string;
  claim: string;
  source_type: "shot" | "timeline" | "metric" | "comment" | "transcript" | "keyframe";
  source_id: string;
  start_ms: number | null;
  end_ms: number | null;
  confidence: number;
  is_inference: boolean;
}>;

export type AnalysisClaim = Readonly<{
  text: string;
  evidence_ids: readonly string[];
  confidence: number;
  is_inference: boolean;
}>;

export type AnalysisReport = Readonly<{
  schema_version: typeof ANALYSIS_REPORT_VERSION;
  fixture_data: boolean;
  status: "draft" | "reviewed" | "accepted";
  revision: number;
  analysis_id: string;
  video_id: string;
  duration_ms: number;
  source: Readonly<{
    source_id: string;
    source_type: "douyin_link" | "local_file" | "authorized_api";
    source_uri: string;
    imported_at: string;
    authorization_status: "public" | "authorized" | "internal";
  }>;
  processing: Readonly<{
    provider: string;
    /** Optional on historical reports; every newly generated report records it. */
    purpose?: Extract<GatewayPurpose, "analysis" | "video_review">;
    model_profile_id?: string;
    model: string;
    api_mode: "responses" | "chat_completions" | "fixture" | "external_review";
    response_id: string | null;
    prompt_version: string;
    generated_at: string;
    duration_ms: number;
    upload_summary: Readonly<{
      keyframe_count: number;
      text_characters: number;
      total_bytes: number;
      original_video_uploaded: false;
    }>;
  }>;
  summary: Readonly<{
    video_type: "product_seeding" | "direct_conversion" | "talking_review" | "brand_content" | "other";
    target_audience: AnalysisClaim;
    main_promise: AnalysisClaim;
    overall_conclusion: AnalysisClaim;
    hook_analysis: AnalysisClaim;
    consumer_psychology: AnalysisClaim;
    risks: readonly AnalysisClaim[];
  }>;
  keyframes: readonly Readonly<{ id: string; shot_id: string; timestamp_ms: number; local_path: string }>[];
  shots: readonly Readonly<{
    id: string; start_ms: number; end_ms: number;
    shot_size: "extreme_closeup" | "closeup" | "medium" | "full" | "wide" | "detail" | "screen" | "unknown";
    camera_movement: "static" | "pan" | "tilt" | "dolly" | "handheld" | "zoom" | "mixed" | "unknown";
    composition: string; performer_action: string; product_exposure: string;
    transcript: string; subtitle: string; keyframe_refs: readonly string[];
  }>[];
  timeline: readonly Readonly<{
    id: string; second_index: number; start_ms: number; end_ms: number;
    visual_event: string; transcript: string; subtitle: string; rhythm: "low" | "medium" | "high";
    metric_refs: readonly string[];
  }>[];
  content_structure: readonly Readonly<{
    id: string; order: number; label: string; description: string; start_ms: number; end_ms: number;
    evidence_ids: readonly string[];
  }>[];
  emotion_curve: readonly Readonly<{
    id: string; start_ms: number; end_ms: number; intensity: number; label: string; reason: string;
    evidence_ids: readonly string[];
  }>[];
  comments: readonly Readonly<{
    id: string; text: string; source_label: string; like_count: number | null;
    is_author_reply: boolean; confidence: number;
  }>[];
  comment_insights: readonly Readonly<{
    id: string; topic: string; summary: string; sentiment: "positive" | "neutral" | "negative" | "mixed";
    representative_text: string; source_label: string; confidence: number; evidence_ids: readonly string[];
  }>[];
  metric_snapshots: readonly Readonly<{ id: string; captured_at: string; source_type: string; confidence: number; values: Readonly<Record<string, number | null>> }>[];
  evidence: readonly Evidence[];
  scores: Readonly<{
    score_version: string;
    total_score: number;
    items: readonly Readonly<{ dimension: string; weight: number; score: number; evidence_ids: readonly string[] }>[];
  }>;
  pattern_candidates: readonly Readonly<{
    id: string; name: string; mechanism: string;
    mechanism_key?: "result_then_visual_proof" | "question_then_demonstration" | "pain_point_then_solution" |
      "contrast_then_proof" | "identity_scenario" | "value_anchor" | "curiosity_gap" | "social_proof" |
      "direct_product_pitch" | "verbal_overload_failure" | "fit_reassurance" | "other";
    reuse_mode?: "reuse" | "avoid" | "uncertain";
    steps: readonly Readonly<{ id: string; order: number; description: string; evidence_ids: readonly string[] }>[];
    necessary_conditions: readonly string[]; failure_signals: readonly string[];
  }>[];
  review: Readonly<{ reviewer: string | null; reviewed_at: string | null; note: string | null }>;
}>;

export type S3Readiness = Readonly<{
  stage: "S3";
  engineering_ready: boolean;
  ocr_ready: boolean;
  gateway_configured: boolean;
  queue_ready: boolean;
  max_cloud_workers: 2;
  pending_tasks: number;
  running_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  accepted_real_analyses: number;
  required_real_analyses: 20;
  business_ready: boolean;
  pending_reason: string | null;
  analysis_method?: Readonly<{
    id: "huashu-douyin-script";
    title: string;
    status: "ready" | "not_installed" | "invalid";
    prompt_version: string;
    dimensions: readonly string[];
    message: string;
  }>;
}>;
