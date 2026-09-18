import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ViralSkillReviewForm, skillReviewDraftKey } from "./ViralSkillReviewForm";

function candidate(overrides: Record<string, unknown> = {}) {
  return {
    candidate_id: "candidate_a",
    revision: 3,
    active: true,
    suggested_name: "结果同步展示",
    suggested_mechanism: "口播和动作同步。",
    suggested_reuse_mode: "reuse",
    linked_skill: null,
    ...overrides,
  } as never;
}

describe("ViralSkillReviewForm conflict recovery", () => {
  it("keeps one draft identity across a same-candidate revision reload", () => {
    expect(skillReviewDraftKey(candidate({ revision: 3 }))).toBe(
      skillReviewDraftKey(candidate({ revision: 4 })),
    );
  });

  it("shows an explicit reload action and blocks stale resubmission after HTTP 409", () => {
    const markup = renderToStaticMarkup(<ViralSkillReviewForm
      candidate={candidate()}
      skill={null}
      busy={false}
      conflictMessage="服务器上的修订已变化；当前填写内容已保留。"
      isReloadingLatest={false}
      approve={async () => false}
      changeStatus={async () => false}
      reloadLatest={async () => true}
    />);

    expect(markup).toContain("当前填写内容已保留");
    expect(markup).toContain("重新载入最新证据与修订");
    expect(markup).toMatch(/type="submit"[^>]*disabled/);
  });

  it("marks an inactive source and prevents approving it for reuse", () => {
    const staleCandidate = candidate({
      active: false,
      linked_skill: {
        skill_id: "skill_a",
        revision: 2,
        status: "approved",
        reuse_mode: "reuse",
        source_status: "inactive",
        update_available: true,
        eligibility: { s5_eligible: false, reason_code: "source_inactive", reason: "来源候选已失效" },
      },
    });
    const markup = renderToStaticMarkup(<ViralSkillReviewForm
      candidate={staleCandidate}
      skill={{
        skill_id: "skill_a",
        revision: 2,
        status: "approved",
        reuse_mode: "reuse",
        name: "结果同步展示",
        mechanism: "口播和动作同步。",
        source_status: "inactive",
        update_available: true,
        eligibility: { s5_eligible: false, reason_code: "source_inactive", reason: "来源候选已失效" },
      } as never}
      busy={false}
      conflictMessage={null}
      isReloadingLatest={false}
      approve={async () => false}
      changeStatus={async () => false}
      reloadLatest={async () => true}
    />);

    expect(markup).toContain("来源候选已失效");
    const reuseModeInputs = markup.match(/<input(?=[^>]*name="skill-reuse-mode")[^>]*>/g) ?? [];
    expect(reuseModeInputs).toHaveLength(2);
    expect(reuseModeInputs.every((input) => input.includes("disabled"))).toBe(true);
    expect(markup).toMatch(/type="submit"[^>]*disabled/);
  });
});
