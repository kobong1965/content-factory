import { runtimeApiBaseUrl } from './runtimeApi';
import type {
  ProductProfile,
  ScriptContentGoal,
  ScriptPackage,
  ScriptRevision,
  ScriptSummary,
  ScriptTask,
  S5Product,
  S5Readiness,
  S5Template,
  ViralSkill,
} from "@content-factory/contracts";

const API_BASE_URL = runtimeApiBaseUrl;

export type ScriptContext = Readonly<{
  product: ProductProfile;
  skill?: ViralSkill | null;
  pattern: Readonly<{
    id: string;
    name: string;
    mechanism: string;
    steps: readonly Readonly<{ id: string; order: number; description: string; evidence_ids: readonly string[] }>[];
    necessary_conditions: readonly string[];
    failure_signals: readonly string[];
  }>;
  analysis: Readonly<{
    analysis_id: string;
    revision: number;
    summary: unknown;
    evidence?: readonly Readonly<{
      id: string;
      claim: string;
      source_type: string;
      source_id: string;
      start_ms: number;
      end_ms: number;
      confidence: number;
      is_inference: boolean;
    }>[];
  }>;
}>;

export type ScriptSkillTrace = Readonly<{
  name: string;
  revision: number;
  distinctVideoCount: number;
  evidenceCount: number;
  sources: readonly Readonly<{
    sourceName: string;
    videoId: string;
    startMs: number | null;
    endMs: number | null;
    dialogue: string | null;
    action: string | null;
  }>[];
}>;

export type ScriptSkillSummary = Omit<S5Template, "status" | "reuse_mode"> & Readonly<{
  skill_id: string;
  skill_revision: number;
  status: "approved" | "disabled";
  reuse_mode: "reuse" | "avoid";
  distinct_video_count: number;
  occurrence_count: number;
  script_usage_count: number;
  classification: "single_video" | "common_candidate";
  evidence_level: "content_observation" | "content_inference" | "metric_correlation";
  causality_status: "not_established";
  representative_sources: readonly Readonly<{
    source_name: string;
    video_id: string;
    start_ms: number | null;
    end_ms: number | null;
    dialogue: string | null;
    action: string | null;
  }>[];
}>;

export const DEFAULT_S5_READINESS: S5Readiness = {
  stage: "S5",
  engineering_ready: false,
  gateway_configured: false,
  available_templates: 0,
  available_products: 0,
  pending_tasks: 0,
  running_tasks: 0,
  failed_tasks: 0,
  pending_review_scripts: 0,
  approved_scripts: 0,
  accepted_real_scripts: 0,
  required_real_scripts: 1,
  business_ready: false,
  pending_reason: "本机接口未连接",
};

export const scriptGoalLabels: Record<ScriptContentGoal, string> = {
  seeding: "商品种草",
  conversion: "带货成交",
  review: "真人测评",
  brand: "品牌内容",
};

export const scriptReviewLabels = {
  draft: "草稿",
  pending: "待人工审核",
  approved: "可拍摄",
  rejected: "已驳回",
} as const;

export const scriptTaskLabels = {
  pending: "等待生成",
  running: "正在生成",
  retry_wait: "等待重试",
  completed: "生成完成",
  failed: "生成失败",
} as const;

export const scriptStepLabels = {
  queued: "排队",
  prepare: "整理事实与模板",
  gateway: "中转站生成",
  validate: "检查事实与可拍性",
  persist: "保存脚本",
  completed: "完成",
  failed: "失败",
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
    // Preserve the stable fallback for a non-JSON relay or service failure.
  }
  throw new Error(message);
}

export function formatScriptTime(milliseconds: number): string {
  const seconds = Math.max(0, milliseconds) / 1000;
  return `${Number.isInteger(seconds) ? seconds : seconds.toFixed(1)}s`;
}

export function fetchS5Readiness(signal?: AbortSignal): Promise<S5Readiness> {
  return fetch(`${API_BASE_URL}/s5/readiness`, { signal }).then(responseJson<S5Readiness>);
}

export function eligibleScriptSkills(skills: readonly ScriptSkillSummary[]): ScriptSkillSummary[] {
  return skills.filter((skill) => (
    skill.status === "approved"
    && skill.reuse_mode === "reuse"
    && skill.eligibility.s5_eligible
  ));
}

