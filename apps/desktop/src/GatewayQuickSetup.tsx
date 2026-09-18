import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import {
  ApiResponseError,
  connectGatewayModel,
  discoverGatewayModels,
  type GatewayConnectionResult,
  type GatewayCredentialInput,
  type GatewayDiscoveredModel,
  type GatewayDiscoveryResult,
} from "./analysis";
import {
  discoveredModelCapabilityLabel,
  matchingSavedCredentialModelId,
  preferredDiscoveredModelId,
  selectableDiscoveredModels,
  type DraftModel,
} from "./gatewayModelSettings";

type DiscoveryStatus = "idle" | "discovering" | "ready" | "empty" | "error";
type ConnectionStatus = "idle" | "connecting" | "ready" | "error";
type ManualFallbackSource = "catalog_unsupported" | "catalog_truncated";

export type GatewayOperationKind = "discovering" | "connecting";
export type GatewayOperation = { modelId: string; kind: GatewayOperationKind };

type GatewayQuickSetupProps = {
  model: DraftModel;
  savedModels: DraftModel[];
  offline: boolean;
  busy: boolean;
  operation: GatewayOperation | null;
  update: (changes: Partial<DraftModel>) => void;
  onConnected: (result: GatewayConnectionResult) => void | Promise<void>;
  onManualFallbackStateChange: (modelId: string, enabled: boolean) => void;
  onOperationStateChange: (modelId: string, kind: GatewayOperationKind | null) => void;
};

const providerLabels = {
  openai: "OpenAI",
  qwen: "阿里云百炼 / Qwen",
  openai_compatible: "OpenAI 兼容中转站",
} as const;

function publicOperationError(error: unknown, fallback: string): string {
  if (error instanceof ApiResponseError && error.detail && typeof error.detail === "object") {
    const message = error.detail.message?.trim();
    const suggestion = error.detail.suggestion?.trim();
    if (message) return suggestion && !message.includes(suggestion) ? `${message} 建议：${suggestion}` : message;
  }
  return error instanceof Error && error.message.trim() ? error.message : fallback;
}

function optionLabel(model: GatewayDiscoveredModel): string {
  const display = model.display_name.trim();
  const identity = display && display !== model.upstream_model_id
    ? `${display} — ${model.upstream_model_id}`
    : model.upstream_model_id;
  return `${identity} · ${discoveredModelCapabilityLabel(model)}`;
}

export function quickIdentityInputsDisabled({
  busy,
  blockedByOtherOperation,
  connectionStatus,
}: {
  busy: boolean;
  blockedByOtherOperation: boolean;
  connectionStatus: ConnectionStatus;
}): boolean {
  return busy || blockedByOtherOperation || connectionStatus === "connecting";
}

export function GatewayCatalogPicker({
  catalog,
  query,
  selectedModelId,
  disabled,
  fieldId,
  onQueryChange,
  onModelChange,
  onManualFallback,
}: {
  catalog: GatewayDiscoveryResult;
  query: string;
  selectedModelId: string;
  disabled: boolean;
  fieldId: string;
  onQueryChange: (query: string) => void;
  onModelChange: (modelId: string) => void;
  onManualFallback: () => void;
}) {
  const selectable = selectableDiscoveredModels(catalog.models);
  const matches = selectableDiscoveredModels(catalog.models, query);
  const selected = selectable.find((model) => model.upstream_model_id === selectedModelId) ?? null;
  const options = selected && !matches.some((model) => model.upstream_model_id === selected.upstream_model_id)
    ? [selected, ...matches]
    : matches;
  const excludedCount = catalog.models.length - selectable.length;
  const helpId = `${fieldId}-help`;

  return <div className="gateway-catalog-picker">
    <div className="gateway-catalog-tools">
      <label className="gateway-quick-field gateway-catalog-search" htmlFor={`${fieldId}-search`}>
        <span>搜索模型 ID 或名称</span>
        <input
          id={`${fieldId}-search`}
          type="search"
          autoComplete="off"
          spellCheck={false}
          value={query}
          disabled={disabled}
          placeholder="例如 qwen-vl、gpt-4.1"
          onChange={(event) => onQueryChange(event.target.value)}
        />
      </label>
      <div className="gateway-catalog-count" role="status" aria-live="polite">
        <strong>{selectable.length} 个可验证候选</strong>
        <span>服务商返回 {catalog.models.length} 个；已排除 {excludedCount} 个明确不支持图片或结构化输出的模型{query.trim() ? `；当前匹配 ${matches.length} 个` : ""}。</span>
      </div>
    </div>

    {catalog.truncated && <div className="gateway-catalog-warning" role="note">
      <div><strong>目录已截断</strong><span>当前列表不是服务商的全部模型；目录外模型仍必须通过真实图文探针。</span></div>
      <button className="text-button" type="button" disabled={disabled} onClick={onManualFallback}>手动填写目录外模型 ID</button>
    </div>}
    {catalog.warnings.length > 0 && <ul className="gateway-catalog-warnings" aria-label="服务商目录提示">
      {catalog.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}
    </ul>}

    <label className="gateway-quick-field" htmlFor={fieldId}>
      <span>选择模型</span>
      <select
        id={fieldId}
        value={selected?.upstream_model_id ?? ""}
        disabled={disabled || selectable.length === 0}
        aria-describedby={helpId}
        onChange={(event) => onModelChange(event.target.value)}
      >
        <option value="">{matches.length ? "请选择模型" : "没有匹配模型，请调整搜索"}</option>
        {options.map((item) => <option value={item.upstream_model_id} key={item.upstream_model_id}>{optionLabel(item)}</option>)}
      </select>
      <small id={helpId}>{selected
        ? `${selected.upstream_model_id} · 保存时仍会真实验证图文与结构化输出`
        : "模型 ID 原样来自服务商，不会改写大小写、连字符或版本后缀。"}</small>
      {selected && <span className="gateway-selected-capabilities" aria-label={`目录能力：${discoveredModelCapabilityLabel(selected)}`}>
        {discoveredModelCapabilityLabel(selected).split(" · ").map((label) => <span key={label}>{label}</span>)}
      </span>}
    </label>
  </div>;
}

