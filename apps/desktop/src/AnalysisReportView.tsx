import { useEffect, useMemo, useState, type FormEvent } from "react";
import { analysisReportMethodLabel } from "./AnalysisMethodNotice";

import { analysisProcessingPurpose, analysisPurposeCopy, isAnalysisConclusionDirty, keyframeUrl } from "./analysis";
import { gatewayModeLabel } from "./gatewayModelSettings";
import { useUnsavedChanges } from "./unsavedChanges";
import type { useS3Analysis } from "./useS3Analysis";

const dimensionLabels: Record<string, string> = {
  hook_strength: "钩子强度", proof_strength: "证明力度", rhythm_efficiency: "节奏效率",
  product_fit: "商品适配", consumer_psychology: "消费心理", interaction_design: "互动设计",
  data_result: "数据结果",
};

function timecode(ms: number | null): string {
  if (ms === null) return "非时间证据";
  const seconds = ms / 1000;
  return `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;
}

const reportStatus = { draft: "AI 草稿 · 待人工审核", reviewed: "已人工复核", accepted: "已人工接受" } as const;

export function AnalysisReportView({ analysis, onDirtyChange }: {
  analysis: ReturnType<typeof useS3Analysis>;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const report = analysis.report;
  const [conclusion, setConclusion] = useState("");

  useEffect(() => setConclusion(report?.summary.overall_conclusion.text ?? ""), [report]);
  const conclusionDirty = Boolean(report && isAnalysisConclusionDirty(report.summary.overall_conclusion.text, conclusion));
  useUnsavedChanges(conclusionDirty, "深度分析结论");
  useEffect(() => { onDirtyChange?.(conclusionDirty); }, [conclusionDirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);
  const evidenceById = useMemo(() => new Map(report?.evidence.map((item) => [item.id, item]) ?? []), [report]);

  if (!report || !analysis.selectedTaskId) {
    return <div className="empty-state report-empty" role="status"><span className="empty-symbol" aria-hidden="true">空</span><h3>还没有可查看的分析报告</h3><p>选择已完成任务，或从一个 S2 视频创建深度分析。</p></div>;
  }

  function submitConclusion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (conclusion.trim()) void analysis.saveConclusion(conclusion.trim());
  }

  const firstFrame = report.keyframes[0];
  const processingPurpose = analysisProcessingPurpose(report.processing);
  const processingCopy = analysisPurposeCopy[processingPurpose];
  const processingMode = report.processing.api_mode === "fixture"
    ? "本地验收数据"
    : report.processing.api_mode === "external_review"
    ? "外部整理导入（非网关调用）"
    : gatewayModeLabel(report.processing.api_mode);
  return <section className="analysis-report" aria-labelledby="report-title">
    <div className="report-toolbar">
      <div>
        <h2 id="report-title">{processingPurpose === "video_review" ? "可追溯 AI 视频审核" : "可追溯爆点研究"}</h2>
        <p className="report-processing-meta">
          <strong>任务类型：{processingCopy.label}</strong>
          <span>修订：{report.revision}</span>
          <span>模型：{report.processing.model}</span>
          <span>分析方法：{analysisReportMethodLabel(processingPurpose, report.processing.prompt_version)}</span>
          <span>接口：{processingMode}</span>
          <span>{report.processing.upload_summary.keyframe_count} 张关键帧 · 原视频未上传</span>
        </p>
      </div>
      <span className={`report-status report-status-${report.status}`}>{report.processing.api_mode === "external_review" && report.status === "accepted" ? "已授权导入 · 以复核说明为准" : reportStatus[report.status]}</span>
    </div>

    <div className="report-overview">
      <article className="frame-stage">
        {firstFrame ? <img src={keyframeUrl(analysis.selectedTaskId, firstFrame.id)} alt="视频第一镜头关键帧" /> : <div className="frame-placeholder">无关键帧</div>}
        <div><span>{Math.round(report.duration_ms / 1000)} 秒 · {report.shots.length} 个镜头</span><strong>{report.summary.video_type.replaceAll("_", " ")}</strong></div>
      </article>
      <article className="conclusion-card">
        <h3 className="conclusion-heading">总体判断</h3>
        <form onSubmit={submitConclusion}>
          <label htmlFor="overall-conclusion">人工可修订，证据引用保持不变</label>
          <textarea id="overall-conclusion" value={conclusion} onChange={(event) => setConclusion(event.target.value)} />
          <div className="claim-meta"><span>置信度 {Math.round(report.summary.overall_conclusion.confidence * 100)}%</span><span>{report.summary.overall_conclusion.is_inference ? "模型推断" : "直接事实"}</span><span>{report.summary.overall_conclusion.evidence_ids.length} 条证据</span></div>
          <button className="secondary-button" type="submit" disabled={analysis.isSubmitting || !conclusionDirty}>保存人工修订</button>
        </form>
      </article>
    </div>

    <div className="score-strip" aria-label="七维爆点评分">
      {report.scores.items.map((item) => <article key={item.dimension}><span>{dimensionLabels[item.dimension] ?? item.dimension}</span><strong>{Math.round(item.score)}</strong><small>{item.evidence_ids.length} 条证据</small></article>)}
      <article className="score-total"><span>综合分</span><strong>{Math.round(report.scores.total_score)}</strong><small>加权结果</small></article>
    </div>

    <div className="analysis-columns analysis-columns-single">
      <section className="analysis-panel" aria-labelledby="shots-title">
        <div className="panel-heading"><h3 id="shots-title">逐镜头与逐秒</h3><span>{report.shots.length} 个镜头</span></div>
        <div className="shot-stack">{report.shots.map((shot, index) => {
          const frame = report.keyframes.find((item) => item.shot_id === shot.id);
          const timeline = report.timeline.filter((item) => item.start_ms < shot.end_ms && item.end_ms > shot.start_ms);
          return <article className="shot-card" key={shot.id}>
            <div className="shot-preview">{frame && <img src={keyframeUrl(analysis.selectedTaskId!, frame.id)} alt={`镜头 ${index + 1} 关键帧`} />}</div>
            <div className="shot-copy"><div><strong>镜头 {index + 1}</strong><span>{timecode(shot.start_ms)} — {timecode(shot.end_ms)}</span></div><p>{shot.composition}</p><small>{shot.performer_action} · {shot.product_exposure}</small>{timeline[0] && <blockquote>{timeline[0].visual_event}{timeline[0].subtitle ? ` · 字幕：${timeline[0].subtitle}` : ""}</blockquote>}</div>
          </article>;
        })}</div>
      </section>

    </div>

    <section className="pattern-section" aria-labelledby="patterns-title">
      <div className="panel-heading"><h3 id="patterns-title">爆点 Skill 候选</h3><span>接受报告后进入候选库，还需单独批准</span></div>
      <p className="pattern-causality-note">这里说明内容中哪个时间点、哪句话与哪个动作可能形成停留或转化信号；没有平台内部数据时，只标记为有证据的内容推断，不宣称“算法一定因此放量”。</p>
      <div className="pattern-grid">{report.pattern_candidates.map((pattern) => <article key={pattern.id}><h4>{pattern.name}</h4><p>{pattern.mechanism}</p><ol>{pattern.steps.map((step) => {
        const references = step.evidence_ids.map((id) => evidenceById.get(id)).filter((item) => item !== undefined);
        return <li key={step.id}>{step.description}<small>{references.length ? references.map((item) => `${timecode(item.start_ms)} · ${item.claim}`).join("；") : "证据引用待补"}</small></li>;
      })}</ol><div><strong>失败信号</strong><span>{pattern.failure_signals.join("；")}</span></div></article>)}</div>
    </section>

  </section>;
}
