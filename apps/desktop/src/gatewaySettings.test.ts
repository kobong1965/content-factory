import { describe, expect, it } from "vitest";

import type { GatewayDiscoveredModel } from "./analysis";
import type { DraftModel } from "./gatewayModelSettings";

import {
  buildAdvancedGatewayModels,
  applyProviderPreset,
  createDraft,
  discoveredModelCapabilityLabel,
  endpointGuidance,
  gatewayConnectionErrorMessage,
  gatewayModelLimitReachedDescription,
  gatewayRequestUrl,
  inferProvider,
  initialGatewayDraftState,
  knownModelCapabilityWarning,
  matchingSavedCredentialModelId,
  selectableDiscoveredModels,
  preferredDiscoveredModelId,
  providerPresets,
  purposeOptions,
  updateOwnedModelId,
  verifiedRouteCount,
  setImageCapability,
  supportsPurpose,
} from "./gatewayModelSettings";

describe("multimodal gateway settings", () => {
  it("does not trust relay hostnames that merely contain Qwen provider words", () => {
    expect(inferProvider({
      base_url: "https://relay-dashscope.example/v1",
      model: "gpt-4.1",
    })).toBe("openai_compatible");
    expect(inferProvider({
      base_url: "https://evilaliyuncs.com/v1",
      model: "gpt-4.1",
    })).toBe("openai_compatible");
    expect(inferProvider({
      base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      model: "",
    })).toBe("qwen");
    expect(inferProvider({
      base_url: "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
      model: "",
    })).toBe("qwen");
    expect(inferProvider({
      base_url: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
      model: "",
    })).toBe("qwen");
    expect(inferProvider({
      base_url: "https://coding.dashscope.aliyuncs.com/compatible-mode/v1",
      model: "",
    })).toBe("qwen");
    expect(inferProvider({
      base_url: "https://relay.example/v1",
      model: "qwen3-vl-plus",
    })).toBe("openai_compatible");
    expect(inferProvider({
      base_url: "https://dashscope.aliyuncs.com.attacker.example/v1",
      model: "gpt-4.1",
    })).toBe("openai_compatible");
  });

  it("does not show Qwen credential hints for a spoofed relay hostname", () => {
    const message = gatewayConnectionErrorMessage(
      new Error("invalid api key"),
      { base_url: "https://relay-dashscope.example/v1", api_mode: "chat_completions", model: "gpt-4.1" },
    );

    expect(message).not.toContain("Qwen 百炼");
    expect(message).not.toContain("qwen3.7-plus");
  });

  it("describes the eight-model queue-continuity lock without promising a deletion workflow", () => {
    expect(gatewayModelLimitReachedDescription).toContain("当前版本为保护排队任务不提供旧连接删除");
    expect(gatewayModelLimitReachedDescription).toContain("请继续使用已有连接");
    expect(gatewayModelLimitReachedDescription).not.toContain("先移除");
    expect(gatewayModelLimitReachedDescription).not.toContain("完成或清理相关任务");
  });

  it("creates selectable GPT and Qwen profiles for all four tasks", () => {
    const gpt = createDraft(1, "openai");
    const qwen = createDraft(2, "qwen");

    expect(gpt.base_url).toBe("https://api.openai.com/v1");
    expect(gpt.api_mode).toBe("responses");
    expect(qwen.base_url).toBe("https://dashscope.aliyuncs.com/compatible-mode/v1");
    expect(qwen.api_mode).toBe("chat_completions");
    expect(providerPresets.find((preset) => preset.value === "qwen")?.description).toContain("中国内地");
    expect(providerPresets.find((preset) => preset.value === "qwen")?.description).toContain("地域匹配");
    expect(providerPresets.find((preset) => preset.value === "qwen")?.description).toContain("qwen3.7-plus");
    expect(purposeOptions.map((purpose) => purpose.value)).toEqual([
      "video_review", "analysis", "material", "script",
    ]);
    for (const purpose of purposeOptions) {
      expect(supportsPurpose(gpt, purpose.value)).toBe(true);
      expect(supportsPurpose(qwen, purpose.value)).toBe(true);
    }
  });

  it("selects the exact catalog id when a saved model differs only by case", () => {
    expect(preferredDiscoveredModelId("Qwen3.7-Plus", [
      { upstream_model_id: "qwen3.7-plus", display_name: "Qwen 3.7 Plus" },
      { upstream_model_id: "qwen3.6-plus", display_name: "Qwen 3.6 Plus" },
    ])).toBe("qwen3.7-plus");
  });

  it("reuses a saved key only for the same credential origin", () => {
    const gateway = {
      ...initialGatewayDraftState({
        schema_version: "2.1.0" as const,
        base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: "qwen3.7-plus",
        api_mode: "chat_completions" as const,
        api_key_configured: true,
        default_model_id: "model_qwen",
        models: [{
          model_id: "model_qwen",
          display_name: "Qwen",
          provider: "qwen" as const,
          base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
          model: "qwen3.7-plus",
          api_mode: "chat_completions" as const,
          modalities: ["text", "image"] as const,
          purposes: ["analysis", "script", "material", "video_review"] as const,
          enabled: true,
          api_key_configured: true,
        }],
        routing: {
          analysis: "model_qwen",
          script: "model_qwen",
          material: "model_qwen",
          video_review: "model_qwen",
        },
        updated_at: "2026-09-08T00:00:00Z",
      }).models[0]!,
    };
    expect(matchingSavedCredentialModelId([gateway], "https://dashscope.aliyuncs.com/api/v1", "model_qwen"))
      .toBe("model_qwen");
    expect(matchingSavedCredentialModelId([gateway], "https://relay.example/v1", "model_qwen"))
      .toBeNull();
  });

  it("never borrows a key from a different profile on the same origin", () => {
    const first = {
      ...createDraft(1, "openai_compatible"),
      model_id: "model_account_a",
      base_url: "https://relay.example/v1",
      api_key_configured: true,
    };
    const current = {
      ...createDraft(2, "openai_compatible"),
      model_id: "model_account_b",
      base_url: "https://relay.example/v1/chat/completions",
      api_key_configured: false,
    };

    expect(matchingSavedCredentialModelId([first, current], current.base_url, current.model_id)).toBeNull();
    expect(matchingSavedCredentialModelId([
      first,
      { ...current, api_key_configured: true },
    ], current.base_url, current.model_id)).toBe(current.model_id);
  });

  it("keeps unknown catalog entries but filters models explicitly incompatible with image or structured output", () => {
    const catalog: GatewayDiscoveredModel[] = [
      { upstream_model_id: "vision-ready", display_name: "Vision Ready", input_modalities: ["text", "image"], output_modalities: ["text"], supports_structured_output: true, capability_source: "provider_metadata" },
      { upstream_model_id: "text-only", display_name: "Text only", input_modalities: ["text"], output_modalities: ["text"], supports_structured_output: true, capability_source: "provider_metadata" },
      { upstream_model_id: "no-schema", display_name: "No schema", input_modalities: ["text", "image"], output_modalities: ["text"], supports_structured_output: false, capability_source: "provider_metadata" },
      { upstream_model_id: "relay-unknown", display_name: "Relay Unknown", input_modalities: null, output_modalities: null, supports_structured_output: null, capability_source: "unknown" },
    ];

    expect(selectableDiscoveredModels(catalog).map((item) => item.upstream_model_id))
      .toEqual(["vision-ready", "relay-unknown"]);
    expect(selectableDiscoveredModels(catalog, "RELAY").map((item) => item.upstream_model_id))
      .toEqual(["relay-unknown"]);
    expect(discoveredModelCapabilityLabel(catalog[0]!)).toContain("图文");
    expect(discoveredModelCapabilityLabel(catalog[0]!)).toContain("结构化");
    expect(discoveredModelCapabilityLabel(catalog[3]!)).toContain("待实测");
  });

  it("merges advanced edits onto verified identities and refuses unverified drafts", () => {
    const verified = {
      ...createDraft(1, "qwen"),
      model_id: "model_verified",
      model: "qwen3.7-plus",
      api_key_configured: true,
    };
    const edited: DraftModel = {
      ...verified,
      provider: "custom" as const,
      base_url: "https://attacker.invalid/v1",
      model: "different-model",
      api_mode: "responses" as const,
      modalities: ["text"],
      purposes: ["script"],
      api_key: "replacement-key",
      display_name: "团队显示名",
      enabled: false,
    };

    expect(buildAdvancedGatewayModels([edited], [verified])).toEqual([{
      model_id: verified.model_id,
      provider: verified.provider,
      display_name: "团队显示名",
      base_url: verified.base_url,
      model: verified.model,
      api_mode: verified.api_mode,
      modalities: verified.modalities,
      purposes: verified.purposes,
      enabled: verified.enabled,
    }]);
    expect(buildAdvancedGatewayModels([createDraft(2)], [verified])).toBeNull();
    expect(buildAdvancedGatewayModels([], [verified])).toEqual([{
      model_id: verified.model_id,
      provider: verified.provider,
      display_name: verified.display_name,
      base_url: verified.base_url,
      model: verified.model,
      api_mode: verified.api_mode,
      modalities: verified.modalities,
      purposes: verified.purposes,
      enabled: verified.enabled,
    }]);

    const persistedWithoutKey = {
      ...verified,
      model_id: "model_legacy_without_key",
      api_key_configured: false,
    };
    expect(buildAdvancedGatewayModels([verified], [verified, persistedWithoutKey])).toEqual([
      expect.objectContaining({ model_id: verified.model_id }),
      expect.objectContaining({
        model_id: persistedWithoutKey.model_id,
        base_url: persistedWithoutKey.base_url,
        model: persistedWithoutKey.model,
        enabled: persistedWithoutKey.enabled,
      }),
    ]);
  });

  it("clears owned transient state when the same quick setup unmounts or switches", () => {
    expect(updateOwnedModelId(null, "model_a", true)).toBe("model_a");
    expect(updateOwnedModelId("model_a", "model_a", false)).toBeNull();
    expect(updateOwnedModelId("model_b", "model_a", false)).toBe("model_b");
  });

  it("does not report four routed capabilities for the unverified empty draft", () => {
    const empty = initialGatewayDraftState({
      schema_version: "2.1.0",
      base_url: "",
      model: "",
      api_mode: "responses",
      api_key_configured: false,
      default_model_id: null,
      models: [],
      routing: { analysis: null, script: null, material: null, video_review: null },
      updated_at: null,
    });

    expect(verifiedRouteCount(empty.models, empty.routing)).toBe(0);
  });

  it("shows the exact endpoint produced by each API mode", () => {
    expect(gatewayRequestUrl("https://relay.example/v1/", "responses"))
      .toBe("https://relay.example/v1/responses");
    expect(gatewayRequestUrl("https://relay.example/v1", "chat_completions"))
      .toBe("https://relay.example/v1/chat/completions");
    expect(gatewayRequestUrl("https://relay.example/v1/responses", "chat_completions"))
      .toBe("https://relay.example/v1/chat/completions");
    expect(gatewayRequestUrl("https://relay.example/v1/chat/completions", "responses"))
      .toBe("https://relay.example/v1/responses");
  });

  it("clears a previous provider secret when applying a new preset", () => {
    const existing = {
      ...createDraft(1, "openai_compatible"),
      model: "old-model",
      api_key: "old-secret",
      api_key_configured: true,
    };
    const qwen = applyProviderPreset(existing, "qwen");

    expect(qwen.provider).toBe("qwen");
    expect(qwen.model).toBe("");
    expect(qwen.api_key).toBe("");
    expect(qwen.api_key_configured).toBe(false);
  });

  it("turns a declared vision model into a text-only model without leaving image tasks enabled", () => {
    const vision = createDraft(1, "qwen");
    const textOnly = setImageCapability(vision, false);

    expect(textOnly.modalities).toEqual(["text"]);
    expect(textOnly.purposes).toEqual(["script"]);
    expect(supportsPurpose(textOnly, "script")).toBe(true);
    expect(supportsPurpose(textOnly, "analysis")).toBe(false);
    expect(setImageCapability(textOnly, true).modalities).toEqual(["text", "image"]);
    expect(setImageCapability(textOnly, true).purposes).toEqual(["script"]);
  });

  it("prioritizes an exact model-name diagnosis over a generic 404 hint", () => {
    const message = gatewayConnectionErrorMessage(
      new Error("模型标识不存在（model_not_found）"),
      { base_url: "https://api.apikey.fun", api_mode: "chat_completions", model: "gpt-5.6sol" },
    );

    expect(message).toContain("逐字复制模型标识");
    expect(message).toContain("POST https://api.apikey.fun/chat/completions");
    expect(message).not.toContain("缺少 /v1");
  });

  it("uses structured diagnostics as the source of truth without duplicating a request path", () => {
    const error = Object.assign(new Error("本地接口返回 502"), {
      detail: {
        diagnostic_code: "model_not_found",
        message: "中转站找不到模型标识“gpt-5.6sol”",
        endpoint_url: "https://relay.example/v1/chat/completions",
        model: "gpt-5.6sol",
        api_mode: "chat_completions",
      },
    });
    const message = gatewayConnectionErrorMessage(error, {
      base_url: "https://stale-preview.example/v1",
      api_mode: "responses",
      model: "stale-model",
    });

    expect(message).toContain("中转站找不到模型标识“gpt-5.6sol”");
    expect(message.match(/https:\/\/relay\.example\/v1\/chat\/completions/g)).toHaveLength(1);
    expect(message).not.toContain("stale-preview.example");
    expect(message).not.toContain("stale-model");
  });

  it.each([
    ["image_input_unsupported", "该模型不支持图片输入", "图片能力不匹配", "qwen3.7-plus"],
    ["image_input_invalid", "验证图片尺寸不符合限制", "图片测试输入不符合限制", "不代表模型一定不支持图片"],
    ["authentication_failed", "中转站拒绝了密钥", "密钥或地域不匹配", "同一地域"],
    ["structured_output_unsupported", "该模型不支持结构化 JSON 输出", "结构化输出不支持", "JSON Schema"],
    ["request_incompatible", "中转站请求不兼容（HTTP 400；上游代码：invalid_parameter_error）", "请求参数不兼容", "参数组合"],
  ])("renders %s as an actionable, distinct diagnosis", (diagnosticCode, backendMessage, title, action) => {
    const error = Object.assign(new Error("本地接口返回 502"), {
      detail: {
        diagnostic_code: diagnosticCode,
        message: backendMessage,
        endpoint_url: "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        model: "qwen3.7-max",
        api_mode: "chat_completions",
      },
    });

    const message = gatewayConnectionErrorMessage(error, {
      base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      api_mode: "chat_completions",
      model: "qwen3.7-max",
    });
    expect(message).toContain(`${title}：`);
    expect(message).toContain(action);
  });

  it("warns before assigning the text-only qwen3.7-max alias to image tasks", () => {
    const qwen = { ...createDraft(1, "qwen"), model: "qwen3.7-max" };
    expect(knownModelCapabilityWarning(qwen)).toContain("纯文本模型");
    expect(knownModelCapabilityWarning(qwen)).toContain("qwen3.7-plus");

    const textOnly = {
      ...setImageCapability(qwen, false),
      purposes: ["script" as const],
    };
    expect(knownModelCapabilityWarning(textOnly)).toBeNull();
  });

  it("redacts credentials and ignores unapproved upstream detail fields", () => {
    const error = Object.assign(new Error("不应使用的错误容器"), {
      detail: {
        diagnostic_code: "authentication_failed",
        message: "Authorization: Bearer sk-super-secret-value; callback=https://relay.example/v1?token=top-secret",
        endpoint_url: "https://relay.example/v1/chat/completions?api_key=endpoint-secret",
        model: "qwen3.7-max",
        api_mode: "chat_completions",
        upstream_message: "RAW-UPSTREAM-SECRET",
        raw: "RAW-BODY-SECRET",
      },
    });
    const message = gatewayConnectionErrorMessage(error, {
      base_url: "https://stale.example/v1",
      api_mode: "responses",
      model: "stale-model",
    });

    expect(message).toContain("已隐藏");
    expect(message).toContain("https://relay.example/v1/chat/completions");
    expect(message).not.toMatch(/super-secret|top-secret|endpoint-secret|RAW-UPSTREAM|RAW-BODY/);
  });

  it("does not warn that a custom relay root must end in v1", () => {
    expect(endpointGuidance({
      provider: "openai_compatible",
      base_url: "https://api.apikey.fun",
      api_mode: "chat_completions",
    })).toBeNull();
    expect(endpointGuidance({
      provider: "openai_compatible",
      base_url: "https://relay.example/v1/chat/completions",
      api_mode: "responses",
    })).toBeNull();
  });

  it("migrates an image-capable 2.0 profile into the new video-review route", () => {
    const state = initialGatewayDraftState({
      schema_version: "2.0.0",
      base_url: "https://relay.example",
      model: "vision-model",
      api_mode: "chat_completions",
      api_key_configured: true,
      default_model_id: "legacy-model",
      models: [{
        model_id: "legacy-model",
        display_name: "旧版视觉模型",
        base_url: "https://relay.example",
        model: "vision-model",
        api_mode: "chat_completions",
        modalities: ["text", "image"],
        purposes: ["analysis", "script", "material"],
        enabled: true,
        api_key_configured: true,
      }],
      routing: { analysis: "legacy-model", script: "legacy-model", material: "legacy-model" },
      updated_at: null,
    });

    expect(state.models[0]?.purposes).toContain("video_review");
    expect(state.routing.video_review).toBe("legacy-model");
    expect(supportsPurpose(state.models[0]!, "video_review")).toBe(true);
  });
});
