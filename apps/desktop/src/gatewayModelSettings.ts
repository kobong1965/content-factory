import type {
  GatewayApiMode,
  GatewayInputModality,
  GatewayModelSettings,
  GatewayProvider,
  GatewayPurpose,
  GatewaySettings,
} from "@content-factory/contracts";

import type { GatewayDiscoveredModel, GatewayModelSaveInput } from "./analysis";

export type { GatewayProvider } from "@content-factory/contracts";

export type DraftModel = Omit<GatewayModelSaveInput, "api_key"> & {
  api_key: string;
  api_key_configured: boolean;
};

export type ActiveRouting = Record<GatewayPurpose, string>;

export const gatewayModelLimitReachedDescription = "已达 8 个连接上限；当前版本为保护排队任务不提供旧连接删除，请继续使用已有连接。安全归档将在后续版本提供。";

export const purposeOptions: ReadonlyArray<{
  value: GatewayPurpose;
  label: string;
  detail: string;
  needsImage: boolean;
}> = [
  { value: "video_review", label: "视频审核", detail: "口播 + OCR + 关键帧", needsImage: true },
  { value: "analysis", label: "爆点研究", detail: "口播 + OCR + 关键帧图片", needsImage: true },
  { value: "material", label: "素材识别", detail: "文字 + 关键帧图片", needsImage: true },
  { value: "script", label: "脚本生成", detail: "商品事实 + 结构化文字", needsImage: false },
];

export const providerPresets: ReadonlyArray<{
  value: GatewayProvider;
  label: string;
  shortLabel: string;
  description: string;
  baseUrl: string;
  apiMode: GatewayApiMode;
  displayName: string;
  modelPlaceholder: string;
}> = [
  {
    value: "openai",
    label: "OpenAI / GPT",
    shortLabel: "GPT",
    description: "OpenAI 官方地址，默认使用 Responses API",
    baseUrl: "https://api.openai.com/v1",
    apiMode: "responses",
    displayName: "GPT 多模态模型",
    modelPlaceholder: "输入当前账号可用的 GPT 视觉模型",
  },
  {
    value: "qwen",
    label: "Qwen / 阿里云百炼（中国内地）",
    shortLabel: "Qwen",
    description: "阿里云百炼中国内地 OpenAI 兼容地址；API Key 必须与已开通模型的地域匹配，图文与结构化任务优先使用 qwen3.7-plus",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    apiMode: "chat_completions",
    displayName: "Qwen 多模态模型",
    modelPlaceholder: "图文与结构化任务优先 qwen3.7-plus；或粘贴控制台中的精确模型标识",
  },
  {
    value: "openai_compatible",
    label: "OpenAI 兼容中转站",
    shortLabel: "兼容中转站",
    description: "使用服务商给出的完整基础地址和模型标识",
    baseUrl: "",
    apiMode: "chat_completions",
    displayName: "兼容中转站模型",
    modelPlaceholder: "例如：服务商控制台显示的视觉模型标识",
  },
  {
    value: "custom",
    label: "自定义兼容服务",
    shortLabel: "自定义",
    description: "手动配置兼容 Responses 或 Chat Completions 的服务",
    baseUrl: "",
    apiMode: "chat_completions",
    displayName: "自定义多模态模型",
    modelPlaceholder: "输入服务商支持的模型标识",
  },
];

const imagePurposes = new Set<GatewayPurpose>(["video_review", "analysis", "material"]);

function newModelId(): string {
  const random = globalThis.crypto?.randomUUID?.().replaceAll("-", "").slice(0, 16);
  return `model_${random || Date.now().toString(36)}`;
}

function presetFor(provider: GatewayProvider) {
  return providerPresets.find((item) => item.value === provider) ?? providerPresets.at(-1)!;
}

