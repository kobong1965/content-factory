import { runtimeApiBaseUrl } from './runtimeApi';
import type {
  AnalysisReport,
  AnalysisTask,
  GatewayApiMode,
  GatewayInputModality,
  GatewayProvider,
  GatewayPurpose,
  GatewaySettings,
  S3Readiness,
} from "@content-factory/contracts";

const API_BASE_URL = runtimeApiBaseUrl;

export function keyframeUrl(taskId: string, frameId: string): string {
  return `${API_BASE_URL}/s3/tasks/${encodeURIComponent(taskId)}/keyframes/${encodeURIComponent(frameId)}`;
}

export const DEFAULT_S3_READINESS: S3Readiness = {
  stage: "S3",
  engineering_ready: false,
  ocr_ready: false,
  gateway_configured: false,
  queue_ready: false,
  max_cloud_workers: 2,
  pending_tasks: 0,
  running_tasks: 0,
  completed_tasks: 0,
  failed_tasks: 0,
  accepted_real_analyses: 0,
  required_real_analyses: 20,
  business_ready: false,
  pending_reason: "本地接口未连接",
};

export const DEFAULT_GATEWAY_SETTINGS: GatewaySettings = {
  schema_version: "2.1.0",
  base_url: "",
  model: "",
  api_mode: "responses",
  api_key_configured: false,
  default_model_id: null,
  models: [],
  routing: { analysis: null, script: null, material: null, video_review: null },
  updated_at: null,
};

export type GatewayModelSaveInput = {
  model_id: string;
  provider: GatewayProvider;
  display_name: string;
  base_url: string;
  model: string;
  api_mode: GatewayApiMode;
  modalities: GatewayInputModality[];
  purposes: GatewayPurpose[];
  enabled: boolean;
  api_key?: string;
};

export type GatewayCatalogModality = GatewayInputModality | "audio" | "video";

export type GatewayDiscoveredModel = {
  upstream_model_id: string;
  display_name: string;
  input_modalities: GatewayCatalogModality[] | null;
  output_modalities: GatewayCatalogModality[] | null;
  supports_structured_output: boolean | null;
  capability_source: "provider_metadata" | "unknown";
};

export type GatewayDiscoveryResult = {
  status: "ok";
  provider: Exclude<GatewayProvider, "custom">;
  normalized_base_url: string;
  catalog_source: "openai_models" | "dashscope_models";
  models: GatewayDiscoveredModel[];
  warnings: string[];
  truncated: boolean;
  fetched_at: string;
};

export type GatewayCredentialInput = {
  base_url: string;
  api_key?: string;
  saved_model_id?: string;
};

export type GatewayConnectionResult = {
  status: "ok";
  connected_model_id: string;
  latency_ms: number;
  response_id: string | null;
  settings: GatewaySettings;
};

export type AnalysisModelPurpose = Extract<GatewayPurpose, "analysis" | "video_review">;

export const analysisPurposeCopy: Readonly<Record<AnalysisModelPurpose, {
  label: string;
  shortLabel: string;
  description: string;
  creating: string;
  queued: string;
}>> = {
  analysis: {
    label: "爆点研究智能体",
    shortLabel: "爆点研究",
    description: "定位什么时候说了什么、做了什么，并把有证据的机制沉淀成可复用 Skill。",
    creating: "正在创建爆点研究任务…",
    queued: "爆点研究已进入队列。",
  },
  video_review: {
    label: "AI 视频审核",
    shortLabel: "视频审核",
    description: "检查画面、口播、OCR 文字和内容风险线索。",
    creating: "正在创建 AI 视频审核任务…",
    queued: "AI 视频审核已进入队列。",
  },
};

export function analysisProcessingPurpose(
  processing: Pick<AnalysisReport["processing"], "purpose">,
): AnalysisModelPurpose {
  return processing.purpose === "video_review" ? "video_review" : "analysis";
}

export function isAnalysisConclusionDirty(savedText: string, visibleText: string): boolean {
  return savedText.trim() !== visibleText.trim();
}

