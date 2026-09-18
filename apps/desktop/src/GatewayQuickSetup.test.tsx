import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  GatewayCatalogPicker,
  GatewayManualModelFallback,
  GatewayQuickSetup,
  quickIdentityInputsDisabled,
} from "./GatewayQuickSetup";
import type { GatewayDiscoveryResult } from "./analysis";
import { createDraft } from "./gatewayModelSettings";

describe("GatewayQuickSetup", () => {
  it("shows only the address, secret, discovery, exact model choice and connect action by default", () => {
    const model = createDraft(1, "openai_compatible", true);
    const markup = renderToStaticMarkup(<GatewayQuickSetup
      model={model}
      savedModels={[model]}
      offline={false}
      busy={false}
      operation={null}
      update={() => undefined}
      onConnected={() => undefined}
      onManualFallbackStateChange={() => undefined}
      onOperationStateChange={() => undefined}
    />);

    expect(markup).toContain("API 接口地址");
    expect(markup).toContain("API Key");
    expect(markup).toContain("连接并读取模型");
    expect(markup).toContain("选择模型");
    expect(markup).toContain("保存并验证");
    expect(markup).toContain("输入地址和密钥后");
    expect(markup).not.toContain("另一个模型连接正在操作");
    expect(markup).not.toContain("服务商预设");
    expect(markup).not.toContain("接口模式");
    expect(markup).not.toContain("任务模型分配");
  });

  it("lets a saved same-origin credential read the model catalog without re-entering the key", () => {
    const model = {
      ...createDraft(1, "qwen", true),
      model_id: "model_qwen",
      model: "qwen3.7-plus",
      api_key_configured: true,
    };
    const markup = renderToStaticMarkup(<GatewayQuickSetup
      model={model}
      savedModels={[model]}
      offline={false}
      busy={false}
      operation={null}
      update={() => undefined}
      onConnected={() => undefined}
      onManualFallbackStateChange={() => undefined}
      onOperationStateChange={() => undefined}
    />);

    expect(markup).toContain("已安全保存，可直接读取模型");
    expect(markup).toContain("placeholder=\"••••••••（已安全保存）\"");
    expect(markup).toContain(">连接并读取模型</button>");
  });

  it("does not offer a saved credential after the draft address changes origin", () => {
    const savedModel = {
      ...createDraft(1, "qwen", true),
      model_id: "model_qwen",
      model: "qwen3.7-plus",
      api_key_configured: true,
    };
    const changedDraft = { ...savedModel, base_url: "https://relay.example/v1" };
    const markup = renderToStaticMarkup(<GatewayQuickSetup
      model={changedDraft}
      savedModels={[savedModel]}
      offline={false}
      busy={false}
      operation={null}
      update={() => undefined}
      onConnected={() => undefined}
      onManualFallbackStateChange={() => undefined}
      onOperationStateChange={() => undefined}
    />);

    expect(markup).toContain("placeholder=\"粘贴该服务的 API Key\"");
    expect(markup).not.toContain("已安全保存，可直接读取模型");
  });

  it("does not enable discovery for a visibly incomplete API key", () => {
    const model = {
      ...createDraft(1, "openai_compatible", true),
      base_url: "https://relay.example/v1",
      api_key: "short",
    };
    const markup = renderToStaticMarkup(<GatewayQuickSetup
      model={model}
      savedModels={[]}
      offline={false}
      busy={false}
      operation={null}
      update={() => undefined}
      onConnected={() => undefined}
      onManualFallbackStateChange={() => undefined}
      onOperationStateChange={() => undefined}
    />);

    expect(markup).toContain("API Key 至少需要 8 个字符");
    expect(markup).toContain("aria-invalid=\"true\"");
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*>连接并读取模型<\/button>/);
  });

  it("keeps the unsupported-catalog manual fallback on the atomic validation path", () => {
    const markup = renderToStaticMarkup(<GatewayManualModelFallback
      modelId="vendor-vision-model"
      disabled={false}
      fieldId="manual-model"
      onChange={() => undefined}
    />);

    expect(markup).toContain("高级兼容：手动模型 ID");
    expect(markup).toContain("逐字粘贴服务商提供的模型 ID");
    expect(markup).toContain("再点击“保存并验证”");
    expect(markup).toContain("图文探针通过前不会写入配置");
    expect(markup).not.toContain("打开高级设置");
    expect(markup).not.toContain("保存高级设置");
  });

  it("makes a large truncated catalog searchable and exposes provider warnings", () => {
    const catalog: GatewayDiscoveryResult = {
      status: "ok" as const,
      provider: "qwen" as const,
      normalized_base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      catalog_source: "dashscope_models" as const,
      models: [{
        upstream_model_id: "qwen-vl-exact",
        display_name: "Qwen VL Exact",
        input_modalities: ["text", "image"],
        output_modalities: ["text"],
        supports_structured_output: true,
        capability_source: "provider_metadata" as const,
      }],
      warnings: ["目录仅返回当前地域已开通模型"],
      truncated: true,
      fetched_at: "2026-09-08T00:00:00Z",
    };
    const markup = renderToStaticMarkup(<GatewayCatalogPicker
      catalog={catalog}
      query="qwen"
      selectedModelId="qwen-vl-exact"
      disabled={false}
      fieldId="catalog-model"
      onQueryChange={() => undefined}
      onModelChange={() => undefined}
      onManualFallback={() => undefined}
    />);

    expect(markup).toContain("搜索模型 ID 或名称");
    expect(markup).toContain("1 个可验证候选");
    expect(markup).toContain("图文 · 结构化");
    expect(markup).toContain("目录已截断");
    expect(markup).toContain("目录仅返回当前地域已开通模型");
    expect(markup).toContain("手动填写目录外模型 ID");
  });

  it("freezes identity inputs for an atomic connect but lets discovery be cancelled by editing", () => {
    expect(quickIdentityInputsDisabled({
      busy: false,
      blockedByOtherOperation: false,
      connectionStatus: "connecting",
    })).toBe(true);
    expect(quickIdentityInputsDisabled({
      busy: false,
      blockedByOtherOperation: false,
      connectionStatus: "idle",
    })).toBe(false);
  });
});
