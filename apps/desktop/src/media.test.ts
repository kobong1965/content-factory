import { describe, expect, it } from "vitest";

import { DEFAULT_S2_READINESS, completionPercent, taskStatusLabel, taskStepLabel } from "./media";

describe("S2 desktop media helpers", () => {
  it("does not pretend the offline fallback is engineering ready", () => {
    expect(DEFAULT_S2_READINESS.engineering_ready).toBe(false);
    expect(DEFAULT_S2_READINESS.business_ready).toBe(false);
  });

  it("uses plain-language queue labels", () => {
    expect(taskStatusLabel("retry_wait")).toBe("准备重试");
    expect(taskStepLabel("keyframes")).toBe("提取关键画面");
  });

  it("keeps real acceptance percentages within range", () => {
    expect(completionPercent(9, 10)).toBe(90);
    expect(completionPercent(11, 10)).toBe(100);
    expect(completionPercent(1, 0)).toBe(0);
  });
});