export type ApiErrorDetail = string | {
  diagnostic_code?: string;
  message?: string;
  endpoint_url?: string;
  request_url?: string;
  endpoint?: string;
  api_mode?: string;
  model?: string;
  suggestion?: string;
};

export class ApiResponseError extends Error {
  readonly status: number;
  readonly detail: ApiErrorDetail | undefined;

  constructor(message: string, status: number, detail: ApiErrorDetail | undefined) {
    super(message);
    this.name = "ApiResponseError";
    this.status = status;
    this.detail = detail;
  }
}

async function localApiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error("本机服务未启动或无法连接。请从桌面“爆款内容工厂”快捷方式重新打开软件后再试。", { cause: error });
    }
    throw error;
  }
}

const statusLabels: Record<AnalysisTask["status"], string> = {
  pending: "等待分析",
  running: "正在分析",
  retry_wait: "准备重试",
  succeeded: "报告已生成",
  completed: "报告已生成",
  failed: "分析失败",
  cancelled: "已取消",
};

const stepLabels: Record<AnalysisTask["current_step"], string> = {
  queued: "已加入队列",
  ocr: "识别画面文字",
  prepare: "整理证据材料",
  segmenting: "规划分析分段",
  segment: "分段分析中",
  gateway: "模型深度理解",
  summarizing: "汇总分析结论",
  validate: "核对证据引用",
  persist: "保存报告",
  recovered: "已从中断恢复",
  cancelling: "正在安全取消",
  cancelled: "已取消并保留检查点",
  completed: "全部完成",
  failed: "已停止",
};

export function analysisStatusLabel(status: AnalysisTask["status"]): string {
  return statusLabels[status];
}

export function analysisStepLabel(step: AnalysisTask["current_step"]): string {
  return stepLabels[step];
}

export function apiErrorDetailMessage(detail: ApiErrorDetail | undefined, status: number): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object") {
    const message = detail.message?.trim() || `本地接口返回 ${status}`;
    const requestUrl = detail.endpoint_url?.trim() || detail.request_url?.trim() || detail.endpoint?.trim();
    const mode = detail.api_mode?.trim();
    const model = detail.model?.trim();
    const modelTrace = model && !message.includes(model) ? `模型：${model}。` : "";
    const trace = requestUrl && !message.includes(requestUrl) ? `实际请求：POST ${requestUrl}${mode ? `（${mode}）` : ""}。` : "";
    const suggestion = detail.suggestion?.trim() ? `建议：${detail.suggestion.trim()}` : "";
    return [message, modelTrace, trace, suggestion].filter(Boolean).join("");
  }
  return `本地接口返回 ${status}`;
}

async function responseJson<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;
  let message = apiErrorDetailMessage(undefined, response.status);
  let detail: ApiErrorDetail | undefined;
  try {
    const payload = (await response.json()) as { detail?: ApiErrorDetail };
    detail = payload.detail;
    message = apiErrorDetailMessage(detail, response.status);
  } catch {
    // Keep the stable fallback for non-JSON failures.
  }
  throw new ApiResponseError(message, response.status, detail);
}

export async function fetchS3Readiness(signal?: AbortSignal): Promise<S3Readiness> {
  return responseJson(await localApiFetch(`${API_BASE_URL}/s3/readiness`, { signal }));
}

export async function fetchGatewaySettings(signal?: AbortSignal): Promise<GatewaySettings> {
  return responseJson(await localApiFetch(`${API_BASE_URL}/s3/gateway`, { signal }));
}

export async function saveGatewaySettings(input: {
  models: GatewayModelSaveInput[];
  default_model_id: string;
  routing: Record<GatewayPurpose, string>;
}): Promise<GatewaySettings> {
  return responseJson(await localApiFetch(`${API_BASE_URL}/s3/gateway`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      default_model_id: input.default_model_id,
      routing: input.routing,
      models: input.models.map((model) => ({ ...model, api_key: model.api_key?.trim() || null })),
    }),
  }));
}

