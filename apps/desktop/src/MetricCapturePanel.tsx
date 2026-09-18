import { FileDropInput } from "./FileDropInput";
import { useState, type FormEvent } from "react";
import type { MetricImportCandidate, MetricImportDraft, Publication, PublicationMetrics } from "@content-factory/contracts";

import {
  clearDraftRejectNote,
  draftRejectNote,
  parseMetricInput,
  resetAfterSuccessfulSubmission,
  toIsoTime,
  updateDraftRejectNote,
  type DraftRejectNotes,
  type MetricImportCandidateOverride,
} from "./publishing";

const localNow = () => {
  const date = new Date(); date.setMinutes(date.getMinutes() - date.getTimezoneOffset()); return date.toISOString().slice(0, 16);
};
const fields: readonly [keyof PublicationMetrics, string, string][] = [
  ["views", "播放量", "12800"], ["followers_gained", "增粉", "86"], ["likes", "点赞", "920"],
  ["comments", "评论", "74"], ["favorites", "收藏", "188"], ["shares", "分享", "96"],
  ["product_clicks", "商品点击", "540"], ["orders", "订单", "31"], ["gmv_cents", "成交额（元）", "9280"],
  ["retention_3s", "3 秒留存（%）", "64"], ["completion_rate", "完播率（%）", "31"],
  ["product_ctr", "商品点击率（%）", "4.22"], ["conversion_rate", "转化率（%）", "5.74"],
];

type CandidateEdit = { capturedAt?: string; metrics?: Record<string, string> };

function localDateTimeValue(isoValue: string): string {
  const date = new Date(isoValue);
  if (Number.isNaN(date.getTime())) return "";
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
}

function editableMetricValue(field: string, value: number): string {
  if (["retention_3s", "completion_rate", "product_ctr", "conversion_rate"].includes(field)) return String(value * 100);
  if (field === "gmv_cents") return String(value / 100);
  return String(value);
}

