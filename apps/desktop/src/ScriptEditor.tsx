import { useEffect, useMemo, useState, type FormEvent } from "react";
import type { ScriptContentGoal, ScriptPackage, ScriptRevision, ScriptVersion } from "@content-factory/contracts";

import { ScriptReviewPanel } from "./ScriptReviewPanel";
import { ScriptVersionEditor } from "./ScriptVersionEditor";
import { downloadShootingScript } from "./scriptExport";
import { scriptGoalLabels, scriptReviewLabels, type ScriptContext } from "./scripts";
import { runDraftTransition, useUnsavedChanges } from "./unsavedChanges";

export function ScriptEditor({
  script, context, revisions, isSubmitting, onSave, onReview,
}: {
  script: ScriptPackage;
  context: ScriptContext;
  revisions: readonly ScriptRevision[];
  isSubmitting: boolean;
  onSave: (script: ScriptPackage, actor: string) => Promise<boolean>;
  onReview: (status: "approved" | "rejected", reviewer: string, note: string) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState(script);
  const [activeVersion, setActiveVersion] = useState(0);
  const actor = "本机";

  useEffect(() => {
    setDraft(script);
    setActiveVersion((index) => Math.min(index, script.versions.length - 1));
  }, [script]);

  const dirty = JSON.stringify(draft) !== JSON.stringify(script);
  useUnsavedChanges(dirty, "脚本修改");
  const selectedVersion = draft.versions[activeVersion];
  const shootingVersionId = draft.selected_version_id ?? draft.versions[0]?.id;
  const shootingVersion = draft.versions.find((version) => version.id === shootingVersionId) ?? draft.versions[0];
  const shootingShotIds = new Set(shootingVersion?.shots.map((shot) => shot.id) ?? []);
  const factLabels = useMemo(() => Object.fromEntries(context.product.facts.map((fact) => [fact.id, `${fact.label}：${fact.value}${fact.unit ? ` ${fact.unit}` : ""}`])), [context.product.facts]);
  const evidenceLabels = useMemo(() => Object.fromEntries((context.analysis.evidence ?? []).map((item) => [
    item.id,
    `${formatEvidenceTime(item.start_ms)}–${formatEvidenceTime(item.end_ms)} · ${item.claim}${item.is_inference ? "（推断）" : "（事实）"}`,
  ])), [context.analysis.evidence]);
  const updateVersion = (next: ScriptVersion) => setDraft({
    ...draft, versions: draft.versions.map((version, index) => index === activeVersion ? next : version),
  });
  const save = (event: FormEvent) => {
    event.preventDefault();
    if (actor.trim()) void onSave(draft, actor.trim());
  };

  return <form className="script-editor" onSubmit={save}>
    <header className="script-editor-head">
      <div><h2>{context.product.name}</h2><p>脚本修订 {draft.revision} · {context.pattern.name} · {scriptReviewLabels[draft.review.status]} · {draft.versions.length} 个爆点版本</p></div>
      <div className="editor-actions"><button className="primary-button" type="submit" disabled={!dirty || !actor.trim() || isSubmitting}>保存新版本</button></div>
    </header>

    <section className="script-brief">
      <label>内容目标<select value={draft.content_goal} onChange={(event) => setDraft({ ...draft, content_goal: event.target.value as ScriptContentGoal })}>{Object.entries(scriptGoalLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>目标人群<input value={draft.target_audience} onChange={(event) => setDraft({ ...draft, target_audience: event.target.value })} /></label>
      <div><span>机制原理</span><p>{context.pattern.mechanism}</p></div>
    </section>

    <section className="fixed-script-policy" aria-label="本条脚本固定拍摄方式">
      <strong>本条拍摄基线</strong>
      <span>1 名主播 · 固定直播间 · 固定机位 · 固定灯光 · 一条连续长镜头</span>
      <small>主播在画面内移动和展示商品；后期只允许覆盖同款裤腰、口袋、走线等细节画面，主播原声保持连续。</small>
    </section>

    <nav className="script-version-tabs" aria-label="脚本版本">
      {draft.versions.map((version, index) => <button key={version.id} type="button" className={`${activeVersion === index ? "script-version-active" : ""}${shootingVersionId === version.id ? " script-version-selected" : ""}`} aria-current={activeVersion === index ? "page" : undefined} onClick={() => {
        if (activeVersion === index) return;
        runDraftTransition(() => setActiveVersion(index), "preserves-draft");
      }}><span>版本 {String.fromCharCode(65 + index)}{shootingVersionId === version.id ? " · 当前拍摄版" : ""}</span><strong>{version.name}</strong><small>{version.shots.length} 个口播段落 · {version.similarity_risk === "low" ? "低相似" : version.similarity_risk === "medium" ? "中相似" : "高相似"}</small></button>)}
    </nav>

    {selectedVersion ? <>
      <div className="script-export-actions"><button type="button" className="secondary-button" disabled={dirty || isSubmitting} onClick={() => downloadShootingScript(context.product.name, script, selectedVersion)}>导出当前版主播拍摄稿</button><small>{dirty ? "请先保存修改，再导出。" : "导出包含话术、语气、停顿、表情与动作的 TXT；会标明审核状态。"}</small></div>
      <section className="shooting-version-choice" aria-live="polite">
        <div><strong>{shootingVersionId === selectedVersion.id ? "这版将进入拍摄与剪辑" : "当前正在查看备选版本"}</strong><p>软件只为一个版本匹配主播长镜头和细节素材，不再要求一次拍齐 3—5 个备选版本。</p></div>
        <button className={shootingVersionId === selectedVersion.id ? "secondary-button" : "primary-button"} type="button" disabled={shootingVersionId === selectedVersion.id} onClick={() => setDraft({ ...draft, selected_version_id: selectedVersion.id })}>{shootingVersionId === selectedVersion.id ? "已选为拍摄版" : "选择这版拍摄"}</button>
      </section>
      <ScriptVersionEditor version={selectedVersion} factLabels={factLabels} evidenceLabels={evidenceLabels} onChange={updateVersion} />
    </> : <div className="script-side-empty">脚本版本无法读取，请刷新后重试。</div>}

    <section className="production-plan">
      <h3>当前拍摄版一次录完</h3>
      <ol>{draft.shooting_order.map((group, index) => {
        const selectedCount = group.shot_ids.filter((id) => shootingShotIds.has(id)).length;
        return selectedCount ? <li key={`${group.scene}-${index}`}><strong>{group.scene}</strong><span>{group.equipment}</span><small>{selectedCount} 个连续口播段落</small></li> : null;
      })}</ol>
      <h3>当前拍摄版素材清单</h3>
      <ul>{draft.material_checklist.filter((item) => shootingShotIds.has(item.shot_id)).map((item) => <li key={item.shot_id}><b>{item.shot_id}</b><span>{item.notes}</span><em>{item.status}</em></li>)}</ul>
    </section>

    <ScriptReviewPanel script={script} context={context} revisions={revisions} dirty={dirty} isSubmitting={isSubmitting} onReview={onReview} />
  </form>;
}

function formatEvidenceTime(milliseconds: number): string {
  const totalSeconds = Math.max(0, milliseconds) / 1000;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${seconds.toFixed(1).padStart(4, "0")}`;
}
