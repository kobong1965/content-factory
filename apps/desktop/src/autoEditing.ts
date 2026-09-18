import { runtimeApiBaseUrl } from './runtimeApi';
const API_BASE_URL = runtimeApiBaseUrl;

export type AutoEditStatus = "draft" | "queued" | "analyzing" | "planning" | "render_pending" | "rendering" | "review" | "completed" | "failed" | "cancelled";

export type AutoEditSettings = Readonly<{
  target_count: number;
  duration_min_ms: number;
  duration_max_ms: number;
  subtitle_font_size: number;
  keyword_color: string;
  keyword_scale: number;
  top_title_enabled: boolean;
  subtitle_font?: 'heiti' | 'yahei' | 'songti' | 'kaiti';
  subtitle_effect?: 'none' | 'fade' | 'pop';
}>;

export type AutoEditPlanItem = Readonly<{
  source_id?: string;
  candidate_id: string;
  title: string;
  duration_ms: number;
  selection_reason?: string;
  clips: readonly Readonly<{ start_ms: number; end_ms: number }>[];
}>;

export type AutoEditProject = Readonly<{
  project_id: string;
  title: string;
  status: AutoEditStatus;
  revision: number;
  progress: number;
  error: string | null;
  output_batch_id: string | null;
  source: Readonly<{ file_name: string; duration_ms: number; sha256: string }>;
  sources?: readonly Readonly<{ source_id: string; file_name: string; duration_ms: number; sha256: string }>[];
  settings: AutoEditSettings;
  analysis_summary: string | null;
  selected_skill: Readonly<{ snapshot: Readonly<{ skill_id: string; revision: number; name: string; mechanism: string }>; reason: string }> | null;
  plan: readonly AutoEditPlanItem[];
  created_at: string;
  updated_at: string;
}>;

export type AutoEditDraft = Readonly<{
  title: string;
  targetCount: number;
  durationMinSeconds: number;
  durationMaxSeconds: number;
  subtitleFontSize: number;
  keywordColor: string;
  keywordScale: number;
  subtitleFont?: AutoEditSettings['subtitle_font'];
  subtitleEffect?: AutoEditSettings['subtitle_effect'];
}>;

const labels: Record<AutoEditStatus, string> = {
  draft: "待开始",
  queued: "队列等待中",
  analyzing: "正在分析原素材",
  planning: "正在生成剪辑方案",
  render_pending: "等待本机剪辑",
  rendering: "正在生成成片",
  review: "等待审核",
  completed: "已完成",
  failed: "处理失败",
  cancelled: "已取消",
};

export function autoEditStatusLabel(status: AutoEditStatus): string {
  return labels[status];
}

export function validateAutoEditDraft(draft: AutoEditDraft): string | null {
  if (!draft.title.trim()) return "请填写项目名称。";
  if (!Number.isInteger(draft.targetCount) || draft.targetCount < 1 || draft.targetCount > 10) return "成片数量必须为 1—10 条。";
  if (draft.durationMinSeconds < 5 || draft.durationMaxSeconds > 180 || draft.durationMinSeconds > draft.durationMaxSeconds) return "成片时长应为 5—180 秒，且最小时长不能大于最大时长。";
  if (draft.subtitleFontSize < 32 || draft.subtitleFontSize > 120) return "字幕字号必须为 32—120。";
  if (!/^#[0-9a-f]{6}$/i.test(draft.keywordColor)) return "重点词颜色格式不正确。";
  if (draft.keywordScale < 1 || draft.keywordScale > 2) return "重点词字号倍率必须为 1.0—2.0。";
  return null;
}

export function draftSettings(draft: AutoEditDraft): AutoEditSettings {
  return {
    target_count: draft.targetCount,
    duration_min_ms: Math.round(draft.durationMinSeconds * 1000),
    duration_max_ms: Math.round(draft.durationMaxSeconds * 1000),
    subtitle_font_size: draft.subtitleFontSize,
    keyword_color: draft.keywordColor.toUpperCase(),
    keyword_scale: draft.keywordScale,
    top_title_enabled: false,
    subtitle_font: draft.subtitleFont ?? 'heiti',
    subtitle_effect: draft.subtitleEffect ?? 'none',
  };
}

async function responseJson<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;
  let message = `本机服务返回 ${response.status}`;
  try {
    const payload = (await response.json()) as { detail?: string | readonly { msg?: string }[] };
    if (typeof payload.detail === "string") message = payload.detail;
    else if (Array.isArray(payload.detail)) message = payload.detail.map(item => item.msg).filter(Boolean).join("；") || message;
  } catch {
    // Keep the stable fallback for non-JSON failures.
  }
  throw new Error(message);
}

export async function fetchAutoEditProjects(signal?: AbortSignal, deleted = false): Promise<AutoEditProject[]> {
  const result = await responseJson<{ projects: AutoEditProject[] }>(await fetch(`${API_BASE_URL}/s7/auto-edit-projects?deleted=${deleted}`, { signal }));
  return result.projects;
}

export async function trashAutoEditProject(project: AutoEditProject, restore = false): Promise<AutoEditProject> {
  return responseJson(await fetch(`${API_BASE_URL}/s7/auto-edit-projects/${project.project_id}/trash?restore=${restore}`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({expected_revision:project.revision}),
  }));
}

export function appendAutoEditFiles(current: File[], added: File[]): File[] {
  const result = [...current];
  for (const file of added) {
    if (!result.some(item => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified)) result.push(file);
  }
  if (result.length > 20) throw new Error('每个项目最多上传 20 条录播视频，本次追加未保存。');
  return result;
}

export async function createAutoEditProject(files: File | File[], draft: AutoEditDraft): Promise<AutoEditProject> {
  const problem = validateAutoEditDraft(draft);
  if (problem) throw new Error(problem);
  const body = new FormData();
  body.append("title", draft.title.trim());
  body.append("settings_json", JSON.stringify(draftSettings(draft)));
  const sources = Array.isArray(files) ? files : [files];
  if (!sources.length || sources.length > 20) throw new Error('请选择 1—20 条录播视频。');
  for (const file of sources) body.append("sources", file);
  return responseJson(await fetch(`${API_BASE_URL}/s7/auto-edit-projects`, { method: "POST", body }));
}

export async function startAutoEditProject(project: AutoEditProject): Promise<AutoEditProject> {
  return responseJson(await fetch(`${API_BASE_URL}/s7/auto-edit-projects/${project.project_id}/start`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: project.revision }),
  }));
}

export async function retryAutoEditProject(project: AutoEditProject): Promise<AutoEditProject> {
  return responseJson(await fetch(`${API_BASE_URL}/s7/auto-edit-projects/${project.project_id}/retry`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: project.revision }),
  }));
}

export async function cancelAutoEditProject(project: AutoEditProject): Promise<AutoEditProject> {
  return responseJson(await fetch(`${API_BASE_URL}/s7/auto-edit-projects/${project.project_id}/cancel`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: project.revision }),
  }));
}
