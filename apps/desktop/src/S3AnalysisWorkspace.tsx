import { useEffect, useMemo, useState, type FormEvent } from "react";
import type { AnalysisTask, Evidence, MediaTask } from "@content-factory/contracts";

import {
  analysisPurposeCopy,
  analysisStatusLabel,
  analysisStepLabel,
  keyframeUrl,
  type AnalysisModelPurpose,
} from "./analysis";
import { AnalysisReportView } from "./AnalysisReportView";
import { AnalysisMethodNotice } from "./AnalysisMethodNotice";
import { ConfirmationDialog } from "./ConfirmationDialog";
import { ModalDialog } from "./ModalDialog";
import { ViralSkillLibrary } from "./ViralSkillLibrary";
import { WorkspaceHeader } from "./WorkspaceHeader";
import { guardUnsavedTransition } from "./unsavedChanges";
import type { useS3Analysis } from "./useS3Analysis";

type AnalysisController = ReturnType<typeof useS3Analysis>;
type QueueFilter = "all" | "active" | "review" | "failed";

function numberOrUndefined(value: string): number | undefined {
  if (!value.trim()) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function dateTimeLabel(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function retryWaitLabel(value: string | null | undefined): string {
  if (!value) return "—";
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return value;
  const remainingSeconds = Math.max(0, Math.ceil((timestamp - Date.now()) / 1000));
  return remainingSeconds > 0 ? `${remainingSeconds} 秒后 · ${dateTimeLabel(value)}` : `即将重试 · ${dateTimeLabel(value)}`;
}

function segmentProgress(task: AnalysisTask): string {
  if (!task.segment_total) return ["succeeded", "completed", "failed"].includes(task.status) ? "旧版单次任务" : "等待分段";
  return `${task.segment_completed ?? 0} / ${task.segment_total} 段`;
}

function evidenceTimecode(ms: number | null): string {
  if (ms === null) return "全片证据";
  const seconds = ms / 1000;
  return `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;
}

function taskMatchesFilter(task: AnalysisTask, filter: QueueFilter): boolean {
  if (filter === "active") return ["pending", "running", "retry_wait"].includes(task.status);
  if (filter === "review") return ["succeeded", "completed"].includes(task.status);
  if (filter === "failed") return ["failed", "cancelled"].includes(task.status);
  return true;
}

function TaskQueue({ analysis, mediaById }: { analysis: AnalysisController; mediaById: ReadonlyMap<string, MediaTask> }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<QueueFilter>("all");
  const visibleTasks = useMemo(() => analysis.tasks.filter((task) => {
    if (!taskMatchesFilter(task, filter)) return false;
    const sourceName = mediaById.get(task.media_task_id)?.source_name ?? "";
    return `${sourceName} ${task.task_id}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
  }), [analysis.tasks, filter, mediaById, query]);

  return <aside className="analysis-task-panel analysis-queue-rail workbench-surface" aria-labelledby="analysis-queue-title">
    <div className="panel-heading">
      <h2 id="analysis-queue-title">任务队列</h2>
      <div className="queue-heading-meta"><span className="queue-scroll-hint">横向滚动查看更多</span><span>{visibleTasks.length} / {analysis.tasks.length}</span></div>
    </div>
    <div className="queue-tools">
      <label className="visually-hidden" htmlFor="analysis-task-search">搜索分析任务</label>
      <input id="analysis-task-search" type="search" placeholder="搜索视频或任务 ID" value={query} onChange={(event) => setQuery(event.target.value)} />
      <label className="visually-hidden" htmlFor="analysis-task-filter">筛选任务状态</label>
      <select id="analysis-task-filter" value={filter} onChange={(event) => setFilter(event.target.value as QueueFilter)}>
        <option value="all">全部状态</option>
        <option value="active">运行中</option>
        <option value="review">待复核</option>
        <option value="failed">异常与取消</option>
      </select>
    </div>
    {visibleTasks.length === 0 ? <div className="mini-empty">没有符合当前筛选条件的任务。</div> : <ul>{visibleTasks.map((task) => <li key={task.task_id}>
      <button
        type="button"
        className={`analysis-task-select${task.task_id === analysis.selectedTaskId ? " analysis-task-active" : ""}`}
        aria-current={task.task_id === analysis.selectedTaskId ? "true" : undefined}
        onClick={() => {
          if (task.task_id === analysis.selectedTaskId) return;
          guardUnsavedTransition(() => analysis.setSelectedTaskId(task.task_id));
        }}
      >
        <span className="analysis-task-primary">
          <strong>{mediaById.get(task.media_task_id)?.source_name ?? analysisPurposeCopy[task.model_purpose ?? "analysis"].shortLabel}</strong>
          <small>{analysisStatusLabel(task.status)} · {analysisStepLabel(task.current_step)}</small>
        </span>
        <b>{task.progress}%</b>
        <span className="analysis-segment-count">{segmentProgress(task)}</span>
        <i aria-hidden="true"><span style={{ width: `${task.progress}%` }} /></i>
      </button>
      {task.error && <small className="analysis-task-error">{task.error}</small>}
    </li>)}</ul>}
  </aside>;
}

