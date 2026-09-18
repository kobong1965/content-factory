import type { ViralSkillEligibility, ViralSkillSourceStatus } from "./skills";

export const SCRIPT_PACKAGE_VERSION = "1.1.0" as const;

export type ScriptContentGoal = "seeding" | "conversion" | "review" | "brand";
export type ScriptReviewStatus = "draft" | "pending" | "approved" | "rejected";
export type ScriptTaskStatus = "pending" | "running" | "retry_wait" | "completed" | "failed";
export type ScriptMaterialStatus = "required" | "reusable" | "reshoot" | "optional";

export type ScriptProductionMode = Readonly<{
  kind: "fixed_livestream_long_take";
  scene: "固定直播间";
  camera: "固定直播间竖屏机位（不移动）";
  lighting: "固定直播间灯光";
  performer_count: 1;
  primary_take: "continuous_long_take";
  detail_overlays: "optional_reusable_same_product";
}>;

export type ScriptIssue = Readonly<{
  code: string;
  severity: "info" | "warning" | "blocking";
  message: string;
  shot_id: string | null;
}>;

export type ScriptShot = Readonly<{
  id: string;
  order: number;
  start_ms: number;
  end_ms: number;
  visual: string;
  voiceover: string;
  action: string;
  shot_size: string;
  camera: string;
  subtitle: string;
  sound_effect: string;
  bgm: string;
  transition: string;
  material_status: ScriptMaterialStatus;
  fact_ids: readonly string[];
  pattern_step_id: string;
  source_shot_id: string | null;
  delivery?: Readonly<{
    tone: string;
    pacing: string;
    emphasis: string;
    pause: string;
  }>;
  performance?: Readonly<{
    expression: string;
    eye_line: string;
    body_action: string;
    product_action: string;
  }>;
  detail_overlay?: Readonly<{
    mode: "none" | "optional_detail";
    detail_tag: string | null;
    instruction: string;
  }>;
  evidence_ids?: readonly string[];
}>;

export type ScriptVersion = Readonly<{
  id: string;
  name: string;
  primary_hook: string;
  differentiation: string;
  similarity_risk: "low" | "medium" | "high";
  duration_ms: number;
  fact_ids: readonly string[];
  pattern_step_ids: readonly string[];
  shots: readonly ScriptShot[];
}>;

export type ScriptPackage = Readonly<{
  schema_version: typeof SCRIPT_PACKAGE_VERSION;
  fixture_data: boolean;
  revision: number;
  script_id: string;
  product_id: string;
  product_revision: number;
  skill_id?: string;
  skill_revision?: number;
  pattern_id: string;
  source_analysis_id: string;
  source_analysis_revision: number;
  content_goal: ScriptContentGoal;
  target_audience: string;
  generation: Readonly<{
    task_id: string;
    provider: string;
    model_profile_id?: string;
    model: string;
    api_mode: "responses" | "chat_completions";
    response_id: string | null;
    prompt_version: string;
    generated_at: string;
    duration_ms: number;
    original_video_uploaded: false;
  }>;
  created_at: string;
  updated_at: string;
  production_mode?: ScriptProductionMode;
  selected_version_id?: string;
  versions: readonly ScriptVersion[];
  shooting_order: readonly Readonly<{ scene: string; equipment: string; shot_ids: readonly string[] }>[];
  material_checklist: readonly Readonly<{ shot_id: string; status: ScriptMaterialStatus; notes: string }>[];
  review: Readonly<{
    status: ScriptReviewStatus;
    checked_by: string | null;
    checked_at: string | null;
    note: string | null;
    issues: readonly ScriptIssue[];
  }>;
}>;

export type ScriptTask = Readonly<{
  schema_version: "1.0.0";
  fixture_data: boolean;
  task_id: string;
  product_id: string;
  product_revision: number;
  source_analysis_id: string;
  source_analysis_revision: number;
  pattern_id: string;
  content_goal: ScriptContentGoal;
  target_audience: string;
  version_count: 3 | 4 | 5;
  status: ScriptTaskStatus;
  progress: number;
  current_step: "queued" | "prepare" | "gateway" | "validate" | "persist" | "completed" | "failed";
  attempt_count: number;
  max_attempts: number;
  input_ref: string;
  result_ref: string | null;
  script_id: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}>;

export type ScriptSummary = Readonly<{
  script_id: string;
  task_id: string;
  product_name: string;
  product_sku: string;
  pattern_name: string;
  skill_id?: string | null;
  skill_revision?: number | null;
  review_status: ScriptReviewStatus;
  revision: number;
  version_count: number;
  target_audience: string;
  updated_at: string;
}>;

export type S5Template = Readonly<{
  template_id: string;
  skill_id: string;
  skill_revision: number;
  status: "approved";
  reuse_mode: "reuse";
  source_analysis_id: string;
  source_analysis_revision: number;
  pattern_id: string;
  name: string;
  mechanism: string;
  steps: readonly Readonly<{ id: string; order: number; description: string }>[];
  necessary_conditions: readonly string[];
  failure_signals: readonly string[];
  analysis_summary: string;
  fixture_data: boolean;
  distinct_video_count: number;
  occurrence_count: number;
  script_usage_count: number;
  classification: "single_video" | "common_candidate";
  evidence_level: "content_observation" | "content_inference" | "metric_correlation";
  causality_status: "not_established";
  source_status: ViralSkillSourceStatus;
  update_available: boolean;
  eligibility: ViralSkillEligibility;
  representative_sources: readonly Readonly<{
    source_name: string;
    video_id: string;
    start_ms: number | null;
    end_ms: number | null;
    dialogue: string | null;
    action: string | null;
  }>[];
}>;

export type S5Product = Readonly<{
  product_id: string;
  revision: number;
  sku: string;
  name: string;
  selling_points: readonly string[];
  max_duration_ms: number;
  status?: "draft" | "active" | "archived";
  script_eligible?: boolean;
  unknown_fields?: readonly string[];
}>;

export type S5Readiness = Readonly<{
  stage: "S5";
  engineering_ready: boolean;
  gateway_configured: boolean;
  available_templates: number;
  available_products: number;
  pending_tasks: number;
  running_tasks: number;
  failed_tasks: number;
  pending_review_scripts: number;
  approved_scripts: number;
  accepted_real_scripts: number;
  required_real_scripts: 1;
  business_ready: boolean;
  pending_reason: string | null;
}>;

export type ScriptRevision = Readonly<{
  revision: number;
  action: "generated" | "updated" | "approved" | "rejected";
  actor: string;
  created_at: string;
  review_status: ScriptReviewStatus;
}>;
