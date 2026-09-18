import { useEffect, useState } from "react";
import type { MaterialSummary, S6ScriptOption, ShootingTask } from "@content-factory/contracts";
import { confirmedMaterialClipKeys, materialClipKey, materialKeyframeUrl, purposeLabels, shootingStatusLabels, suggestionSourceLabel } from "./materials";

export function ShootingTaskPanel({ tasks, scripts, materials, busy, confirm, release }: {
  tasks: readonly ShootingTask[]; scripts: readonly S6ScriptOption[];
  materials: readonly MaterialSummary[];
  busy: boolean;
  confirm: (scriptId: string, shotId: string, materialId: string, clipId: string, actor: string) => void;
  release: (scriptId: string, shotId: string, actor: string) => void;
}) {
  const [scriptId, setScriptId] = useState("");
  const actor = "本机";
  useEffect(() => { if (!scriptId && tasks[0]) setScriptId(tasks[0].script_id); }, [scriptId, tasks]);
  const task = tasks.find((item) => item.script_id === scriptId);
  const option = scripts.find((item) => item.script_id === scriptId);
  const usedMaterialClipKeys = confirmedMaterialClipKeys(task?.requirements ?? []);

  return <section className="shooting-panel" aria-labelledby="shooting-title">
    <header><div><h2 id="shooting-title">当前拍摄版的素材清单</h2><p>只处理脚本编导中选定的一版；一条主播连续长镜头可以满足这一版的多个口播段落，细节镜头可选。</p></div><div className="shooting-controls"><label>批准脚本<select value={scriptId} onChange={(event) => setScriptId(event.target.value)}><option value="">选择脚本</option>{scripts.map((item) => <option key={item.script_id} value={item.script_id}>{item.product_name} · {item.selected_version_name}</option>)}</select></label></div></header>
    {!task ? <div className="material-empty"><strong>还没有可处理的拍摄任务</strong><span>先在脚本编导中批准一份脚本。</span></div> : <>
      <div className={`shooting-state shooting-${task.status}`}><div><span>当前状态</span><strong>{shootingStatusLabels[task.status]}</strong></div><div><span>已匹配</span><strong>{task.matched_count}</strong></div><div><span>待补拍</span><strong>{task.missing_count}</strong></div><p>{option?.product_name} · 拍摄版“{option?.selected_version_name ?? task.selected_version_id}” · 脚本修订 {task.script_revision}</p></div>
      <ol className="requirement-list">{task.requirements.map((requirement) => <li key={requirement.script_shot_id}>
        <div className="requirement-copy"><span>{requirement.requirement_kind === "detail_overlay" ? "同款细节覆盖（可选）" : purposeLabels[requirement.purpose]}</span><strong>{requirement.visual}</strong><p>{requirement.voiceover}</p><small>{requirement.requirement_status === "optional" ? "可选镜头，不阻塞剪辑；未上传时保留主播主画面" : requirement.match_status === "confirmed" ? "已满足" : "未满足，会进入补拍清单"}</small></div>
        {requirement.confirmed_match ? <div className="confirmed-match"><strong>已确认片段</strong><span>{suggestionSourceLabel(materials, requirement.confirmed_match.material_id, requirement.confirmed_match.clip_id)}</span><small>重复风险：{requirement.confirmed_match.repeat_risk}</small><button className="text-button" type="button" disabled={busy || !actor.trim()} onClick={() => release(task.script_id, requirement.script_shot_id, actor)}>撤销匹配</button></div> : <div className="suggestion-list">
          {requirement.suggestions.length === 0 ? <p>暂无合适素材，需要补拍</p> : requirement.suggestions.map((suggestion) => {
            const alreadyUsed = usedMaterialClipKeys.has(materialClipKey(suggestion.material_id, suggestion.clip_id));
            const canReuse = alreadyUsed && suggestion.continuous_take_reusable === true && requirement.requirement_kind !== "detail_overlay";
            const unavailable = alreadyUsed && !canReuse;
            return <article key={`${suggestion.material_id}-${suggestion.clip_id}`}><img src={materialKeyframeUrl(suggestion.material_id, suggestion.clip_id)} alt={suggestionSourceLabel(materials, suggestion.material_id, suggestion.clip_id)} /><div><strong>{suggestionSourceLabel(materials, suggestion.material_id, suggestion.clip_id)}</strong><span>{suggestion.score} 分 · {suggestion.reason}</span><small>{canReuse ? "主播连续长镜头：可继续分配给本版下一段" : unavailable ? "已被本任务其他要求使用" : `重复风险：${suggestion.repeat_risk}`}</small></div><button className="secondary-button" type="button" disabled={busy || !actor.trim() || unavailable} onClick={() => confirm(task.script_id, requirement.script_shot_id, suggestion.material_id, suggestion.clip_id, actor)}>{canReuse ? "复用这条长镜头" : unavailable ? "已使用" : "确认使用"}</button></article>;
          })}
        </div>}
      </li>)}</ol>
    </>}
  </section>;
}
