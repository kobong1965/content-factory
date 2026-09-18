export const VIRAL_SKILL_VERSION = "1.0.0" as const;

export const viralSkillReuseModes = ["reuse", "avoid", "uncertain"] as const;
export type ViralSkillReuseMode = (typeof viralSkillReuseModes)[number];
export type ApprovedViralSkillReuseMode = Exclude<ViralSkillReuseMode, "uncertain">;
export type ViralSkillStatus = "approved" | "disabled";
export type ViralSkillClassification = "single_video" | "common_candidate";
export type ViralSkillEvidenceLevel = "content_observation" | "content_inference" | "metric_correlation";
export type ViralSkillSourceStatus = "current" | "updated" | "inactive" | "missing";
export type ViralSkillEligibilityReason =
  | "eligible"
  | "skill_disabled"
  | "avoid_only"
  | "source_missing"
  | "source_inactive"
  | "source_updated";
export type ViralSkillEligibility = Readonly<{
  s5_eligible: boolean;
  reason_code: ViralSkillEligibilityReason;
  reason: string | null;
}>;

export type ViralSkillEvidence = Readonly<{
  evidence_id: string;
  qualified_evidence_id: string;
  claim: string;
  source_type: "shot" | "timeline" | "metric" | "comment" | "transcript" | "keyframe";
  source_id: string;
  start_ms: number | null;
  end_ms: number | null;
  confidence: number;
  is_inference: boolean;
  exact_dialogue: string | null;
  subtitle: string | null;
  action: string | null;
  visual_event: string | null;
  metric_values: Readonly<Record<string, number | null>> | null;
  comment_text: string | null;
}>;

export type ViralSkillOccurrence = Readonly<{
  occurrence_id: string;
  analysis_task_id: string;
  media_task_id: string;
  analysis_id: string;
  analysis_revision: number;
  report_sha256: string;
  video_id: string;
  source_id: string;
  source_type: "douyin_link" | "local_file" | "authorized_api";
  source_name: string;
  pattern_id: string;
  pattern_name: string;
  pattern_mechanism: string;
  accepted_by: string;
  accepted_at: string;
  start_ms: number | null;
  end_ms: number | null;
  has_primary_evidence: boolean;
  steps: readonly Readonly<{
    order: number;
    description: string;
    evidence: readonly ViralSkillEvidence[];
  }>[];
}>;

export type ViralSkillLink = Readonly<{
  skill_id: string;
  revision: number;
  status: ViralSkillStatus;
  reuse_mode: ApprovedViralSkillReuseMode;
  source_status: ViralSkillSourceStatus;
  update_available: boolean;
  eligibility: ViralSkillEligibility;
}>;

export type ViralSkillCandidate = Readonly<{
  schema_version: typeof VIRAL_SKILL_VERSION;
  candidate_id: string;
  cluster_key: string;
  revision: number;
  active: boolean;
  suggested_name: string;
  suggested_mechanism: string;
  suggested_reuse_mode: ViralSkillReuseMode;
  classification: ViralSkillClassification;
  is_common: boolean;
  evidence_level: ViralSkillEvidenceLevel;
  causality_status: "not_established";
  distinct_video_count: number;
  occurrence_count: number;
  steps: readonly Readonly<{ order: number; description: string }>[];
  necessary_conditions: readonly string[];
  failure_signals: readonly string[];
  occurrences: readonly ViralSkillOccurrence[];
  refreshed_at: string;
  linked_skill: ViralSkillLink | null;
}>;

export type ViralSkill = Readonly<{
  schema_version: typeof VIRAL_SKILL_VERSION;
  skill_id: string;
  origin_candidate_id: string;
  based_on_candidate_revision: number;
  revision: number;
  status: ViralSkillStatus;
  reuse_mode: ApprovedViralSkillReuseMode;
  name: string;
  mechanism: string;
  classification: ViralSkillClassification;
  evidence_level: ViralSkillEvidenceLevel;
  causality_status: "not_established";
  distinct_video_count: number;
  occurrence_count: number;
  representative_occurrence_id: string;
  steps: readonly Readonly<{ order: number; description: string }>[];
  necessary_conditions: readonly string[];
  failure_signals: readonly string[];
  occurrences: readonly ViralSkillOccurrence[];
  review: Readonly<{ reviewer: string; reviewed_at: string; note: string | null }>;
  created_at: string;
  updated_at: string;
}>;

// Management APIs add live source state to the immutable, schema-validated
// formal Skill snapshot. These fields are deliberately not persisted in the
// viral-skill JSON contract.
export type ViralSkillView = ViralSkill & Readonly<{
  source_status: ViralSkillSourceStatus;
  update_available: boolean;
  eligibility: ViralSkillEligibility;
}>;

export type ViralSkillCandidateSummary = Omit<ViralSkillCandidate, "occurrences">;
export type ViralSkillSummary = Omit<ViralSkillView, "occurrences" | "steps" | "necessary_conditions" | "failure_signals">;
