import { useEffect, useState } from "react";
import type { MaterialAsset, MaterialClip, MaterialPurpose, MaterialUsage } from "@content-factory/contracts";

import { materialKeyframeUrl, materialProxyUrl, purposeLabels, recognitionLabels } from "./materials";
import { guardUnsavedTransition, useUnsavedChanges } from "./unsavedChanges";

const purposes = Object.keys(purposeLabels) as MaterialPurpose[];
const captureRoleLabels = {
  host_take: "主播连续长镜头",
  detail: "同款商品细节",
  standard: "普通素材",
} as const;

export function MaterialEditor({ material, usage, busy, gatewayConfigured, save, recognize }: {
  material: MaterialAsset; usage: readonly MaterialUsage[]; busy: boolean; gatewayConfigured: boolean;
  save: (draft: MaterialAsset, actor: string) => void; recognize: () => void;
}) {
  const [draft, setDraft] = useState(material);
  const actor = "本机";
  useEffect(() => setDraft(material), [material]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(material);
  useUnsavedChanges(dirty, "素材标签与归档");

  const patchArchive = (field: keyof MaterialAsset["archive"], value: string) => {
    setDraft({ ...draft, archive: { ...draft.archive, [field]: value } });
  };
  const patchClip = (clipId: string, changes: Partial<MaterialClip>) => {
    setDraft({ ...draft, clips: draft.clips.map((clip) => clip.id === clipId ? { ...clip, ...changes } : clip) });
  };
  const tags = (value: string) => [...new Set(value.split(/[，,]/).map((item) => item.trim()).filter(Boolean))];

  return <article className="material-editor">
    <header className="material-editor-head">
      <div><h2>{material.file.original_name}</h2><p>{captureRoleLabels[material.capture_role ?? "standard"]} · 修订 {material.revision} · {material.file.width}×{material.file.height} · {(material.file.duration_ms / 1000).toFixed(1)} 秒 · {material.clips.length} 个片段</p></div>
      <div><span className={`recognition-badge recognition-${material.processing.recognition_status}`}>{recognitionLabels[material.processing.recognition_status]}</span><button className="secondary-button" type="button" disabled={busy || !gatewayConfigured} onClick={() => guardUnsavedTransition(recognize)}>重新识别关键帧</button></div>
    </header>
    <video className="material-player" controls preload="metadata" src={materialProxyUrl(material.material_id)} />
    <section className="material-archive" aria-labelledby="archive-title">
      <h3 id="archive-title">归档信息</h3>
      <label>模特<input value={draft.archive.model_name} onChange={(event) => patchArchive("model_name", event.target.value)} /></label>
      <label>场景<input value={draft.archive.scene} onChange={(event) => patchArchive("scene", event.target.value)} /></label>
      <label>拍摄日期<input type="date" value={draft.archive.shot_date} onChange={(event) => patchArchive("shot_date", event.target.value)} /></label>
      <label>拍摄批次<input value={draft.archive.batch} onChange={(event) => patchArchive("batch", event.target.value)} /></label>
      <label className="archive-note">素材备注<textarea value={draft.archive.note} onChange={(event) => patchArchive("note", event.target.value)} /></label>
    </section>
    <section className="clip-editor-list" aria-labelledby="clip-title">
      <div className="section-title-row"><h3 id="clip-title">逐片段标签</h3><p>AI 只看低清关键帧；你保存的人工修改优先。</p></div>
      {draft.clips.map((clip) => <article className="material-clip-card" key={clip.id}>
        <img src={materialKeyframeUrl(material.material_id, clip.id)} alt={`片段 ${clip.order} 关键帧`} />
        <div className="clip-fields">
          <header><strong>{clip.capture_scope === "full_take" ? "连续主片" : `片段 ${String(clip.order).padStart(2, "0")}`}</strong><span>{(clip.start_ms / 1000).toFixed(1)}s—{(clip.end_ms / 1000).toFixed(1)}s</span><b>质量 {clip.quality.overall}</b></header>
          <fieldset><legend>用途</legend>{purposes.map((purpose) => <label key={purpose}><input type="checkbox" checked={clip.purpose_tags.includes(purpose)} onChange={(event) => {
            const next = event.target.checked ? [...clip.purpose_tags, purpose] : clip.purpose_tags.filter((item) => item !== purpose);
            patchClip(clip.id, { purpose_tags: next.length ? next : ["broll"] });
          }} />{purposeLabels[purpose]}</label>)}</fieldset>
          <label>画面标签<input value={clip.visual_tags.join("，")} onChange={(event) => patchClip(clip.id, { visual_tags: tags(event.target.value) })} placeholder="如：正面展示，面料特写" /></label>
          <label>动作标签<input value={clip.action_tags.join("，")} onChange={(event) => patchClip(clip.id, { action_tags: tags(event.target.value) })} placeholder="如：自然站立，整理领口" /></label>
          <label>口播<textarea value={clip.transcript} onChange={(event) => patchClip(clip.id, { transcript: event.target.value })} /></label>
          <label>片段备注<textarea value={clip.note} onChange={(event) => patchClip(clip.id, { note: event.target.value })} /></label>
          <div className="clip-flags"><label><input type="checkbox" checked={clip.standalone_usable} onChange={(event) => patchClip(clip.id, { standalone_usable: event.target.checked })} />可单独使用</label><label><input type="checkbox" checked={clip.reusable} onChange={(event) => patchClip(clip.id, { reusable: event.target.checked })} />允许复用</label></div>
        </div>
      </article>)}
    </section>
    <footer className="material-savebar"><span>使用记录 {usage.filter((item) => item.action === "confirmed").length} 次 · 当前第 {material.revision} 版</span><button className="primary-button" type="button" disabled={busy || !actor.trim() || draft.clips.some((clip) => clip.purpose_tags.length === 0)} onClick={() => save(draft, actor)}>保存标签与归档</button></footer>
  </article>;
}
