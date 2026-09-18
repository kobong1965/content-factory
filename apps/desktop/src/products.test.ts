import { describe, expect, it, vi } from "vitest";
import type { ProductProfile } from "@content-factory/contracts";

import { completenessPercent, lines, prepareConfirmedProfile, productStatusLabels } from "./products";

describe("S4 product workspace helpers", () => {
  it("uses plain-language status labels and bounded completeness", () => {
    expect(productStatusLabels.draft).toBe("临时商品");
    expect(productStatusLabels.active).toBe("正式商品");
    expect(productStatusLabels.archived).toBe("已归档");
    expect(completenessPercent(-1)).toBe(0);
    expect(completenessPercent(0.836)).toBe(84);
    expect(completenessPercent(2)).toBe(100);
  });

  it("normalizes line lists without duplicate expressions", () => {
    expect(lines("全网最低\n百分百， 全网最低")).toEqual(["全网最低", "百分百"]);
  });

  it("adds confirmation timestamps only when a person confirmed", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-29T09:00:00Z"));
    const profile = {
      brand_boundary_confirmed_by: "运营甲",
      brand_boundary_confirmed_at: null,
      shooting_constraints: { confirmed_by: "摄影乙", confirmed_at: null },
    } as unknown as ProductProfile;

    const prepared = prepareConfirmedProfile(profile);

    expect(prepared.brand_boundary_confirmed_at).toBe("2026-08-29T09:00:00.000Z");
    expect(prepared.shooting_constraints.confirmed_at).toBe("2026-08-29T09:00:00.000Z");
    vi.useRealTimers();
  });
});
