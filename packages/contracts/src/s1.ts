export const S1_SCHEMA_VERSION = "1.0.0" as const;

export const s1SchemaCatalog = [
  { kind: "analysis", file: "analysis-report.schema.json", name: "爆款视频深度分析" },
  { kind: "product", file: "product-profile.schema.json", name: "商品事实档案" },
  { kind: "script", file: "script-package.schema.json", name: "可拍摄脚本包" },
  { kind: "gold_case", file: "gold-set-case.schema.json", name: "金标准案例" },
  { kind: "gold_manifest", file: "gold-set-manifest.schema.json", name: "金标准清单" },
] as const;

export type S1SchemaKind = (typeof s1SchemaCatalog)[number]["kind"];

export const evidenceSourceTypes = [
  "transcript",
  "frame",
  "ocr",
  "audio",
  "metadata",
  "manual_annotation",
] as const;

export type EvidenceSourceType = (typeof evidenceSourceTypes)[number];

export type EvidenceRef = Readonly<{
  evidence_id: string;
  source_type: EvidenceSourceType;
  source_id: string;
  start_ms: number;
  end_ms: number;
  confidence: number;
  is_inference: boolean;
  statement: string;
}>;

export type S1Readiness = Readonly<{
  stage: "S1";
  schema_version: typeof S1_SCHEMA_VERSION;
  schema_count: number;
  engineering_ready: boolean;
  business_ready: boolean;
  accepted_videos: number;
  required_videos: 20;
  accepted_products: number;
  required_products: number;
  pending_reason: string | null;
}>;

export const s1EngineeringChecks = [
  { id: "schemas", name: "5 类数据标准", detail: "分析、商品、脚本、金标准案例和清单" },
  { id: "validator", name: "自动校验器", detail: "格式、引用、时间范围和禁用表达一起检查" },
  { id: "fixtures", name: "工程样例", detail: "仅用于自动测试，明确标为非真实业务数据" },
  { id: "gold_slots", name: "真实数据槽位", detail: "20 条视频与 3 款商品的采集位置已建立" },
  { id: "api", name: "S1 就绪接口", detail: "工程就绪与真实数据就绪分开汇报" },
] as const;

export type S1EngineeringCheck = (typeof s1EngineeringChecks)[number];
