import { afterEach, describe, expect, it, vi } from "vitest";

import {
  confirmedMaterialClipKeys, confirmMaterialMatch, materialKeyframeUrl, materialProxyUrl, suggestionSourceLabel, uploadMaterial,
} from "./materials";

afterEach(() => vi.unstubAllGlobals());

describe("S6 material API", () => {
  it("builds controlled local resource URLs", () => {
    expect(materialProxyUrl("material_demo_001")).toContain("/s6/materials/material_demo_001/proxy");
    expect(materialKeyframeUrl("material_demo_001", "clip_demo_001")).toContain("/clips/clip_demo_001/keyframe");
  });

  it("uploads archive fields and the selected video as multipart data", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ task_id: "material_task_demo" }), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    const video = new File(["fixture"], "男装样片.mp4", { type: "video/mp4" });

    await uploadMaterial({
      product_id: "product_demo_001", source_script_id: "", model_name: "模特甲", scene: "白墙",
      shot_date: "2026-08-29", batch: "B01", imported_by: "摄影甲", capture_role: "host_take",
      note: "第一遍连续录制", video,
    });

    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("source_script_id")).toBeNull();
    expect((init.body as FormData).get("capture_role")).toBe("host_take");
    expect((init.body as FormData).get("note")).toBe("第一遍连续录制");
    expect((init.body as FormData).get("video")).toBe(video);
  });

  it("sends an explicit human confirmation for a material match", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "materials_uploaded" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await confirmMaterialMatch("script_demo_001", {
      script_shot_id: "shot_demo_001", material_id: "material_demo_001", clip_id: "clip_demo_001", actor: "编导甲",
    });

    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toMatchObject({ actor: "编导甲", clip_id: "clip_demo_001" });
  });

  it("describes a suggested clip with its source file and shooting batch", () => {
    expect(suggestionSourceLabel([{
      material_id: "material_demo", product_id: "product_demo", product_name: "垂感西裤", product_sku: "XZ-01",
      revision: 1, original_name: "白墙正面.mp4", model_name: "模特甲", scene: "白墙", shot_date: "2026-09-06",
      batch: "B-02", clip_count: 2, reusable_clip_count: 2, recognition_status: "completed", updated_at: "2026-09-06T10:00:00Z",
    }], "material_demo", "clip_material_demo_002")).toBe("白墙正面.mp4 · B-02 · 片段 02");

    expect(suggestionSourceLabel([], "material_missing", "clip_material_missing_003")).toBe("clip_material_missing_003");
  });

  it("marks clips already confirmed elsewhere in the same shooting task", () => {
    expect(confirmedMaterialClipKeys([
      { confirmed_match: { material_id: "material_a", clip_id: "clip_a" } },
      { confirmed_match: null },
      { confirmed_match: { material_id: "material_b", clip_id: "clip_b" } },
    ])).toEqual(new Set(["material_a::clip_a", "material_b::clip_b"]));
  });
});
