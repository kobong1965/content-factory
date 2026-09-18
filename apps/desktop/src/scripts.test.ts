import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createScriptGeneration,
  eligibleScriptSkills,
  formatScriptTime,
  reconcileSelectedProductId,
  reconcileSelectedSkillId,
  scriptSkillTrace,
  scriptGoalLabels,
  scriptReviewLabels,
  scriptStepLabels,
  type ScriptSkillSummary,
} from "./scripts";

describe("S5 script helpers", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders stable business labels", () => {
    expect(scriptGoalLabels.conversion).toBe("带货成交");
    expect(scriptReviewLabels.approved).toBe("可拍摄");
    expect(scriptStepLabels.validate).toContain("事实");
  });

  it("formats second-level storyboard timing", () => {
    expect(formatScriptTime(0)).toBe("0s");
    expect(formatScriptTime(1500)).toBe("1.5s");
    expect(formatScriptTime(6000)).toBe("6s");
  });

  it("offers only approved reusable Skills to script generation", () => {
    const summaries = [
      { template_id: "skill_reuse", skill_id: "skill_reuse", status: "approved", reuse_mode: "reuse", name: "可复用机制", eligibility: { s5_eligible: true } },
      { template_id: "skill_updated", skill_id: "skill_updated", status: "approved", reuse_mode: "reuse", name: "证据已更新", eligibility: { s5_eligible: false } },
      { template_id: "skill_avoid", skill_id: "skill_avoid", status: "approved", reuse_mode: "avoid", name: "避坑反例", eligibility: { s5_eligible: false } },
      { template_id: "skill_disabled", skill_id: "skill_disabled", status: "disabled", reuse_mode: "reuse", name: "已停用", eligibility: { s5_eligible: false } },
    ] as ScriptSkillSummary[];

    expect(eligibleScriptSkills(summaries).map((item) => item.skill_id)).toEqual(["skill_reuse"]);
  });

  it("clears a stale Skill selection instead of submitting its old id", () => {
    const available = [
      { template_id: "skill_new", skill_id: "skill_new", status: "approved", reuse_mode: "reuse" },
    ] as ScriptSkillSummary[];

    expect(reconcileSelectedSkillId("skill_removed", available)).toBe("");
    expect(reconcileSelectedSkillId("skill_new", available)).toBe("skill_new");
  });

  it("clears a stale product instead of treating its removed id as ready", () => {
    const products: Parameters<typeof reconcileSelectedProductId>[1] = [{
      product_id: "product_new",
      revision: 1,
      sku: "J82",
      name: "J82 男士休闲裤",
      selling_points: ["已确认上身版型"],
      max_duration_ms: 60_000,
    }];

    expect(reconcileSelectedProductId("product_removed", products)).toBe("");
    expect(reconcileSelectedProductId("product_new", products)).toBe("product_new");
  });

  it("builds a review trace from the frozen formal Skill and supports legacy null", () => {
    expect(scriptSkillTrace({ skill: null })).toBeNull();

    const trace = scriptSkillTrace({
      skill: {
        skill_id: "skill_0123456789abcdef0123456789abcdef",
        revision: 4,
        name: "原话同步上身证明",
        distinct_video_count: 3,
        occurrence_count: 5,
        occurrences: [{
          source_name: "4月20日.mp4",
          video_id: "video_a",
          start_ms: 2300,
          end_ms: 4900,
          steps: [{ evidence: [{ exact_dialogue: "你看这个垂感", action: "双手拉平裤腿" }] }],
        }],
      } as never,
    });

    expect(trace).toMatchObject({ name: "原话同步上身证明", revision: 4, distinctVideoCount: 3, evidenceCount: 5 });
    expect(trace?.sources[0]).toMatchObject({ sourceName: "4月20日.mp4", dialogue: "你看这个垂感", action: "双手拉平裤腿" });
  });

  it("submits the formal skill_id contract for a generation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ task_id: "script_task_a" }), {
      status: 202,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await createScriptGeneration({
      skill_id: "skill_0123456789abcdef0123456789abcdef",
      product_id: "product_a",
      content_goal: "conversion",
      target_audience: "在意版型和真实上身效果的男装消费者",
      version_count: 3,
    });

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const body = JSON.parse(String(request.body));
    expect(body.skill_id).toBe("skill_0123456789abcdef0123456789abcdef");
    expect(body).not.toHaveProperty("template_id");
  });
});
