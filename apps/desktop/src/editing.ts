import { runtimeApiBaseUrl } from './runtimeApi';
import type {
  EditAudioAsset, EditClip, EditProject, EditSettings, EditVariant, RenderOutput, RenderTask,
  S7EligibleScript, S7Readiness,
} from "@content-factory/contracts";

const API_BASE_URL = runtimeApiBaseUrl;

export const DEFAULT_S7_READINESS: S7Readiness = {
  stage: "S7", engineering_ready: false, ffmpeg_ready: false, ffprobe_ready: false,
  eligible_scripts: 0, project_count: 0, pending_renders: 0, failed_renders: 0,
  outputs_waiting_review: 0, accepted_real_outputs: 0, required_real_outputs: 1,
  max_parallel_renders: 2, business_ready: false, pending_reason: "本机接口未连接",
  jianying_mode: "experimental_handoff_only",
};

export const renderStatusLabels = {
  pending: "等待渲染", running: "正在渲染", retry_wait: "等待自动重试", completed: "渲染完成", failed: "渲染失败",
} as const;

export const outputStatusLabels = { video_review: "待看成片", approved: "已通过", rejected: "已驳回" } as const;
export const projectStatusLabels = { draft: "编辑中", video_review: "待看成片", approved: "已通过", rejected: "需修改" } as const;

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
  try {
    const payload = (await response.json()) as { detail?: unknown };
    message = validationMessage(payload.detail) ?? message;
  } catch { /* Keep the readable fallback. */ }
  throw new Error(message);
}

export function fetchS7Readiness(signal?: AbortSignal) { return fetch(`${API_BASE_URL}/s7/readiness`, { signal }).then(responseJson<S7Readiness>); }
export function fetchEligibleScripts(signal?: AbortSignal) { return fetch(`${API_BASE_URL}/s7/eligible-scripts`, { signal }).then(responseJson<S7EligibleScript[]>); }
export function fetchEditProjects(signal?: AbortSignal) { return fetch(`${API_BASE_URL}/s7/projects`, { signal }).then(responseJson<EditProject[]>); }
export function fetchRenderTasks(signal?: AbortSignal) { return fetch(`${API_BASE_URL}/s7/render-tasks`, { signal }).then(responseJson<RenderTask[]>); }
export function fetchRenderOutputs(signal?: AbortSignal) { return fetch(`${API_BASE_URL}/s7/outputs`, { signal }).then(responseJson<RenderOutput[]>); }
export function fetchEditAudio(signal?: AbortSignal) { return fetch(`${API_BASE_URL}/s7/audio`, { signal }).then(responseJson<EditAudioAsset[]>); }

export function createEditProject(scriptId: string, actor: string) {
  return fetch(`${API_BASE_URL}/s7/projects`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ script_id: scriptId, actor }),
  }).then(responseJson<{ project: EditProject; duplicate: boolean }>);
}

export function saveEditProject(project: EditProject, actor: string) {
  return fetch(`${API_BASE_URL}/s7/projects/${encodeURIComponent(project.project_id)}`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_revision: project.revision, actor, settings: project.settings, variants: project.variants }),
  }).then(responseJson<EditProject>);
}

export function startProjectRender(project: EditProject, variantId: string) {
  return fetch(`${API_BASE_URL}/s7/projects/${encodeURIComponent(project.project_id)}/render`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_revision: project.revision, variant_id: variantId }),
  }).then(responseJson<RenderTask>);
}

export function retryProjectRender(taskId: string) {
  return fetch(`${API_BASE_URL}/s7/render-tasks/${encodeURIComponent(taskId)}/retry`, { method: "POST" }).then(responseJson<RenderTask>);
}

export function reviewRenderOutput(outputId: string, decision: "approved" | "rejected", reviewer: string, note: string) {
  return fetch(`${API_BASE_URL}/s7/outputs/${encodeURIComponent(outputId)}/review`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision, reviewer, note: note || null }),
  }).then(responseJson<RenderOutput>);
}

export function uploadEditAudio(input: { kind: "bgm" | "sound_effect"; name: string; license_note: string; imported_by: string; audio: File }) {
  const body = new FormData();
  Object.entries(input).forEach(([key, value]) => body.append(key, value));
  return fetch(`${API_BASE_URL}/s7/audio`, { method: "POST", body }).then(responseJson<{ asset: EditAudioAsset; duplicate: boolean }>);
}

export const materialPreviewUrl = (materialId: string) => `${API_BASE_URL}/s6/materials/${encodeURIComponent(materialId)}/proxy`;
export const outputResourceUrl = (output: RenderOutput, field: keyof Omit<RenderOutput["resources"], "jianying_experimental">, download = false) =>
  `${API_BASE_URL}/s7/outputs/${encodeURIComponent(output.output_id)}/resources/${encodeURIComponent(String(output.resources[field]))}${download ? "?download=true" : ""}`;

export function formatEditDuration(milliseconds: number): string {
  const seconds = milliseconds / 1000;
  return seconds >= 60 ? `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, "0")}` : `${seconds.toFixed(seconds % 1 ? 1 : 0)} 秒`;
}

export function resequenceClips(variant: EditVariant, clips: readonly EditClip[]): EditVariant {
  let cursor = 0;
  const next = clips.map((clip, index) => {
    const duration = clip.timeline_end_ms - clip.timeline_start_ms;
    const changed = { ...clip, order: index + 1, timeline_start_ms: cursor, timeline_end_ms: cursor + duration };
    cursor += duration;
    return changed;
  });
  return { ...variant, clips: next, duration_ms: cursor, latest_output_id: null };
}

export function patchSettings(settings: EditSettings, changes: Partial<EditSettings>): EditSettings {
  return { ...settings, ...changes };
}

const cloneProject = (project: EditProject): EditProject => structuredClone(project);
const editableProjectState = (project: EditProject) => JSON.stringify([project.settings, project.variants]);

/** Keep a local edit when polling returns another object for the same project. */
export function reconcileEditDraft(
  currentDraft: EditProject | null,
  previousServerProject: EditProject | null,
  incomingServerProject: EditProject,
): EditProject {
  if (!currentDraft || currentDraft.project_id !== incomingServerProject.project_id) {
    return cloneProject(incomingServerProject);
  }
  const hasUnsavedChanges = Boolean(
    previousServerProject
    && previousServerProject.project_id === currentDraft.project_id
    && editableProjectState(currentDraft) !== editableProjectState(previousServerProject),
  );
  return hasUnsavedChanges ? currentDraft : cloneProject(incomingServerProject);
}