export function inferProvider(model: Pick<GatewayModelSettings, "base_url" | "model"> & { provider?: string }): GatewayProvider {
  if (providerPresets.some((preset) => preset.value === model.provider)) return model.provider as GatewayProvider;
  const host = (() => {
    try { return new URL(model.base_url).hostname.toLowerCase().replace(/\.$/, ""); } catch { return ""; }
  })();
  const isOfficialQwenHost = host === "dashscope.aliyuncs.com"
    || host.endsWith(".dashscope.aliyuncs.com")
    || /^dashscope-[a-z0-9-]+\.aliyuncs\.com$/.test(host)
    || host.endsWith(".maas.aliyuncs.com");
  if (isOfficialQwenHost) return "qwen";
  if (host === "api.openai.com") return "openai";
  return model.base_url ? "openai_compatible" : "custom";
}

function credentialOrigin(value: string): string | null {
  try {
    const parsed = new URL(value.trim());
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.origin.toLowerCase() : null;
  } catch {
    return null;
  }
}

export function matchingSavedCredentialModelId(
  models: ReadonlyArray<Pick<DraftModel, "model_id" | "base_url" | "api_key_configured">>,
  baseUrl: string,
  currentModelId: string,
): string | null {
  const origin = credentialOrigin(baseUrl);
  if (!origin) return null;
  const current = models.find((model) => model.model_id === currentModelId);
  return current?.api_key_configured && credentialOrigin(current.base_url) === origin
    ? current.model_id
    : null;
}

export function selectableDiscoveredModels(
  models: ReadonlyArray<GatewayDiscoveredModel>,
  query = "",
): GatewayDiscoveredModel[] {
  const normalizedQuery = query.trim().toLocaleLowerCase("zh-CN");
  return models.filter((model) => {
    const providerMetadata = model.capability_source === "provider_metadata";
    const explicitlyLacksImage = providerMetadata
      && model.input_modalities !== null
      && !model.input_modalities.includes("image");
    const explicitlyLacksStructuredOutput = providerMetadata
      && model.supports_structured_output === false;
    if (explicitlyLacksImage || explicitlyLacksStructuredOutput) return false;
    if (!normalizedQuery) return true;
    return `${model.upstream_model_id} ${model.display_name}`
      .toLocaleLowerCase("zh-CN")
      .includes(normalizedQuery);
  });
}

export function discoveredModelCapabilityLabel(model: GatewayDiscoveredModel): string {
  if (model.capability_source === "unknown") return "图文与结构化能力待实测";
  const image = model.input_modalities === null
    ? "图片待实测"
    : model.input_modalities.includes("image") ? "图文" : "不支持图片";
  const structured = model.supports_structured_output === null
    ? "结构化待实测"
    : model.supports_structured_output ? "结构化" : "不支持结构化";
  return `${image} · ${structured}`;
}

export function buildAdvancedGatewayModels(
  drafts: ReadonlyArray<DraftModel>,
  persistedModels: ReadonlyArray<DraftModel>,
): GatewayModelSaveInput[] | null {
  const persistedById = new Map(persistedModels.map((model) => [model.model_id, model]));
  if (drafts.some((draft) => !persistedById.has(draft.model_id))) return null;
  const draftsById = new Map(drafts.map((model) => [model.model_id, model]));
  return persistedModels.map((identity) => {
    const draft = draftsById.get(identity.model_id);
    return {
      model_id: identity.model_id,
      provider: identity.provider,
      display_name: draft?.display_name ?? identity.display_name,
      base_url: identity.base_url,
      model: identity.model,
      api_mode: identity.api_mode,
      modalities: [...identity.modalities],
      purposes: [...identity.purposes],
      enabled: identity.enabled,
    };
  });
}

export function updateOwnedModelId(
  current: string | null,
  modelId: string,
  enabled: boolean,
): string | null {
  return enabled ? modelId : current === modelId ? null : current;
}

export function verifiedRouteCount(
  models: ReadonlyArray<DraftModel>,
  routing: ActiveRouting,
): number {
  return purposeOptions.filter((purpose) => {
    const model = models.find((item) => item.model_id === routing[purpose.value] && item.api_key_configured);
    return model ? supportsPurpose(model, purpose.value) : false;
  }).length;
}

