import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FileDropInput } from "./FileDropInput";
import { FootageBatchPanel } from "./FootageBatchPanel";
import { guardUnsavedTransition } from './unsavedChanges';
import {
  autoEditStatusLabel,
  appendAutoEditFiles,
  cancelAutoEditProject,
  createAutoEditProject,
  fetchAutoEditProjects,
  retryAutoEditProject,
  startAutoEditProject,
  trashAutoEditProject,
  validateAutoEditDraft,
  type AutoEditDraft,
  type AutoEditProject,
} from "./autoEditing";
import "./auto-edit-workspace.css";

const initialDraft: AutoEditDraft = {
  title: "",
  targetCount: 5,
  durationMinSeconds: 20,
  durationMaxSeconds: 40,
  subtitleFontSize: 68,
  keywordColor: "#FFD400",
  keywordScale: 1.3,
  subtitleFont: 'yahei',
  subtitleEffect: 'none',
};

function seconds(milliseconds: number): string {
  return `${Math.round(milliseconds / 1000)} 秒`;
}

export function AutoEditWorkspace({ reviewRequested = 0 }: { reviewRequested?: number }) {
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
  const refreshSequence = useRef(0);
  const selected = useMemo(() => projects.find(project => project.project_id === selectedId) ?? projects[0] ?? null, [projects, selectedId]);

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

  const moveSource = (index: number, delta: number) => setSources(current => {
    const next = [...current];
    const target = index + delta;
    if (target < 0 || target >= next.length) return current;
    [next[index], next[target]] = [next[target]!, next[index]!];
    return next;
  });

  useEffect(() => {
    void refresh(true);
    const timer = window.setInterval(() => void refresh(true), 4000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (reviewRequested > 0) setShowReview(true);
  }, [reviewRequested]);

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
    if (!selected) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const updated = action === "start" ? await startAutoEditProject(selected) : action === "retry" ? await retryAutoEditProject(selected) : await cancelAutoEditProject(selected);
      setProjects(current => current.map(project => project.project_id === updated.project_id ? updated : project));
      setMessage(action === "cancel" ? "项目已取消。" : "项目已进入后台队列，可以继续建立其他项目。 ");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败，请刷新后重试。 ");
    } finally { setBusy(false); }
  };

  if (showReview) return <section className="auto-edit-workspace"><div className="workspace-inline-header"><div><p className="eyebrow">视频剪辑</p><h1>成片审核</h1></div><button className="secondary-button" onClick={() => guardUnsavedTransition(() => setShowReview(false))}>返回项目</button></div><FootageBatchPanel compact /></section>;

  return <section className="content auto-edit-workspace">
    <div className="workspace-inline-header"><div><p className="eyebrow">视频剪辑</p><h1 data-page-title tabIndex={-1}>自动剪辑项目</h1><p>上传录播并设置成片范围。系统会提取话术、镜头和关键画面，自动匹配已审核的剪辑 Skill，再生成待审核成片。</p></div><button className="primary-button" onClick={() => setShowCreate(value => !value)}>{showCreate ? "收起" : "新建项目"}</button></div>
    {showCreate && <div className="auto-edit-create" aria-label="新建自动剪辑项目">
      <div className="auto-edit-create-columns"><section className="auto-edit-source-panel" aria-label="素材上传与排列"><h2>素材上传与排列</h2><p className="assistive">先上传录播，再调整素材顺序。每个项目最多 20 条。</p><div><label>待剪录播视频<FileDropInput multiple accept=".mp4,.mov,.mkv,.m4v,.webm" disabled={busy} onChange={event => {
        try { setSources(appendAutoEditFiles(sources, Array.from(event.target.files ?? []))); setError(''); }
        catch (reason) { setError(reason instanceof Error ? reason.message : '追加文件失败'); }
        event.target.value = '';
      }} /></label><p className="assistive">支持多选或拖入多个文件，继续选择可追加。成片数量按整个项目计算；排列顺序不代表强制拼接。</p>{sources.length > 0 ? <div className="selected-sources"><p role="status">已选择 {sources.length} 条录播</p><ul>{sources.map((file, index) => <li key={`${file.name}-${file.size}-${file.lastModified}`}><span><strong>{index + 1}. {file.name}</strong><small>{(file.size / 1024 / 1024).toFixed(1)} MB</small></span><div className="source-order-actions"><button type="button" className="text-button" disabled={busy || index === 0} aria-label={`上移 ${file.name}`} onClick={() => moveSource(index,-1)}>上移</button><button type="button" className="text-button" disabled={busy || index === sources.length-1} aria-label={`下移 ${file.name}`} onClick={() => moveSource(index,1)}>下移</button><button type="button" className="text-button" disabled={busy} aria-label={`移除 ${file.name}`} onClick={() => setSources(current => current.filter((_, i) => i !== index))}>移除</button></div></li>)}</ul></div> : <p className="source-empty">上传后的素材会显示在这里，可上移、下移或移除。</p>}</div></section>
      <fieldset disabled={busy} className="auto-edit-settings-panel" aria-label="项目名称与剪辑设置"><legend>项目名称与剪辑设置</legend><label>项目名称<input value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })} placeholder="例如：J85 直播录播 · 上午第一批" /></label>
      <div className="auto-edit-parameters"><label>成片数量<input type="number" min="1" max="10" value={draft.targetCount} onChange={event => setDraft({ ...draft, targetCount: Number(event.target.value) })} /></label><label>最短秒数<input type="number" min="5" max="180" value={draft.durationMinSeconds} onChange={event => setDraft({ ...draft, durationMinSeconds: Number(event.target.value) })} /></label><label>最长秒数<input type="number" min="5" max="180" value={draft.durationMaxSeconds} onChange={event => setDraft({ ...draft, durationMaxSeconds: Number(event.target.value) })} /></label><label>字幕字号<input type="number" min="32" max="120" value={draft.subtitleFontSize} onChange={event => setDraft({ ...draft, subtitleFontSize: Number(event.target.value) })} /></label><label>重点词颜色<span className="color-input"><input type="color" value={draft.keywordColor} onChange={event => setDraft({ ...draft, keywordColor: event.target.value })} /><input value={draft.keywordColor} onChange={event => setDraft({ ...draft, keywordColor: event.target.value })} /></span></label><label>重点词放大<input type="number" min="1" max="2" step="0.1" value={draft.keywordScale} onChange={event => setDraft({ ...draft, keywordScale: Number(event.target.value) })} /></label></div>
      <div className="auto-edit-parameters"><label>字幕字体<select aria-label="字幕字体" disabled={busy} value={draft.subtitleFont} onChange={event => setDraft({...draft,subtitleFont:event.target.value as AutoEditDraft['subtitleFont']})}><option value="yahei">微软雅黑</option><option value="heiti">黑体</option><option value="songti">宋体</option><option value="kaiti">楷体</option></select></label><label>字幕特效<select aria-label="字幕特效" disabled={busy} value={draft.subtitleEffect} onChange={event => setDraft({...draft,subtitleEffect:event.target.value as AutoEditDraft['subtitleEffect']})}><option value="none">无特效 · 口播同步</option><option value="fade">淡入淡出</option><option value="pop">轻微弹入</option></select></label></div>
      <div className="auto-edit-create-actions"><span>只显示简体口播字幕；字幕不叠加，静音时隐藏，不添加顶部标题。</span><button className="primary-button" disabled={busy} onClick={() => void create()}>{busy ? "正在保存…" : "保存项目"}</button></div></fieldset></div>
    </div>}
    {(error || message) && <p className={error ? "workspace-alert is-error" : "workspace-alert"} role={error ? "alert" : "status"}>{error || message}</p>}
    {deleteTarget && <div role="alert" className="library-inline-editor"><p>将项目“{deleteTarget.title}”移入回收站？原始录播和已生成成片仍保留。</p><button className="secondary-button danger-text" disabled={busy} onClick={() => void manageProject(deleteTarget)}>确认删除项目</button><button className="secondary-button" disabled={busy} onClick={() => setDeleteTarget(null)}>取消</button></div>}
    <div className="project-management-bar"><button className="secondary-button" aria-pressed={trash} onClick={() => {++refreshSequence.current; setTrash(!trash); setProjects([]); setDeleteTarget(null);}}>{trash ? '返回项目队列' : '项目回收站'}</button>{selected && (trash ? <button className="secondary-button" disabled={busy} onClick={() => void manageProject(selected,true)}>恢复项目</button> : <button className="secondary-button danger-text" disabled={busy || !['draft','failed','cancelled','review','completed'].includes(selected.status)} onClick={() => setDeleteTarget(selected)}>删除项目</button>)}{selected && !trash && !['draft','failed','cancelled','review','completed'].includes(selected.status) && <span className="assistive">处理中，请先取消或等待完成后删除。</span>}</div>
    <div className="auto-edit-layout">
      <aside className="auto-edit-projects" aria-label="自动剪辑项目列表"><div className="section-heading"><h2>项目队列</h2><button className="text-button" onClick={() => void refresh()}>刷新</button></div>{projects.length === 0 ? <p className="empty-state">还没有剪辑项目。点击“新建项目”上传第一条录播。</p> : projects.map(project => <button key={project.project_id} className="auto-edit-project-row" aria-current={selected?.project_id === project.project_id ? "true" : undefined} onClick={() => setSelectedId(project.project_id)}><span><strong>{project.title}</strong><small>{project.source.file_name}</small></span><span className={`status-pill status-${project.status}`}>{autoEditStatusLabel(project.status)}</span></button>)}</aside>
      <div className="auto-edit-detail">{!selected ? <div className="empty-state"><h2>建立第一批剪辑项目</h2><p>项目会独立保存和排队，你不需要等待上一批完成。</p></div> : <>
        <div className="project-detail-heading"><div><p className="eyebrow">{autoEditStatusLabel(selected.status)} · {selected.progress}%</p><h2>{selected.title}</h2><p>{selected.source.file_name} · {seconds(selected.source.duration_ms)}</p></div>{!trash && <div className="project-actions">{selected.status === "draft" && <button className="primary-button" disabled={busy} onClick={() => void run("start")}>开始剪辑</button>}{selected.status === "failed" && <button className="primary-button" disabled={busy} onClick={() => void run("retry")}>重新尝试</button>}{["queued", "analyzing", "planning", "render_pending"].includes(selected.status) && <button className="secondary-button" disabled={busy} onClick={() => void run("cancel")}>取消项目</button>}{selected.output_batch_id && <button className="primary-button" onClick={() => setShowReview(true)}>审核成片</button>}</div>}</div>
        {selected.sources && selected.sources.length > 1 && <section className="selected-sources"><h3>项目素材 · {selected.sources.length} 条</h3><ul>{selected.sources.map(source => <li key={source.source_id}><span>{source.file_name}</span><small>{seconds(source.duration_ms)}</small></li>)}</ul></section>}
        <div className="project-facts"><span><small>成片总数量</small><strong>{selected.settings.target_count} 条</strong></span><span><small>时长范围</small><strong>{seconds(selected.settings.duration_min_ms)}–{seconds(selected.settings.duration_max_ms)}</strong></span><span><small>字幕</small><strong>{selected.settings.subtitle_font_size}px</strong></span><span><small>重点词</small><strong style={{ color: selected.settings.keyword_color }}>{selected.settings.keyword_color} · {selected.settings.keyword_scale}×</strong></span></div>
        {selected.error && <p className="workspace-alert is-error" role="alert">{selected.error}</p>}
        <section className="auto-skill-result"><div className="section-heading"><h3>系统自动匹配的 Skill</h3><span>只读</span></div>{selected.selected_skill ? <><strong>{selected.selected_skill.snapshot.name}</strong><p>{selected.selected_skill.snapshot.mechanism}</p><p className="assistive">版本 {selected.selected_skill.snapshot.revision} · {selected.selected_skill.reason}</p></> : <p className="empty-state">项目开始后，系统会读取原素材的 ASR 话术、镜头与关键帧，并从已审核方法库中自动匹配，不需要手动选择。模型或转写不可用时会明确失败并保留项目，修复后可重试。</p>}</section>
        {selected.plan.length > 0 && <section className="auto-edit-plan"><h3>剪辑方案</h3>{selected.plan.map(item => <article key={item.candidate_id}><div><strong>{item.title}</strong><small>{seconds(item.duration_ms)}</small></div><p>{item.clips.map(clip => `${seconds(clip.start_ms)}–${seconds(clip.end_ms)}`).join(" + ")}</p><p>{item.selection_reason}</p></article>)}</section>}
      </>}</div>
    </div>
  </section>;
}
