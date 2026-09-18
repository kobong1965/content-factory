import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { GatewayModelCard } from "./GatewayModelCard";
import { createDraft, initialGatewayDraftState } from "./gatewayModelSettings";
import { DEFAULT_GATEWAY_SETTINGS } from "./analysis";

describe("GatewayModelCard", () => {
  it("keeps verified upstream identity read-only while allowing presentation edits", () => {
    const verified = {
      ...createDraft(1, "qwen", true),
      model_id: "model_verified",
      model: "qwen3.7-plus",
      api_key_configured: true,
    };
    const routing = {
      ...initialGatewayDraftState(DEFAULT_GATEWAY_SETTINGS).routing,
      video_review: verified.model_id,
      analysis: verified.model_id,
      material: verified.model_id,
      script: verified.model_id,
    };
    const markup = renderToStaticMarkup(<GatewayModelCard
      model={verified}
      persistedIdentity={verified}
      index={0}
      routing={routing}
      modelCount={1}
      busy={false}
      offline={false}
      update={() => undefined}
      remove={() => undefined}
      test={() => undefined}
    />);

    expect(markup).toContain("已验证连接身份（只读）");
    expect(markup).toContain("qwen3.7-plus");
    expect(markup).toContain("https://dashscope.aliyuncs.com/compatible-mode/v1");
    expect(markup).toContain("显示名称");
    expect(markup).toContain("队列连续性保护");
    expect(markup).not.toContain("type=\"password\"");
    expect(markup).not.toContain("type=\"checkbox\"");
    expect(markup).not.toContain("服务商预设");
    expect(markup).not.toContain("切换时会清空旧密钥");
  });

  it("treats a persisted legacy profile without a key as protected, not as a removable draft", () => {
    const persisted = {
      ...createDraft(1, "openai_compatible", true),
      model_id: "model_persisted_without_key",
      base_url: "https://relay.example/v1",
      model: "vision-model",
      api_key_configured: false,
    };
    const routing = initialGatewayDraftState(DEFAULT_GATEWAY_SETTINGS).routing;
    const markup = renderToStaticMarkup(<GatewayModelCard
      model={persisted}
      persistedIdentity={persisted}
      index={0}
      routing={routing}
      modelCount={2}
      busy={false}
      offline={false}
      update={() => undefined}
      remove={() => undefined}
      test={() => undefined}
    />);

    expect(markup).toContain("已保存连接身份（只读）");
    expect(markup).toContain("已保存，待验证");
    expect(markup).toContain("尚未配置，请使用上方连接向导");
    expect(markup).not.toContain("取消草稿");
    expect(markup).toContain("该连接尚未通过保存验证");
    expect(markup).toContain("disabled=\"\"");
  });
});