export function GatewayManualModelFallback({
  modelId,
  disabled,
  fieldId,
  source = "catalog_unsupported",
  onChange,
  onReturnToCatalog,
}: {
  modelId: string;
  disabled: boolean;
  fieldId: string;
  source?: ManualFallbackSource;
  onChange: (modelId: string) => void;
  onReturnToCatalog?: () => void;
}) {
  const helpId = `${fieldId}-help`;
  const reason = source === "catalog_truncated"
    ? "服务商目录已截断，这个模型可能未出现在当前列表。"
    : "该服务未开放模型目录。";
  return <div className="gateway-manual-model-wrap">
    <label className="gateway-quick-field gateway-manual-model" htmlFor={fieldId}>
      <span>高级兼容：手动模型 ID</span>
      <input
        id={fieldId}
        type="text"
        required
        maxLength={120}
        value={modelId}
        disabled={disabled}
        placeholder="逐字粘贴服务商提供的模型 ID"
        spellCheck={false}
        aria-describedby={helpId}
        onChange={(event) => onChange(event.target.value)}
      />
      <small id={helpId}>{reason}请逐字粘贴模型 ID，再点击“保存并验证”；图文探针通过前不会写入配置。</small>
    </label>
    {onReturnToCatalog && <button className="text-button" type="button" disabled={disabled} onClick={onReturnToCatalog}>返回模型列表</button>}
  </div>;
}