function TaskRuntimeDetails({ analysis, task }: { analysis: AnalysisController; task: AnalysisTask | undefined }) {
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const isActive = task && ["pending", "running", "retry_wait"].includes(task.status);
  if (!task) return <div className="mini-empty">选择任务后可查看分段、检查点和恢复状态。</div>;

  return <div className="runtime-details">
    <div className={`task-health task-health-${task.status}`} role="status">
      <strong>{analysisStatusLabel(task.status)}</strong>
      <span>{analysisStepLabel(task.current_step)}</span>
      {task.recovering && <b>正在从本地检查点恢复</b>}
    </div>
    <dl className="task-diagnostics">
      <div><dt>总进度</dt><dd>{task.progress}%</dd></div>
      <div><dt>分析分段</dt><dd>{segmentProgress(task)}</dd></div>
      <div><dt>当前分段</dt><dd>{task.current_segment == null ? "—" : `第 ${task.current_segment + 1} 段`}</dd></div>
      <div><dt>已完成至</dt><dd>{task.last_completed_segment == null ? "—" : `第 ${task.last_completed_segment + 1} 段`}</dd></div>
      <div><dt>最近检查点</dt><dd>{dateTimeLabel(task.last_checkpoint_at)}</dd></div>
      <div><dt>下次重试</dt><dd>{retryWaitLabel(task.next_retry_at)}</dd></div>
      <div><dt>累计自动重试</dt><dd>{task.retry_count ?? Math.max(0, task.attempt_count - 1)} 次</dd></div>
    </dl>
    {task.status === "retry_wait" && <p className="inspector-notice">暂时性故障会按服务端建议或指数退避自动继续，已成功分段不会重做。</p>}
    {["succeeded", "completed"].includes(task.status) && <p className="inspector-notice inspector-notice-success">{task.segment_total ? "全部分段和最终汇总已完整落盘。" : "这是升级前完成的单次分析报告。"}</p>}
    {task.status === "cancelled" && <p className="inspector-notice">任务已停止；成功分段、检查点和诊断记录仍保留。</p>}
    {task.error && <div className="diagnostic-block" role="alert"><strong>失败原因</strong><p>{task.error}</p>{task.diagnostic_code && <code>{task.diagnostic_code}</code>}</div>}
    <div className="inspector-actions">
      {isActive && <button className="danger-button" type="button" disabled={analysis.isSubmitting || task.cancel_requested} onClick={() => setShowCancelConfirm(true)}>{task.cancel_requested ? "正在取消…" : "取消任务"}</button>}
      {task.status === "failed" && <button className="primary-button" type="button" disabled={analysis.isSubmitting} onClick={() => void analysis.retry(task.task_id)}>从失败分段继续</button>}
    </div>
    <ConfirmationDialog
      open={showCancelConfirm}
      title="确认取消分析任务"
      description="任务会在安全检查点停止；已完成分段、诊断记录和检查点不会删除。"
      confirmLabel="确认取消任务"
      destructive
      busy={analysis.isSubmitting}
      details={<><strong>{task.current_segment == null ? "正在准备或汇总" : `正在处理第 ${task.current_segment + 1} 段`}</strong><span>当前进度 {task.progress}%</span></>}
      onClose={() => setShowCancelConfirm(false)}
      onConfirm={() => { setShowCancelConfirm(false); void analysis.cancel(task.task_id); }}
    />
    <dl className="technical-trace">
      <div><dt>任务 ID</dt><dd><code>{task.task_id}</code></dd></div>
      <div><dt>Trace ID</dt><dd><code>{task.trace_id ?? "旧任务暂无"}</code></dd></div>
      <div><dt>更新时间</dt><dd>{dateTimeLabel(task.updated_at)}</dd></div>
    </dl>
  </div>;
}

