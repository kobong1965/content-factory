import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ViralSkillLibrary } from "./ViralSkillLibrary";
import { useS3Skills } from "./useS3Skills";

vi.mock("./useS3Skills", () => ({ useS3Skills: vi.fn() }));

const mockedUseS3Skills = vi.mocked(useS3Skills);

function libraryState(overrides: Record<string, unknown> = {}) {
  return {
    candidates: [],
    skills: [],
    selectedCandidateId: "candidate_a",
    candidate: null,
    skill: null,
    counts: { pending: 0, reusable: 0, common: 0 },
    isLoading: false,
    isSubmitting: false,
    detailState: "error",
    detailError: "正式 Skill 来源读取失败",
    conflictMessage: null,
    isReloadingDetail: false,
    actionMessage: null,
    actionMessageKind: "status",
    setSelectedCandidateId: vi.fn(),
    refresh: vi.fn(),
    reconcile: vi.fn(),
    reloadSelectedDetail: vi.fn(),
    approve: vi.fn(),
    changeStatus: vi.fn(),
    ...overrides,
  } as never;
}

describe("ViralSkillLibrary resilient detail state", () => {
  beforeEach(() => mockedUseS3Skills.mockReset());

  it("shows a terminal detail error with retry and never renders a misleading approval form", () => {
    mockedUseS3Skills.mockReturnValue(libraryState());

    const markup = renderToStaticMarkup(<ViralSkillLibrary />);

    expect(markup).toContain("Skill 证据未能读取");
    expect(markup).toContain("正式 Skill 来源读取失败");
    expect(markup).toContain("重新读取这个 Skill");
    expect(markup).not.toContain("人工确认");
    expect(markup).not.toContain("正在读取完整证据…");
  });

  it("locks candidate selection while a mutation is in flight", () => {
    mockedUseS3Skills.mockReturnValue(libraryState({
      candidates: [{
        candidate_id: "candidate_a",
        suggested_name: "同步展示",
        suggested_mechanism: "口播动作同步",
        is_common: true,
        distinct_video_count: 2,
        occurrence_count: 2,
        linked_skill: null,
      }],
      counts: { pending: 1, reusable: 0, common: 1 },
      isSubmitting: true,
      detailState: "loading",
      detailError: null,
    }));

    const markup = renderToStaticMarkup(<ViralSkillLibrary />);

    expect(markup).toMatch(/<button(?=[^>]*disabled)(?=[^>]*aria-current="true")[^>]*>/);
  });
});
