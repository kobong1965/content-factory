import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { S5SkillPicker } from "./S5SkillPicker";
import type { ScriptSkillSummary } from "./scripts";

const skill: ScriptSkillSummary = {
  template_id: "skill_0123456789abcdef0123456789abcdef",
  skill_id: "skill_0123456789abcdef0123456789abcdef",
  skill_revision: 3,
  status: "approved",
  reuse_mode: "reuse",
  source_analysis_id: "analysis_a",
  source_analysis_revision: 5,
  pattern_id: "pattern_a",
  name: "原话同步上身证明",
  mechanism: "说出结果的同时用固定机位上身动作证明。",
  steps: [{ id: "step_a", order: 1, description: "先给结果，再同步展示" }],
  necessary_conditions: ["口播和动作同步"],
  failure_signals: ["只说不展示"],
  analysis_summary: "来源报告结论",
  fixture_data: false,
  distinct_video_count: 3,
  occurrence_count: 5,
  script_usage_count: 2,
  classification: "common_candidate",
  evidence_level: "content_observation",
  causality_status: "not_established",
  source_status: "current",
  update_available: false,
  eligibility: { s5_eligible: true, reason_code: "eligible", reason: null },
  representative_sources: [{
    source_name: "4月20日.mp4",
    video_id: "video_a",
    start_ms: 2300,
    end_ms: 4900,
    dialogue: "你看这个垂感",
    action: "双手拉平裤腿",
  }],
};

describe("S5 approved Skill picker", () => {
  it("keeps the selected Skill summary and representative evidence visible", () => {
    const markup = renderToStaticMarkup(<S5SkillPicker skills={[skill]} selectedSkillId={skill.skill_id} onChange={() => undefined} />);

    expect(markup).toContain('role="radiogroup"');
    expect(markup).toContain("已选 Skill · R3");
    expect(markup).toContain("3 条不同视频");
    expect(markup).toContain("5 次证据");
    expect(markup).toContain("4月20日.mp4 · 00:02.3—00:04.9");
    expect(markup).toContain("你看这个垂感");
    expect(markup).toContain("双手拉平裤腿");
  });
});