function EvidenceInspector({ analysis, reportDirty }: { analysis: AnalysisController; reportDirty: boolean }) {
  const report = analysis.report;
  const task = analysis.tasks.find((item) => item.task_id === analysis.selectedTaskId);
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(null);
  const reviewer = "本机";
  const [note, setNote] = useState("");
  const [showAcceptConfirm, setShowAcceptConfirm] = useState(false);

  useEffect(() => {
    setSelectedEvidenceId(report?.summary.overall_conclusion.evidence_ids[0] ?? report?.evidence[0]?.id ?? null);
    setNote("");
    setShowAcceptConfirm(false);
  }, [analysis.selectedTaskId, report?.revision]);

  const evidence = report?.evidence.find((item) => item.id === selectedEvidenceId) ?? report?.evidence[0];
  const relatedFrame = evidence && report
    ? report.keyframes.find((frame) => frame.timestamp_ms >= (evidence.start_ms ?? 0)) ?? report.keyframes[0]
    : undefined;

  return <aside className="evidence-inspector workbench-surface" aria-labelledby="evidence-inspector-title">
    <header className="inspector-header">
      <h2 id="evidence-inspector-title">爆点证据与人工确认</h2>
    </header>
    {!report || !analysis.selectedTaskId ? <>
      <div className="mini-empty">选择已生成报告的任务，在这里核对画面、时间码与结论。</div>
      <details className="runtime-disclosure"><summary>查看任务运行信息</summary><TaskRuntimeDetails analysis={analysis} task={task} /></details>
    </> : <>
      <div className="inspector-frame">
        {relatedFrame ? <img src={keyframeUrl(analysis.selectedTaskId, relatedFrame.id)} alt={`证据时间 ${evidenceTimecode(evidence?.start_ms ?? null)} 的关键帧`} /> : <div className="frame-placeholder">暂无对应关键帧</div>}
        <time>{evidenceTimecode(evidence?.start_ms ?? null)}</time>
      </div>
      <section className="inspector-conclusion" aria-label="总体结论">
        <span>总体结论</span>
        <p>{report.summary.overall_conclusion.text}</p>
        <small>置信度 {Math.round(report.summary.overall_conclusion.confidence * 100)}% · {report.summary.overall_conclusion.evidence_ids.length} 条引用</small>
      </section>
      {evidence && <section className="selected-evidence" aria-label="当前证据">
        <div><span className={`evidence-kind ${evidence.is_inference ? "evidence-inference" : "evidence-fact"}`}>{evidence.is_inference ? "推断" : "事实"}</span><time>{evidenceTimecode(evidence.start_ms)}</time></div>
        <p>{evidence.claim}</p>
        <small>{evidence.source_type} · 置信度 {Math.round(evidence.confidence * 100)}%</small>
      </section>}
      <div className="review-compact">
        <label htmlFor="inspector-review-note">审核备注</label>
        <textarea id="inspector-review-note" value={note} onChange={(event) => setNote(event.target.value)} placeholder="记录修订依据或待补证据" />
        {reportDirty && <p className="review-warning" role="status">总体结论还有未保存修改，请先保存新修订再审核。</p>}
        <div className="review-actions">
          <button className="secondary-button" type="button" disabled={reportDirty || !reviewer.trim() || analysis.isSubmitting} onClick={() => void analysis.review("reviewed", reviewer, note)}>标记已复核</button>
          <button className="primary-button" type="button" disabled={reportDirty || !reviewer.trim() || analysis.isSubmitting} onClick={() => setShowAcceptConfirm(true)}>接受报告</button>
        </div>
      </div>
      <ConfirmationDialog
        open={showAcceptConfirm}
        title="确认接受这份分析报告"
        description="接受后，这一修订中的爆点机制会进入候选库；还需在下方逐项核对原话、动作与时间码，才能批准给脚本使用。"
        confirmLabel="确认接受报告"
        busy={analysis.isSubmitting}
        details={<span>{note.trim() || "未填写额外备注"}</span>}
        onClose={() => setShowAcceptConfirm(false)}
        onConfirm={() => { setShowAcceptConfirm(false); void analysis.review("accepted", reviewer, note); }}
      />
      <div className="evidence-selector" role="list" aria-label="报告证据">
        {report.evidence.map((item: Evidence, index) => <button
          type="button"
          role="listitem"
          key={item.id}
          className={item.id === evidence?.id ? "selected" : undefined}
          onClick={() => setSelectedEvidenceId(item.id)}
        ><span>{String(index + 1).padStart(2, "0")}</span><strong>{evidenceTimecode(item.start_ms)}</strong><small>{item.claim}</small></button>)}
      </div>
      <details className="runtime-disclosure"><summary>运行诊断与检查点</summary><TaskRuntimeDetails analysis={analysis} task={task} /></details>
    </>}
  </aside>;
}

