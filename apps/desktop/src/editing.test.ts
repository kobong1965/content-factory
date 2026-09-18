import { describe, expect, it } from "vitest";
import type { EditProject, EditVariant, RenderOutput } from "@content-factory/contracts";

import { formatEditDuration, outputResourceUrl, reconcileEditDraft, resequenceClips } from "./editing";

const fixtureVariant: EditVariant = {
  id: "edit_variant_test", script_version_id: "version_test", name: "测试版", duration_ms: 3000,
  warnings: [], latest_output_id: null,
  clips: [
    { id: "edit_clip_a", order: 1, script_shot_id: "shot_a", material_id: "material_a", material_clip_id: "clip_a", source_start_ms: 0, source_end_ms: 1000, timeline_start_ms: 0, timeline_end_ms: 1000, speed: 1, crop_mode: "fill", focus_x: .5, focus_y: .5, transition: "cut", transition_ms: 0, subtitle: "A", voiceover: "A", sound_effect: "无", has_source_audio: true, original_volume: 1, note: "" },
    { id: "edit_clip_b", order: 2, script_shot_id: "shot_b", material_id: "material_a", material_clip_id: "clip_b", source_start_ms: 1000, source_end_ms: 3000, timeline_start_ms: 1000, timeline_end_ms: 3000, speed: 1, crop_mode: "fit", focus_x: .5, focus_y: .5, transition: "fade", transition_ms: 120, subtitle: "B", voiceover: "B", sound_effect: "轻响", has_source_audio: true, original_volume: 1, note: "" },
  ],
};

describe("S7 editing helpers", () => {
  it("reorders clips while keeping a continuous timeline", () => {
    const changed = resequenceClips(fixtureVariant, [...fixtureVariant.clips].reverse());
    expect(changed.clips.map((item) => [item.order, item.timeline_start_ms, item.timeline_end_ms])).toEqual([[1, 0, 2000], [2, 2000, 3000]]);
    expect(changed.duration_ms).toBe(3000);
  });

  it("formats short and minute durations for editors", () => {
    expect(formatEditDuration(6500)).toBe("6.5 秒");
    expect(formatEditDuration(65000)).toBe("1:05");
  });

  it("encodes output and resource identifiers", () => {
    const output = { output_id: "output/with space", resources: { video_ref: "video/ref", clean_video_ref: "clean", subtitle_ref: "sub", project_ref: "project", jianying_package_ref: "zip", jianying_experimental: true } } as unknown as RenderOutput;
    expect(outputResourceUrl(output, "video_ref", true)).toContain("output%2Fwith%20space/resources/video%2Fref?download=true");
  });

  it("does not let polling overwrite an unsaved edit draft", () => {
    const server = {
      project_id: "edit_project_demo",
      revision: 1,
      settings: { subtitle: true },
      variants: [{ ...fixtureVariant, clips: fixtureVariant.clips.map((clip) => ({ ...clip })) }],
    } as unknown as EditProject;
    const dirty = {
      ...structuredClone(server),
      variants: server.variants.map((variant, index) => index ? variant : {
        ...variant,
        clips: variant.clips.map((clip, clipIndex) => clipIndex ? clip : { ...clip, subtitle: "未保存字幕" }),
      }),
    } as EditProject;

    const next = reconcileEditDraft(dirty, server, structuredClone(server));

    expect(next).toBe(dirty);
    expect(next.variants[0]!.clips[0]!.subtitle).toBe("未保存字幕");
  });

  it("accepts a newer server project when the current draft is still clean", () => {
    const server = {
      project_id: "edit_project_demo",
      revision: 1,
      settings: { subtitle: true },
      variants: [fixtureVariant],
    } as unknown as EditProject;
    const incoming = { ...structuredClone(server), revision: 2 } as EditProject;

    const next = reconcileEditDraft(structuredClone(server), server, incoming);

    expect(next).not.toBe(incoming);
    expect(next.revision).toBe(2);
  });
});
