export const PUBLICATION_VERSION = "1.0.0" as const;

export type Publication = Readonly<{
  schema_version: typeof PUBLICATION_VERSION; fixture_data: boolean; revision: number;
  publication_id: string; platform: "douyin_cn"; output_id: string; project_id: string; project_revision: number;
  variant_id: string; script_id: string; script_version_id: string; product_id: string;
  status: "published" | "archived"; work_url: string; work_id: string; account_label: string;
  title: string; published_at: string; registered_by: string; note: string; output_sha256: string;
  business_review: Readonly<{ status: "pending" | "confirmed"; reviewed_by: string | null; reviewed_at: string | null; note: string }>;
  history: readonly Readonly<{ revision: number; action: "registered" | "corrected" | "archived" | "restored" | "loop_confirmed"; actor: string; at: string; note: string }>[];
  created_at: string; updated_at: string;
}>;

export type MetricName = "views" | "followers_gained" | "likes" | "comments" | "favorites" | "shares" | "product_clicks" | "orders" | "gmv_cents" | "retention_3s" | "completion_rate" | "product_ctr" | "conversion_rate";
export type PublicationMetrics = Readonly<Partial<Record<MetricName, number>>>;

export type MetricSnapshot = Readonly<{
  schema_version: "1.0.0"; fixture_data: boolean; snapshot_id: string; publication_id: string;
  captured_at: string; observation_minutes: number; source: "manual" | "csv" | "screenshot_ocr" | "authorized_api";
  confidence: "confirmed" | "estimated" | "unconfirmed"; confirmed_by: string; confirmed_at: string;
  is_correction: boolean; correction_reason: string | null; metrics: PublicationMetrics; created_at: string;
}>;

export type MetricImportCandidate = Readonly<{
  candidate_id: string; publication_id: string; captured_at: string; confidence: "confirmed" | "estimated" | "unconfirmed";
  metrics: PublicationMetrics; field_confidence: Readonly<Record<string, number>>;
}>;

export type MetricImportDraft = Readonly<{
  schema_version: "1.0.0"; fixture_data: boolean; draft_id: string; import_kind: "csv" | "ocr";
  status: "pending" | "confirmed" | "rejected"; source_name: string; source_sha256: string; raw_text: string;
  candidates: readonly MetricImportCandidate[]; errors: readonly string[]; confirmed_snapshot_ids: readonly string[];
  created_by: string; created_at: string; updated_at: string;
}>;

export type LearningMetric = "views" | "retention_3s" | "completion_rate" | "engagement_per_1000" | "product_ctr" | "conversion_rate" | "orders" | "gmv_cents";
export type LearningReport = Readonly<{
  schema_version: "1.0.0"; fixture_data: boolean; report_id: string; product_id: string;
  status: "ready" | "insufficient_data"; primary_metric: LearningMetric; target_window_minutes: number;
  publication_ids: readonly string[];
  comparison: readonly Readonly<{ publication_id: string; snapshot_id: string; variant_id: string; observation_minutes: number; value: number | null; views: number | null; engagement_per_1000: number | null }>[];
  winner_publication_id: string | null; observations: readonly string[];
  evidence: readonly Readonly<{ publication_id: string; snapshot_id: string; statement: string }>[];
  limitations: readonly string[]; next_tests: readonly string[]; generated_at: string;
}>;

export type S8Readiness = Readonly<{
  stage: "S8"; engineering_ready: boolean; ocr_ready: boolean; eligible_outputs: number; publication_count: number;
  pending_imports: number; snapshot_count: number; report_count: number; closed_real_loops: number;
  required_real_loops: 1; business_ready: boolean; pending_reason: string | null;
  publishing_mode: "manual_registration"; learning_mode: "local_explainable_rules";
}>;
