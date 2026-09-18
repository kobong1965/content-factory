import { runtimeApiBaseUrl } from './runtimeApi';
import type {
  LearningMetric, LearningReport, MetricImportCandidate, MetricImportDraft, MetricSnapshot, Publication,
  PublicationMetrics, S8Readiness,
} from "@content-factory/contracts";

const API_BASE_URL = runtimeApiBaseUrl;

export type EligibleOutput = Readonly<{
  output_id: string; project_id: string; project_revision: number; variant_id: string; variant_name: string;
  script_id: string; script_version_id: string; product_id: string; filename: string; duration_ms: number;
  fixture_data: boolean; approved_at: string;
}>;

export type MetricImportCandidateOverride = Pick<MetricImportCandidate, "candidate_id" | "captured_at" | "confidence" | "metrics">;

export const DEFAULT_S8_READINESS: S8Readiness = {
  stage: "S8", engineering_ready: false, ocr_ready: false, eligible_outputs: 0, publication_count: 0,
  pending_imports: 0, snapshot_count: 0, report_count: 0, closed_real_loops: 0,
  required_real_loops: 1, business_ready: false, pending_reason: "本机接口未连接",
  publishing_mode: "manual_registration", learning_mode: "local_explainable_rules",
};

function validationMessage(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return null;
  const first = detail[0] as { loc?: unknown[]; msg?: string } | undefined;
  const field = first?.loc?.slice(1).join(" → ");
  return first?.msg ? (field ? `${field}：${first.msg}` : first.msg) : null;
}

async function responseJson<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;
  let message = `本地接口返回 ${response.status}`;
  try { const payload = (await response.json()) as { detail?: unknown }; message = validationMessage(payload.detail) ?? message; }
  catch { /* Keep the readable fallback. */ }
  throw new Error(message);
}

export const fetchS8Readiness = (signal?: AbortSignal) => fetch(`${API_BASE_URL}/s8/readiness`, { signal }).then(responseJson<S8Readiness>);
export const fetchEligibleOutputs = (signal?: AbortSignal) => fetch(`${API_BASE_URL}/s8/eligible-outputs`, { signal }).then(responseJson<EligibleOutput[]>);
export const fetchPublications = (signal?: AbortSignal) => fetch(`${API_BASE_URL}/s8/publications`, { signal }).then(responseJson<Publication[]>);
export const fetchMetricSnapshots = (signal?: AbortSignal) => fetch(`${API_BASE_URL}/s8/metric-snapshots`, { signal }).then(responseJson<MetricSnapshot[]>);
export const fetchMetricImports = (signal?: AbortSignal) => fetch(`${API_BASE_URL}/s8/imports`, { signal }).then(responseJson<MetricImportDraft[]>);
export const fetchLearningReports = (signal?: AbortSignal) => fetch(`${API_BASE_URL}/s8/learning-reports`, { signal }).then(responseJson<LearningReport[]>);

export function registerPublication(input: {
  output_id: string; work_url: string; work_id: string; account_label: string; title: string;
  published_at: string; actor: string; note: string;
}) {
  return fetch(`${API_BASE_URL}/s8/publications`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  }).then(responseJson<{ publication: Publication; duplicate: boolean }>);
}

export function addMetricSnapshot(publicationId: string, input: {
  captured_at: string; confirmed_by: string; metrics: PublicationMetrics;
  confidence?: "confirmed" | "estimated"; is_correction?: boolean; correction_reason?: string | null;
}) {
  return fetch(`${API_BASE_URL}/s8/publications/${encodeURIComponent(publicationId)}/metric-snapshots`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  }).then(responseJson<MetricSnapshot>);
}

export function confirmBusinessReview(publicationId: string, expectedRevision: number, reviewedBy: string, note: string) {
  return fetch(`${API_BASE_URL}/s8/publications/${encodeURIComponent(publicationId)}/confirm-business-review`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_revision: expectedRevision, reviewed_by: reviewedBy, note }),
  }).then(responseJson<Publication>);
}