function NewAnalysisForm({
  analysis,
  completedMedia,
  openSettings,
  onSubmitted,
}: {
  analysis: AnalysisController;
  completedMedia: MediaTask[];
  openSettings: () => void;
  onSubmitted: () => void;
}) {
  const [modelPurpose, setModelPurpose] = useState<AnalysisModelPurpose>("analysis");
  const [mediaTaskId, setMediaTaskId] = useState("");
  const [playCount, setPlayCount] = useState("");
  const [retention, setRetention] = useState("");
  const [completion, setCompletion] = useState("");
  const [clickRate, setClickRate] = useState("");
  const [transactionCount, setTransactionCount] = useState("");
  const [comments, setComments] = useState("");
  const [hasSubmitted, setHasSubmitted] = useState(false);
  const selectedPurpose = analysisPurposeCopy[modelPurpose];
  const launchDisabledReason = !mediaTaskId
    ? "请先选择一条已完成本地处理的视频。"
    : !analysis.readiness.gateway_configured
      ? `请先配置支持图文输入的${selectedPurpose.shortLabel}模型。`
      : modelPurpose === "analysis" && analysis.readiness.analysis_method?.status === "invalid"
        ? analysis.readiness.analysis_method.message
        : analysis.isSubmitting
        ? "当前操作尚未完成，请稍候。"
        : null;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (launchDisabledReason) return;
    const values: Record<string, number> = {};
    const count = numberOrUndefined(playCount);
    const threeSecond = numberOrUndefined(retention);
    const completed = numberOrUndefined(completion);
    const clicks = numberOrUndefined(clickRate);
    const transactions = numberOrUndefined(transactionCount);
    if (count !== undefined) values.play_count = Math.max(0, Math.round(count));
    if (threeSecond !== undefined) values.three_second_retention = Math.min(1, Math.max(0, threeSecond / 100));
    if (completed !== undefined) values.completion_rate = Math.min(1, Math.max(0, completed / 100));
    if (clicks !== undefined) values.product_click_rate = Math.min(1, Math.max(0, clicks / 100));
    if (transactions !== undefined) values.transaction_count = Math.max(0, Math.round(transactions));
    guardUnsavedTransition(() => { setHasSubmitted(true); void analysis.startAnalysis({
        media_task_id: mediaTaskId,
        model_purpose: modelPurpose,
        metric_snapshots: Object.keys(values).length ? [{ source_type: "manual", confidence: 1, values }] : [],
        comments: comments.split(/\r?\n/).map((text) => text.trim()).filter(Boolean).map((text) => ({ text, source_label: "人工录入", confidence: 1 })),
      }).then((created) => { if (created) onSubmitted(); });
    });
  }

  return <form className="analysis-launcher modal-form" onSubmit={submit}>
    <fieldset className="analysis-purpose-picker" aria-describedby="analysis-purpose-privacy">
      <legend>任务类型</legend>
      <div className="analysis-purpose-options">
        {(Object.entries(analysisPurposeCopy) as Array<[AnalysisModelPurpose, typeof selectedPurpose]>).map(([value, copy]) => <label className={modelPurpose === value ? "analysis-purpose-active" : undefined} key={value}>
          <input type="radio" name="analysis-model-purpose" value={value} checked={modelPurpose === value} onChange={() => setModelPurpose(value)} />
          <span><strong>{copy.label}</strong><small>{copy.description}</small></span>
        </label>)}
      </div>
      <p id="analysis-purpose-privacy">音频先在本地转写，视频先在本地抽帧与 OCR；云端只接收必要文字与关键帧，不上传原视频。</p>
      {modelPurpose === "analysis" ? <AnalysisMethodNotice method={analysis.readiness.analysis_method} /> : null}
    </fieldset>
    <label htmlFor="analysis-media">已完成本地处理的视频</label>
    <select id="analysis-media" required value={mediaTaskId} onChange={(event) => setMediaTaskId(event.target.value)}>
      <option value="">请选择视频</option>
      {completedMedia.map((task) => <option value={task.task_id} key={task.task_id}>{task.source_name}</option>)}
    </select>
    {completedMedia.length === 0 && <p className="inline-warning">还没有 S2 已完成视频，请先到“视频处理”导入并完成一个任务。</p>}
    <details className="optional-inputs">
      <summary>补充经营数据与评论（可选）</summary>
      <fieldset>
        <legend>经营数据；缺失时模型不得伪造</legend>
        <div className="metric-field-grid">
          <label>播放量<input inputMode="numeric" min="0" type="number" value={playCount} onChange={(event) => setPlayCount(event.target.value)} /></label>
          <label>3 秒留存 %<input inputMode="decimal" min="0" max="100" step="0.1" type="number" value={retention} onChange={(event) => setRetention(event.target.value)} /></label>
          <label>完播率 %<input inputMode="decimal" min="0" max="100" step="0.1" type="number" value={completion} onChange={(event) => setCompletion(event.target.value)} /></label>
          <label>商品点击率 %<input inputMode="decimal" min="0" max="100" step="0.1" type="number" value={clickRate} onChange={(event) => setClickRate(event.target.value)} /></label>
          <label>成交量<input inputMode="numeric" min="0" type="number" value={transactionCount} onChange={(event) => setTransactionCount(event.target.value)} /></label>
        </div>
      </fieldset>
      <label htmlFor="analysis-comments">评论摘录<span>每行一条；没有就留空</span></label>
      <textarea id="analysis-comments" value={comments} placeholder="这条裤子小个子能穿吗？&#10;面料看着挺有垂感" onChange={(event) => setComments(event.target.value)} />
    </details>
    {hasSubmitted && analysis.actionMessageKind === "error" && !analysis.isSubmitting
      ? <p className="inline-warning" role="alert">{analysis.actionMessage}</p>
      : null}
    <div className="analysis-launch-actions modal-sticky-actions">
      {!analysis.readiness.gateway_configured && <button className="secondary-button" type="button" onClick={openSettings}>配置多模态模型</button>}
      <button className="primary-button" type="submit" disabled={Boolean(launchDisabledReason)} aria-describedby={launchDisabledReason ? "analysis-launch-disabled-reason" : undefined}>开始{selectedPurpose.label}</button>
    </div>
    {launchDisabledReason && <p className="control-disabled-reason" id="analysis-launch-disabled-reason">{launchDisabledReason}</p>}
  </form>;
}

