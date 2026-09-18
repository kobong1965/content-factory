import { useMemo, useState } from "react";
import type { ViralSkillLink } from "@content-factory/contracts";

import { useS3Skills } from "./useS3Skills";
import {
  filterSkillCandidates,
  type SkillCandidateCommonalityFilter,
  type SkillCandidateStatusFilter,
} from "./viralSkills";
import { ViralSkillEvidence } from "./ViralSkillEvidence";
import { ViralSkillReviewForm, skillReviewDraftKey } from "./ViralSkillReviewForm";

function linkedStatusLabel(link: ViralSkillLink): string {
  if (link.source_status === "inactive") return "来源失效";
  if (link.source_status === "missing") return "来源不可用";
  if (link.source_status === "updated") return "证据待更新";
  if (link.status === "disabled") return "已停用";
  return link.reuse_mode === "reuse" ? "可用于脚本" : "避坑案例";
}

export function ViralSkillLibrary() {
  const library = useS3Skills();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<SkillCandidateStatusFilter>("all");
  const [commonality, setCommonality] = useState<SkillCandidateCommonalityFilter>("all");
  const visibleCandidates = useMemo(() => filterSkillCandidates(library.candidates, {
    query, status, commonality,
  }), [commonality, library.candidates, query, status]);

  return <section className="viral-skill-library workbench-surface" aria-labelledby="viral-skill-library-title">
    <header className="viral-skill-library-header">
      <div>
        <span className="section-kicker">VIRAL POINT SKILLS</span>
        <h2 id="viral-skill-library-title">正式爆点 Skill 库</h2>
        <p>系统先从已接受报告生成候选；只有人工明确批准为“可用于脚本”后，才会进入 S5。</p>
      </div>
      <dl>
        <div><dt>待批准</dt><dd>{library.counts.pending}</dd></div>
        <div><dt>可复用</dt><dd>{library.counts.reusable}</dd></div>
        <div><dt>共同候选</dt><dd>{library.counts.common}</dd></div>
      </dl>
      <button className="secondary-button" type="button" disabled={library.isSubmitting} onClick={() => void library.reconcile()}>{library.isSubmitting ? "正在核对…" : "核对已接受报告"}</button>
    </header>

    {library.actionMessage && <div className={`action-message${library.actionMessageKind === "error" ? " action-message-error" : ""}`} role={library.actionMessageKind === "error" ? "alert" : "status"} aria-live={library.actionMessageKind === "error" ? "assertive" : "polite"}>{library.actionMessage}</div>}

    <div className="viral-skill-tools">
      <label htmlFor="viral-skill-search">搜索 Skill<input id="viral-skill-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索机制、步骤或成立条件" /></label>
      <label htmlFor="viral-skill-status">收录状态<select id="viral-skill-status" value={status} onChange={(event) => setStatus(event.target.value as SkillCandidateStatusFilter)}>
        <option value="all">全部状态</option>
        <option value="pending">待批准</option>
        <option value="reusable">可用于脚本</option>
        <option value="avoid">避坑案例</option>
        <option value="disabled">已停用</option>
        <option value="update">有新证据待确认</option>
      </select></label>
      <label htmlFor="viral-skill-commonality">视频范围<select id="viral-skill-commonality" value={commonality} onChange={(event) => setCommonality(event.target.value as SkillCandidateCommonalityFilter)}>
        <option value="all">全部范围</option>
        <option value="common">两条及以上共同</option>
        <option value="single">单视频候选</option>
      </select></label>
      <span>{visibleCandidates.length} / {library.candidates.length}</span>
    </div>

    <div className="viral-skill-layout">
      <aside className="viral-skill-list" aria-label="Skill 候选列表">
        {library.isLoading && library.candidates.length === 0 ? <div className="mini-empty" aria-busy="true">正在读取 Skill 库…</div> : visibleCandidates.length === 0 ? <div className="mini-empty">没有符合当前筛选的 Skill 候选。</div> : <ul>{visibleCandidates.map((candidate) => <li key={candidate.candidate_id}>
          <button
            type="button"
            disabled={library.isSubmitting}
            className={library.selectedCandidateId === candidate.candidate_id ? "viral-skill-active" : undefined}
            aria-current={library.selectedCandidateId === candidate.candidate_id ? "true" : undefined}
            onClick={() => library.setSelectedCandidateId(candidate.candidate_id)}
          >
            <span className="skill-list-heading"><strong>{candidate.suggested_name}</strong>{candidate.is_common && <b>共同</b>}</span>
            <p>{candidate.suggested_mechanism}</p>
            <span className="skill-list-meta"><span>{candidate.distinct_video_count} 条视频</span><span>{candidate.occurrence_count} 次证据</span><span>{candidate.linked_skill ? linkedStatusLabel(candidate.linked_skill) : "待批准"}</span></span>
            {candidate.linked_skill?.update_available && <small>{candidate.linked_skill.eligibility.reason ?? "候选证据已变化，需重新确认"}</small>}
          </button>
        </li>)}</ul>}
      </aside>

      <div className="viral-skill-detail">
        {!library.selectedCandidateId ? <div className="mini-empty">选择一个 Skill 候选查看时间码、原话、动作和证据。</div> : library.detailState === "error" ? <div className="mini-empty skill-detail-load-error" role="alert">
          <strong>Skill 证据未能读取</strong>
          <p>{library.detailError ?? "本机服务暂时没有返回完整证据。"}</p>
          <button className="secondary-button" type="button" disabled={library.isReloadingDetail} onClick={() => void library.reloadSelectedDetail()}>
            {library.isReloadingDetail ? "正在重新读取…" : "重新读取这个 Skill"}
          </button>
        </div> : library.detailState !== "ready" || !library.candidate ? <div className="mini-empty" aria-busy="true">正在读取完整证据…</div> : <div className="viral-skill-detail-grid">
          <ViralSkillEvidence candidate={library.candidate} skill={library.skill} />
          <ViralSkillReviewForm
            key={skillReviewDraftKey(library.candidate)}
            candidate={library.candidate}
            skill={library.skill}
            busy={library.isSubmitting}
            conflictMessage={library.conflictMessage}
            isReloadingLatest={library.isReloadingDetail}
            approve={library.approve}
            changeStatus={library.changeStatus}
            reloadLatest={library.reloadSelectedDetail}
          />
        </div>}
      </div>
    </div>
  </section>;
}