export function reconcileSelectedSkillId(current: string, skills: readonly ScriptSkillSummary[]): string {
  return current && skills.some((skill) => skill.skill_id === current) ? current : "";
}

export function reconcileSelectedProductId(current: string, products: readonly S5Product[]): string {
  return current && products.some((product) => product.product_id === current) ? current : "";
}

export function scriptSkillTrace(context: Pick<ScriptContext, "skill">): ScriptSkillTrace | null {
  const skill = context.skill;
  if (!skill) return null;
  return {
    name: skill.name,
    revision: skill.revision,
    distinctVideoCount: skill.distinct_video_count,
    evidenceCount: skill.occurrence_count,
    sources: skill.occurrences.slice(0, 3).map((occurrence) => {
      const evidence = occurrence.steps
        .flatMap((step) => step.evidence)
        .find((item) => item.exact_dialogue || item.action);
      return {
        sourceName: occurrence.source_name,
        videoId: occurrence.video_id,
        startMs: occurrence.start_ms,
        endMs: occurrence.end_ms,
        dialogue: evidence?.exact_dialogue ?? evidence?.subtitle ?? null,
        action: evidence?.action ?? evidence?.visual_event ?? null,
      };
    }),
  };
}

export function fetchS5Templates(signal?: AbortSignal): Promise<ScriptSkillSummary[]> {
  return fetch(`${API_BASE_URL}/s5/templates`, { signal }).then(responseJson<ScriptSkillSummary[]>);
}

export function fetchS5Products(signal?: AbortSignal): Promise<S5Product[]> {
  return fetch(`${API_BASE_URL}/s5/products`, { signal }).then(responseJson<S5Product[]>);
}

export function fetchScriptTasks(signal?: AbortSignal): Promise<ScriptTask[]> {
  return fetch(`${API_BASE_URL}/s5/tasks`, { signal }).then(responseJson<ScriptTask[]>);
}

export function fetchScripts(signal?: AbortSignal): Promise<ScriptSummary[]> {
  return fetch(`${API_BASE_URL}/s5/scripts`, { signal }).then(responseJson<ScriptSummary[]>);
}

export function createScriptGeneration(input: {
  skill_id: string;
  product_id: string;
  content_goal: ScriptContentGoal;
  target_audience: string;
  version_count: 3 | 4 | 5;
}): Promise<ScriptTask> {
  return fetch(`${API_BASE_URL}/s5/generations`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  }).then(responseJson<ScriptTask>);
}

export function retryScriptTask(taskId: string): Promise<ScriptTask> {
  return fetch(`${API_BASE_URL}/s5/tasks/${encodeURIComponent(taskId)}/retry`, { method: "POST" }).then(responseJson<ScriptTask>);
}

export function fetchScript(scriptId: string): Promise<ScriptPackage> {
  return fetch(`${API_BASE_URL}/s5/scripts/${encodeURIComponent(scriptId)}`).then(responseJson<ScriptPackage>);
}

export function fetchScriptContext(scriptId: string): Promise<ScriptContext> {
  return fetch(`${API_BASE_URL}/s5/scripts/${encodeURIComponent(scriptId)}/context`).then(responseJson<ScriptContext>);
}

export function fetchScriptRevisions(scriptId: string): Promise<ScriptRevision[]> {
  return fetch(`${API_BASE_URL}/s5/scripts/${encodeURIComponent(scriptId)}/versions`).then(responseJson<ScriptRevision[]>);
}

export function saveScript(script: ScriptPackage, actor: string): Promise<ScriptPackage> {
  const { revision, content_goal, target_audience, selected_version_id, versions, shooting_order, material_checklist } = script;
  return fetch(`${API_BASE_URL}/s5/scripts/${encodeURIComponent(script.script_id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      expected_revision: revision, actor, content_goal, target_audience,
      selected_version_id: selected_version_id ?? versions[0]?.id,
      versions, shooting_order, material_checklist,
    }),
  }).then(responseJson<ScriptPackage>);
}

export function reviewScript(
  script: ScriptPackage, status: "approved" | "rejected", reviewer: string, note: string,
): Promise<ScriptPackage> {
  return fetch(`${API_BASE_URL}/s5/scripts/${encodeURIComponent(script.script_id)}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_revision: script.revision, status, reviewer, note: note.trim() || null }),
  }).then(responseJson<ScriptPackage>);
}
