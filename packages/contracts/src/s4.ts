export const PRODUCT_PROFILE_VERSION = "1.1.0" as const;

export type ProductStatus = "draft" | "active" | "archived";
export type ProductSourceKind = "detail_page" | "image" | "video" | "document";
export type ProductFactField = "fabric" | "fit" | "size" | "color" | "price" | "activity" | "feature" | "care" | "other";
export type ProductFactSourceType = "official_detail_page" | "supplier_document" | "manual_confirmed" | "media_evidence";

export type ProductSource = Readonly<{
  id: string;
  kind: ProductSourceKind;
  label: string;
  source_ref: string;
  asset_id: string | null;
  mime_type: string | null;
  sha256: string | null;
  size_bytes: number | null;
  managed: boolean;
}>;

export type ProductFact = Readonly<{
  id: string;
  field: ProductFactField;
  label: string;
  value: string;
  unit: string | null;
  source_type: ProductFactSourceType;
  source_id: string | null;
  source_ref: string;
  confirmed_by: string;
  confirmed_at: string;
}>;

export type ProductCompleteness = Readonly<{
  required_fields: 10;
  completed_fields: number;
  ratio: number;
  missing_fields: readonly string[];
}>;

export type ShootingConstraints = Readonly<{
  models: readonly string[];
  locations: readonly string[];
  equipment: readonly string[];
  lights: readonly string[];
  daily_available_minutes: number;
  budget_cny: number;
  reusable_assets_allowed: boolean;
  brand_tone: string;
  required_disclosures: readonly string[];
  max_duration_ms: number;
  confirmed_by: string | null;
  confirmed_at: string | null;
}>;

export type ProductProfile = Readonly<{
  schema_version: typeof PRODUCT_PROFILE_VERSION;
  fixture_data: boolean;
  revision: number;
  product_id: string;
  sku: string;
  name: string;
  category: "mens_clothing";
  status: ProductStatus;
  sources: readonly ProductSource[];
  facts: readonly ProductFact[];
  selling_point_fact_ids: readonly string[];
  forbidden_expressions: readonly string[];
  unprovable_claims: readonly string[];
  brand_boundary_confirmed_by: string | null;
  brand_boundary_confirmed_at: string | null;
  completeness: ProductCompleteness;
  shooting_constraints: ShootingConstraints;
  created_at: string;
  updated_at: string;
}>;

export type ProductListItem = Readonly<{
  product_id: string;
  sku: string;
  name: string;
  status: ProductStatus;
  revision: number;
  completeness: ProductCompleteness;
  selling_points: readonly string[];
  source_count: number;
  updated_at: string;
}>;

export type ProductVersion = Readonly<{
  revision: number;
  action: "created" | "updated" | "activated" | "archived" | "asset_added";
  actor: string;
  created_at: string;
}>;

export type ProductScriptEligibility = Readonly<{
  eligible: boolean;
  usable_fact_ids: readonly string[];
  blockers: readonly string[];
  warnings: readonly string[];
  unknown_fields: readonly string[];
}>;

export type ProductFactSuggestion = Readonly<{
  source_id: string;
  field: "color" | "fit" | "feature" | "other";
  label: string;
  value: string;
  evidence: string;
  confidence: "high" | "medium" | "low";
  source_type: "media_evidence";
}>;

export type S4Readiness = Readonly<{
  stage: "S4";
  engineering_ready: boolean;
  contract_version: typeof PRODUCT_PROFILE_VERSION;
  total_products: number;
  draft_products: number;
  active_products: number;
  archived_products: number;
  script_eligible_products: number;
  accepted_real_products: number;
  required_real_products: 3;
  business_ready: boolean;
  pending_reason: string | null;
}>;
