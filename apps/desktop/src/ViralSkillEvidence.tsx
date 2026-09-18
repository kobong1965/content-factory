import type { ViralSkillCandidate, ViralSkillOccurrence, ViralSkillView } from "@content-factory/contracts";

import { formatSkillTimecode } from "./viralSkills";

function occurrenceRange(occurrence: ViralSkillOccurrence): string {
  if (occurrence.start_ms === null) return "时间码待补";
  const end = occurrence.end_ms === null ? occurrence.start_ms : occurrence.end_ms;
  return `${formatSkillTimecode(occurrence.start_ms)}—${formatSkillTimecode(end)}`;
}

function formalSkillState(skill: ViralSkillView | null): Readonly<{ className: string; label: string }> {
  if (!skill) return { className: "pending", label: "待批准" };
  if (skill.source_status === "inactive") return { className: "pending", label: "来源失效" };
  if (skill.source_status === "missing") return { className: "pending", label: "来源不可用" };
  if (skill.source_status === "updated") return { className: "pending", label: "证据待更新" };
  if (skill.status === "disabled") return { className: "disabled", label: "已停用" };
  return skill.reuse_mode === "reuse"
    ? { className: "approved", label: "可用于脚本" }
    : { className: "approved", label: "避坑案例" };
}

export function ViralSkillEvidence({ candidate, skill }: {
  candidate: ViralSkillCandidate;
  skill: ViralSkillView | null;
}) {
  // Approval must always inspect the latest candidate evidence. When a formal
  // Skill has update_available=true, its frozen occurrences intentionally lag.
  const occurrences = candidate.occurrences;
  const steps = candidate.steps;
  const state = formalSkillState(skill);

  return <div className="viral-skill-evidence">
    <section className="skill-mechanism" aria-labelledby="skill-mechanism-title">
      <div className="skill-detail-heading">
        <div>
          <span>{candidate.is_common ? "多视频共同候选" : "单视频候选"}</span>
          <h3 id="skill-mechanism-title">{skill?.name ?? candidate.suggested_name}</h3>
        </div>
        <span className={`skill-state-badge skill-state-${state.className}`}>
          {state.label}
        </span>
      </div>
      <p>{skill?.mechanism ?? candidate.suggested_mechanism}</p>
      <dl className="skill-detail-metrics">
        <div><dt>不同视频</dt><dd>{candidate.distinct_video_count}</dd></div>
        <div><dt>证据出现</dt><dd>{candidate.occurrence_count}</dd></div>
        <div><dt>证据等级</dt><dd>{candidate.evidence_level === "metric_correlation" ? "内容+数据关联" : candidate.evidence_level === "content_inference" ? "内容推断" : "内容观察"}</dd></div>
        <div><dt>版本</dt><dd>C{candidate.revision}{skill ? ` / S${skill.revision}` : ""}</dd></div>
      </dl>
      <p className="skill-causality-note" role="note">只能证明这些内容机制在来源素材中出现；没有平台内部数据时，不得表述为“被算法抓取”的因果。</p>
      {skill && !skill.eligibility.s5_eligible && <p className="skill-causality-note" role="alert">{skill.eligibility.reason}</p>}
    </section>

    <section className="skill-structure" aria-labelledby="skill-structure-title">
      <h4 id="skill-structure-title">可执行结构</h4>
      <ol>{steps.map((step) => <li key={`${step.order}-${step.description}`}><span>{step.order}</span><p>{step.description}</p></li>)}</ol>
      {(skill?.necessary_conditions ?? candidate.necessary_conditions).length > 0 && <div><strong>成立条件</strong><ul>{(skill?.necessary_conditions ?? candidate.necessary_conditions).map((item) => <li key={item}>{item}</li>)}</ul></div>}
      {(skill?.failure_signals ?? candidate.failure_signals).length > 0 && <div><strong>失效信号</strong><ul>{(skill?.failure_signals ?? candidate.failure_signals).map((item) => <li key={item}>{item}</li>)}</ul></div>}
    </section>

    <section className="skill-source-evidence" aria-labelledby="skill-source-title">
      <div className="panel-heading"><h4 id="skill-source-title">来源原话、动作与证据</h4><span>{occurrences.length} 次出现</span></div>
      {occurrences.length === 0 ? <div className="mini-empty">当前候选没有可展示的定位证据，不能批准为可复用 Skill。</div> : <div className="skill-occurrence-list">{occurrences.map((occurrence, index) => <details open={index === 0} key={occurrence.occurrence_id}>
        <summary>
          <span>{String(index + 1).padStart(2, "0")}</span>
          <strong>{occurrence.source_name}</strong>
          <time>{occurrenceRange(occurrence)}</time>
          <small>{occurrence.video_id}</small>
        </summary>
        <div className="skill-occurrence-body">
          <p className="skill-acceptance-trace">报告 R{occurrence.analysis_revision} · {occurrence.accepted_by} 已接受 · {occurrence.has_primary_evidence ? "已定位主证据" : "缺少主证据"}</p>
          {occurrence.steps.map((step) => <article key={`${occurrence.occurrence_id}-${step.order}`}>
            <header><span>第 {step.order} 步</span><strong>{step.description}</strong></header>
            {step.evidence.map((evidence) => <dl key={evidence.qualified_evidence_id}>
              <div><dt>时间</dt><dd>{evidence.start_ms === null ? "未定位" : `${formatSkillTimecode(evidence.start_ms)}—${formatSkillTimecode(evidence.end_ms ?? evidence.start_ms)}`}</dd></div>
              <div><dt>原话</dt><dd>{evidence.exact_dialogue || evidence.subtitle || "源报告未转写可引用原话"}</dd></div>
              <div><dt>动作</dt><dd>{evidence.action || evidence.visual_event || "源报告未单独标注动作"}</dd></div>
              <div><dt>证据</dt><dd>{evidence.claim}<small>{evidence.is_inference ? "推断" : "观察"} · 置信度 {Math.round(evidence.confidence * 100)}%</small></dd></div>
            </dl>)}
          </article>)}
        </div>
      </details>)}</div>}
    </section>
  </div>;
}
