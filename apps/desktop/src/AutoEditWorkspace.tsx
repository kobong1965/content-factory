import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FileDropInput } from "./FileDropInput";
import { FootageBatchPanel } from "./FootageBatchPanel";
import { LocalVideoPreview } from './LocalVideoPreview';
import { StudioIcon } from './StudioIcon';
import { ProjectFailure } from './ProjectFailure';
import { projectStage } from './projectPresentation';
import { guardUnsavedTransition, useUnsavedChanges } from './unsavedChanges';
import {
  autoEditStatusLabel,
  canRetryRegistration,
  draftFromProject,
  selectDurationPolicy,
  generationBlockers,
  appendAutoEditFiles,
  cancelAutoEditProject,
  createAutoEditProject,
  fetchAutoEditProjects,
  purgeAutoEditProject,
  previewAutoEditPurge,
  type PurgePreview,
  retryAutoEditProject,
  startAutoEditProject,
  trashAutoEditProject,
  updateAutoEditProject,
  validateAutoEditDraft,
  type AutoEditDraft,
  type AutoEditProject,
} from "./autoEditing";
import "./auto-edit-workspace.css";

const initialDraft: AutoEditDraft = {
  title: "",
  sku: "",
  targetCount: 5,
  durationMinSeconds: 15,
  durationMaxSeconds: 30,
  durationPolicy: 'bounded_15_30',
  subtitleFontSize: 68,
  keywordColor: "#FFD400",
  keywordScale: 1.3,
  subtitleFont: 'yahei',
  subtitleEffect: 'none',
  subtitleMode: 'sentence',
};

function GenerationConditionsEditor({
  draft,
  busy,
  onChange,
  onSave,
  onSaveAndRetry,
  onCancel,
}: {
  draft: AutoEditDraft;
  busy: boolean;
  onChange: (draft: AutoEditDraft) => void;
  onSave: () => void;
  onSaveAndRetry: () => void;
  onCancel: () => void;
}) {
  const change = (patch: Partial<AutoEditDraft>) => onChange({ ...draft, ...patch });
  return <section className="generation-condition-editor" aria-label="修改生成条件">
    <div className="section-heading"><div><h3>修改生成条件</h3><p className="assistive">调整后会使旧的失败方案失效，保存后再按新条件重新规划；原素材和项目记录保留。</p></div></div>
    <div className="auto-edit-parameters">
      <label>成片数量<input type="number" min="1" max="20" value={draft.targetCount} disabled={busy} onChange={event => change({ targetCount: Number(event.target.value) })} /></label>
      <label>时长策略<select aria-label="修改时长策略" disabled={busy} value={draft.durationPolicy} onChange={event => onChange(selectDurationPolicy(draft, event.target.value as NonNullable<AutoEditDraft['durationPolicy']>))}><option value="bounded_15_30">完整片段优先 · 15—30 秒</option><option value="custom">自定义时长范围</option></select></label>
      <label>硬下限（秒）<input type="number" min={draft.durationPolicy === 'bounded_15_30' ? 15 : 5} max={draft.durationPolicy === 'bounded_15_30' ? 30 : 180} value={draft.durationMinSeconds} disabled={busy} onChange={event => change({ durationMinSeconds: Number(event.target.value) })} /></label>
      <label>硬上限（秒）<input type="number" min={draft.durationPolicy === 'bounded_15_30' ? 15 : 5} max={draft.durationPolicy === 'bounded_15_30' ? 30 : 180} value={draft.durationMaxSeconds} disabled={busy} onChange={event => change({ durationMaxSeconds: Number(event.target.value) })} /></label>
      <label>字幕字号<input type="number" min="32" max="120" value={draft.subtitleFontSize} disabled={busy} onChange={event => change({ subtitleFontSize: Number(event.target.value) })} /></label>
      <label>重点词颜色<span className="color-input"><input type="color" value={draft.keywordColor} disabled={busy} onChange={event => change({ keywordColor: event.target.value })} /><input value={draft.keywordColor} disabled={busy} onChange={event => change({ keywordColor: event.target.value })} /></span></label>
      <label>重点词放大<input type="number" min="1" max="2" step="0.1" value={draft.keywordScale} disabled={busy} onChange={event => change({ keywordScale: Number(event.target.value) })} /></label>
    </div>
    <div className="auto-edit-parameters">
      <label>字幕字体<select aria-label="修改字幕字体" disabled={busy} value={draft.subtitleFont} onChange={event => change({ subtitleFont: event.target.value as AutoEditDraft['subtitleFont'] })}><option value="yahei">微软雅黑</option><option value="heiti">黑体</option><option value="songti">宋体</option><option value="kaiti">楷体</option></select></label>
      <label>字幕特效<select aria-label="修改字幕特效" disabled={busy} value={draft.subtitleEffect} onChange={event => change({ subtitleEffect: event.target.value as AutoEditDraft['subtitleEffect'] })}><option value="none">无特效 · 口播同步</option><option value="fade">淡入淡出</option><option value="pop">轻微弹入</option></select></label>
      <label>字幕显示模式<select aria-label="修改字幕显示模式" disabled={busy} value={draft.subtitleMode} onChange={event => change({ subtitleMode: event.target.value as AutoEditDraft['subtitleMode'] })}><option value="sentence">普通电影字幕 · 口播同步</option><option value="reveal">逐字揭示</option><option value="highlight">重点词高亮</option><option value="auto">自动混合三种模式</option><option value="none">仅保留原声 · 不生成字幕</option></select></label>
    </div>
    <p className="assistive">完整片段模式优先 20—25 秒，实际成片必须落在所设的 15—30 秒范围内。{draft.subtitleMode === 'none' ? '仅保留原声音轨，不调用 ASR；切点依据画面镜头边界，不保证口播句子完整。' : '普通电影字幕整句随口播显示；自动混合按成片轮换三种模式，均使用真实原声时间。'}</p>
    <div className="condition-editor-actions"><button className="secondary-button" disabled={busy} onClick={onCancel}>取消修改</button><span className="assistive">保存后可单独检查，也可以直接保存并重新生成。</span><button className="secondary-button" disabled={busy} onClick={onSave}>{busy ? '正在保存…' : '保存生成条件'}</button><button className="primary-button" disabled={busy} onClick={onSaveAndRetry}>{busy ? '正在提交…' : '保存并重新生成'}</button></div>
  </section>;
}

