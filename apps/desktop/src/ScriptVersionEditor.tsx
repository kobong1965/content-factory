import { useState } from "react";
import type { ScriptShot, ScriptVersion } from "@content-factory/contracts";

import { formatScriptTime } from "./scripts";

const materialLabels = {
  required: "必须新拍",
  reusable: "可复用",
  reshoot: "需要补拍",
  optional: "可选",
} as const;

export function ScriptVersionEditor({
  version, factLabels, evidenceLabels, onChange,
}: {
  version: ScriptVersion;
  factLabels: Readonly<Record<string, string>>;
  evidenceLabels: Readonly<Record<string, string>>;
  onChange: (version: ScriptVersion) => void;
}) {
  const [view, setView] = useState<"shooting" | "editing">("shooting");
  const update = (changes: Partial<ScriptVersion>) => onChange({ ...version, ...changes });
  const updateShot = (shotId: string, changes: Partial<ScriptShot>) => update({
    shots: version.shots.map((shot) => shot.id === shotId ? { ...shot, ...changes } : shot),
  });
  const updateDelivery = (shot: ScriptShot, field: "tone" | "pacing" | "emphasis" | "pause", value: string) => {
    const current = shot.delivery ?? {
      tone: "像直播间真实推荐一样直接、可信",
      pacing: "开头稍快，卖点处放慢",
      emphasis: "重读本段核心卖点",
      pause: "关键词后自然停顿",
    };
    updateShot(shot.id, { delivery: { ...current, [field]: value } });
  };
  const updatePerformance = (
    shot: ScriptShot,
    field: "expression" | "eye_line" | "body_action" | "product_action",
    value: string,
  ) => {
    const current = shot.performance ?? {
      expression: "自然、可信",
      eye_line: "主体直视镜头，指向商品时短暂看商品",
      body_action: shot.action,
      product_action: "按口播同步展示商品",
    };
    updateShot(shot.id, { performance: { ...current, [field]: value } });
  };
  const updateDetail = (
    shot: ScriptShot, field: "mode" | "detail_tag" | "instruction", value: string | null,
  ) => {
    const current = shot.detail_overlay ?? { mode: "none" as const, detail_tag: null, instruction: "保持主播连续画面和原声" };
    updateShot(shot.id, { detail_overlay: { ...current, [field]: value } as ScriptShot["detail_overlay"] });
  };

  return <section className="script-version-editor" aria-labelledby={`script-version-${version.id}`}>
    <div className="script-version-summary">
      <label>版本名称<input value={version.name} onChange={(event) => update({ name: event.target.value })} /></label>
      <label>主钩子<textarea value={version.primary_hook} onChange={(event) => update({ primary_hook: event.target.value })} /></label>
      <label>与其他版本的差异<textarea value={version.differentiation} onChange={(event) => update({ differentiation: event.target.value })} /></label>
      <label>相似风险<select value={version.similarity_risk} onChange={(event) => update({ similarity_risk: event.target.value as ScriptVersion["similarity_risk"] })}><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label>
      <div className="script-version-meta"><span>总时长 <strong>{formatScriptTime(version.duration_ms)}</strong></span><span>镜头 <strong>{version.shots.length}</strong></span></div>
    </div>

    <div className="script-view-tabs" role="group" aria-label="脚本阅读视图"><button type="button" aria-pressed={view === "shooting"} onClick={() => setView("shooting")}>主播拍摄稿</button><button type="button" aria-pressed={view === "editing"} onClick={() => setView("editing")}>后期剪辑稿</button></div>
    <p>{view === "shooting" ? "话术、语气和动作同步执行；固定机位，一条长镜头录完。" : "与当前拍摄版逐段对应；细节覆盖仅限同款，保留主播连续原声。"}</p>
    <div className="script-shot-list">
      {version.shots.map((shot) => {
        const delivery = shot.delivery ?? { tone: "", pacing: "", emphasis: "", pause: "" };
        const performance = shot.performance ?? { expression: "", eye_line: "", body_action: shot.action, product_action: "" };
        const detail = shot.detail_overlay ?? { mode: "none" as const, detail_tag: null, instruction: "保持主播连续画面和原声" };
        return <article className="script-shot-card" key={shot.id}>
        <header>
          <div><span className="shot-order">{String(shot.order).padStart(2, "0")}</span><strong>口播段落 · {formatScriptTime(shot.start_ms)} — {formatScriptTime(shot.end_ms)}</strong></div>
          <span className={`material-state material-${shot.material_status}`}>{materialLabels[shot.material_status]}</span>
        </header>
        <div className="shot-time-grid">
          <label>开始（毫秒）<input type="number" min="0" value={shot.start_ms} onChange={(event) => updateShot(shot.id, { start_ms: Number(event.target.value) })} /></label>
          <label>结束（毫秒）<input type="number" min="1" value={shot.end_ms} onChange={(event) => updateShot(shot.id, { end_ms: Number(event.target.value) })} /></label>
          <label>素材状态<select value={shot.material_status} onChange={(event) => updateShot(shot.id, { material_status: event.target.value as ScriptShot["material_status"] })}>{Object.entries(materialLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        </div>
        <div className="shot-copy-grid">
          <label hidden={view !== "shooting"}>逐字口播<textarea aria-label="逐字口播" value={shot.voiceover} onChange={(event) => updateShot(shot.id, { voiceover: event.target.value })} /></label>
          <label hidden={view !== "editing"}>同步字幕<textarea aria-label="同步字幕" value={shot.subtitle} onChange={(event) => updateShot(shot.id, { subtitle: event.target.value })} /></label>
          <label>固定画面内发生什么<textarea value={shot.visual} onChange={(event) => updateShot(shot.id, { visual: event.target.value })} /></label>
          <label hidden={view !== "shooting"}>动作总指令<textarea value={shot.action} onChange={(event) => updateShot(shot.id, { action: event.target.value })} /></label>
        </div>
        <div className="structured-direction-grid delivery-grid" hidden={view !== "shooting"}>
          <label>语气<textarea value={delivery.tone} onChange={(event) => updateDelivery(shot, "tone", event.target.value)} placeholder="例如：像提醒朋友，直接可信" /></label>
          <label>语速与节奏<textarea value={delivery.pacing} onChange={(event) => updateDelivery(shot, "pacing", event.target.value)} placeholder="例如：开头稍快，证明处放慢" /></label>
          <label>重读词<textarea value={delivery.emphasis} onChange={(event) => updateDelivery(shot, "emphasis", event.target.value)} placeholder="写清需要加重的词" /></label>
          <label>停顿<textarea value={delivery.pause} onChange={(event) => updateDelivery(shot, "pause", event.target.value)} placeholder="写清在哪句话后停多久" /></label>
        </div>
        <div className="structured-direction-grid performance-grid" hidden={view !== "shooting"}>
          <label>表情<textarea value={performance.expression} onChange={(event) => updatePerformance(shot, "expression", event.target.value)} /></label>
          <label>目光<textarea value={performance.eye_line} onChange={(event) => updatePerformance(shot, "eye_line", event.target.value)} /></label>
          <label>身体动作<textarea value={performance.body_action} onChange={(event) => updatePerformance(shot, "body_action", event.target.value)} /></label>
          <label>商品动作<textarea value={performance.product_action} onChange={(event) => updatePerformance(shot, "product_action", event.target.value)} /></label>
        </div>
        <div className="shot-detail-grid">
          <label>景别<input value={shot.shot_size} onChange={(event) => updateShot(shot.id, { shot_size: event.target.value })} /></label>
          <label>机位（已锁定）<input value={shot.camera} readOnly aria-readonly="true" title="固定直播间铁律，不允许改为跟拍或移动机位" /></label>
          <label hidden={view !== "editing"}>音效<input value={shot.sound_effect} onChange={(event) => updateShot(shot.id, { sound_effect: event.target.value })} /></label>
          <label hidden={view !== "editing"}>BGM<input value={shot.bgm} onChange={(event) => updateShot(shot.id, { bgm: event.target.value })} /></label>
          <label>转场（已锁定）<input value={shot.transition} readOnly aria-readonly="true" title="连续长镜头铁律，口播段落之间不切镜" /></label>
        </div>
        <div className="detail-overlay-editor" hidden={view !== "editing"}>
          <label>是否覆盖同款细节<select value={detail.mode} onChange={(event) => {
            const mode = event.target.value as "none" | "optional_detail";
            updateShot(shot.id, { detail_overlay: mode === "none"
              ? { mode, detail_tag: null, instruction: "保持主播连续画面和原声" }
              : { mode, detail_tag: detail.detail_tag || "裤子细节", instruction: detail.instruction || "覆盖同款细节画面，保留主播连续原声" } });
          }}><option value="none">不覆盖，保留主播画面</option><option value="optional_detail">覆盖同款细节画面</option></select></label>
          <label>细节标签<input disabled={detail.mode === "none"} value={detail.detail_tag ?? ""} onChange={(event) => updateDetail(shot, "detail_tag", event.target.value || null)} placeholder="裤腰 / 口袋 / 走线" /></label>
          <label>覆盖说明<textarea disabled={detail.mode === "none"} value={detail.instruction} onChange={(event) => updateDetail(shot, "instruction", event.target.value)} /></label>
        </div>
        <footer>
          <span>机制步骤：<b>{shot.pattern_step_id}</b></span>
          <span>商品事实：{shot.fact_ids.length ? shot.fact_ids.map((id) => factLabels[id] ?? id).join("、") : "此镜头不说商品事实"}</span>
          <span className="script-evidence-list">爆点证据：{shot.evidence_ids?.length ? shot.evidence_ids.map((id) => evidenceLabels[id] ?? id).join("；") : "旧脚本未记录逐段证据，请重新生成"}</span>
          <span>原镜头参考：{shot.source_shot_id ?? "根据爆点重新编导"}</span>
        </footer>
      </article>;})}
    </div>
  </section>;
}
