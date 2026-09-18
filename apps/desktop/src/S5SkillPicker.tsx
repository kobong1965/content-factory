import { useMemo, useState } from "react";

import type { ScriptSkillSummary } from "./scripts";
import { formatSkillTimecode } from "./viralSkills";

function sourceRange(source: ScriptSkillSummary["representative_sources"][number]): string {
  if (source.start_ms === null) return "时间码待补";
  return `${formatSkillTimecode(source.start_ms)}—${formatSkillTimecode(source.end_ms ?? source.start_ms)}`;
}

export function S5SkillPicker({ skills, selectedSkillId, onChange }: {
  skills: readonly ScriptSkillSummary[];
  selectedSkillId: string;
  onChange: (skillId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const selected = skills.find((item) => item.skill_id === selectedSkillId) ?? null;
  const visible = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return skills;
    return skills.filter((skill) => [
      skill.name,
      skill.mechanism,
      ...skill.representative_sources.map((source) => `${source.source_name} ${source.dialogue ?? ""} ${source.action ?? ""}`),
    ].join(" ").toLocaleLowerCase().includes(normalized));
  }, [query, skills]);

  return <section className="s5-skill-picker" aria-labelledby="s5-skill-picker-title">
    <div className="skill-picker-heading">
      <div><h3 id="s5-skill-picker-title">选择已批准的爆点 Skill</h3><p>只显示 S3 中明确批准为“可用于脚本”且仍在启用状态的 Skill。</p></div>
      <span>{skills.length} 个可用</span>
    </div>
    <label className="skill-picker-search" htmlFor="s5-skill-search">搜索 Skill<input id="s5-skill-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索机制、来源视频或原话" /></label>
    {visible.length === 0 ? <div className="mini-empty">{skills.length === 0 ? "尚无已批准的可复用 Skill。" : "没有匹配搜索条件的 Skill。"}</div> : <div className="s5-skill-options" role="radiogroup" aria-label="已批准的爆点 Skill">{visible.map((skill) => <label className={selectedSkillId === skill.skill_id ? "s5-skill-option selected" : "s5-skill-option"} key={skill.skill_id}>
      <input type="radio" name="script-skill" value={skill.skill_id} checked={selectedSkillId === skill.skill_id} onChange={() => onChange(skill.skill_id)} />
      <span className="s5-skill-option-copy">
        <strong>{skill.name}</strong>
        <small>{skill.mechanism}</small>
        <span className="s5-skill-stats"><span>{skill.distinct_video_count} 条不同视频</span><span>{skill.occurrence_count} 次证据</span><span>{skill.script_usage_count} 次脚本使用</span></span>
      </span>
    </label>)}</div>}

    {selected && <article className="selected-skill-brief" aria-live="polite">
      <header><div><span>已选 Skill · R{selected.skill_revision}</span><h4>{selected.name}</h4></div><b>{selected.distinct_video_count > 1 ? "多视频共同" : "单视频候选"}</b></header>
      <p>{selected.mechanism}</p>
      <ol>{selected.steps.map((step) => <li key={step.id}><span>{step.order}</span>{step.description}</li>)}</ol>
      <div className="selected-skill-sources">
        <strong>代表来源</strong>
        {selected.representative_sources.length === 0 ? <span>来源摘要待补</span> : selected.representative_sources.map((source, index) => <p key={`${source.video_id}-${source.start_ms}-${index}`}><span>{source.source_name} · {sourceRange(source)}</span><q>{source.dialogue || "无可引用原话"}</q><small>{source.action || "未单独标注动作"}</small></p>)}
      </div>
      <small className="skill-causality-copy">内容相关证据，不代表已证明抖音算法因果。生成时会冻结该 Skill 版本与来源。</small>
    </article>}
  </section>;
}
