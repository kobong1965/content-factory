import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CreatorSkillDetail, activeStepIndex, parseStepTime } from "./CreatorSkillDetail";
import type { ScriptSkillSummary } from "./scripts";

const skill: ScriptSkillSummary = {
  template_id: "skill_test", skill_id: "skill_test", skill_revision: 1,
  status: "approved", reuse_mode: "reuse", source_analysis_id: "analysis_test",
  source_analysis_revision: 1, pattern_id: "pattern_test", name: "4.20 J85",
  mechanism: "先看局部，再展示整裤。",
  steps: [
    { id: "step_1", order: 1, description: "00:00.000—00:07.800 细节先入眼" },
    { id: "step_2", order: 2, description: "00:07.800—00:12.000 退后展示整裤" },
  ],
  necessary_conditions: ["固定机位"], failure_signals: ["不得跨款复用"],
  analysis_summary: "仅为观察", fixture_data: false, distinct_video_count: 2,
  occurrence_count: 2, script_usage_count: 0, classification: "common_candidate",
  evidence_level: "content_observation", causality_status: "not_established",
  source_status: "current", update_available: false,
  eligibility: { s5_eligible: true, reason_code: "eligible", reason: null },
  representative_sources: [
    { source_name: "4月20日.mp4", video_id: "video_a", start_ms: 0, end_ms: 12000, dialogue: null, action: "展示整裤" },
    { source_name: "另一条.mp4", video_id: "video_b", start_ms: 2300, end_ms: 4900, dialogue: null, action: "腰头细节" },
  ],
};

describe("Skill source playback contract", () => {
  it("renders the real source video endpoint with controls and never autoplays", () => {
    const markup = renderToStaticMarkup(<CreatorSkillDetail skill={skill} onClose={() => undefined} onSelect={() => undefined} />);
    expect(markup).toMatch(/<video\b[^>]*aria-label="Skill 原素材"/);
    expect(markup).toContain("/s5/skills/skill_test/sources/video_a/media");
    expect(markup).toMatch(/<video\b[^>]*controls/);
    expect(markup).not.toMatch(/<video\b[^>]*autoplay/i);
    expect(markup).not.toContain("尚未接入原视频播放");
  });

  it("does not substitute an unrelated demo video when no source exists", () => {
    const markup = renderToStaticMarkup(<CreatorSkillDetail skill={{ ...skill, representative_sources: [] }} onClose={() => undefined} onSelect={() => undefined} />);
    expect(markup).not.toContain("<video");
    expect(markup).not.toContain("/sources/undefined/");
  });

  it("preserves millisecond boundaries as seconds for video seeking", () => {
    expect(parseStepTime("00:07.800—00:12.000 退后展示整裤")).toEqual({ start: 7.8, end: 12 });
    expect(parseStepTime("01:02.250—01:09.500 展示后袋")).toEqual({ start: 62.25, end: 69.5 });
  });

  it("does not invent seek positions for untimed or invalid steps", () => {
    for (const description of ["先给结果，再同步展示", "00:07.800—00:07.800 空段", "00:12.000—00:07.800 倒序", "00:99.000—01:40.000 非法秒数"]) {
      expect(parseStepTime(description)).toBeNull();
    }
  });

  it("switches the active chapter exactly at the shared time boundary", () => {
    expect(activeStepIndex(skill.steps, 0)).toBe(0);
    expect(activeStepIndex(skill.steps, 7.799)).toBe(0);
    expect(activeStepIndex(skill.steps, 7.8)).toBe(1);
    expect(activeStepIndex(skill.steps, 12)).toBe(-1);
  });

  it("leaves gaps and untimed steps unhighlighted", () => {
    const steps = [{ description: "00:00.000—00:02.000 开场" }, { description: "无时间码" }, { description: "00:05.000—00:08.000 展示" }];
    expect(activeStepIndex(steps, 3)).toBe(-1);
    expect(activeStepIndex(steps, 6)).toBe(2);
    expect(activeStepIndex(steps, -1)).toBe(-1);
    expect(activeStepIndex(steps, Number.NaN)).toBe(-1);
  });
});
