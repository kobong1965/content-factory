import { runtimeApiBaseUrl } from './runtimeApi';
const BASE = `${runtimeApiBaseUrl}/s7/footage-batches`;
export type ReviewStatus = 'pending' | 'approved' | 'changes_requested';
export type FootageCandidate = {
  id: string; title: string; hook: string; source_path: string; duration_ms: number;
  clips: { start_ms: number; end_ms: number }[];
  benchmark_refs: string[]; review_notes: string[]; review_status: ReviewStatus;
  review_note: string; reviewed_by: string;
};
export type FootageBatch = { id: string; title: string; sku?: string; analysis_summary: string; revision: number; candidates: FootageCandidate[] };
export type FinishedLibraryData = { total: number; groups: { sku: string; count: number; items: { batch_id: string; batch_title: string; revision: number; video_url?: string; subtitle_url?: string; candidate: FootageCandidate }[] }[]; unavailable: { title: string; reason: string }[] };
export const fetchFinishedLibrary = (signal?: AbortSignal) => request<FinishedLibraryData>('/library', { signal });
export const saveBatchProduct = (batch: FootageBatch, sku: string) => request<FootageBatch>(`/${encodeURIComponent(batch.id)}/product`, {
  method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: batch.revision, sku }),
});
export const reviewLabels: Record<ReviewStatus, string> = { pending: '待审核', approved: '审核通过', changes_requested: '需要修改' };
export const batchMediaUrl = (batch: string, candidate: string, kind: 'video' | 'source' | 'subtitle' | 'cover', download = false) =>
  `${BASE}/${encodeURIComponent(batch)}/candidates/${encodeURIComponent(candidate)}/media/${kind}${download ? '?download=true' : ''}`;
async function request<T>(suffix: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${suffix}`, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    throw new Error(typeof body?.detail === 'string' ? body.detail : `批次操作失败（${response.status}），请检查输入后重试。`);
  }
  return response.json() as Promise<T>;
}
export const fetchFootageBatches = (signal?: AbortSignal) => request<{ batches: FootageBatch[] }>('', { signal });
export const importFootageBatch = (manifest_path: string) => request<FootageBatch>('/import', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ manifest_path }),
});
export const saveFootageReview = (batch: FootageBatch, candidate: FootageCandidate, status: ReviewStatus, note: string, reviewed_by: string) =>
  request<FootageBatch>(`/${encodeURIComponent(batch.id)}/candidates/${encodeURIComponent(candidate.id)}/review`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: batch.revision, status, note, reviewed_by }),
  });
export function sourceAtTime(clips: FootageCandidate['clips'], seconds: number): number {
  let cursor = Math.max(0, seconds * 1000);
  for (const clip of clips) {
    const duration = clip.end_ms - clip.start_ms;
    if (cursor < duration) return (clip.start_ms + cursor) / 1000;
    cursor -= duration;
  }
  return (clips.at(-1)?.end_ms ?? 0) / 1000;
}
