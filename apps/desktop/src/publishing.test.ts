import { afterEach, describe, expect, it, vi } from "vitest";

import {
  clearDraftRejectNote,
  deriveDouyinWorkId,
  draftRejectNote,
  formatMetric,
  confirmMetricImport,
  isValidLocalDateTime,
  parseMetricInput,
  resetAfterSuccessfulSubmission,
  updateDraftRejectNote,
  toIsoTime,
} from "./publishing";

describe("S8 publishing helpers", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("derives a work id only from a structured Douyin URL", () => {
    expect(deriveDouyinWorkId("https://www.douyin.com/video/7600000000000000001")).toBe("7600000000000000001");
    expect(deriveDouyinWorkId("not a url")).toBe("");
  });

  it("turns percent form values into contract ratios", () => {
    expect(parseMetricInput("completion_rate", "31")).toBe(.31);
    expect(parseMetricInput("views", "12800")).toBe(12800);
    expect(parseMetricInput("gmv_cents", "9280")).toBe(928000);
    expect(() => parseMetricInput("product_ctr", "101")).toThrow("不能超过");
  });

  it("formats ratios, money, and local time", () => {
    expect(formatMetric("product_ctr", .0422)).toBe("4.22%");
    expect(formatMetric("gmv_cents", 928000)).toBe("¥9280.00");
    expect(toIsoTime("2026-08-30T09:00")).toMatch(/^2026-08-30T\d{2}:00:00\.000Z$/);
  });

  it("keeps form values when a handled API failure returns null", () => {
    const reset = vi.fn();

    expect(resetAfterSuccessfulSubmission(null, reset)).toBe(false);
    expect(reset).not.toHaveBeenCalled();

    expect(resetAfterSuccessfulSubmission({ publication_id: "publication_ok" }, reset)).toBe(true);
    expect(reset).toHaveBeenCalledOnce();
  });

  it("requires a valid local date before serializing a form submission", () => {
    expect(isValidLocalDateTime("")).toBe(false);
    expect(isValidLocalDateTime("not-a-date")).toBe(false);
    expect(isValidLocalDateTime("2026-09-06T19:30")).toBe(true);
  });

  it("sends operator-corrected import candidates to the confirmation API", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ draft: {}, snapshots: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await confirmMetricImport("metric_import_demo", "运营甲", [{
      candidate_id: "candidate_demo",
      captured_at: "2026-09-06T10:00:00.000Z",
      confidence: "confirmed",
      metrics: { views: 18801, completion_rate: .35 },
    }]);

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(request.body))).toEqual({
      confirmed_by: "运营甲",
      candidates: [{
        candidate_id: "candidate_demo",
        captured_at: "2026-09-06T10:00:00.000Z",
        confidence: "confirmed",
        metrics: { views: 18801, completion_rate: .35 },
      }],
    });
  });

  it("keeps rejection reasons isolated between two pending import drafts", () => {
    let notes = {};
    notes = updateDraftRejectNote(notes, "draft_a", "A 的截图数字无法辨认");
    notes = updateDraftRejectNote(notes, "draft_b", "B 的 CSV 时间错误");

    expect(draftRejectNote(notes, "draft_a")).toBe("A 的截图数字无法辨认");
    expect(draftRejectNote(notes, "draft_b")).toBe("B 的 CSV 时间错误");

    const afterARejected = clearDraftRejectNote(notes, "draft_a");
    expect(draftRejectNote(afterARejected, "draft_a")).toBe("");
    expect(draftRejectNote(afterARejected, "draft_b")).toBe("B 的 CSV 时间错误");
  });
});