export function GatewayQuickSetup({
  model,
  savedModels,
  offline,
  busy,
  operation,
  update,
  onConnected,
  onManualFallbackStateChange,
  onOperationStateChange,
}: GatewayQuickSetupProps) {
  const [discoveryStatus, setDiscoveryStatus] = useState<DiscoveryStatus>("idle");
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("idle");
  const [catalog, setCatalog] = useState<GatewayDiscoveryResult | null>(null);
  const [modelSearch, setModelSearch] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [manualFallbackSource, setManualFallbackSource] = useState<ManualFallbackSource | null>(null);
  const requestVersion = useRef(0);
  const activeController = useRef<AbortController | null>(null);
  const localOperation = useRef<GatewayOperationKind | null>(null);
  const savedCredentialModelId = useMemo(
    () => matchingSavedCredentialModelId(savedModels, model.base_url, model.model_id),
    [savedModels, model.base_url, model.model_id],
  );
  const enteredKeyLength = model.api_key.trim().length;
  const hasEnteredKey = enteredKeyLength > 0;
  const enteredKeyIsValid = enteredKeyLength >= 8;
  const hasCredential = hasEnteredKey ? enteredKeyIsValid : Boolean(savedCredentialModelId);
  const selectableCatalog = useMemo(
    () => selectableDiscoveredModels(catalog?.models ?? []),
    [catalog],
  );
  const selectedCatalogModel = selectableCatalog.find((item) => item.upstream_model_id === model.model) ?? null;
  const catalogHasSelection = Boolean(selectedCatalogModel);
  const manualFallbackEnabled = manualFallbackSource !== null;
  const operating = discoveryStatus === "discovering" || connectionStatus === "connecting";
  const blockedByOtherOperation = operation !== null && operation.modelId !== model.model_id;
  const identityInputsDisabled = quickIdentityInputsDisabled({ busy, blockedByOtherOperation, connectionStatus });
  const actionDisabled = busy || operation !== null || operating;

  const clearManualFallback = useCallback(() => {
    setManualFallbackSource(null);
    onManualFallbackStateChange(model.model_id, false);
  }, [model.model_id, onManualFallbackStateChange]);

  const finishLocalOperation = useCallback((kind: GatewayOperationKind) => {
    if (localOperation.current !== kind) return;
    localOperation.current = null;
    onOperationStateChange(model.model_id, null);
  }, [model.model_id, onOperationStateChange]);

  const invalidatePendingRequest = useCallback(() => {
    requestVersion.current += 1;
    activeController.current?.abort();
    activeController.current = null;
    if (localOperation.current === "discovering") finishLocalOperation("discovering");
    setCatalog(null);
    setModelSearch("");
    setDiscoveryStatus("idle");
    setConnectionStatus("idle");
    clearManualFallback();
    setMessage(null);
  }, [clearManualFallback, finishLocalOperation]);

  useEffect(() => {
    invalidatePendingRequest();
  }, [model.base_url, model.api_key, invalidatePendingRequest]);

  useEffect(() => () => {
    requestVersion.current += 1;
    activeController.current?.abort();
    activeController.current = null;
    const operationAtUnmount = localOperation.current;
    localOperation.current = null;
    if (operationAtUnmount) onOperationStateChange(model.model_id, null);
    onManualFallbackStateChange(model.model_id, false);
  }, [model.model_id, onManualFallbackStateChange, onOperationStateChange]);

  function beginLocalOperation(kind: GatewayOperationKind): boolean {
    if (localOperation.current || operation) return false;
    localOperation.current = kind;
    onOperationStateChange(model.model_id, kind);
    return true;
  }

  function credentialInput(): GatewayCredentialInput | null {
    const apiKey = model.api_key.trim();
    if (apiKey) return { base_url: model.base_url, api_key: apiKey };
    if (savedCredentialModelId) return { base_url: model.base_url, saved_model_id: savedCredentialModelId };
    return null;
  }

  async function discover(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (offline || busy || operating || blockedByOtherOperation || !beginLocalOperation("discovering")) return;
    if (!model.base_url.trim()) {
      finishLocalOperation("discovering");
      setDiscoveryStatus("error");
      setMessage("请填写 API 接口地址。");
      return;
    }
    if (hasEnteredKey && !enteredKeyIsValid) {
      finishLocalOperation("discovering");
      setDiscoveryStatus("error");
      setMessage("API Key 至少需要 8 个字符，请完整粘贴后再读取模型。");
      return;
    }
    const credential = credentialInput();
    if (!credential) {
      finishLocalOperation("discovering");
      setDiscoveryStatus("error");
      setMessage("请输入 API Key。已保存的密钥只能由当前连接在同一服务地址下复用。");
      return;
    }

    activeController.current?.abort();
    const controller = new AbortController();
    activeController.current = controller;
    const version = ++requestVersion.current;
    setCatalog(null);
    setModelSearch("");
    setDiscoveryStatus("discovering");
    setConnectionStatus("idle");
    clearManualFallback();
    setMessage("正在通过本机服务读取模型列表…");
    try {
      const result = await discoverGatewayModels(credential, controller.signal);
      if (version !== requestVersion.current) return;
      const candidates = selectableDiscoveredModels(result.models);
      setCatalog(result);
      update({ provider: result.provider });
      if (!result.models.length) {
        setDiscoveryStatus("empty");
        setMessage("该服务没有返回可选择的模型。请检查 API Key 的账号权限和已开通模型。");
        return;
      }
      if (!candidates.length) {
        update({ model: "" });
        setDiscoveryStatus("empty");
        setMessage(`服务商返回 ${result.models.length} 个模型，但都明确不支持图片或结构化输出，不能用于当前四类任务。`);
        return;
      }
      const preferred = preferredDiscoveredModelId(model.model, candidates);
      const selected = candidates.find((item) => item.upstream_model_id === preferred);
      update({
        model: preferred,
        display_name: selected?.display_name || model.display_name,
      });
      setDiscoveryStatus("ready");
      setMessage(`已从${providerLabels[result.provider]}读取 ${result.models.length} 个模型，筛出 ${candidates.length} 个图文候选。请选择后保存并验证。${result.truncated ? " 目录已截断，请留意下方提示。" : ""}`);
    } catch (error) {
      if (controller.signal.aborted || version !== requestVersion.current) return;
      setCatalog(null);
      setDiscoveryStatus("error");
      const allowsManualFallback = error instanceof ApiResponseError
        && typeof error.detail === "object"
        && error.detail?.diagnostic_code === "catalog_unsupported";
      setManualFallbackSource(allowsManualFallback ? "catalog_unsupported" : null);
      onManualFallbackStateChange(model.model_id, allowsManualFallback);
      setMessage(publicOperationError(error, "模型列表读取失败。请检查接口地址和 API Key 后重试。"));
    } finally {
      if (version === requestVersion.current) activeController.current = null;
      finishLocalOperation("discovering");
    }
  }

  async function connect() {
    if (offline || busy || operating || blockedByOtherOperation || !model.model.trim() || !beginLocalOperation("connecting")) return;
    const credential = credentialInput();
    if (!credential) {
      finishLocalOperation("connecting");
      setConnectionStatus("error");
      setMessage("请输入 API Key。已保存的密钥只能由当前连接在同一服务地址下复用。");
      return;
    }
    if (!catalogHasSelection && !manualFallbackEnabled) {
      finishLocalOperation("connecting");
      setConnectionStatus("error");
      setMessage("请先读取模型列表，并从下拉框选择模型。");
      return;
    }

    activeController.current?.abort();
    const controller = new AbortController();
    activeController.current = controller;
    const version = ++requestVersion.current;
    setConnectionStatus("connecting");
    setMessage(`正在验证 ${model.model} 的文字、图片和结构化输出能力…`);
    try {
      const result = await connectGatewayModel({
        ...credential,
        upstream_model_id: catalogHasSelection ? model.model : model.model.trim(),
        ...(manualFallbackEnabled && !catalogHasSelection ? { manual_model_id: true } : {}),
      }, controller.signal);
      if (version !== requestVersion.current) return;
      setConnectionStatus("ready");
      setMessage(`${model.model} 已通过图文验证并保存，四类任务已自动使用该模型。`);
      await onConnected(result);
    } catch (error) {
      if (controller.signal.aborted || version !== requestVersion.current) return;
      setConnectionStatus("error");
      setMessage(publicOperationError(error, "模型验证失败。配置没有保存，请检查后重试。"));
    } finally {
      if (version === requestVersion.current) activeController.current = null;
      finishLocalOperation("connecting");
    }
  }

  function enterTruncatedCatalogFallback() {
    setManualFallbackSource("catalog_truncated");
    onManualFallbackStateChange(model.model_id, true);
    update({ model: "" });
    setConnectionStatus("idle");
    setMessage("目录已截断。请逐字填写目录外模型 ID，再保存并验证；探针通过前不会写入配置。");
  }

  function returnToCatalog() {
    clearManualFallback();
    update({ model: "" });
    setConnectionStatus("idle");
    setMessage("已返回模型目录，请搜索并选择模型。");
  }

  const statusKind = discoveryStatus === "error" || connectionStatus === "error"
    ? "error"
    : discoveryStatus === "ready" || connectionStatus === "ready"
      ? "success"
      : "neutral";

  return <section className="gateway-quick-card" aria-labelledby={`quick-setup-${model.model_id}`} aria-busy={operating}>
    <div className="gateway-quick-heading">
      <div>
        <h3 id={`quick-setup-${model.model_id}`}>连接模型服务</h3>
        <p>填写接口地址和 API Key，软件会读取可用模型并自动识别服务商与协议。</p>
      </div>
      {catalog && <span className="gateway-detected-provider">已识别：{providerLabels[catalog.provider]}</span>}
    </div>

    <form className="gateway-quick-form" onSubmit={discover} noValidate>
      <label className="gateway-quick-field gateway-quick-url" htmlFor={`quick-url-${model.model_id}`}>
        <span>API 接口地址</span>
        <input
          id={`quick-url-${model.model_id}`}
          type="url"
          required
          maxLength={500}
          value={model.base_url}
          disabled={identityInputsDisabled}
          placeholder="https://服务商地址/v1"
          aria-describedby={`quick-url-help-${model.model_id}`}
          onChange={(event) => {
            invalidatePendingRequest();
            update({ base_url: event.target.value });
          }}
        />
        <small id={`quick-url-help-${model.model_id}`}>支持 GPT、阿里云百炼 Qwen 和 OpenAI 兼容中转站，可粘贴基础地址或完整请求地址。</small>
      </label>
      <label className="gateway-quick-field gateway-quick-key" htmlFor={`quick-key-${model.model_id}`}>
        <span>API Key</span>
        <input
          id={`quick-key-${model.model_id}`}
          type="password"
          autoComplete="new-password"
          maxLength={500}
          value={model.api_key}
          disabled={identityInputsDisabled}
          placeholder={savedCredentialModelId ? "••••••••（已安全保存）" : "粘贴该服务的 API Key"}
          aria-invalid={hasEnteredKey && !enteredKeyIsValid}
          aria-describedby={`quick-key-help-${model.model_id}`}
          onChange={(event) => {
            invalidatePendingRequest();
            update({ api_key: event.target.value });
          }}
        />
        <small id={`quick-key-help-${model.model_id}`}>{hasEnteredKey
          ? enteredKeyIsValid
            ? "将使用刚输入的密钥；成功保存后不会回显。"
            : "API Key 至少需要 8 个字符，请完整粘贴。"
          : savedCredentialModelId
            ? "当前连接的密钥已安全保存，可直接读取模型；不会借用同域名下其他连接的密钥。"
            : "密钥只交给本机服务，并由 Windows 本机加密保存。"}</small>
      </label>
      <div className="gateway-discover-action">
        <button className="secondary-button" type="submit" disabled={offline || actionDisabled || !model.base_url.trim() || !hasCredential} aria-describedby={`quick-status-${model.model_id}`}>
          {discoveryStatus === "discovering" ? "正在读取模型…" : catalog ? "重新读取模型" : "连接并读取模型"}
        </button>
      </div>
    </form>

    <div className="gateway-model-choice-row">
      {manualFallbackEnabled
        ? <GatewayManualModelFallback
          modelId={model.model}
          disabled={identityInputsDisabled}
          fieldId={`quick-manual-model-${model.model_id}`}
          source={manualFallbackSource}
          onChange={(modelId) => {
            update({ model: modelId, display_name: model.display_name || modelId });
            setConnectionStatus("idle");
          }}
          onReturnToCatalog={catalog ? returnToCatalog : undefined}
        />
        : catalog
          ? <GatewayCatalogPicker
            catalog={catalog}
            query={modelSearch}
            selectedModelId={model.model}
            disabled={identityInputsDisabled}
            fieldId={`quick-model-${model.model_id}`}
            onQueryChange={setModelSearch}
            onModelChange={(exactId) => {
              const selected = selectableCatalog.find((item) => item.upstream_model_id === exactId);
              update({ model: exactId, display_name: selected?.display_name || exactId });
              setConnectionStatus("idle");
            }}
            onManualFallback={enterTruncatedCatalogFallback}
          />
          : <label className="gateway-quick-field" htmlFor={`quick-model-${model.model_id}`}>
            <span>选择模型</span>
            <select id={`quick-model-${model.model_id}`} value={model.model || ""} disabled aria-describedby={`quick-model-help-${model.model_id}`}>
              {model.model && <option value={model.model}>{model.model}</option>}
              <option value="">先读取模型列表</option>
            </select>
            <small id={`quick-model-help-${model.model_id}`}>{model.model
              ? `当前已保存：${model.model}。重新读取列表后可更换。`
              : "模型 ID 将直接来自服务商，不再需要手工猜测大小写和连字符。"}</small>
          </label>}
      <button className="primary-button" type="button" disabled={offline || actionDisabled || !model.model.trim() || (!catalogHasSelection && !manualFallbackEnabled)} aria-describedby={`quick-status-${model.model_id}`} onClick={() => void connect()}>
        {connectionStatus === "connecting" ? "正在验证并保存…" : "保存并验证"}
      </button>
    </div>

    <div className={`gateway-discovery-status gateway-discovery-${statusKind}`} id={`quick-status-${model.model_id}`} role={statusKind === "error" ? "alert" : "status"} aria-live={statusKind === "error" ? "assertive" : "polite"}>
      <span>{message ?? (offline ? "本机服务未连接，暂时无法读取模型。" : operation && operation.modelId !== model.model_id ? "另一个模型连接正在操作，请等待完成。" : "输入地址和密钥后，点击“连接并读取模型”。")}</span>
      {manualFallbackEnabled && <strong>只有本区域的“保存并验证”会提交该模型；验证失败不会覆盖现有配置。</strong>}
    </div>
  </section>;
}
