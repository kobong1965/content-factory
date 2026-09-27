import { runtimeApiBaseUrl } from './runtimeApi';
const API_BASE_URL = runtimeApiBaseUrl;
const MAX_CLIPS_PER_CANDIDATE = 30;

export type AutoEditStatus = "draft" | "queued" | "analyzing" | "planning" | "render_pending" | "rendering" | "review" | "completed" | "failed" | "cancelled" | "purging" | "purge_failed" | "purged";

export type AutoEditSettings = Readonly<{
  target_count: number;
  duration_min_ms: number;
  duration_max_ms: number;
  duration_policy?: 'custom' | 'bounded_15_30';
  subtitle_font_size: number;
  keyword_color: string;
  keyword_scale: number;
  top_title_enabled: boolean;
  subtitle_font?: 'heiti' | 'yahei' | 'songti' | 'kaiti';
  subtitle_effect?: 'none' | 'fade' | 'pop';
  subtitle_mode?: 'sentence' | 'reveal' | 'highlight' | 'auto' | 'none';
}>;

export type AutoEditPlanItem = Readonly<{
  source_id?: string;
  candidate_id: string;
  title: string;
  sku?: string;
  duration_ms: number;
  selection_reason?: string;
  skill_id?: string;
  skill_snapshot?: Readonly<{ skill_id: string; revision: number; name: string; mechanism?: string }>;
  clips: readonly Readonly<{ start_ms: number; end_ms: number }>[];
}>;

export type AutoEditProject = Readonly<{
  project_id: string;
  title: string;
  sku?: string;
  status: AutoEditStatus;
  revision: number;
  progress: number;
  error: string | null;
  output_batch_id: string | null;
  registration_checkpoint?: Readonly<{ manifest_path: string; manifest_sha256: string }> | null;
  generation_check?: Readonly<{ ready: boolean; blockers: readonly string[] }>;
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
  sku?: string;
  targetCount: number;
  durationMinSeconds: number;
  durationMaxSeconds: number;
  durationPolicy?: AutoEditSettings['duration_policy'];
  subtitleFontSize: number;
  keywordColor: string;
  keywordScale: number;
  subtitleFont?: AutoEditSettings['subtitle_font'];
  subtitleEffect?: AutoEditSettings['subtitle_effect'];
  subtitleMode?: AutoEditSettings['subtitle_mode'];
}>;

export function draftFromProject(project: Pick<AutoEditProject, 'title' | 'sku' | 'settings'>): AutoEditDraft {
  return {
    title: project.title,
    sku: project.sku ?? '',
    targetCount: project.settings.target_count,
    durationMinSeconds: project.settings.duration_min_ms / 1000,
    durationMaxSeconds: project.settings.duration_max_ms / 1000,
    durationPolicy: project.settings.duration_policy ?? 'custom',
    subtitleFontSize: project.settings.subtitle_font_size,
    keywordColor: project.settings.keyword_color.toUpperCase(),
    keywordScale: project.settings.keyword_scale,
    subtitleFont: project.settings.subtitle_font ?? 'heiti',
    subtitleEffect: project.settings.subtitle_effect ?? 'none',
    subtitleMode: project.settings.subtitle_mode ?? 'reveal',
  };
}

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
  purging: '正在永久删除',
  purge_failed: '删除未完成，可重试',
  purged: '已永久删除',
};

export function autoEditStatusLabel(status: AutoEditStatus): string {
  return labels[status];
}

export function canRetryRegistration(project: Pick<AutoEditProject, 'status' | 'registration_checkpoint'>): boolean {
  return project.status === 'failed' && Boolean(project.registration_checkpoint);
}

export function generationBlockers(project: AutoEditProject): readonly string[] {
  if (canRetryRegistration(project)) return [];
  if (project.generation_check) return project.generation_check.blockers;
  // Saved drafts/older API responses may not yet include the server file check.
  const sources = project.sources?.length ? project.sources : [project.source];
  const longest = Math.max(0, ...sources.map(source => source.duration_ms));
  const minimum = project.settings.duration_min_ms;
  return longest * MAX_CLIPS_PER_CANDIDATE < minimum
    ? [`每条成片最多 ${MAX_CLIPS_PER_CANDIDATE} 段；最长原片 ${(longest / 1000).toFixed(3)} 秒重复使用后，仍不足最短成片 ${(minimum / 1000).toFixed(3)} 秒。请降低最短秒数或上传更长的素材。`]
    : [];
}

