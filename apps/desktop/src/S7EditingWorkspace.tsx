import { FileDropInput } from "./FileDropInput";
import { FootageBatchPanel } from "./FootageBatchPanel";
import { useEffect, useRef, useState, type FormEvent } from "react";
import type { EditClip, EditProject, EditSettings } from "@content-factory/contracts";

import { EditInspector } from "./EditInspector";
import { EditProjectSidebar } from "./EditProjectSidebar";
import { EditTimeline } from "./EditTimeline";
import { FinishedVideoPanel } from "./FinishedVideoPanel";
import { ConfirmationDialog } from "./ConfirmationDialog";
import { ModalDialog } from "./ModalDialog";
import { RenderQueuePanel } from "./RenderQueuePanel";
import { WorkspaceHeader } from "./WorkspaceHeader";
import { formatEditDuration, materialPreviewUrl, outputResourceUrl, reconcileEditDraft, resequenceClips } from "./editing";
import { guardUnsavedTransition, runDraftTransition, useUnsavedChanges } from "./unsavedChanges";
import type { useS7Editing } from "./useS7Editing";

const cloneProject = (project: EditProject): EditProject => JSON.parse(JSON.stringify(project)) as EditProject;

export function S7EditingWorkspace({ studio, openMaterials }: {
  studio: ReturnType<typeof useS7Editing>; openMaterials: () => void;
}) {
  const [draft, setDraft] = useState<EditProject | null>(studio.project ? cloneProject(studio.project) : null);
  const [variantId, setVariantId] = useState<string | null>(studio.project?.variants[0]?.id ?? null);
  const [clipId, setClipId] = useState<string | null>(studio.project?.variants[0]?.clips[0]?.id ?? null);
  const actor = "本机";
  const [scriptId, setScriptId] = useState("");
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [audioName, setAudioName] = useState("");
  const [licenseNote, setLicenseNote] = useState("");
  const audioActor = "本机";
  const [showCreate, setShowCreate] = useState(false);
  const [showScriptEditor, setShowScriptEditor] = useState(false);
  const [showAudioImport, setShowAudioImport] = useState(false);
  const [showRenderConfirm, setShowRenderConfirm] = useState(false);
  const previousServerProject = useRef<EditProject | null>(studio.project);

  useEffect(() => {
    if (!studio.project) {
      previousServerProject.current = null;
      setDraft(null); setVariantId(null); setClipId(null); return;
    }
    const incoming = studio.project;
    const previous = previousServerProject.current;
    setDraft((current) => reconcileEditDraft(current, previous, incoming));
    previousServerProject.current = incoming;
    setVariantId((current) => incoming.variants.some((item) => item.id === current) ? current : incoming.variants[0]?.id ?? null);
  }, [studio.project]);
  useEffect(() => {
    const first = studio.eligibleScripts.find((item) => item.shooting_status === "ready_for_edit" && !item.project_id);
    if (!scriptId && first) setScriptId(first.script_id);
  }, [scriptId, studio.eligibleScripts]);

  const variant = draft?.variants.find((item) => item.id === variantId) ?? draft?.variants[0] ?? null;
  const clip = variant?.clips.find((item) => item.id === clipId) ?? variant?.clips[0] ?? null;
  const output = variant ? studio.latestOutput(variant.id) : undefined;
  const dirty = Boolean(draft && studio.project && JSON.stringify([draft.settings, draft.variants]) !== JSON.stringify([studio.project.settings, studio.project.variants]));
  useUnsavedChanges(dirty, "剪辑工程");
  const preview = output ? outputResourceUrl(output, "video_ref") : clip ? `${materialPreviewUrl(clip.material_id)}#t=${clip.source_start_ms / 1000},${clip.source_end_ms / 1000}` : "";

  const replaceDraft = (apply: (current: EditProject) => EditProject) => setDraft((current) => current ? apply(current) : current);
  const updateClip = (changes: Partial<EditClip>) => replaceDraft((current) => ({ ...current, variants: current.variants.map((item) => item.id !== variant?.id ? item : { ...item, latest_output_id: null, clips: item.clips.map((entry) => entry.id === clip?.id ? { ...entry, ...changes } : entry) }) }));
  const updateSettings = (changes: Partial<EditSettings>) => replaceDraft((current) => ({ ...current, settings: { ...current.settings, ...changes } }));
  const moveClip = (id: string, direction: -1 | 1) => replaceDraft((current) => ({ ...current, variants: current.variants.map((item) => {
    if (item.id !== variant?.id) return item;
    const clips = [...item.clips]; const from = clips.findIndex((entry) => entry.id === id); const to = from + direction;
    if (from < 0 || to < 0 || to >= clips.length) return item;
    const moved = clips[from]; const displaced = clips[to];
    if (!moved || !displaced) return item;
    clips[from] = displaced; clips[to] = moved; return resequenceClips(item, clips);
  }) }));

  const create = (event: FormEvent) => {
    event.preventDefault();
    if (!scriptId || !actor.trim()) return;
    guardUnsavedTransition(() => {
      void studio.create(scriptId, actor.trim()).then((result) => { if (result) { setShowCreate(false); setShowScriptEditor(true); } });
    });
  };
  const uploadAudio = async (event: FormEvent) => {
    event.preventDefault(); if (!audioFile || !audioActor.trim() || !audioName.trim() || !licenseNote.trim()) return;
    const result = await studio.uploadAudio({ kind: "bgm", name: audioName.trim(), license_note: licenseNote.trim(), imported_by: audioActor.trim(), audio: audioFile });
    if (result) { setAudioFile(null); setAudioName(""); setLicenseNote(""); setShowAudioImport(false); }
  };
  const render = async () => {
    if (!draft || !variant) return;
    const result = await studio.render(draft, variant.id);
    if (result) setShowRenderConfirm(false);
  };
  const save = async () => {
    if (!draft || !actor.trim()) return;
    const saved = await studio.save(draft, actor.trim());
    if (saved) setDraft(cloneProject(saved));
  };

  return <div className="content editing-content">
    {showScriptEditor ? <WorkspaceHeader
      stage="S7 · 剪辑工作台"
      title="成片制作台"
      description="在本机完成时间线、竖屏裁切、字幕、音效、BGM、降噪和色彩；逐段可修改，成片须人工验收。"
      current={<><strong>当前工程</strong><span>{draft ? `${studio.eligibleScripts.find((item) => item.script_id === draft.script_id)?.product_name ?? draft.project_id} · 修订 ${draft.revision}` : "尚未选择"}</span></>}
      metrics={[
        { label: "可剪脚本", value: studio.readiness.eligible_scripts },
        { label: "渲染中", value: studio.readiness.pending_renders, tone: studio.readiness.pending_renders ? "warning" : "default" },
        { label: "待验收", value: studio.readiness.outputs_waiting_review, tone: studio.readiness.outputs_waiting_review ? "warning" : "success" },
      ]}
      action={<button className="primary-button" type="button" onClick={() => setShowCreate(true)}>建立剪辑工程</button>}
    /> : <header className="creator-heading"><div><h1 data-page-title tabIndex={-1}>实拍成片审核</h1><p>逐版核对画面、原声和字幕。审核通过后自动进入成片素材库。</p></div></header>}
    {studio.actionMessage && <div className="action-message" role="status" aria-live="polite">{studio.actionMessage}</div>}
    <div className="secondary-action-row editing-view-switch"><button className="secondary-button" aria-pressed={!showScriptEditor} onClick={() => guardUnsavedTransition(() => setShowScriptEditor(false))}>实拍成片审核</button><button className="secondary-button" aria-pressed={showScriptEditor} onClick={() => guardUnsavedTransition(() => setShowScriptEditor(true))}>脚本剪辑</button></div>
    {!showScriptEditor && <FootageBatchPanel />}
    <ModalDialog open={showCreate} title="建立剪辑工程" description="从素材已配齐的脚本建立工程；同一脚本持续修订，不重复创建。" onClose={() => setShowCreate(false)}>
      <form className="edit-start-panel modal-form compact-create-form" onSubmit={(event) => void create(event)}><label>脚本<select value={scriptId} onChange={(event) => setScriptId(event.target.value)}><option value="">选择待剪脚本</option>{studio.eligibleScripts.map((item) => <option key={item.script_id} value={item.script_id} disabled={item.shooting_status !== "ready_for_edit"}>{item.product_name} · {item.shooting_status === "ready_for_edit" ? item.project_id ? `拍摄版“${item.selected_version_name}”已有工程` : `拍摄版“${item.selected_version_name}”` : `缺 ${item.missing_count} 段主素材`}</option>)}</select></label><button className="primary-button" type="submit" disabled={studio.isSubmitting || !scriptId || !actor.trim()}>建立剪辑工程</button>{studio.readiness.eligible_scripts === 0 && <button className="text-button" type="button" onClick={openMaterials}>去素材中心完成匹配</button>}</form>
    </ModalDialog>
    {showScriptEditor && <><section className="editing-workbench workbench-surface"><EditProjectSidebar projects={studio.projects} selectedId={studio.selectedId} select={(id) => {
      if (studio.selectedId === id) return;
      guardUnsavedTransition(() => studio.selectProject(id));
    }} /><main className="edit-canvas">{draft && variant ? <>
      <header className="edit-canvas-head"><div><h2>{variant.name}</h2><p>脚本已选拍摄版 · 工程修订 {draft.revision} · 时长 {formatEditDuration(variant.duration_ms)}</p></div><div><button className="secondary-button" type="button" disabled={studio.isSubmitting || !dirty || !actor.trim()} onClick={() => void save()}>保存新版本</button><button className="primary-button" type="button" disabled={studio.isSubmitting || dirty} onClick={() => setShowRenderConfirm(true)}>{dirty ? "先保存再渲染" : "渲染这个版本"}</button></div></header>
      <div className="edit-variant-tabs" role="tablist">{draft.variants.map((item) => <button type="button" role="tab" aria-selected={item.id === variant.id} className={item.id === variant.id ? "edit-variant-active" : ""} key={item.id} onClick={() => {
        if (item.id === variant.id) return;
        runDraftTransition(() => { setVariantId(item.id); setClipId(item.clips[0]?.id ?? null); }, "preserves-draft");
      }}>{item.name}<small>{formatEditDuration(item.duration_ms)}</small></button>)}</div>
      <div className="edit-preview"><video key={preview} controls preload="metadata" src={preview} /><div><span>{output ? "最近成片" : "当前源片段预览"}</span><strong>{clip?.subtitle || variant.name}</strong></div></div>
      <EditTimeline variant={variant} selectedClipId={clip?.id ?? null} selectClip={setClipId} moveClip={moveClip} />
    </> : <div className="edit-canvas-empty"><span className="empty-symbol">空</span><h2>选择或建立一个剪辑工程</h2><p>当前拍摄版的主播长镜头配齐后，系统会自动排好时间线、字幕和可选细节覆盖。</p></div>}</main>{draft && <EditInspector clip={clip} settings={draft.settings} audio={studio.audio} updateClip={updateClip} updateSettings={updateSettings} />}</section>
    <div className="secondary-action-row"><button className="secondary-button" type="button" onClick={() => setShowAudioImport(true)}>导入有授权的 BGM</button><span>文件复制到本机托管目录，不经过中转站。</span></div>
    <ModalDialog open={showAudioImport} title="导入有授权的 BGM" description="请保留授权说明；文件将复制到本机托管目录。" onClose={() => setShowAudioImport(false)}><form className="audio-import-panel modal-form compact-create-form" onSubmit={(event) => void uploadAudio(event)}><label>名称<input value={audioName} onChange={(event) => setAudioName(event.target.value)} placeholder="如：轻快通勤" /></label><label>版权说明<input value={licenseNote} onChange={(event) => setLicenseNote(event.target.value)} placeholder="如：品牌自有授权" /></label><label>音频<FileDropInput type="file" accept="audio/mpeg,audio/wav,audio/mp4,audio/aac,audio/flac,audio/ogg" onChange={(event) => setAudioFile(event.target.files?.[0] ?? null)} /></label><button className="primary-button" type="submit" disabled={studio.isSubmitting || !audioFile || !audioName.trim() || !licenseNote.trim() || !audioActor.trim()}>导入 BGM</button></form></ModalDialog>
    <ConfirmationDialog
      open={showRenderConfirm}
      title="确认开始本机渲染"
      description="渲染会占用本机 CPU、显卡和磁盘；任务进入队列后可在下方查看进度，意外中断可恢复。"
      confirmLabel="确认并开始渲染"
      busy={studio.isSubmitting}
      details={draft && variant && <><strong>{variant.name}</strong><span>{formatEditDuration(variant.duration_ms)} · 工程修订 {draft.revision}</span></>}
      onClose={() => setShowRenderConfirm(false)}
      onConfirm={() => void render()}
    />
    <RenderQueuePanel tasks={studio.tasks} busy={studio.isSubmitting} retry={(id) => void studio.retry(id)} />
    {draft && <FinishedVideoPanel outputs={studio.outputs} variants={draft.variants} busy={studio.isSubmitting} review={(...args) => void studio.review(...args)} />}</>}
  </div>;
}