export function preferredDiscoveredModelId(
  savedModelId: string,
  models: ReadonlyArray<Pick<GatewayDiscoveredModel, "upstream_model_id" | "display_name">>,
): string {
  const saved = savedModelId.trim();
  if (!saved) return "";
  const exact = models.find((model) => model.upstream_model_id === saved);
  if (exact) return exact.upstream_model_id;
  const folded = saved.toLocaleLowerCase("en-US");
  return models.find((model) => model.upstream_model_id.toLocaleLowerCase("en-US") === folded)?.upstream_model_id ?? "";
}

export function createDraft(index: number, provider: GatewayProvider = "openai_compatible", primary = false): DraftModel {
  const preset = presetFor(provider);
  return {
    model_id: newModelId(),
    provider,
    display_name: primary && provider === "openai_compatible"
      ? "主力多模态模型"
      : index > 1 ? `${preset.displayName} ${index}` : preset.displayName,
    base_url: preset.baseUrl,
    model: "",
    api_mode: preset.apiMode,
    modalities: ["text", "image"],
    purposes: purposeOptions.map((purpose) => purpose.value),
    enabled: true,
    api_key: "",
    api_key_configured: false,
  };
}

export function toDraft(model: GatewayModelSettings): DraftModel {
  const provider = inferProvider(model);
  const purposes = model.purposes.length
    ? [...model.purposes]
    : model.modalities.includes("image")
      ? purposeOptions.map((purpose) => purpose.value)
      : (["script"] satisfies GatewayPurpose[]);
  return {
    model_id: model.model_id,
    provider,
    display_name: model.display_name,
    base_url: model.base_url,
    model: model.model,
    api_mode: model.api_mode,
    modalities: [...model.modalities],
    purposes,
    enabled: model.enabled,
    api_key: "",
    api_key_configured: model.api_key_configured,
  };
}

export function applyProviderPreset(model: DraftModel, provider: GatewayProvider): DraftModel {
  if (model.provider === provider) return model;
  const preset = presetFor(provider);
  return {
    ...model,
    provider,
    display_name: preset.displayName,
    base_url: preset.baseUrl,
    model: "",
    api_mode: preset.apiMode,
    modalities: ["text", "image"],
    purposes: purposeOptions.map((purpose) => purpose.value),
    api_key: "",
    api_key_configured: false,
  };
}

export function initialGatewayDraftState(gateway: GatewaySettings): { models: DraftModel[]; routing: ActiveRouting } {
  const models = gateway.models.length
    ? gateway.models.map((storedModel) => {
      const model = toDraft(storedModel);
      if (gateway.schema_version === "2.0.0" && model.modalities.includes("image") && !model.purposes.includes("video_review")) {
        const purposes: GatewayPurpose[] = [...model.purposes, "video_review"];
        return { ...model, purposes };
      }
      return model;
    })
    : [createDraft(1, "openai_compatible", true)];
  const firstText = models.find((model) => model.enabled && model.modalities.includes("text"))?.model_id ?? models[0]!.model_id;
  const firstVision = models.find((model) => model.enabled && model.modalities.includes("image"))?.model_id ?? firstText;
  const savedRouting = gateway.routing as Partial<Record<GatewayPurpose, string | null>>;
  return {
    models,
    routing: Object.fromEntries(purposeOptions.map((purpose) => [
      purpose.value,
      savedRouting[purpose.value] ?? (purpose.needsImage ? savedRouting.analysis ?? firstVision : firstText),
    ])) as ActiveRouting,
  };
}

export function purposeNeedsImage(purpose: GatewayPurpose): boolean {
  return imagePurposes.has(purpose);
}

export function supportsPurpose(model: DraftModel, purpose: GatewayPurpose): boolean {
  if (!model.enabled || !model.modalities.includes("text") || !model.purposes.includes(purpose)) return false;
  return !purposeNeedsImage(purpose) || model.modalities.includes("image");
}

