import { afterEach, describe, expect, it, vi } from "vitest";
import type { ViralSkillCandidateSummary } from "@content-factory/contracts";

import {
  ViralSkillApiError,
  approveViralSkillCandidate,
  createSkillSelectionGuard,
  filterSkillCandidates,
  formatSkillTimecode,
  setViralSkillStatus,
  summarizeSkillLibrary,
  type SkillCandidateFilter,
} from "./viralSkills";

function candidate(overrides: Partial<ViralSkillCandidateSummary> = {}): ViralSkillCandidateSummary {
  return {
    schema_version: "1.0.0",
    candidate_id: "candidate_0123456789abcdef0123456789abcdef",
    cluster_key: "mechanism:result_then_visual_proof:reuse",
    revision: 3,
    active: true,
    suggested_name: "先给结果，再用上身画面证明",
    suggested_mechanism: "先说穿着结果，再同步展示裤型和垂感。",
    suggested_reuse_mode: "reuse",
    classification: "common_candidate",
    is_common: true,
    evidence_level: "content_observation",
    causality_status: "not_established",
    distinct_video_count: 3,
    occurrence_count: 4,
    steps: [{ order: 1, description: "先说结果" }],
    necessary_conditions: ["口播与动作同步"],
    failure_signals: ["只说不展示"],
    refreshed_at: "2026-09-07T12:00:00Z",
    linked_skill: null,
    ...overrides,
  };
}

describe("S3 viral Skill helpers", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("filters candidates by search, formal status and cross-video commonality", () => {
    const pending = candidate();
    const disabled = candidate({
      candidate_id: "candidate_1123456789abcdef0123456789abcdef",
      suggested_name: "反例：话术堆砌",
      is_common: false,
      classification: "single_video",
      linked_skill: {
        skill_id: "skill_1123456789abcdef0123456789abcdef",
        revision: 2,
        status: "disabled",
        reuse_mode: "avoid",
        source_status: "current",
        update_available: false,
        eligibility: {
          s5_eligible: false,
          reason_code: "skill_disabled",
          reason: "Skill 已停用",
        },
      },
    });
    const filter: SkillCandidateFilter = { query: "结果", status: "pending", commonality: "common" };

    expect(filterSkillCandidates([pending, disabled], filter)).toEqual([pending]);
  });

  it("maps the reusable UI filter to the persisted reuse status", () => {
    const reusable = candidate({
      linked_skill: {
        skill_id: "skill_3123456789abcdef0123456789abcdef",
        revision: 1,
        status: "approved",
        reuse_mode: "reuse",
        source_status: "current",
        update_available: false,
        eligibility: { s5_eligible: true, reason_code: "eligible", reason: null },
      },
    });

    expect(filterSkillCandidates([reusable], {
      query: "",
      status: "reusable",
      commonality: "all",
    })).toEqual([reusable]);
  });

  it("formats evidence timecodes without inventing unavailable timing", () => {
    expect(formatSkillTimecode(null)).toBe("未定位");
    expect(formatSkillTimecode(0)).toBe("00:00.0");
    expect(formatSkillTimecode(65_250)).toBe("01:05.3");
  });

  it("does not label repeated reports from one video as a cross-video common Skill", () => {
    const first = candidate({ is_common: false, classification: "single_video", distinct_video_count: 1 });
    const second = candidate({
      candidate_id: "candidate_2123456789abcdef0123456789abcdef",
      suggested_name: "另一个单视频爆点",
      is_common: false,
      classification: "single_video",
      distinct_video_count: 1,
    });

    expect(summarizeSkillLibrary([first, second], [])).toEqual({ pending: 2, reusable: 0, common: 0 });
  });

  it("sends explicit candidate and formal revisions when approving for scripts", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ skill_id: "skill_a" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await approveViralSkillCandidate("candidate_a", {
      expected_candidate_revision: 4,
      expected_skill_revision: 2,
      reviewer: "张三",
      reuse_mode: "reuse",
      name: "结果同步展示",
      mechanism: "口播给出结果时同步用上身画面证明。",
      note: "已核对三条不同视频",
    });

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(request.body))).toMatchObject({
      expected_candidate_revision: 4,
      expected_skill_revision: 2,
      reuse_mode: "reuse",
    });
  });

  it("requires the current formal revision when disabling a Skill", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ skill_id: "skill_a" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await setViralSkillStatus("skill_a", {
      expected_revision: 7,
      status: "disabled",
      reviewer: "李四",
      note: "来源商品与当前类目不匹配",
    });

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(request.body))).toEqual({
      expected_revision: 7,
      status: "disabled",
      reviewer: "李四",
      note: "来源商品与当前类目不匹配",
    });
  });

  it("rejects a stale mutation commit after the operator switches candidates", () => {
    const selection = createSkillSelectionGuard("candidate_a");
    const requestCandidateId = selection.current();

    selection.select("candidate_b");

    expect(selection.isCurrent(requestCandidateId)).toBe(false);
    expect(selection.isCurrent("candidate_b")).toBe(true);
  });

  it("keeps HTTP 409 identifiable so the UI can preserve the draft and offer a reload", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: "candidate revision conflict: expected 3, current 4",
    }), {
      status: 409,
      headers: { "Content-Type": "application/json" },
    })));

    const request = approveViralSkillCandidate("candidate_a", {
      expected_candidate_revision: 3,
      expected_skill_revision: null,
      reviewer: "张三",
      reuse_mode: "reuse",
      name: "结果同步展示",
      mechanism: "口播和动作同步。",
      note: null,
    });

    await expect(request).rejects.toBeInstanceOf(ViralSkillApiError);
    await expect(request).rejects.toMatchObject({ status: 409 });
  });
});