export function S3AnalysisWorkspace({ analysis, mediaTasks, openSettings, compact = false }: { analysis: AnalysisController; mediaTasks: MediaTask[]; openSettings: () => void; compact?: boolean }) {
  const [showNewTask, setShowNewTask] = useState(false);
  const [reportDirty, setReportDirty] = useState(false);
  const completedMedia = useMemo(() => mediaTasks.filter((task) => task.status === "completed"), [mediaTasks]);
  const mediaById = useMemo(() => new Map(mediaTasks.map((task) => [task.task_id, task])), [mediaTasks]);
  const runningCount = analysis.tasks.filter((task) => ["pending", "running", "retry_wait"].includes(task.status)).length;
  const reviewCount = analysis.tasks.filter((task) => ["succeeded", "completed"].includes(task.status)).length;

  return <div className="content analysis-content analysis-desk">
    <WorkspaceHeader
      stage="S3 · 爆点研究"
      title="爆点研究智能体"
      description={analysis.readiness.analysis_method?.status === "ready"
        ? "新任务使用 Huashu 七维拆解，定位原话、动作与证据；核对并批准爆点后，再用于商品脚本。"
        : "逐秒定位口播、动作与视觉证明；先接受报告生成候选，再人工批准为可复用爆点 Skill。"}
      current={<><strong>当前任务</strong><span>{analysis.selectedTaskId ? mediaById.get(analysis.tasks.find((task) => task.task_id === analysis.selectedTaskId)?.media_task_id ?? "")?.source_name ?? analysis.selectedTaskId : "尚未选择"}</span></>}
      metrics={[
        { label: "运行中", value: runningCount, tone: runningCount ? "warning" : "default" },
        { label: "待复核", value: reviewCount, tone: reviewCount ? "success" : "default" },
        { label: "模型", value: analysis.readiness.gateway_configured ? "已配置" : "待配置", tone: analysis.readiness.gateway_configured ? "success" : "danger" },
      ]}
      action={<button className="primary-button" type="button" onClick={() => setShowNewTask(true)}>分析一条爆款</button>}
    />
    {analysis.actionMessage && <div className={`action-message${analysis.actionMessageKind === "error" ? " action-message-error" : ""}`} role={analysis.actionMessageKind === "error" ? "alert" : "status"} aria-live={analysis.actionMessageKind === "error" ? "assertive" : "polite"}>{analysis.actionMessage}</div>}
    <section className="analysis-workbench" aria-label="分析工作台">
      <TaskQueue analysis={analysis} mediaById={mediaById} />
      <div className="analysis-workstage workbench-surface"><AnalysisReportView analysis={analysis} onDirtyChange={setReportDirty} /></div>
      <EvidenceInspector analysis={analysis} reportDirty={reportDirty} />
    </section>
    {!compact && <ViralSkillLibrary />}
    <ModalDialog open={showNewTask} title="新建分析任务" description="选择本地处理结果；任务会逐段保存，并可从中断处恢复。" onClose={() => setShowNewTask(false)}>
      <NewAnalysisForm analysis={analysis} completedMedia={completedMedia} openSettings={openSettings} onSubmitted={() => setShowNewTask(false)} />
    </ModalDialog>
  </div>;
}