export function setImageCapability(model: DraftModel, enabled: boolean): DraftModel {
  const modalities: GatewayInputModality[] = enabled
    ? Array.from(new Set<GatewayInputModality>([...model.modalities, "image"]))
    : model.modalities.filter((item) => item !== "image");
  const purposes = enabled
    ? model.purposes
    : model.purposes.filter((purpose) => !purposeNeedsImage(purpose));
  return { ...model, modalities, purposes };
}

export function modelPlaceholder(provider: GatewayProvider): string {
  return presetFor(provider).modelPlaceholder;
}

export function gatewayRequestUrl(baseUrl: string, mode: GatewayApiMode): string {
  const suffix = mode === "responses" ? "responses" : "chat/completions";
  const base = baseUrl.trim().replace(/\/+$/, "");
  if (base.endsWith("/responses") || base.endsWith("/chat/completions")) {
    return base.replace(/\/(?:responses|chat\/completions)$/, `/${suffix}`);
  }
  return `${base}/${suffix}`;
}

export function gatewayModeLabel(mode: GatewayApiMode): string {
  return mode === "responses" ? "Responses API" : "Chat Completions";
}

export function endpointGuidance(model: Pick<DraftModel, "base_url" | "provider" | "api_mode">): string | null {
  if (!model.base_url.trim()) return null;
  const path = (() => {
    try { return new URL(model.base_url).pathname.replace(/\/+$/, ""); } catch { return ""; }
  })();
  if (model.provider === "qwen" && model.api_mode !== "chat_completions") {
    return "当前 Qwen 预设使用 Chat Completions；仅在你的模型与账号明确支持 Responses API 时切换。";
  }
  if (model.provider === "openai" && path !== "/v1") {
    return "OpenAI 官方基础地址应为 https://api.openai.com/v1；如果你使用的是中转站，请改选“OpenAI 兼容中转站”。";
  }
  return null;
}

export function knownModelCapabilityWarning(
  model: Pick<DraftModel, "provider" | "model" | "modalities" | "purposes">,
): string | null {
  const modelId = model.model.trim().toLowerCase();
  const needsVision = model.modalities.includes("image")
    || model.purposes.some((purpose) => purposeNeedsImage(purpose));
  if (model.provider === "qwen" && modelId === "qwen3.7-max" && needsVision) {
    return "qwen3.7-max 当前别名是纯文本模型，不能接收视频关键帧或商品图片。请改为 qwen3.7-plus 后再测试；若只保留 qwen3.7-max，请关闭“图片”，并仅分配脚本生成。";
  }
  return null;
}

