import { useCallback, useEffect, useState, type FormEvent } from "react";

import { GatewayModelCard } from "./GatewayModelCard";
import { GatewayQuickSetup, type GatewayOperation, type GatewayOperationKind } from "./GatewayQuickSetup";
import type { GatewayConnectionResult } from "./analysis";
import {
  buildAdvancedGatewayModels,
  createDraft,
  gatewayModelLimitReachedDescription,
  initialGatewayDraftState,
  providerPresets,
  purposeOptions,
  supportsPurpose,
  updateOwnedModelId,
  verifiedRouteCount,
  type ActiveRouting,
  type DraftModel,
} from "./gatewayModelSettings";
import type { useS3Analysis } from "./useS3Analysis";
import { runDraftTransition } from "./unsavedChanges";

export function GatewaySettings({ analysis, onDirtyChange }: {
  analysis: ReturnType<typeof useS3Analysis>;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [initial] = useState(() => initialGatewayDraftState(analysis.gateway));
  const [baseline, setBaseline] = useState(initial);
  const [models, setModels] = useState<DraftModel[]>(initial.models);
  const [routing, setRouting] = useState<ActiveRouting>(initial.routing);
  const [selectedModelId, setSelectedModelId] = useState(initial.models[0]?.model_id ?? "");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [manualFallbackModelId, setManualFallbackModelId] = useState<string | null>(null);
  const [gatewayOperation, setGatewayOperation] = useState<GatewayOperation | null>(null);
  const gatewayRevision = JSON.stringify(analysis.gateway);

  useEffect(() => {
    const next = initialGatewayDraftState(analysis.gateway);
    setBaseline(next);
    setModels(next.models);
    setRouting(next.routing);
    setSelectedModelId((current) => next.models.some((model) => model.model_id === current) ? current : next.models[0]?.model_id ?? "");
  }, [gatewayRevision]);

  const dirty = JSON.stringify({ models, routing }) !== JSON.stringify(baseline);
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);

  const handleManualFallbackStateChange = useCallback((modelId: string, enabled: boolean) => {
    setManualFallbackModelId((current) => updateOwnedModelId(current, modelId, enabled));
  }, []);

  const handleGatewayOperationStateChange = useCallback((modelId: string, kind: GatewayOperationKind | null) => {
    if (kind) analysis.clearActionMessage();
    setGatewayOperation((current) => kind
      ? { modelId, kind }
      : current?.modelId === modelId ? null : current);
  }, [analysis.clearActionMessage]);

  function updateModel(modelId: string, changes: Partial<DraftModel>) {
    if (analysis.isSubmitting) return;
    setModels((current) => current.map((item) => item.model_id === modelId ? { ...item, ...changes } : item));
  }

  const gatewayOperationBusy = gatewayOperation !== null;

  function addModel() {
    if (analysis.isSubmitting || gatewayOperationBusy || models.length >= 8) return;
    const draft = createDraft(models.length + 1, "openai_compatible");
    setModels((current) => [...current, draft]);
    setSelectedModelId(draft.model_id);
    setAdvancedOpen(false);
  }

  function removeModel(modelId: string) {
    if (analysis.isSubmitting || gatewayOperationBusy || persistedModelIds.has(modelId) || models.length === 1 || Object.values(routing).includes(modelId)) return;
    const removedIndex = models.findIndex((item) => item.model_id === modelId);
    const nextSelection = models[removedIndex + 1] ?? models[removedIndex - 1];
    setModels((current) => current.filter((item) => item.model_id !== modelId));
    setSelectedModelId(nextSelection?.model_id ?? "");
  }

  const persistedModelIds = new Set(baseline.models.map((model) => model.model_id));
  const verifiedModelIds = new Set(baseline.models
    .filter((model) => model.api_key_configured)
    .map((model) => model.model_id));
  const routeIsValid = purposeOptions.every((purpose) => {
    const selected = models.find((model) => model.model_id === routing[purpose.value]);
    return selected && verifiedModelIds.has(selected.model_id) ? supportsPurpose(selected, purpose.value) : false;
  });
  const advancedModels = buildAdvancedGatewayModels(models, baseline.models);
  const invalidDisplayName = models.find((model) => !model.display_name.trim());
  const legacySaveDisabledReason = analysis.connectionState === "offline"
    ? "本机服务未连接，暂时无法保存。"
    : analysis.isSubmitting
      ? "当前操作尚未完成，请稍候。"
      : gatewayOperationBusy
        ? `正在${gatewayOperation?.kind === "connecting" ? "验证并保存连接" : "读取模型目录"}，完成前不能修改或保存高级设置。`
        : advancedModels === null
          ? "存在尚未验证的新连接，请先使用上方“保存并验证”。"
          : invalidDisplayName
            ? "请填写模型显示名称。"
            : !routeIsValid
              ? "请为四类任务选择能力匹配的已启用模型。"
              : null;
  const manualFallbackBlocksAdvancedSave = manualFallbackModelId === selectedModelId;
  const saveDisabledReason = manualFallbackBlocksAdvancedSave
    ? "当前模型正在使用手动兼容验证，请回到上方点击“保存并验证”；验证通过前不能用旧流程直接写入。"
    : legacySaveDisabledReason;
  const canSave = saveDisabledReason === null;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSave || !advancedModels) return;
    const saved = await analysis.saveGateway({
      default_model_id: routing.analysis,
      routing,
      models: advancedModels,
    });
    if (saved) setModels((current) => current.map((model) => ({ ...model, api_key: "" })));
  }

  const configuredCount = analysis.gateway.models.filter((model) => model.api_key_configured && model.enabled).length;
  const routeSummary = verifiedRouteCount(baseline.models, routing);
  const selectedModel = models.find((model) => model.model_id === selectedModelId) ?? models[0];
  const selectedModelIndex = selectedModel ? models.findIndex((model) => model.model_id === selectedModel.model_id) : -1;
  const selectedPersistedIdentity = selectedModel
    ? baseline.models.find((model) => model.model_id === selectedModel.model_id) ?? null
    : null;

  async function acceptConnectedModel(result: GatewayConnectionResult) {
    const next = initialGatewayDraftState(result.settings);
    setBaseline(next);
    setModels(next.models);
    setRouting(next.routing);
    setSelectedModelId(result.connected_model_id);
    setAdvancedOpen(false);
    await analysis.acceptGatewayConnection(result);
  }

  return <div className="content settings-content">
    <header className="settings-page-head" aria-labelledby="settings-title">
      <div>
        <h1 id="settings-title" data-page-title tabIndex={-1}>模型连接</h1>
        <p>填写接口地址和 API Key，读取服务商提供的精确模型列表；选择后会自动验证文字、图片和结构化输出并完成任务分配。</p>
      </div>
      <div className={`gateway-status ${configuredCount ? "gateway-ready" : "gateway-missing"}`}>
        <span>{configuredCount ? "模型路由已配置" : "等待配置"}</span>
        <strong>{configuredCount ? `${configuredCount} 个模型已保存` : "添加 GPT 或 Qwen"}</strong>
        <small>{routeSummary} / {purposeOptions.length} 类任务能力匹配 · 密钥由 Windows 本机加密保存</small>
      </div>
    </header>

    {analysis.connectionState === "offline" && <div className="service-alert" role="alert">
      <strong>本机服务未启动</strong>
      <span>当前窗口无法读取或保存模型配置。请关闭后，从桌面“爆款内容工厂”快捷方式重新打开。</span>
    </div>}

    <section className="settings-panel" aria-labelledby="gateway-form-title">
      <div className="section-heading model-settings-heading">
        <h2 id="gateway-form-title">模型服务</h2>
        <p className="section-copy" id="gateway-model-limit-description">{analysis.isSubmitting ? "当前操作尚未完成，模型配置已临时锁定。" : gatewayOperationBusy ? `正在${gatewayOperation?.kind === "connecting" ? "验证并保存模型" : "读取模型目录"}，模型列表已临时锁定。` : models.length >= 8 ? gatewayModelLimitReachedDescription : "支持 GPT、Qwen 和 OpenAI 兼容中转站，最多保存 8 个模型。"}</p>
      </div>

      <div className="model-settings-workbench">
        <aside className="model-master-panel" aria-label="已配置模型">
          <div className="model-master-heading"><strong>模型列表</strong><span>{models.length} / 8</span></div>
          <div className="model-master-list">
            {models.map((model, index) => {
              const assigned = purposeOptions.filter((purpose) => routing[purpose.value] === model.model_id);
              const preset = providerPresets.find((item) => item.value === model.provider);
              return <button type="button" className={model.model_id === selectedModel?.model_id ? "model-master-active" : undefined} disabled={analysis.isSubmitting || gatewayOperationBusy} aria-pressed={model.model_id === selectedModel?.model_id} aria-controls="selected-model-settings" onClick={() => {
                if (model.model_id === selectedModel?.model_id) return;
                runDraftTransition(() => {
                  setSelectedModelId(model.model_id);
                  setAdvancedOpen(false);
                }, "preserves-draft");
              }} key={model.model_id}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{model.display_name || `模型 ${index + 1}`}</strong>
                <small>{preset?.shortLabel ?? "兼容模型"} · {assigned.length ? `${assigned.length} 项任务` : "未分配任务"}</small>
              </button>;
            })}
          </div>
          <button className="secondary-button model-add-connection" type="button" disabled={analysis.isSubmitting || gatewayOperationBusy || models.length >= 8} aria-describedby="gateway-model-limit-description" onClick={addModel}>
            添加模型服务
          </button>
        </aside>

        <div className="model-detail-panel" id="selected-model-settings">
          {selectedModel && <GatewayQuickSetup
            key={`quick-${selectedModel.model_id}`}
            model={selectedModel}
            savedModels={baseline.models}
            busy={analysis.isSubmitting}
            operation={gatewayOperation}
            offline={analysis.connectionState === "offline"}
            update={(changes) => updateModel(selectedModel.model_id, changes)}
            onConnected={acceptConnectedModel}
            onManualFallbackStateChange={handleManualFallbackStateChange}
            onOperationStateChange={handleGatewayOperationStateChange}
          />}

          <details className="gateway-advanced-settings" open={advancedOpen} onToggle={(event) => {
            if (gatewayOperationBusy) {
              setAdvancedOpen(false);
              return;
            }
            setAdvancedOpen(event.currentTarget.open);
          }}>
            <summary aria-disabled={gatewayOperationBusy} onClick={(event) => {
              if (gatewayOperationBusy) event.preventDefault();
            }}>
              <span><strong>高级设置</strong><small>显示名称与多模型任务分配；连接身份和队列凭据只读</small></span>
              <span className="gateway-advanced-summary">{routeSummary} / {purposeOptions.length} 项任务已分配</span>
            </summary>
            <form className="multimodal-settings-form gateway-advanced-body" onSubmit={submit}>
              <div className="model-config-list">
                {selectedModel && <GatewayModelCard
                  key={selectedModel.model_id}
                  model={selectedModel}
                  persistedIdentity={selectedPersistedIdentity}
                  index={selectedModelIndex}
                  routing={routing}
                  modelCount={models.length}
                  busy={analysis.isSubmitting || gatewayOperationBusy}
                  offline={analysis.connectionState === "offline"}
                  update={(changes) => updateModel(selectedModel.model_id, changes)}
                  remove={() => removeModel(selectedModel.model_id)}
                  test={() => void analysis.checkGateway(selectedModel.model_id)}
                />}
              </div>

              <section className="model-routing-panel" aria-labelledby="model-routing-title">
                <div className="model-routing-heading">
                  <h3 id="model-routing-title">任务模型分配</h3>
                  <p>通常无需修改。只有需要让不同任务使用不同模型时，才在这里调整。</p>
                </div>
                <div className="model-routing-grid">
                  {purposeOptions.map((purpose) => <label className="route-field" htmlFor={`route-${purpose.value}`} key={purpose.value}>
                    <span>{purpose.label}</span><small>{purpose.detail}</small>
                    <select id={`route-${purpose.value}`} value={routing[purpose.value]} disabled={analysis.isSubmitting || gatewayOperationBusy} aria-describedby={analysis.isSubmitting || gatewayOperationBusy ? "gateway-save-description" : undefined} onChange={(event) => {
                      if (!analysis.isSubmitting && !gatewayOperationBusy) setRouting((current) => ({ ...current, [purpose.value]: event.target.value }));
                    }}>
                      {models.map((item) => <option key={item.model_id} value={item.model_id} disabled={!verifiedModelIds.has(item.model_id) || !supportsPurpose(item, purpose.value)}>
                        {item.display_name || item.model || "未命名模型"}{verifiedModelIds.has(item.model_id) && supportsPurpose(item, purpose.value) ? "" : "（尚未验证或能力不匹配）"}
                      </option>)}
                    </select>
                  </label>)}
                </div>
                {!routeIsValid && <p className="routing-warning" role="alert">请为四类任务分配能力匹配的已启用模型。视频审核、爆点研究和素材识别需要图文输入能力。</p>}
              </section>

              <div className="multimodal-form-actions">
                <p id="gateway-save-description">高级保存只会更新显示名称和新任务路由；不会删除或停用已验证模型，也不会写入上方尚未验证的地址、模型 ID、协议、API Key 或能力声明。{saveDisabledReason && <strong className="form-disabled-reason">当前不能保存：{saveDisabledReason}</strong>}</p>
                <button className="primary-button" type="submit" disabled={!canSave} aria-describedby="gateway-save-description">{analysis.isSubmitting ? "正在保存…" : gatewayOperationBusy ? "模型操作进行中" : manualFallbackBlocksAdvancedSave ? "请使用上方保存并验证" : "保存高级设置"}</button>
              </div>
            </form>
          </details>
        </div>
      </div>
    </section>
    {analysis.actionMessage && <div className={`action-message gateway-action-message${analysis.actionMessageKind === "error" ? " action-message-error" : ""}`} role={analysis.actionMessageKind === "error" ? "alert" : "status"} aria-live={analysis.actionMessageKind === "error" ? "assertive" : "polite"}>{analysis.actionMessage}</div>}
  </div>;
}