export function uploadMetricCsv(file: File, createdBy: string) {
  const body = new FormData(); body.append("created_by", createdBy); body.append("data", file);
  return fetch(`${API_BASE_URL}/s8/imports/csv`, { method: "POST", body }).then(responseJson<{ draft: MetricImportDraft; duplicate: boolean }>);
}

export function uploadMetricScreenshot(file: File, publicationId: string, capturedAt: string, createdBy: string) {
  const body = new FormData(); body.append("publication_id", publicationId); body.append("captured_at", capturedAt);
  body.append("created_by", createdBy); body.append("image", file);
  return fetch(`${API_BASE_URL}/s8/imports/ocr`, { method: "POST", body }).then(responseJson<{ draft: MetricImportDraft; duplicate: boolean }>);
}

export function confirmMetricImport(
  draftId: string,
  confirmedBy: string,
  candidates: readonly MetricImportCandidateOverride[] = [],
) {
  return fetch(`${API_BASE_URL}/s8/imports/${encodeURIComponent(draftId)}/confirm`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed_by: confirmedBy, candidates }),
  }).then(responseJson<{ draft: MetricImportDraft; snapshots: MetricSnapshot[] }>);
}

export function rejectMetricImport(draftId: string, actor: string, note: string) {
  return fetch(`${API_BASE_URL}/s8/imports/${encodeURIComponent(draftId)}/reject`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ actor, note }),
  }).then(responseJson<MetricImportDraft>);
}

export function generateLearningReport(productId: string, primaryMetric: LearningMetric, targetWindowMinutes: number) {
  return fetch(`${API_BASE_URL}/s8/learning-reports`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ product_id: productId, primary_metric: primaryMetric, target_window_minutes: targetWindowMinutes }),
  }).then(responseJson<LearningReport>);
}

export function deriveDouyinWorkId(url: string): string {
  try { return new URL(url).pathname.match(/\/(?:video|note)\/([A-Za-z0-9_-]{6,40})/)?.[1] ?? ""; }
  catch { return ""; }
}

export function toIsoTime(localValue: string): string {
  const date = new Date(localValue);
  if (Number.isNaN(date.getTime())) throw new Error("请填写有效时间");
  return date.toISOString();
}

export function isValidLocalDateTime(value: string): boolean {
  if (!value.trim()) return false;
  return !Number.isNaN(new Date(value).getTime());
}

/**
 * Hooks in this app return `null` after they have converted an API failure to
 * an operator-facing message. Forms must only discard user input for a real
 * successful result.
 */
export function resetAfterSuccessfulSubmission(result: unknown, reset: () => void): boolean {
  if (result === null || result === undefined) return false;
  reset();
  return true;
}

export type DraftRejectNotes = Readonly<Record<string, string>>;

export function draftRejectNote(notes: DraftRejectNotes, draftId: string): string {
  return notes[draftId] ?? "";
}

export function updateDraftRejectNote(
  notes: DraftRejectNotes,
  draftId: string,
  note: string,
): DraftRejectNotes {
  return { ...notes, [draftId]: note };
}

export function clearDraftRejectNote(notes: DraftRejectNotes, draftId: string): DraftRejectNotes {
  if (!(draftId in notes)) return notes;
  const next = { ...notes };
  delete next[draftId];
  return next;
}

export function parseMetricInput(field: keyof PublicationMetrics, value: string): number | undefined {
  const clean = value.trim();
  if (!clean) return undefined;
  const number = Number(clean);
  if (!Number.isFinite(number) || number < 0) throw new Error("指标必须是非负数字");
  if (["retention_3s", "completion_rate", "product_ctr", "conversion_rate"].includes(field)) {
    if (number > 100) throw new Error("百分比不能超过 100");
    return number / 100;
  }
  if (field === "gmv_cents") return Math.round(number * 100);
  return Math.round(number);
}

export function formatMetric(field: string, value: number | undefined): string {
  if (value === undefined) return "—";
  if (["retention_3s", "completion_rate", "product_ctr", "conversion_rate"].includes(field)) return `${(value * 100).toFixed(2)}%`;
  if (field === "gmv_cents") return `¥${(value / 100).toFixed(2)}`;
  return new Intl.NumberFormat("zh-CN").format(value);
}
