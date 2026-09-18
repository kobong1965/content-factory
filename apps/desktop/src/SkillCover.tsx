import { runtimeApiBaseUrl } from './runtimeApi';
import { useState } from "react";
import type { ScriptSkillSummary } from "./scripts";

export function SkillCover({ skill, onOpen }: { skill: ScriptSkillSummary; onOpen: () => void }) {
  const [failed, setFailed] = useState(false);
  const source = skill.representative_sources[0];
  const url = source ? `${runtimeApiBaseUrl}/s5/skills/${encodeURIComponent(skill.skill_id)}/sources/${encodeURIComponent(source.video_id)}/cover` : null;
  return <button type="button" className="skill-cover" onClick={onOpen} aria-label={`查看 ${skill.name} 原素材`}>
    {url && !failed ? <img src={url} alt={`${skill.name} 原素材封面`} loading="lazy" decoding="async" onError={() => setFailed(true)} /> : <span>封面暂不可用</span>}
    <span className="skill-cover-caption">查看原素材</span>
  </button>;
}