export function MetricCapturePanel({ publication, publications, imports, ocrReady, busy, addSnapshot, uploadCsv, uploadScreenshot, confirmImport, rejectImport }: {
  publication: Publication | null; publications: readonly Publication[]; imports: readonly MetricImportDraft[]; ocrReady: boolean; busy: boolean;
  addSnapshot: (id: string, input: { captured_at: string; confirmed_by: string; metrics: PublicationMetrics; confidence?: "confirmed" | "estimated"; is_correction?: boolean; correction_reason?: string | null }) => Promise<unknown>;
  uploadCsv: (file: File, actor: string) => Promise<unknown>;
  uploadScreenshot: (file: File, publicationId: string, capturedAt: string, actor: string) => Promise<unknown>;
  confirmImport: (id: string, actor: string, candidates?: readonly MetricImportCandidateOverride[]) => Promise<unknown>;
  rejectImport: (id: string, actor: string, note: string) => Promise<unknown>;
}) {
  const actor = "本机";
  const [capturedAt, setCapturedAt] = useState(localNow);
  const [values, setValues] = useState<Record<string, string>>({});
  const [correction, setCorrection] = useState(false);
  const [correctionReason, setCorrectionReason] = useState("");
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [screenFile, setScreenFile] = useState<File | null>(null);
  const [rejectNotes, setRejectNotes] = useState<DraftRejectNotes>({});
  const [candidateEdits, setCandidateEdits] = useState<Record<string, CandidateEdit>>({});
  const [localError, setLocalError] = useState<string | null>(null);
  const pending = imports.filter((item) => item.status === "pending");

  const submitSnapshot = async (event: FormEvent) => {
    event.preventDefault(); setLocalError(null);
    if (!publication || !actor.trim()) return;
    try {
      const metrics: Record<string, number> = {};
      fields.forEach(([field]) => { const value = parseMetricInput(field, values[field] ?? ""); if (value !== undefined) metrics[field] = value; });
      if (!Object.keys(metrics).length) throw new Error("至少填写一个指标");
      const result = await addSnapshot(publication.publication_id, {
        captured_at: toIsoTime(capturedAt), confirmed_by: actor.trim(), metrics,
        is_correction: correction, correction_reason: correction ? correctionReason.trim() : null,
      });
      resetAfterSuccessfulSubmission(result, () => {
        setValues({}); setCorrection(false); setCorrectionReason("");
      });
    } catch (error) { setLocalError(error instanceof Error ? error.message : "指标格式不正确"); }
  };
  const csvSubmit = async (event: FormEvent) => { event.preventDefault(); if (csvFile && actor.trim()) await uploadCsv(csvFile, actor.trim()); };
  const screenshotSubmit = async (event: FormEvent) => {
    event.preventDefault(); if (screenFile && publication && actor.trim()) await uploadScreenshot(screenFile, publication.publication_id, toIsoTime(capturedAt), actor.trim());
  };
  const candidateValue = (candidate: MetricImportCandidate, field: string, value: number): string =>
    candidateEdits[candidate.candidate_id]?.metrics?.[field] ?? editableMetricValue(field, value);
  const candidateCapturedAt = (candidate: MetricImportCandidate): string =>
    candidateEdits[candidate.candidate_id]?.capturedAt ?? localDateTimeValue(candidate.captured_at);
  const updateCandidateMetric = (candidateId: string, field: string, value: string) => setCandidateEdits((current) => ({
    ...current,
    [candidateId]: {
      ...current[candidateId],
      metrics: { ...current[candidateId]?.metrics, [field]: value },
    },
  }));
  const updateCandidateTime = (candidateId: string, value: string) => setCandidateEdits((current) => ({
    ...current,
    [candidateId]: { ...current[candidateId], capturedAt: value },
  }));
  const confirmDraft = async (draft: MetricImportDraft) => {
    setLocalError(null);
    try {
      const overrides: MetricImportCandidateOverride[] = draft.candidates.map((candidate) => {
        const metrics: Record<string, number> = {};
        Object.entries(candidate.metrics).forEach(([field, value]) => {
          const parsed = parseMetricInput(
            field as keyof PublicationMetrics,
            candidateValue(candidate, field, value),
          );
          if (parsed !== undefined) metrics[field] = parsed;
        });
        return {
          candidate_id: candidate.candidate_id,
          captured_at: toIsoTime(candidateCapturedAt(candidate)),
          confidence: "confirmed",
          metrics,
        };
      });
      const result = await confirmImport(draft.draft_id, actor.trim(), overrides);
      if (result !== null && result !== undefined) {
        setCandidateEdits((current) => {
          const next = { ...current };
          draft.candidates.forEach((candidate) => { delete next[candidate.candidate_id]; });
          return next;
        });
      }
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : "请核对候选指标格式");
    }
  };
  const rejectDraft = async (draft: MetricImportDraft) => {
    const note = draftRejectNote(rejectNotes, draft.draft_id).trim();
    if (!actor.trim() || !note) return;
    const result = await rejectImport(draft.draft_id, actor.trim(), note);
    resetAfterSuccessfulSubmission(result, () => {
      setRejectNotes((current) => clearDraftRejectNote(current, draft.draft_id));
    });
  };

  return <section className="metric-capture" aria-labelledby="metric-capture-title">
    <header><div><h2 id="metric-capture-title">保存指标快照</h2><p>{publication ? `当前作品：${publication.title}` : "先从上方选择一条已登记作品。"}</p></div></header>
    {localError && <div className="feedback-error" role="alert">{localError}</div>}
    <div className="metric-capture-grid">
      <form className="manual-metric-form" onSubmit={(event) => void submitSnapshot(event)}>
        <h3>人工填写</h3><label>采集时间<input type="datetime-local" value={capturedAt} onChange={(event) => setCapturedAt(event.target.value)} /></label>
        <div className="metric-field-grid">{fields.map(([field, label, placeholder]) => <label key={field}>{label}<input inputMode="decimal" value={values[field] ?? ""} onChange={(event) => setValues((current) => ({ ...current, [field]: event.target.value }))} placeholder={placeholder} /></label>)}</div>
        <label className="feedback-check"><input type="checkbox" checked={correction} onChange={(event) => setCorrection(event.target.checked)} />这是对平台口径或历史数字的修正</label>
        {correction && <label>修正原因<input value={correctionReason} onChange={(event) => setCorrectionReason(event.target.value)} placeholder="必须说明为什么数字回退或变化" /></label>}
        <button className="primary-button" type="submit" disabled={busy || !publication || !actor.trim() || (correction && !correctionReason.trim())}>保存快照</button>
      </form>
      <div className="metric-imports">
        <form onSubmit={(event) => void csvSubmit(event)}><h3>CSV 批量导入</h3><p>整批先变成草稿；任一行错误，整批都不会写入正式数据。</p><FileDropInput aria-label="选择指标 CSV" type="file" accept=".csv,text/csv" onChange={(event) => setCsvFile(event.target.files?.[0] ?? null)} /><button className="secondary-button" type="submit" disabled={busy || !csvFile || !actor.trim()}>解析 CSV</button></form>
        <form onSubmit={(event) => void screenshotSubmit(event)}><h3>截图 OCR</h3><p>{ocrReady ? "本地识别数字，结果必须由你确认。" : "本地 OCR 当前不可用，可改用 CSV 或人工填写。"}</p><FileDropInput aria-label="选择数据截图" type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => setScreenFile(event.target.files?.[0] ?? null)} /><button className="secondary-button" type="submit" disabled={busy || !ocrReady || !screenFile || !publication || !actor.trim()}>识别截图</button></form>
      </div>
    </div>
    <div className="import-review"><header><h3>待确认导入</h3><span>{pending.length} 批</span></header>{pending.length === 0 ? <div className="feedback-empty">没有等待确认的导入。</div> : <div className="import-draft-list">{pending.map((draft) => <article key={draft.draft_id}><div><strong>{draft.source_name}</strong><span>{draft.import_kind === "ocr" ? "截图 OCR" : "CSV"} · {draft.candidates.length} 条候选</span></div>{draft.candidates.map((candidate) => <section className="import-candidate-editor" key={candidate.candidate_id} aria-label="导入候选核对"><header><div><strong>{publications.find((item) => item.publication_id === candidate.publication_id)?.title ?? candidate.publication_id}</strong><small>原始置信度：{candidate.confidence === "unconfirmed" ? "待核对" : candidate.confidence === "estimated" ? "估算" : "已确认"}</small></div><label>采集时间<input type="datetime-local" value={candidateCapturedAt(candidate)} onChange={(event) => updateCandidateTime(candidate.candidate_id, event.target.value)} /></label></header><div className="import-candidate-fields">{Object.entries(candidate.metrics).map(([field, value]) => <label key={field}>{fields.find(([key]) => key === field)?.[1] ?? field}<input inputMode="decimal" value={candidateValue(candidate, field, value)} onChange={(event) => updateCandidateMetric(candidate.candidate_id, field, event.target.value)} /><small>识别置信度 {Math.round((candidate.field_confidence[field] ?? 0) * 100)}%</small></label>)}</div></section>)}<div className="import-actions"><button className="text-button danger-text" type="button" disabled={busy || !actor.trim() || !draftRejectNote(rejectNotes, draft.draft_id).trim()} onClick={() => void rejectDraft(draft)}>拒绝</button><input aria-label={`${draft.source_name} 拒绝原因`} value={draftRejectNote(rejectNotes, draft.draft_id)} onChange={(event) => setRejectNotes((current) => updateDraftRejectNote(current, draft.draft_id, event.target.value))} placeholder="拒绝时填写原因" /><button className="primary-button" type="button" disabled={busy || !actor.trim()} onClick={() => void confirmDraft(draft)}>核对并写入快照</button></div></article>)}</div>}</div>
  </section>;
}