function gatewayCredentialBody(input: GatewayCredentialInput): GatewayCredentialInput {
  const baseUrl = input.base_url.trim();
  const apiKey = input.api_key?.trim();
  const savedModelId = input.saved_model_id?.trim();
  if (!baseUrl) throw new Error("请填写 API 接口地址。");
  if (Boolean(apiKey) === Boolean(savedModelId)) {
    throw new Error("请输入新的 API Key，或复用同一服务地址已保存的密钥。");
  }
  return apiKey
    ? { base_url: baseUrl, api_key: apiKey }
    : { base_url: baseUrl, saved_model_id: savedModelId };
}

export async function discoverGatewayModels(
  input: GatewayCredentialInput,
  signal?: AbortSignal,
): Promise<GatewayDiscoveryResult> {
  return responseJson(await localApiFetch(`${API_BASE_URL}/s3/gateway/discover`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(gatewayCredentialBody(input)),
    signal,
  }));
}

export async function connectGatewayModel(
  input: GatewayCredentialInput & { upstream_model_id: string; manual_model_id?: boolean },
  signal?: AbortSignal,
): Promise<GatewayConnectionResult> {
  const upstreamModelId = input.upstream_model_id;
  if (!upstreamModelId) throw new Error("请从读取到的模型列表中选择一个模型。");
  return responseJson(await localApiFetch(`${API_BASE_URL}/s3/gateway/connect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...gatewayCredentialBody(input),
      upstream_model_id: upstreamModelId,
      ...(input.manual_model_id ? { manual_model_id: true } : {}),
    }),
    signal,
  }));
}

export async function testGateway(modelId: string): Promise<{
  status: "ok";
  model_id: string;
  model: string;
  provider: GatewayProvider;
  api_mode: GatewayApiMode;
  endpoint_url: string;
  tested_modalities: GatewayInputModality[];
  latency_ms: number;
  response_id: string | null;
}> {
  const query = new URLSearchParams({ model_id: modelId });
  return responseJson(await localApiFetch(`${API_BASE_URL}/s3/gateway/test?${query}`, { method: "POST" }));
}

export async function fetchAnalysisTasks(signal?: AbortSignal): Promise<AnalysisTask[]> {
  return responseJson(await localApiFetch(`${API_BASE_URL}/s3/tasks`, { signal }));
}

export async function createAnalysis(input: {
  media_task_id: string;
  model_purpose: AnalysisModelPurpose;
  metric_snapshots: Array<{
    source_type: "manual";
    confidence: number;
    values: Record<string, number>;
  }>;
  comments: Array<{ text: string; source_label: string; confidence: number }>;
}): Promise<AnalysisTask> {
  return responseJson(await localApiFetch(`${API_BASE_URL}/s3/analyses`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function retryAnalysisTask(taskId: string): Promise<AnalysisTask> {
  return responseJson(await localApiFetch(`${API_BASE_URL}/s3/tasks/${encodeURIComponent(taskId)}/retry`, {
    method: "POST",
  }));
}

export async function cancelAnalysisTask(taskId: string): Promise<AnalysisTask> {
  return responseJson(await localApiFetch(`${API_BASE_URL}/s3/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: "POST",
  }));
}

export async function fetchAnalysisReport(taskId: string, signal?: AbortSignal): Promise<AnalysisReport> {
  return responseJson(await localApiFetch(`${API_BASE_URL}/s3/tasks/${encodeURIComponent(taskId)}/report`, { signal }));
}

export async function updateAnalysisReport(
  taskId: string,
  expectedRevision: number,
  changes: Partial<AnalysisReport>,
): Promise<AnalysisReport> {
  return responseJson(await localApiFetch(`${API_BASE_URL}/s3/tasks/${encodeURIComponent(taskId)}/report`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_revision: expectedRevision, changes }),
  }));
}

export async function reviewAnalysisReport(
  taskId: string,
  input: { expected_revision: number; status: "reviewed" | "accepted"; reviewer: string; note: string | null },
): Promise<AnalysisReport> {
  return responseJson(await localApiFetch(`${API_BASE_URL}/s3/tasks/${encodeURIComponent(taskId)}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}
