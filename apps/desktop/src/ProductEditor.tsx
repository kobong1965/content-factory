import { FileDropInput } from "./FileDropInput";
import { useEffect, useState, type FormEvent } from "react";
import type {
  ProductFact, ProductFactSuggestion, ProductProfile, ProductScriptEligibility,
  ProductSource, ProductStatus, ProductVersion,
} from "@content-factory/contracts";

import { ProductConstraintEditor } from "./ProductConstraintEditor";
import { ProductFactEditor } from "./ProductFactEditor";
import { ConfirmationDialog } from "./ConfirmationDialog";
import { completenessPercent, productStatusLabels } from "./products";
import { guardUnsavedTransition, useUnsavedChanges } from "./unsavedChanges";

function sourceId(): string {
  return `source_${crypto.randomUUID().replaceAll("-", "")}`;
}

export function ProductEditor({
  profile, versions, eligibility, suggestions, isSubmitting, onSave, onStatus, onUpload, onSuggest,
  onSuggestionsAccepted,
}: {
  profile: ProductProfile;
  versions: readonly ProductVersion[];
  eligibility: ProductScriptEligibility | null;
  suggestions: readonly ProductFactSuggestion[];
  isSubmitting: boolean;
  onSave: (profile: ProductProfile, actor: string) => Promise<boolean>;
  onStatus: (status: ProductStatus, actor: string) => Promise<boolean>;
  onUpload: (file: File, actor: string) => Promise<boolean>;
  onSuggest: () => Promise<boolean>;
  onSuggestionsAccepted: () => void;
}) {
  const [draft, setDraft] = useState(profile);
  const actor = "本机";
  const [sourceLabel, setSourceLabel] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [showArchiveConfirm, setShowArchiveConfirm] = useState(false);
  const [selectedSuggestions, setSelectedSuggestions] = useState<string[]>([]);

  useEffect(() => setDraft(profile), [profile]);
  useEffect(() => setSelectedSuggestions([]), [suggestions]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(profile);
  useUnsavedChanges(dirty, "商品资料");

  const save = (event: FormEvent) => {
    event.preventDefault();
    if (actor.trim()) void onSave(draft, actor.trim());
  };
  const addSource = () => {
    if (!sourceLabel.trim() || !sourceUrl.trim()) return;
    const source: ProductSource = {
      id: sourceId(), kind: "detail_page", label: sourceLabel.trim(), source_ref: sourceUrl.trim(),
      asset_id: null, mime_type: null, sha256: null, size_bytes: null, managed: false,
    };
    setDraft({ ...draft, sources: [...draft.sources, source] });
    setSourceLabel("");
    setSourceUrl("");
  };
  const removeSource = (id: string) => setDraft({ ...draft, sources: draft.sources.filter((source) => source.id !== id) });
  const upload = () => {
    if (!file || !actor.trim()) return;
    const selectedFile = file;
    guardUnsavedTransition(() => {
      void onUpload(selectedFile, actor.trim()).then((saved) => { if (saved) setFile(null); });
    });
  };
  const percent = completenessPercent(draft.completeness.ratio);
  const acceptSuggestions = async () => {
    if (!actor.trim() || selectedSuggestions.length === 0) return;
    const timestamp = new Date().toISOString();
    const accepted = suggestions.filter((_, index) => selectedSuggestions.includes(String(index)));
    const existing = new Set(draft.facts.map((fact) => `${fact.field}:${fact.value.trim().toLocaleLowerCase("zh-CN")}`));
    const additions: ProductFact[] = accepted.flatMap((suggestion) => {
      const key = `${suggestion.field}:${suggestion.value.trim().toLocaleLowerCase("zh-CN")}`;
      if (existing.has(key)) return [];
      existing.add(key);
      return [{
        id: `fact_${crypto.randomUUID().replaceAll("-", "")}`,
        field: suggestion.field,
        label: suggestion.label,
        value: suggestion.value,
        unit: null,
        source_type: "media_evidence",
        source_id: suggestion.source_id,
        source_ref: suggestion.evidence,
        confirmed_by: actor.trim(),
        confirmed_at: timestamp,
      }];
    });
    if (additions.length === 0) return;
    const next = {
      ...draft,
      facts: [...draft.facts, ...additions],
      selling_point_fact_ids: [...new Set([...draft.selling_point_fact_ids, ...additions.map((fact) => fact.id)])],
    };
    setDraft(next);
    if (await onSave(next, actor.trim())) onSuggestionsAccepted();
  };
  const archive = async () => {
    if (!actor.trim()) return;
    guardUnsavedTransition(() => {
      void onStatus("archived", actor.trim()).then((saved) => { if (saved) setShowArchiveConfirm(false); });
    });
  };

  return <>
  <form className="product-editor" onSubmit={save}>
    <header className="product-editor-head">
      <div><h2>{draft.name}</h2><p>{draft.sku} · 第 {draft.revision} 版 · {productStatusLabels[draft.status]}</p></div>
      <div className="editor-actions">

        <button className="primary-button" type="submit" disabled={!actor.trim() || isSubmitting || draft.status === "archived"}>保存新版本</button>
      </div>
    </header>

    <section className={`script-eligibility-card ${eligibility?.eligible ? "is-ready" : "needs-confirmation"}`} aria-label="脚本可用性">
      <div><span>现在能不能写脚本</span><strong>{eligibility?.eligible ? "可以写" : "还差一步"}</strong></div>
      {eligibility?.eligible
        ? <p>脚本只会使用 {eligibility.usable_fact_ids.length} 条已确认卖点；{eligibility.unknown_fields.length ? `未知的${eligibility.unknown_fields.join("、")}会自动避开。` : "商品事实已明确。"}</p>
        : <ul>{(eligibility?.blockers ?? ["正在检查商品资料…"]).map((item) => <li key={item}>{item}</li>)}</ul>}
    </section>

    <details className="formal-profile-progress"><summary>正式商品档案完整度 {percent}%（不影响已确认卖点先写脚本）</summary><div className="product-progress" aria-hidden="true"><span style={{ width: `${percent}%` }} /></div>{draft.completeness.missing_fields.length > 0 ? <p>后续需要建立正式档案时再补：{draft.completeness.missing_fields.join("、")}。</p> : <p>资料齐全，可以启用为正式商品。</p>}</details>

    <fieldset className="product-section basic-section" disabled={draft.status === "archived"}>
      <legend>基本资料</legend>
      <label>款号<input value={draft.sku} onChange={(event) => setDraft({ ...draft, sku: event.target.value })} /></label>
      <label>商品名称<input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
    </fieldset>

    <fieldset className="product-section" disabled={draft.status === "archived"}>
      <legend>资料来源</legend>
      <p>本地文件会复制到 S4 托管目录；链接只登记来源，不会自动抓取。</p>
      <ul className="source-list">
        {draft.sources.map((source) => <li key={source.id}><span><strong>{source.label}</strong><small>{source.managed ? `${source.kind} · 本机托管 · ${Math.ceil((source.size_bytes ?? 0) / 1024)} KB` : source.source_ref}</small></span>{source.managed ? <em>已托管</em> : <button className="text-button danger-text" type="button" onClick={() => removeSource(source.id)}>移除</button>}</li>)}
      </ul>
      <div className="source-entry-grid">
        <label>详情页名称<input value={sourceLabel} onChange={(event) => setSourceLabel(event.target.value)} placeholder="例如：抖店官方详情页" /></label>
        <label>完整链接<input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://..." /></label>
        <button className="secondary-button" type="button" disabled={!sourceLabel.trim() || !sourceUrl.trim()} onClick={addSource}>添加链接</button>
      </div>
      <div className="asset-upload-row">
        <label className="file-picker"><FileDropInput className="visually-hidden" type="file" accept=".jpg,.jpeg,.png,.webp,.mp4,.mov,.pdf" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><span>{file?.name ?? "选择商品图片、视频或 PDF"}</span><strong>选择文件</strong></label>
        <button className="secondary-button" type="button" disabled={!file || !actor.trim() || isSubmitting} onClick={() => void upload()}>复制到本机资料库</button>
        <button className="secondary-button" type="button" disabled={isSubmitting || !draft.sources.some((source) => source.managed && source.kind === "image")} onClick={() => void onSuggest()}>重新识别图片卖点</button>
      </div>
    </fieldset>

    {suggestions.length > 0 && <section className="product-suggestion-panel" aria-labelledby="product-suggestion-title">
      <header><div><h3 id="product-suggestion-title">AI 找到的可见卖点</h3><p>这些还不是正式事实。核对图片后勾选，保存时才会标记为人工确认。</p></div><span>{suggestions.length} 条候选</span></header>
      <div className="product-suggestion-list">{suggestions.map((suggestion, index) => {
        const key = String(index);
        const source = draft.sources.find((item) => item.id === suggestion.source_id);
        return <label key={`${suggestion.source_id}-${suggestion.field}-${suggestion.value}`} className="product-suggestion-item">
          <input type="checkbox" checked={selectedSuggestions.includes(key)} onChange={(event) => setSelectedSuggestions(event.target.checked ? [...selectedSuggestions, key] : selectedSuggestions.filter((item) => item !== key))} />
          <span><strong>{suggestion.label}</strong><b>{suggestion.value}</b><small>证据：{suggestion.evidence} · 来源：{source?.label ?? suggestion.source_id} · 置信度：{{ high: "高", medium: "中", low: "低" }[suggestion.confidence]}</small></span>
        </label>;
      })}</div>
      <footer><span>不会采用未勾选内容，也不会从图片猜面料成分、尺码、价格或功效。</span><button className="primary-button" type="button" disabled={!actor.trim() || selectedSuggestions.length === 0 || isSubmitting} onClick={() => void acceptSuggestions()}>确认选中卖点并保存</button></footer>
    </section>}

    <ProductFactEditor profile={draft} actor={actor} onChange={setDraft} />
    <ProductConstraintEditor profile={draft} onChange={setDraft} />

    <section className="product-section version-section">
      <div className="section-title-row"><h3>版本与状态</h3><div className="status-actions">{draft.status === "active" && <button className="secondary-button" type="button" disabled={!actor.trim() || isSubmitting} onClick={() => guardUnsavedTransition(() => { void onStatus("draft", actor.trim()); })}>退回草稿</button>}{draft.status !== "archived" && <button className="secondary-button danger-button" type="button" disabled={!actor.trim() || isSubmitting} onClick={() => setShowArchiveConfirm(true)}>归档商品</button>}{draft.status === "archived" && <button className="secondary-button" type="button" disabled={!actor.trim() || isSubmitting} onClick={() => guardUnsavedTransition(() => { void onStatus("draft", actor.trim()); })}>恢复为草稿</button>}{draft.status === "draft" && <button className="primary-button" type="button" disabled={!actor.trim() || isSubmitting} onClick={() => guardUnsavedTransition(() => { void onStatus("active", actor.trim()); })}>检查并启用</button>}</div></div>
      <ol className="version-list">{versions.map((version) => <li key={version.revision}><strong>第 {version.revision} 版</strong><span>{new Date(version.created_at).toLocaleString("zh-CN")}</span><em>{({ created: "建立草稿", updated: "保存资料", activated: "启用商品", archived: "归档商品", asset_added: "添加资料" } as const)[version.action]}</em></li>)}</ol>
    </section>
  </form>
  <ConfirmationDialog
    open={showArchiveConfirm}
    title="确认归档商品"
    description="归档后该商品不能继续用于新脚本，但资料、历史版本和已有引用都会保留。"
    confirmLabel="确认归档"
    destructive
    busy={isSubmitting}
    details={<><strong>{draft.name}</strong><span>{draft.sku}</span></>}
    onClose={() => setShowArchiveConfirm(false)}
    onConfirm={() => void archive()}
  />
  </>;
}