function seconds(milliseconds: number): string {
  return `${Math.round(milliseconds / 1000)} 秒`;
}

export function AutoEditWorkspace({ reviewRequested = 0, initialBatchId, openLibrary }: { reviewRequested?: number; initialBatchId?: string; openLibrary?: (batchId: string) => void }) {
  const [projects, setProjects] = useState<AutoEditProject[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<AutoEditDraft>(initialDraft);
  const [sources, setSources] = useState<File[]>([]);
  const submitting = useRef(false);
  const [showCreate, setShowCreate] = useState(false);
  const [showReview, setShowReview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [trash, setTrash] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<AutoEditProject | null>(null);
  const [purgeTarget, setPurgeTarget] = useState<AutoEditProject | null>(null);
  const [purgePreview, setPurgePreview] = useState<PurgePreview | null>(null);
  const [editingConditionsFor, setEditingConditionsFor] = useState<string | null>(null);
  const [conditionDraft, setConditionDraft] = useState<AutoEditDraft | null>(null);
  const preparePurge = async (project: AutoEditProject) => { if (busy) return; setBusy(true); setError(''); try { const preview = await previewAutoEditPurge(project); setPurgeTarget(project); setPurgePreview(preview); } catch(e) { setError(e instanceof Error ? e.message : '删除范围读取失败'); } finally { setBusy(false); } };
  const refreshSequence = useRef(0);
  const [reviewBatchId, setReviewBatchId] = useState<string | undefined>(initialBatchId);
  useUnsavedChanges(sources.length > 0 || JSON.stringify(draft) !== JSON.stringify(initialDraft), '新建剪辑项目');
  const selected = useMemo(() => projects.find(project => project.project_id === selectedId) ?? projects[0] ?? null, [projects, selectedId]);
  const blockers = selected ? generationBlockers(selected) : [];

  const refresh = useCallback(async (quiet = false) => {
    const sequence = ++refreshSequence.current;
    try {
      const next = await fetchAutoEditProjects(undefined, trash);
      if (sequence !== refreshSequence.current) return;
      setProjects(next);
      setSelectedId(current => current && next.some(project => project.project_id === current) ? current : next[0]?.project_id ?? null);
      if (!quiet) setMessage("项目状态已刷新。");
      if (!quiet) setError("");
    } catch (reason) {
      if (!quiet) setError(reason instanceof Error ? reason.message : "项目列表读取失败");
    }
  }, [trash]);

  const manageProject = async (project: AutoEditProject, restore = false) => {
    if (busy) return;
    setBusy(true); setError('');
    try {
      await trashAutoEditProject(project, restore);
      ++refreshSequence.current;
      setProjects(current => current.filter(p => p.project_id !== project.project_id));
      setDeleteTarget(null);
      setMessage(restore ? '项目已恢复，可返回项目队列查看。' : '项目已移入回收站，已生成的成片和原始录播保留。');
    } catch (e) { setError(e instanceof Error ? e.message : '操作失败，请刷新重试'); }
    finally { setBusy(false); }
  };

  const purge = async (project: AutoEditProject) => {
    if (busy || !purgePreview) return;
    setBusy(true); setError('');
    try {
      const result = await purgeAutoEditProject(project, purgePreview.confirmation_token);
      if (!result.purged) { await refresh(true); setPurgeTarget(null); setPurgePreview(null); setError('有文件未能删除，项目保留在回收站。关闭占用文件的程序后重新预览并重试。'); return; }
      setProjects(current => current.filter(item => item.project_id !== project.project_id));
      setSelectedId(null); setPurgeTarget(null);
      setMessage('项目已永久删除；删除明细以确认结果为准，受引用保护的文件保留。');
    } catch (e) { setError(e instanceof Error ? e.message : '永久删除失败，请刷新回收站重试'); }
    finally { setBusy(false); }
  };

  const moveSource = (index: number, delta: number) => setSources(current => {
    const next = [...current];
    const target = index + delta;
    if (target < 0 || target >= next.length) return current;
    [next[index], next[target]] = [next[target]!, next[index]!];
    return next;
  });

  const beginConditionEdit = (project: AutoEditProject) => {
    setConditionDraft(draftFromProject(project));
    setEditingConditionsFor(project.project_id);
    setError('');
    setMessage('');
  };

  const cancelConditionEdit = () => {
    setEditingConditionsFor(null);
    setConditionDraft(null);
  };

  const saveConditions = async (retryAfterSave: boolean) => {
    if (!selected || !conditionDraft || busy) return;
    const problem = validateAutoEditDraft(conditionDraft);
    if (problem) { setError(problem); return; }
    setBusy(true); setError(''); setMessage('');
    try {
      const updated = await updateAutoEditProject(selected, conditionDraft);
      setProjects(current => current.map(project => project.project_id === updated.project_id ? updated : project));
      if (!retryAfterSave) {
        cancelConditionEdit();
        setMessage('生成条件已保存，旧方案已作废。确认无误后可点击“重新尝试”。');
        return;
      }
      const nextBlockers = generationBlockers(updated);
      if (nextBlockers.length) {
        setError(nextBlockers.join('；'));
        setMessage('条件已保存，但当前条件仍不能生成，请继续修改。');
        return;
      }
      const retried = await retryAutoEditProject(updated);
      setProjects(current => current.map(project => project.project_id === retried.project_id ? retried : project));
      cancelConditionEdit();
      setMessage('新生成条件已保存，项目已重新进入后台队列。');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '生成条件保存失败，请刷新后重试。');
    } finally { setBusy(false); }
  };

  useEffect(() => {
    void refresh(true);
    const timer = window.setInterval(() => void refresh(true), 4000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (reviewRequested > 0) { setReviewBatchId(initialBatchId); setShowReview(true); }
  }, [reviewRequested, initialBatchId]);

  const create = async () => {
    if (submitting.current) return;
    const problem = validateAutoEditDraft(draft);
    if (problem || !sources.length) {
      setError(problem ?? "请上传至少一条待剪录播视频。");
      return;
    }
    submitting.current = true;
    setBusy(true); setError(""); setMessage("");
    try {
      const project = await createAutoEditProject(sources, draft);
      setProjects(current => [project, ...current]);
      setSelectedId(project.project_id);
      setShowCreate(false); setSources([]); setDraft(initialDraft); setTrash(false);
      setMessage("项目已保存。确认参数后点击“开始剪辑”，你也可以继续新建下一批。 ");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "项目创建失败，表单内容已保留。 ");
    } finally { submitting.current = false; setBusy(false); }
  };

  const run = async (action: "start" | "retry" | "cancel") => {
    if (!selected || busy) return;
    if (action !== 'cancel' && blockers.length) { setError(blockers.join('；')); return; }
    setBusy(true); setError(""); setMessage("");
    try {
      const updated = action === "start" ? await startAutoEditProject(selected) : action === "retry" ? await retryAutoEditProject(selected) : await cancelAutoEditProject(selected);
      setProjects(current => current.map(project => project.project_id === updated.project_id ? updated : project));
      setMessage(action === "cancel" ? "项目已取消。" : action === "retry" && canRetryRegistration(selected) ? "项目已进入登记队列，将复用现有成片，不会重新调用模型。" : "项目已进入后台队列，可以继续建立其他项目。 ");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败，请刷新后重试。 ");
    } finally { setBusy(false); }
  };

  if (showReview) return <section className="content auto-edit-workspace"><div className="workspace-inline-header"><h1>成片审核</h1><button className="secondary-button" onClick={() => guardUnsavedTransition(() => setShowReview(false))}>返回项目</button></div><FootageBatchPanel compact initialBatchId={reviewBatchId} /></section>;

  return <section className="content auto-edit-workspace">
    <div className="workspace-inline-header"><div className="studio-page-title"><h1 data-page-title tabIndex={-1}>剪辑项目</h1><span>{trash ? '回收站' : `${projects.length} 个项目`}</span></div><button className="primary-button" onClick={() => setShowCreate(value => !value)}><StudioIcon name="plus"/>{showCreate ? "收起" : "新建项目"}</button></div>
    {showCreate && <div className="auto-edit-create" aria-label="新建自动剪辑项目">
      <div className="auto-edit-create-columns"><section className="auto-edit-source-panel" aria-label="素材上传与排列"><h2>素材上传与排列</h2><p className="assistive">先上传录播，再调整素材顺序。每个项目最多 20 条。</p><div><label>待剪录播视频<FileDropInput multiple accept=".mp4,.mov,.mkv,.m4v,.webm" disabled={busy} onChange={event => {
        try { setSources(appendAutoEditFiles(sources, Array.from(event.target.files ?? []))); setError(''); }
        catch (reason) { setError(reason instanceof Error ? reason.message : '追加文件失败'); }
        event.target.value = '';
      }} /></label><p className="assistive">支持多选或拖入多个文件，继续选择可追加。成片数量按整个项目计算；排列顺序不代表强制拼接。</p>{sources.length > 0 ? <div className="selected-sources"><p role="status">已选择 {sources.length} 条录播</p><ul>{sources.map((file, index) => <li key={`${file.name}-${file.size}-${file.lastModified}`}><LocalVideoPreview file={file} /><span><strong>{index + 1}. {file.name}</strong><small>{(file.size / 1024 / 1024).toFixed(1)} MB</small></span><div className="source-order-actions"><button type="button" className="text-button" disabled={busy || index === 0} aria-label={`上移 ${file.name}`} onClick={() => moveSource(index,-1)}>上移</button><button type="button" className="text-button" disabled={busy || index === sources.length-1} aria-label={`下移 ${file.name}`} onClick={() => moveSource(index,1)}>下移</button><button type="button" className="text-button" disabled={busy} aria-label={`移除 ${file.name}`} onClick={() => setSources(current => current.filter((_, i) => i !== index))}>移除</button></div></li>)}</ul></div> : <p className="source-empty">上传后的素材会显示在这里，可上移、下移或移除。</p>}</div></section>
      <fieldset disabled={busy} className="auto-edit-settings-panel" aria-label="项目名称与剪辑设置"><legend>项目名称与剪辑设置</legend><label>项目名称<input value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })} placeholder="例如：J85 直播录播 · 上午第一批" /></label><label>款号（可选）<input value={draft.sku} maxLength={100} onChange={event => setDraft({ ...draft, sku: event.target.value })} placeholder="例如：J85" /></label>
      <div className="auto-edit-parameters"><label>成片数量<input type="number" min="1" max="20" value={draft.targetCount} onChange={event => setDraft({ ...draft, targetCount: Number(event.target.value) })} /></label><label>时长策略<select aria-label="时长策略" value={draft.durationPolicy} onChange={event => setDraft(selectDurationPolicy(draft, event.target.value as NonNullable<AutoEditDraft['durationPolicy']>))}><option value="bounded_15_30">完整片段优先 · 15—30 秒</option><option value="custom">自定义时长范围</option></select></label><label>硬下限（秒）<input type="number" min={draft.durationPolicy === 'bounded_15_30' ? 15 : 5} max={draft.durationPolicy === 'bounded_15_30' ? 30 : 180} value={draft.durationMinSeconds} onChange={event => setDraft({ ...draft, durationMinSeconds: Number(event.target.value) })} /></label><label>硬上限（秒）<input type="number" min={draft.durationPolicy === 'bounded_15_30' ? 15 : 5} max={draft.durationPolicy === 'bounded_15_30' ? 30 : 180} value={draft.durationMaxSeconds} onChange={event => setDraft({ ...draft, durationMaxSeconds: Number(event.target.value) })} /></label><label>字幕字号<input type="number" min="32" max="120" value={draft.subtitleFontSize} onChange={event => setDraft({ ...draft, subtitleFontSize: Number(event.target.value) })} /></label><label>重点词颜色<span className="color-input"><input type="color" value={draft.keywordColor} onChange={event => setDraft({ ...draft, keywordColor: event.target.value })} /><input value={draft.keywordColor} onChange={event => setDraft({ ...draft, keywordColor: event.target.value })} /></span></label><label>重点词放大<input type="number" min="1" max="2" step="0.1" value={draft.keywordScale} onChange={event => setDraft({ ...draft, keywordScale: Number(event.target.value) })} /></label></div>
      <div className="auto-edit-parameters"><label>字幕字体<select aria-label="字幕字体" disabled={busy} value={draft.subtitleFont} onChange={event => setDraft({...draft,subtitleFont:event.target.value as AutoEditDraft['subtitleFont']})}><option value="yahei">微软雅黑</option><option value="heiti">黑体</option><option value="songti">宋体</option><option value="kaiti">楷体</option></select></label><label>字幕特效<select aria-label="字幕特效" disabled={busy} value={draft.subtitleEffect} onChange={event => setDraft({...draft,subtitleEffect:event.target.value as AutoEditDraft['subtitleEffect']})}><option value="none">无特效 · 口播同步</option><option value="fade">淡入淡出</option><option value="pop">轻微弹入</option></select></label><label>字幕显示模式<select aria-label="字幕显示模式" disabled={busy} value={draft.subtitleMode} onChange={event => setDraft({...draft,subtitleMode:event.target.value as AutoEditDraft['subtitleMode']})}><option value="sentence">普通电影字幕 · 口播同步</option><option value="reveal">逐字揭示</option><option value="highlight">重点词高亮</option><option value="auto">自动混合三种模式</option><option value="none">仅保留原声 · 不生成字幕</option></select></label></div>
      <div className="auto-edit-create-actions"><span>默认硬边界 15—30 秒；优先选择完整自然片段，建议目标 20—25 秒。{draft.subtitleMode === 'none' ? '当前仅保留原声音轨，不调用 ASR，也不生成字幕。' : '字幕使用最终原声时间码，静音时隐藏。'}</span><button className="primary-button" disabled={busy} onClick={() => void create()}>{busy ? "正在保存…" : "保存项目"}</button></div></fieldset></div>
    </div>}
    {(error || message) && <p className={error ? "workspace-alert is-error" : "workspace-alert"} role={error ? "alert" : "status"}>{error || message}</p>}
    {deleteTarget && <div role="alert" className="library-inline-editor"><p>将项目“{deleteTarget.title}”移入回收站？原始录播和已生成成片仍保留。</p><button className="secondary-button danger-text" disabled={busy} onClick={() => void manageProject(deleteTarget)}>确认移入回收站</button><button className="secondary-button" disabled={busy} onClick={() => setDeleteTarget(null)}>取消</button></div>}
    {purgeTarget && purgePreview && <div role="alert" className="library-inline-editor is-danger"><p><strong>永久删除“{purgeTarget.title}”？</strong> 以下 {purgePreview.delete_count} 个托管原片将物理删除，约 {(purgePreview.delete_bytes/1024/1024).toFixed(1)} MB，不可恢复；{purgePreview.preserved_count} 个受保护文件保留。{purgePreview.warning}</p><ul>{purgePreview.files.map(f => <li key={f.path}><strong>{f.file_name}</strong> · {f.action === 'delete' ? '永久删除' : '保留 / 已不存在'} · {f.reason}</li>)}</ul><button className="secondary-button danger-text" disabled={busy} onClick={() => void purge(purgeTarget)}>确认永久删除原素材</button><button className="secondary-button" disabled={busy} onClick={() => setPurgeTarget(null)}>取消</button></div>}
    <div className="project-management-bar"><button className="secondary-button" aria-pressed={trash} onClick={() => {++refreshSequence.current; setTrash(!trash); setProjects([]); setDeleteTarget(null); setPurgeTarget(null);}}>{trash ? '返回项目队列' : '项目回收站'}</button>{selected && (trash ? <><button className="secondary-button" disabled={busy || ['purging','purge_failed'].includes(selected.status)} onClick={() => void manageProject(selected,true)}>恢复项目</button><button className="secondary-button danger-text" disabled={busy} onClick={() => void preparePurge(selected)}>永久删除</button></> : <button className="secondary-button danger-text" disabled={busy || !['draft','failed','cancelled','review','completed'].includes(selected.status)} onClick={() => setDeleteTarget(selected)}>移入回收站</button>)}{selected && !trash && !['draft','failed','cancelled','review','completed'].includes(selected.status) && <span className="assistive">处理中，请先取消或等待完成后删除。</span>}</div>
    <div className="auto-edit-layout">
      <aside className="auto-edit-projects" aria-label="自动剪辑项目列表"><div className="section-heading"><h2>项目队列</h2><button className="text-button" onClick={() => void refresh()}>刷新</button></div>{projects.length === 0 ? <p className="empty-state">还没有剪辑项目。点击“新建项目”上传第一条录播。</p> : projects.map(project => <button key={project.project_id} className="auto-edit-project-row" aria-current={selected?.project_id === project.project_id ? "true" : undefined} onClick={() => setSelectedId(project.project_id)}><span><strong>{project.title}</strong><small>{project.sku || '未填写款号'} · {project.sources?.length ?? 1} 条素材 · {project.settings.target_count} 条成片</small></span><span className={`status-pill status-${project.status}`}>{autoEditStatusLabel(project.status)}</span></button>)}</aside>
      <div className="auto-edit-detail">{!selected ? <div className="empty-state"><h2>建立第一批剪辑项目</h2><p>项目会独立保存和排队，你不需要等待上一批完成。</p></div> : <>
        <div className="project-detail-heading"><div><div className="project-title-line"><h2>{selected.title}</h2><span className={`status-pill status-${selected.status}`}>{autoEditStatusLabel(selected.status)} · {selected.progress}%</span></div><p>{selected.sources?.length ?? 1} 条录播 · 款号 {selected.sku || '未填写'}</p></div>{!trash && <div className="project-actions">{selected.status === "draft" && <button className="primary-button" disabled={busy || blockers.length > 0} aria-describedby="generation-conditions" onClick={() => void run("start")}>开始剪辑</button>}{selected.status === "failed" && !canRetryRegistration(selected) && <button className="secondary-button" disabled={busy} onClick={() => beginConditionEdit(selected)}>修改生成条件</button>}{selected.status === "failed" && <button className="primary-button" disabled={busy || blockers.length > 0} aria-describedby={canRetryRegistration(selected) ? undefined : 'generation-conditions'} onClick={() => void run("retry")}>{canRetryRegistration(selected) ? '重试登记' : '重新尝试'}</button>}{["queued", "analyzing", "planning", "render_pending"].includes(selected.status) && <button className="secondary-button" disabled={busy} onClick={() => void run("cancel")}>取消项目</button>}{selected.output_batch_id && <><button className="secondary-button" onClick={() => { setReviewBatchId(selected.output_batch_id!); setShowReview(true); }}>审核成片</button><button className="primary-button" onClick={() => openLibrary?.(selected.output_batch_id!)}>查看本批成片</button></>}</div>}</div>
        {editingConditionsFor === selected.project_id && conditionDraft && !trash && !canRetryRegistration(selected) && <GenerationConditionsEditor draft={conditionDraft} busy={busy} onChange={setConditionDraft} onCancel={cancelConditionEdit} onSave={() => void saveConditions(false)} onSaveAndRetry={() => void saveConditions(true)} />}
        {!trash && ['draft', 'failed'].includes(selected.status) && !canRetryRegistration(selected) && <section id="generation-conditions" className={blockers.length ? 'project-failure' : 'auto-skill-result'} aria-label="生成条件检查">
          <h3>{blockers.length ? '暂不支持生成，请先满足以下条件' : '生成前检查'}</h3>
          {blockers.length > 0 ? <ul>{blockers.map(problem => <li key={problem}>{problem}</li>)}</ul> : <p>本地生成前检查未发现阻塞项；点击开始时会再次检查文件能否读取。</p>}
          <p className="assistive">项目可以先保存。每条成片只使用一条原片，同一成片内及不同成片之间均可重复使用片段；源选段区间可以重叠，按成片播放顺序拼接，每次使用均计入总时长。每条最多 30 段。模型返回后，全部方案的数量、时长、来源和选段都通过校验才会渲染；不合格则停止，不会自动付费重试。</p>
        </section>}
        {!trash && <ol className="project-stage-track" aria-label="剪辑处理阶段">{['项目已保存','素材分析','匹配方法','剪辑渲染','成片审核'].map((label,index) => <li key={label} aria-current={index === projectStage(selected.status,selected.progress) ? 'step' : undefined}><span>{index + 1}</span>{label}</li>)}</ol>}
        <section className="selected-sources project-source-list"><h3>项目素材 · {selected.sources?.length ?? 1} 条</h3><ul>{(selected.sources?.length ? selected.sources : [selected.source]).map((source,index) => <li key={'source_id' in source ? source.source_id : index}><span className="source-sequence">{String(index+1).padStart(2,'0')}</span><span>{source.file_name}</span><small>{seconds(source.duration_ms)}</small></li>)}</ul></section>
        <div className="project-facts"><span><small>款号</small><strong>{selected.sku || '未填写'}</strong></span><span><small>成片总数量</small><strong>{selected.settings.target_count} 条</strong></span><span><small>时长范围</small><strong>{seconds(selected.settings.duration_min_ms)}–{seconds(selected.settings.duration_max_ms)}</strong></span><span><small>字幕</small><strong>{selected.settings.subtitle_font_size}px</strong></span><span><small>重点词</small><strong className="keyword-style-fact"><i aria-hidden="true" style={{ background: selected.settings.keyword_color }}/>{selected.settings.keyword_color} · {selected.settings.keyword_scale}×</strong></span></div>
        {selected.error && <ProjectFailure error={selected.error} registrationRetry={canRetryRegistration(selected)} />}
        <section className="auto-skill-result"><div className="section-heading"><h3>智能体匹配结果</h3><span>只读</span></div>{selected.selected_skill ? <><strong>{new Set(selected.plan.map(p => p.skill_id ?? selected.selected_skill?.snapshot.skill_id)).size} 种方法 · {selected.plan.length} 条方案</strong><p>每条成片的具体方法、选段和理由见下方计划。</p><p className="assistive">版本 {selected.selected_skill.snapshot.revision} · {selected.selected_skill.reason}</p></> : <p className="empty-state">项目开始后，系统会读取原素材的 {selected.settings.subtitle_mode === 'none' ? '镜头与关键帧，并保留原声音轨（当前关闭字幕，不调用 ASR）' : 'ASR 话术、镜头与关键帧'}，并从已审核方法库中自动匹配，不需要手动选择。{selected.settings.subtitle_mode === 'none' ? '如需电影式或逐字字幕，请改用需要 ASR 的字幕模式。' : '模型或转写不可用时会明确失败并保留项目，修复后可重试。'}</p>}</section>
        {selected.plan.length > 0 && <section className="auto-edit-plan"><h3>剪辑方案 · 每条成片可使用不同 Skill</h3>{selected.plan.map(item => <article key={item.candidate_id}><div><strong>{item.title}</strong><small>{seconds(item.duration_ms)}</small></div><p className="skill-badge">{item.skill_snapshot?.name ?? item.skill_id ?? selected.selected_skill?.snapshot.name ?? '自动匹配'}</p><p>{item.clips.map(clip => `${seconds(clip.start_ms)}–${seconds(clip.end_ms)}`).join(" + ")}</p><p>{item.selection_reason}</p></article>)}</section>}
      </>}</div>
    </div>
  </section>;
}