export function validateAutoEditDraft(draft: AutoEditDraft): string | null {
  if (![draft.durationMinSeconds, draft.durationMaxSeconds, draft.subtitleFontSize, draft.keywordScale].every(Number.isFinite)) return "请填写有效的数字。";
  if (!draft.title.trim()) return "请填写项目名称。";
  if (!Number.isInteger(draft.targetCount) || draft.targetCount < 1 || draft.targetCount > 20) return "成片数量必须为 1—20 条。";
  if (draft.durationMinSeconds < 5 || draft.durationMaxSeconds > 180 || draft.durationMinSeconds > draft.durationMaxSeconds) return "成片时长应为 5—180 秒，且最小时长不能大于最大时长。建议使用 15—30 秒。";
  if (draft.durationPolicy === 'bounded_15_30' && (draft.durationMinSeconds < 15 || draft.durationMaxSeconds > 30)) return "完整片段模式的成片硬边界必须为 15—30 秒。";
  if (draft.subtitleFontSize < 32 || draft.subtitleFontSize > 120) return "字幕字号必须为 32—120。";
  if (!/^#[0-9a-f]{6}$/i.test(draft.keywordColor)) return "重点词颜色格式不正确。";
  if (draft.keywordScale < 1 || draft.keywordScale > 2) return "重点词字号倍率必须为 1.0—2.0。";
  return null;
}

export function selectDurationPolicy(draft: AutoEditDraft, policy: NonNullable<AutoEditSettings['duration_policy']>): AutoEditDraft {
  return policy === 'bounded_15_30'
    ? { ...draft, durationPolicy: policy, durationMinSeconds: 15, durationMaxSeconds: 30 }
    : { ...draft, durationPolicy: policy };
}

export function draftSettings(draft: AutoEditDraft): AutoEditSettings {
  return {
    target_count: draft.targetCount,
    duration_min_ms: Math.round(draft.durationMinSeconds * 1000),
    duration_max_ms: Math.round(draft.durationMaxSeconds * 1000),
    duration_policy: draft.durationPolicy ?? 'custom',
    subtitle_font_size: draft.subtitleFontSize,
    keyword_color: draft.keywordColor.toUpperCase(),
    keyword_scale: draft.keywordScale,
    top_title_enabled: false,
    subtitle_font: draft.subtitleFont ?? 'heiti',
    subtitle_effect: draft.subtitleEffect ?? 'none',
    subtitle_mode: draft.subtitleMode ?? 'sentence',
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

export type PurgePreview = { project_id:string; revision:number; confirmation_token:string; files:{path:string;file_name:string;action:'delete'|'preserve'|'missing';reason:string;size_bytes:number}[];delete_count:number;delete_bytes:number;preserved_count:number;warning:string };
export async function previewAutoEditPurge(project: AutoEditProject): Promise<PurgePreview> {
  return responseJson(await fetch(`${API_BASE_URL}/s7/auto-edit-projects/${project.project_id}/purge-preview?expected_revision=${project.revision}`));
}
export async function purgeAutoEditProject(project: AutoEditProject, confirmation_token: string): Promise<{ project_id: string; purged: boolean; revision:number; removed: string[]; skipped: unknown[]; errors: unknown[] }> {
  return responseJson(await fetch(`${API_BASE_URL}/s7/auto-edit-projects/${project.project_id}/purge`, {
    method: 'DELETE', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({expected_revision:project.revision, confirmation_token}),
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
  if (draft.sku?.trim()) body.append("sku", draft.sku.trim());
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

export async function updateAutoEditProject(project: AutoEditProject, draft: AutoEditDraft): Promise<AutoEditProject> {
  const problem = validateAutoEditDraft(draft);
  if (problem) throw new Error(problem);
  return responseJson(await fetch(`${API_BASE_URL}/s7/auto-edit-projects/${project.project_id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      expected_revision: project.revision,
      title: draft.title.trim(),
      settings: draftSettings(draft),
    }),
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
