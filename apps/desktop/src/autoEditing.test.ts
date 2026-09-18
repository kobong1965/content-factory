import { describe, expect, it } from "vitest";
import { appendAutoEditFiles, autoEditStatusLabel, validateAutoEditDraft } from "./autoEditing";

describe("automatic editing project draft", () => {
  it('appends multiple files without replacing previous selection and prevents duplicates', () => {
    const first = { name: '第一条.mp4', size: 10, lastModified: 1 } as File;
    const second = { name: '第二条.mp4', size: 20, lastModified: 2 } as File;
    const original = [first];
    expect(appendAutoEditFiles(original, [first, second])).toEqual([first, second]);
    expect(original).toEqual([first]);
    expect(() => appendAutoEditFiles([], Array.from({length:21}, (_, i) => ({name:`${i}.mp4`,size:1,lastModified:1} as File)))).toThrow('20');
  });
  const valid = {
    title: "J85 直播录播第一批",
    targetCount: 5,
    durationMinSeconds: 20,
    durationMaxSeconds: 40,
    subtitleFontSize: 68,
    keywordColor: "#FFD400",
    keywordScale: 1.3,
  };

  it("accepts the product defaults", () => {
    expect(validateAutoEditDraft(valid)).toBeNull();
  });

  it("rejects invalid count and duration without clearing the draft", () => {
    expect(validateAutoEditDraft({ ...valid, targetCount: 11 })).toContain("1—10");
    expect(validateAutoEditDraft({ ...valid, durationMinSeconds: 50, durationMaxSeconds: 20 })).toContain("时长");
    expect(valid.title).toBe("J85 直播录播第一批");
  });

  it("has explicit labels for every persisted queue state", () => {
    for (const status of ["draft", "queued", "analyzing", "planning", "render_pending", "rendering", "review", "failed", "cancelled"] as const) {
      expect(autoEditStatusLabel(status)).not.toContain(status);
    }
  });
});