export function gatewayConnectionErrorMessage(
  error: unknown,
  model: Pick<GatewayModelSettings, "base_url" | "api_mode" | "model">,
): string {
  type StructuredGatewayDetail = {
    diagnostic_code?: unknown;
    message?: unknown;
    endpoint_url?: unknown;
    request_url?: unknown;
    endpoint?: unknown;
    api_mode?: unknown;
    model?: unknown;
    suggestion?: unknown;
  };

  type DiagnosticKind =
    | "model"
    | "image"
    | "image_invalid"
    | "credential"
    | "structured_output"
    | "parameter"
    | "endpoint"
    | "rate_limit"
    | "upstream"
    | "connection"
    | "unknown";

  const publicText = (value: string, limit = 700) => value
    .replace(/[\u0000-\u001f\u007f]+/g, " ")
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi, "Bearer [已隐藏]")
    .replace(/\b(?:sk|ak|rk)-[A-Za-z0-9._-]{6,}\b/gi, "[API Key 已隐藏]")
    .replace(/((?:api[_ -]?key|access[_ -]?token|secret|authorization)\s*[:=]\s*)["']?[^"',;，。\s)]+/gi, "$1[已隐藏]")
    .replace(/([?&](?:api[_-]?key|apikey|access_token|token|key|secret)=)[^&#\s)]+/gi, "$1[已隐藏]")
    .replace(/\s{2,}/g, " ")
    .trim()
    .slice(0, limit);
  const safeEndpoint = (value: string | null): string | null => {
    if (!value) return null;
    try {
      const parsed = new URL(value);
      if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
      parsed.username = "";
      parsed.password = "";
      parsed.search = "";
      parsed.hash = "";
      return parsed.toString();
    } catch {
      return null;
    }
  };
  const diagnosisKind = (code: string | null, message: string): DiagnosticKind => {
    const value = `${code ?? ""} ${message}`.toLowerCase();
    if (/model[_ -]?(?:not[_ -]?found|missing|unavailable)|模型.{0,12}(不存在|未配置|未找到)|(不存在|未找到).{0,12}模型/i.test(value)) return "model";
    if (/image[_ -]?input[_ -]?invalid|图片输入.{0,12}(尺寸|格式|编码).{0,12}(无效|不符合|不支持)/i.test(value)) return "image_invalid";
    if (/image[_ -]?(?:input[_ -]?)?(?:unsupported|not[_ -]?supported|invalid)|vision[_ -]?(?:input[_ -]?)?(?:unsupported|not[_ -]?supported)|multimodal[_ -]?unsupported|不支持.{0,12}(图片|图像|视觉)|(图片|图像|视觉).{0,12}(不支持|无能力)/i.test(value)) return "image";
    if (/credential[_ -]?region[_ -]?mismatch|api[_ -]?key[_ -]?region[_ -]?mismatch|region[_ -]?mismatch|authentication[_ -]?failed|invalid[_ -]?(?:api[_ -]?)?key|密钥.{0,12}(地域|地区|无效|拒绝)|(地域|地区).{0,12}(密钥|api key)/i.test(value)) return "credential";
    if (/structured[_ -]?output[_ -]?(?:unsupported|invalid)|json[_ -]?schema[_ -]?(?:unsupported|invalid)|不支持.{0,12}(结构化|json schema)|(结构化|json schema).{0,12}不支持/i.test(value)) return "structured_output";
    if (/invalid[_ -]?parameter(?:[_ -]?error)?|request[_ -]?(?:parameter[_ -]?)?(?:invalid|incompatible)|bad[_ -]?request|参数.{0,12}(无效|错误|不兼容)|请求不兼容/i.test(value)) return "parameter";
    if (/endpoint[_ -]?not[_ -]?found|接口路径.{0,12}(不存在|找不到)|没有这个接口路径/i.test(value)) return "endpoint";
    if (/rate[_ -]?limit|too many requests|限流/i.test(value)) return "rate_limit";
    if (/upstream[_ -]?unavailable|中转站暂时不可用/i.test(value)) return "upstream";
    if (/connection[_ -]?(?:failed|interrupted)|secure[_ -]?connection[_ -]?interrupted|无法连接|连接中断|本机服务未启动/i.test(value)) return "connection";
    return "unknown";
  };

  const candidate = error && typeof error === "object" && "detail" in error
    ? (error as { detail?: unknown }).detail
    : undefined;
  const detail = candidate && typeof candidate === "object"
    ? candidate as StructuredGatewayDetail
    : null;
  const stringValue = (value: unknown) => typeof value === "string" && value.trim() ? value.trim() : null;
  const raw = stringValue(detail?.message)
    ?? (typeof candidate === "string" && candidate.trim() ? candidate.trim() : null)
    ?? (error instanceof Error && error.message.trim() ? error.message.trim() : "模型连接失败");
  const diagnosticCode = stringValue(detail?.diagnostic_code);
  const endpoint = safeEndpoint(stringValue(detail?.endpoint_url)
    ?? stringValue(detail?.request_url)
    ?? stringValue(detail?.endpoint)
    ?? gatewayRequestUrl(model.base_url, model.api_mode));
  const apiMode = stringValue(detail?.api_mode) ?? model.api_mode;
  const testedModel = publicText(stringValue(detail?.model) ?? model.model, 120);
  const suggestion = stringValue(detail?.suggestion) ? publicText(stringValue(detail?.suggestion)!, 300) : null;
  const safeMessage = publicText(raw) || "模型连接失败";
  const mode = apiMode === "responses" || apiMode === "chat_completions"
    ? gatewayModeLabel(apiMode)
    : "未知接口模式";
  const context: string[] = [];
  if (testedModel && !safeMessage.includes(testedModel)) context.push(`模型：${testedModel}`);
  if (endpoint && !safeMessage.includes(endpoint)) context.push(`实际请求：POST ${endpoint}`);
  if (!safeMessage.includes(apiMode) && !safeMessage.includes(mode)) context.push(`模式：${mode}`);
  const withContext = `${safeMessage}${/[.!?。！？]$/.test(safeMessage) ? "" : "。"}${context.length ? `${context.join("；")}。` : ""}`;
  const withSuggestion = suggestion && !withContext.includes(suggestion)
    ? `${withContext}建议：${suggestion}${/[.!?。！？]$/.test(suggestion) ? "" : "。"}`
    : withContext;
  const kind = diagnosisKind(diagnosticCode, raw);
  const isQwenConnection = inferProvider({ base_url: endpoint ?? model.base_url, model: model.model }) === "qwen";
  const qwenRegionHint = isQwenConnection
    ? "Qwen 百炼还需确认 API Key、模型与接口地址属于同一地域。"
    : "";
  const qwenVisionHint = isQwenConnection
    ? model.model.trim().toLowerCase() === "qwen3.7-max"
      ? "当前 qwen3.7-max 别名是纯文本模型；Qwen 图文与结构化任务优先使用 qwen3.7-plus，不要只根据 Max 名称判断视觉能力。"
      : "Qwen 图文与结构化任务可优先使用 qwen3.7-plus，并以百炼控制台实际开通能力为准。"
    : "";
  const presentation: Record<DiagnosticKind, { title: string; action: string }> = {
    model: {
      title: "模型不存在",
      action: "请从服务商控制台逐字复制模型标识，重点核对连字符、版本后缀和大小写。",
    },
    image: {
      title: "图片能力不匹配",
      action: `当前测试按已保存能力发送了图片。请改用明确支持视觉输入的模型；纯文本模型请关闭“图片”，并只分配脚本生成。${qwenVisionHint}`,
    },
    image_invalid: {
      title: "图片测试输入不符合限制",
      action: "这通常是验证图片的尺寸、格式或编码不符合服务商限制，不代表模型一定不支持图片。请更新或重启本机服务后再测试。",
    },
    credential: {
      title: "密钥或地域不匹配",
      action: `请检查 API Key 是否有效、是否已开通当前模型。${qwenRegionHint}`,
    },
    structured_output: {
      title: "结构化输出不支持",
      action: "该模型或兼容网关不接受当前 JSON Schema 输出参数。请改用支持结构化 JSON 输出的模型或正确的接口模式。",
    },
    parameter: {
      title: "请求参数不兼容",
      action: "服务商拒绝了当前参数组合。请核对接口模式；如果是纯文本模型，请关闭“图片”；若明确支持图片，请再确认服务商是否支持结构化 JSON 输出。",
    },
    endpoint: {
      title: "接口路径不正确",
      action: `请按上方“实际请求”核对 API 地址，并确认服务商支持 ${mode}。`,
    },
    rate_limit: { title: "服务商限流", action: "请稍后重试，或检查当前账户的配额与并发限制。" },
    upstream: { title: "服务商暂时不可用", action: "请稍后重试；当前配置不会自动更换模型。" },
    connection: { title: "连接失败", action: "请检查本机服务、网络连接和 HTTPS 接口地址后重试。" },
    unknown: { title: "模型连接失败", action: "请核对模型标识、接口模式和已声明的输入能力后重试。" },
  };
  const selected = presentation[kind];
  const action = withSuggestion.includes(selected.action) ? "" : `处理建议：${selected.action}`;
  return `${selected.title}：${withSuggestion}${action}`;
}
