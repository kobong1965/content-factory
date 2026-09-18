import {
  gatewayModeLabel,
  gatewayRequestUrl,
  providerPresets,
  purposeOptions,
  type ActiveRouting,
  type DraftModel,
} from "./gatewayModelSettings";

type GatewayModelCardProps = {
  model: DraftModel;
  persistedIdentity: DraftModel | null;
  index: number;
  routing: ActiveRouting;
  modelCount: number;
  busy: boolean;
  offline: boolean;
  update: (changes: Partial<DraftModel>) => void;
  remove: () => void;
  test: () => void;
};

export function GatewayModelCard({
  model,
  persistedIdentity,
  index,
  routing,
  modelCount,
  busy,
  offline,
  update,
  remove,
  test,
}: GatewayModelCardProps) {
  const assignedPurposes = purposeOptions.filter((purpose) => routing[purpose.value] === model.model_id);
  const isRouted = assignedPurposes.length > 0;
  const identity = persistedIdentity;
  const isVerified = Boolean(identity?.api_key_configured);
  const provider = identity?.provider ?? model.provider;
  const preset = providerPresets.find((item) => item.value === provider) ?? providerPresets.at(-1)!;
  const requestUrl = identity?.base_url.trim()
    ? gatewayRequestUrl(identity.base_url, identity.api_mode)
    : null;
  const cardTitleId = `model-title-${model.model_id}`;
  const routedControlReasonId = `model-routed-reason-${model.model_id}`;
  const onlyModelReasonId = `model-only-reason-${model.model_id}`;
  const testReasonId = `model-test-reason-${model.model_id}`;
  const testDisabledReason = offline
    ? "本机服务未连接，暂时无法测试。"
    : busy
      ? "当前模型操作尚未完成，请稍候。"
      : !isVerified
        ? "该连接尚未通过保存验证，请先使用上方快速连接。"
        : null;

  return <fieldset className="model-config-card" aria-labelledby={cardTitleId} aria-describedby={busy ? "gateway-save-description" : undefined} disabled={busy}>
    <legend className="sr-only">模型 {index + 1}</legend>
    <div className="model-card-header">
      <div className="model-card-title">
        <span className="model-order" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
        <div>
          <span className={`model-provider-badge provider-${provider}`}>{preset.shortLabel}</span>
          <strong id={cardTitleId}>{model.display_name || `模型 ${index + 1}`}</strong>
          <small>{identity?.model || "尚未通过连接验证"}</small>
        </div>
      </div>
      <div className="model-card-controls">
        {assignedPurposes.map((purpose) => <span className="model-route-badge" key={purpose.value}>{purpose.label}</span>)}
        <span className="model-enabled-status">{isVerified ? identity?.enabled === false ? "已停用（只读）" : "已启用（只读）" : identity ? "已保存，待验证" : "新连接草稿"}</span>
        {!identity && <button className="text-button danger-text-button" type="button" disabled={modelCount === 1 || isRouted} aria-describedby={isRouted ? routedControlReasonId : modelCount === 1 ? onlyModelReasonId : undefined} onClick={remove}>取消草稿</button>}
      </div>
    </div>
    {identity
      ? <p className="model-disabled-reason">队列连续性保护：已保存模型不能删除、停用或修改能力，避免已排队和重试任务失去原模型身份；可在下方调整新任务路由。</p>
      : isRouted
        ? <p className="model-disabled-reason" id={routedControlReasonId}>该草稿仍被任务路由引用，请先在下方更改任务分配再取消。</p>
        : modelCount === 1 && <p className="model-disabled-reason" id={onlyModelReasonId}>至少需保留一个连接位置，因此唯一草稿不能取消。</p>}

    <div className="model-field-grid model-presentation-fields">
      <label className="model-field model-field-wide" htmlFor={`model-name-${model.model_id}`}>
        <span>显示名称</span><small>只改变团队看到的名称，不改变上游连接身份</small>
        <input id={`model-name-${model.model_id}`} required maxLength={60} value={model.display_name} placeholder="例如：主力视觉模型" onChange={(event) => update({ display_name: event.target.value })} />
      </label>
    </div>

    <section className="model-identity-panel" aria-labelledby={`model-identity-${model.model_id}`}>
      <div className="model-identity-heading">
        <strong id={`model-identity-${model.model_id}`}>{isVerified ? "已验证连接身份（只读）" : "已保存连接身份（只读）"}</strong>
        <span>更换地址、Key、模型或协议必须使用上方“保存并验证”</span>
      </div>
      {identity
        ? <dl className="model-identity-grid">
          <div><dt>服务商</dt><dd>{preset.label}</dd></div>
          <div><dt>接口模式</dt><dd>{gatewayModeLabel(identity.api_mode)}</dd></div>
          <div className="model-identity-wide"><dt>API 接口地址</dt><dd><code>{identity.base_url}</code></dd></div>
          <div className="model-identity-wide"><dt>模型 ID</dt><dd><code>{identity.model}</code></dd></div>
          <div><dt>API Key</dt><dd>{isVerified ? "Windows 本机已加密保存" : "尚未配置，请使用上方连接向导"}</dd></div>
          <div><dt>连接状态</dt><dd>{identity.enabled ? "已启用（只读）" : "已停用（只读）"}</dd></div>
          <div><dt>已验证输入</dt><dd>{identity.modalities.includes("image") ? "文字 + 图片" : "文字"}</dd></div>
          <div className="model-identity-wide"><dt>可执行任务</dt><dd>{purposeOptions.filter((purpose) => identity.purposes.includes(purpose.value)).map((purpose) => purpose.label).join("、") || "未声明"}</dd></div>
        </dl>
        : <p className="model-identity-empty">这个草稿尚未验证，不能从高级区保存。请先在上方填写地址和 API Key，读取模型并完成真实图文探针。</p>}
    </section>

    {isVerified && identity && <div className="endpoint-preview" role="note">
      <span>已验证的实际请求</span>
      <code>{requestUrl ? `POST ${requestUrl}` : "尚无请求地址"}</code>
      <small>{gatewayModeLabel(identity.api_mode)}</small>
    </div>}

    <p className="local-preprocess-note">音频先由本地 ASR 转写；视频先由本地抽帧和 OCR；云端模型接收必要文字与关键帧，不上传原视频。</p>

    <div className="model-card-footer">
      <span id={testReasonId}>{testDisabledReason ?? "已保存，可重新实际测试文字、图片和结构化输出。"}</span>
      <button className="secondary-button compact-button" type="button" disabled={Boolean(testDisabledReason)} aria-describedby={testReasonId} onClick={test}>测试已保存配置</button>
    </div>
  </fieldset>;
}
