import { useState, type FormEvent } from "react";
import type { ViralSkillCandidate, ViralSkillView } from "@content-factory/contracts";

import type { SkillCandidateApprovalInput, SkillStatusInput } from "./viralSkills";

export function skillReviewDraftKey(candidate: Pick<ViralSkillCandidate, "candidate_id">): string {
  // A revision reload for the same candidate must not remount the form and
  // erase the operator's draft after an optimistic-concurrency conflict.
  return candidate.candidate_id;
}

export function ViralSkillReviewForm({
  candidate, skill, busy, conflictMessage, isReloadingLatest,
  approve, changeStatus, reloadLatest,
}: {
  candidate: ViralSkillCandidate;
  skill: ViralSkillView | null;
  busy: boolean;
  conflictMessage: string | null;
  isReloadingLatest: boolean;
  approve: (input: SkillCandidateApprovalInput) => Promise<boolean>;
  changeStatus: (input: SkillStatusInput) => Promise<boolean>;
  reloadLatest: () => Promise<boolean>;
}) {
  const reviewer = "本机";
  const [name, setName] = useState(skill?.name ?? candidate.suggested_name);
  const [mechanism, setMechanism] = useState(skill?.mechanism ?? candidate.suggested_mechanism);
  const [reuseMode, setReuseMode] = useState<"reuse" | "avoid">(skill?.reuse_mode ?? (candidate.suggested_reuse_mode === "reuse" ? "reuse" : "avoid"));
  const [note, setNote] = useState("");
  const statusReviewer = "本机";
  const [statusNote, setStatusNote] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || conflictMessage) return;
    const saved = await approve({
      expected_candidate_revision: candidate.revision,
      expected_skill_revision: skill?.revision ?? null,
      reviewer: reviewer.trim(),
      reuse_mode: reuseMode,
      name: name.trim(),
      mechanism: mechanism.trim(),
      note: note.trim() || null,
    });
    if (saved) {
      setName(name.trim());
      setMechanism(mechanism.trim());
      setNote("");
    }
  }

  async function submitStatus(status: "approved" | "disabled") {
    if (!skill || busy || conflictMessage) return;
    const saved = await changeStatus({
      expected_revision: skill.revision,
      status,
      reviewer: statusReviewer.trim(),
      note: statusNote.trim() || null,
    });
    if (saved) {
      setStatusNote("");
    }
  }

  const updateAvailable = candidate.linked_skill?.update_available === true;
  const submitLabel = reuseMode === "reuse"
    ? skill ? updateAvailable ? "核对新证据并更新 Skill" : "保存 Skill 修订" : "批准并用于脚本"
    : skill ? "保存为避坑 Skill" : "收录为避坑案例";
  const sourceUnavailable = !candidate.active
    || candidate.linked_skill?.source_status === "inactive"
    || candidate.linked_skill?.source_status === "missing";
  const controlsBusy = busy || isReloadingLatest;
  // The backend rejects every re-review of an inactive/missing candidate,
  // including converting it to an avoid-only Skill. Keep the form aligned
  // with that contract; an existing formal Skill can still be disabled below.
  const reviewControlsDisabled = controlsBusy || sourceUnavailable;

  return <aside className="viral-skill-review" aria-labelledby="skill-review-title">
    <form onSubmit={(event) => void submit(event)}>
      <div className="panel-heading"><h3 id="skill-review-title">人工确认</h3><span>{skill ? `正式 Skill R${skill.revision}` : "候选尚未入库"}</span></div>
      {conflictMessage && <div className="skill-conflict-recovery" role="alert">
        <strong>检测到其他窗口或任务已经更新</strong>
        <p>{conflictMessage}</p>
        <button className="secondary-button" type="button" disabled={isReloadingLatest} onClick={() => void reloadLatest()}>
          {isReloadingLatest ? "正在重新载入…" : "重新载入最新证据与修订"}
        </button>
      </div>}
      {sourceUnavailable && <p className="skill-causality-note" role="alert">{skill?.eligibility.reason ?? "这个候选的来源已失效，不能重新批准。"}</p>}

      <fieldset>
        <legend>收录方式</legend>
        <label><input type="radio" name="skill-reuse-mode" disabled={reviewControlsDisabled} checked={reuseMode === "reuse"} onChange={() => setReuseMode("reuse")} />
          <span><strong>可用于脚本</strong><small>S5 可选，必须有原话、动作和时间码证据</small></span>
        </label>
        <label><input type="radio" name="skill-reuse-mode" disabled={reviewControlsDisabled} checked={reuseMode === "avoid"} onChange={() => setReuseMode("avoid")} />
          <span><strong>避坑案例</strong><small>保留失效经验，但不进入脚本选择器</small></span>
        </label>
      </fieldset>
      <label htmlFor="skill-name">Skill 名称<input id="skill-name" name="skill-name" autoComplete="off" disabled={reviewControlsDisabled} required maxLength={160} value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label htmlFor="skill-mechanism">Skill 机制<textarea id="skill-mechanism" name="skill-mechanism" autoComplete="off" disabled={reviewControlsDisabled} required maxLength={2000} value={mechanism} onChange={(event) => setMechanism(event.target.value)} /></label>
      <label htmlFor="skill-review-note">审核备注<textarea id="skill-review-note" name="skill-review-note" autoComplete="off" disabled={reviewControlsDisabled} maxLength={2000} value={note} onChange={(event) => setNote(event.target.value)} placeholder="记录为什么可复用，或应避免什么" /></label>
      <button className="primary-button" type="submit" disabled={Boolean(conflictMessage) || reviewControlsDisabled || !reviewer.trim() || !name.trim() || !mechanism.trim()}>{busy ? "正在保存…" : submitLabel}</button>
    </form>

    {skill && <section className="skill-status-control" aria-labelledby="skill-status-title">
      <h4 id="skill-status-title">启用状态</h4>
      <p>{skill.status === "approved" ? "当前已启用。停用后历史证据仍保留，S5 不再提供它。" : "当前已停用。重新启用前请核对来源证据。"}</p>

      <label htmlFor="skill-status-note">状态备注<textarea id="skill-status-note" name="skill-status-note" autoComplete="off" disabled={controlsBusy} value={statusNote} onChange={(event) => setStatusNote(event.target.value)} placeholder={skill.status === "approved" ? "停用时必须填写原因" : "可填写重新启用依据"} /></label>
      <button className={skill.status === "approved" ? "danger-button" : "secondary-button"} type="button" disabled={Boolean(conflictMessage) || controlsBusy || !statusReviewer.trim() || (skill.status === "approved" && !statusNote.trim()) || (skill.status === "disabled" && skill.source_status !== "current")} onClick={() => void submitStatus(skill.status === "approved" ? "disabled" : "approved")}>
        {skill.status === "approved" ? "停用这个 Skill" : "重新启用 Skill"}
      </button>
    </section>}
  </aside>;
}
