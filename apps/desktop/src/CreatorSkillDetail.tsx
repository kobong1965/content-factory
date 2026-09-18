import { runtimeApiBaseUrl } from './runtimeApi';
import { useRef, useState } from "react";
import { ModalDialog } from "./ModalDialog";
import type { ScriptSkillSummary } from "./scripts";
import { formatSkillTimecode } from "./viralSkills";

export function parseStepTime(description: string): { start: number; end: number } | null {
  const m = description.match(/^(\d{2,}):(\d{2}(?:\.\d+)?)\s*[—–-]\s*(\d{2,}):(\d{2}(?:\.\d+)?)/);
  if (!m || Number(m[2]) >= 60 || Number(m[4]) >= 60) return null;
  const start = Number(m[1]) * 60 + Number(m[2]), end = Number(m[3]) * 60 + Number(m[4]);
  return end > start ? { start, end } : null;
}
export function activeStepIndex(steps: readonly { description: string }[], seconds: number): number {
  return steps.findIndex(step => { const range = parseStepTime(step.description); return range !== null && seconds >= range.start && seconds < range.end; });
}

export function CreatorSkillDetail({ skill, onClose, onSelect }: {
  skill: ScriptSkillSummary; onClose: () => void; onSelect: () => void;
}) {
  const [sourceIndex, setSourceIndex] = useState(0);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const source = skill.representative_sources[sourceIndex];
  // A multi-source method has no unambiguous per-step source binding yet.
  const synced = skill.distinct_video_count === 1;
  const active = synced ? activeStepIndex(skill.steps, time) : -1;
  function seek(seconds: number) { if (video.current && duration > 0) { video.current.currentTime = Math.min(duration, Math.max(0, seconds)); setTime(video.current.currentTime); } }
  return <ModalDialog open className="skill-playback-dialog" title={skill.name} description={`Skill R${skill.skill_revision} · ${skill.distinct_video_count} 条来源视频 · ${skill.occurrence_count} 次证据`} onClose={onClose}
    footer={<><span>观察与推断不代表平台算法因果。</span><button type="button" className="secondary-button" onClick={onClose}>返回</button><button type="button" className="primary-button" onClick={onSelect}>选用这个 Skill</button></>}>
    <div className="creator-skill-detail">
      <section className="creator-evidence"><h3>来源与时间定位</h3>
        {source ? <><strong>{source.source_name}</strong><video key={source.video_id} ref={video} aria-label="Skill 原素材" controls playsInline preload="metadata" src={`${runtimeApiBaseUrl}/s5/skills/${encodeURIComponent(skill.skill_id)}/sources/${encodeURIComponent(source.video_id)}/media`} onLoadedMetadata={e => { setDuration(Number.isFinite(e.currentTarget.duration) ? e.currentTarget.duration : 0); setError(false); }} onTimeUpdate={e => setTime(e.currentTarget.currentTime)} onError={() => setError(true)} />
          {error && <p role="alert">原素材暂时无法播放。请检查本机服务和原片/播放代理是否完整；不会用其他视频替代。</p>}
          <label>素材时间线<input aria-label="素材时间线" type="range" min={0} max={duration || 1} step={0.01} value={time} disabled={!duration} onChange={e => seek(Number(e.target.value))} /></label><small>{formatSkillTimecode(time * 1000)} / {formatSkillTimecode(duration * 1000)}</small>
          {source.start_ms != null && <button className="secondary-button" disabled={!duration} onClick={() => seek(source.start_ms! / 1000)}>定位证据 {formatSkillTimecode(source.start_ms)}</button>}
        </> : <p>暂无绑定原素材，请先补充来源视频。</p>}
        <div className="creator-source-tabs" aria-label="来源片段">{skill.representative_sources.map((item, index) => <button key={`${item.video_id}-${index}`} type="button" aria-pressed={index === sourceIndex} onClick={() => { if (item.video_id === source?.video_id) { setSourceIndex(index); if (item.start_ms != null) seek(item.start_ms / 1000); return; } video.current?.pause(); setSourceIndex(index); setTime(0); setDuration(0); setError(false); }}>{item.start_ms != null ? formatSkillTimecode(item.start_ms) : `来源 ${index + 1}`}<small>{item.source_name}</small></button>)}</div>
        <h3>原话</h3><blockquote>{source?.dialogue || "来源未标注原话"}</blockquote><h3>同步动作</h3><p>{source?.action || "来源未单独标注动作"}</p>
      </section>
      <section className="creator-chapters" aria-label="脚本章节"><h3>方法简介</h3><p>{skill.mechanism}</p><h3>脚本结构</h3><p>{synced ? "播放时高亮当前步骤；点击整段可定位。右侧滚动不移动左侧视频。" : "多来源方法尚未逐步绑定原片，暂不提供步骤跳转，避免错配。"}</p><ol>{skill.steps.map((step, index) => { const range = synced ? parseStepTime(step.description) : null; return <li className={index === active ? "is-playing" : ""} key={step.id} aria-current={index === active ? "step" : undefined}>{range ? <button type="button" className="skill-step-button" aria-label={`${formatSkillTimecode(range.start * 1000)} 播放此段`} disabled={!duration} onClick={() => seek(range.start)}><span className="skill-step-heading"><strong>第 {step.order} 步</strong><span>{formatSkillTimecode(range.start * 1000)} 播放此段</span></span><span className="skill-step-copy">{step.description}</span></button> : <div className="skill-step-static"><strong>第 {step.order} 步</strong><p>{step.description}</p></div>}</li>; })}</ol><h3>适用条件</h3><ul>{skill.necessary_conditions.map((item, i) => <li key={i}>{item}</li>)}</ul><h3>注意事项</h3><ul>{skill.failure_signals.map((item, i) => <li key={i}>{item}</li>)}</ul></section>
    </div>
  </ModalDialog>;
}
