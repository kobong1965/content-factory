import { runtimeApiBaseUrl } from './runtimeApi';
import type {
  MaterialAsset, MaterialCaptureRole, MaterialImportTask, MaterialSummary, MaterialUsage, S6Readiness, S6ScriptOption,
  ShootingTask,
} from "@content-factory/contracts";

const API_BASE_URL = runtimeApiBaseUrl;

export type S6Product = Readonly<{
  product_id: string; revision: number; sku: string; name: string; selling_points: readonly string[]; max_duration_ms: number;
}>;

export const DEFAULT_S6_READINESS: S6Readiness = {
  stage: "S6", engineering_ready: false, ffmpeg_ready: false, ffprobe_ready: false, whisper_ready: false,
  gateway_configured: false, material_count: 0, clip_count: 0, pending_imports: 0, failed_imports: 0,
  pending_shoot_tasks: 0, ready_for_edit_tasks: 0, accepted_real_ready_tasks: 0, required_real_ready_tasks: 1,
  business_ready: false, pending_reason: "本机接口未连接",
};

export const importStatusLabels = {
  pending: "等待处理", running: "本机处理中", retry_wait: "等待自动重试", completed: "已入素材库", failed: "处理失败",
} as const;

export const recognitionLabels = {
  pending: "等待 AI 识别", completed: "AI 标签已完成", not_configured: "人工标注模式", failed: "AI 识别失败",
} as const;

export const purposeLabels = {
  hook: "开场钩子", proof: "上身证明", detail: "面料细节", comfort: "舒适体验",
  cta: "行动引导", transition: "转场", broll: "补充画面",
} as const;

export const shootingStatusLabels = {
  pending_shoot: "待拍摄", materials_uploaded: "素材补齐中", ready_for_edit: "待剪辑",
} as const;

function validationMessage(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return null;
  const first = detail[0] as { loc?: unknown[]; msg?: string } | undefined;
  if (!first?.msg) return null;
  const field = first.loc?.slice(1).join(" → ");
  return field ? `${field}：${first.msg}` : first.msg;
}

async function responseJson<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;
  let message = `本地接口返回 ${response.status}`;
  try {
    const payload = (await response.json()) as { detail?: unknown };
    message = validationMessage(payload.detail) ?? message;
  } catch {
    // Keep a readable fallback if a local process exits unexpectedly.
  }
  throw new Error(message);
}

export const materialProxyUrl = (materialId: string) => `${API_BASE_URL}/s6/materials/${encodeURIComponent(materialId)}/proxy`;
export const materialKeyframeUrl = (materialId: string, clipId: string) => `${API_BASE_URL}/s6/materials/${encodeURIComponent(materialId)}/clips/${encodeURIComponent(clipId)}/keyframe`;

export function suggestionSourceLabel(
  materials: readonly MaterialSummary[], materialId: string, clipId: string,
): string {
  const material = materials.find((item) => item.material_id === materialId);
  if (!material) return clipId;
  const order = clipId.match(/_(\d+)$/)?.[1];
  const clipLabel = order ? `片段 ${String(Number(order)).padStart(2, "0")}` : clipId;
  return `${material.original_name} · ${material.batch} · ${clipLabel}`;
}

export function materialClipKey(materialId: string, clipId: string): string {
  return `${materialId}::${clipId}`;
}

export function confirmedMaterialClipKeys(
  requirements: readonly { confirmed_match: { material_id: string; clip_id: string } | null }[],
): Set<string> {
  return new Set(requirements.flatMap((item) => item.confirmed_match
    ? [materialClipKey(item.confirmed_match.material_id, item.confirmed_match.clip_id)]
    : []));
}

export function fetchS6Readiness(signal?: AbortSignal) { return fetch(`${API_BASE_URL}/s6/readiness`, { signal }).then(responseJson<S6Readiness>); }
export function fetchS6Products(signal?: AbortSignal) { return fetch(`${API_BASE_URL}/s6/products`, { signal }).then(responseJson<S6Product[]>); }
export function fetchS6Scripts(signal?: AbortSignal) { return fetch(`${API_BASE_URL}/s6/scripts`, { signal }).then(responseJson<S6ScriptOption[]>); }
export function fetchMaterialImports(signal?: AbortSignal) { return fetch(`${API_BASE_URL}/s6/imports`, { signal }).then(responseJson<MaterialImportTask[]>); }
export function fetchMaterials(signal?: AbortSignal) { return fetch(`${API_BASE_URL}/s6/materials`, { signal }).then(responseJson<MaterialSummary[]>); }
export function fetchShootingTasks(signal?: AbortSignal) { return fetch(`${API_BASE_URL}/s6/shooting-tasks`, { signal }).then(responseJson<ShootingTask[]>); }
export function fetchMaterial(materialId: string) { return fetch(`${API_BASE_URL}/s6/materials/${encodeURIComponent(materialId)}`).then(responseJson<MaterialAsset>); }
export function fetchMaterialUsage(materialId: string) { return fetch(`${API_BASE_URL}/s6/materials/${encodeURIComponent(materialId)}/usage`).then(responseJson<MaterialUsage[]>); }

export type MaterialUploadInput = Readonly<{
  product_id: string; source_script_id: string; model_name: string; scene: string; shot_date: string;
  batch: string; imported_by: string; capture_role: MaterialCaptureRole; note: string; video: File;
}>;

export function uploadMaterial(input: MaterialUploadInput): Promise<MaterialImportTask> {
  const body = new FormData();
  for (const [key, value] of Object.entries(input)) body.append(key, value);
  if (!input.source_script_id) body.delete("source_script_id");
  return fetch(`${API_BASE_URL}/s6/imports`, { method: "POST", body }).then(responseJson<MaterialImportTask>);
}

export function retryMaterialImport(taskId: string) {
  return fetch(`${API_BASE_URL}/s6/imports/${encodeURIComponent(taskId)}/retry`, { method: "POST" }).then(responseJson<MaterialImportTask>);
}

export function saveMaterial(material: MaterialAsset, actor: string): Promise<MaterialAsset> {
  return fetch(`${API_BASE_URL}/s6/materials/${encodeURIComponent(material.material_id)}`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_revision: material.revision, actor, archive: material.archive, clips: material.clips }),
  }).then(responseJson<MaterialAsset>);
}

export function recognizeMaterial(materialId: string): Promise<MaterialAsset> {
  return fetch(`${API_BASE_URL}/s6/materials/${encodeURIComponent(materialId)}/recognize`, { method: "POST" }).then(responseJson<MaterialAsset>);
}

export function confirmMaterialMatch(scriptId: string, input: { script_shot_id: string; material_id: string; clip_id: string; actor: string }) {
  return fetch(`${API_BASE_URL}/s6/shooting-tasks/${encodeURIComponent(scriptId)}/matches`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  }).then(responseJson<ShootingTask>);
}

export function releaseMaterialMatch(scriptId: string, shotId: string, actor: string) {
  return fetch(`${API_BASE_URL}/s6/shooting-tasks/${encodeURIComponent(scriptId)}/matches/${encodeURIComponent(shotId)}`, {
    method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ actor }),
  }).then(responseJson<ShootingTask>);
}
