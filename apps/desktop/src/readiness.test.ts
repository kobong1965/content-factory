import { describe, expect, it } from "vitest";

import { DEFAULT_S1_READINESS, completionPercent, s1StatusLabel } from "./readiness";

describe("S1 desktop readiness", () => {
  it("reports the frozen engineering baseline without pretending fixtures are real", () => {
    expect(DEFAULT_S1_READINESS.engineering_ready).toBe(true);
    expect(DEFAULT_S1_READINESS.business_ready).toBe(false);
    expect(s1StatusLabel(DEFAULT_S1_READINESS)).toBe("工程已通过 · 真实资料待补");
  });

  it("keeps progress within a visible percentage range", () => {
    expect(completionPercent(0, 20)).toBe(0);
    expect(completionPercent(10, 20)).toBe(50);
    expect(completionPercent(21, 20)).toBe(100);
    expect(completionPercent(1, 0)).toBe(0);
  });
});
