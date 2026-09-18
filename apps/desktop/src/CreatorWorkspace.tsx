import { runtimeApiBaseUrl } from './runtimeApi';
import { SkillCover } from "./SkillCover";
import { FileDropInput } from "./FileDropInput";
import { ProductAssetsPanel } from "./ProductAssetsPanel";
import { ProductSheetImport } from "./ProductSheetImport";
import { createProduct as createProductRecord, fetchProduct, uploadProductAsset } from "./products";
import { useEffect, useRef, useState } from "react";
import type { useS4Products } from "./useS4Products";
import type { useS5Scripts } from "./useS5Scripts";
import { ModalDialog } from "./ModalDialog";
import { ProductEditor } from "./ProductEditor";
import { CreatorSkillDetail } from "./CreatorSkillDetail";
import { submitSkillBatch } from "./creationBatch";
import { guardUnsavedTransition, useUnsavedChanges } from "./unsavedChanges";
import type { ScriptSkillSummary } from "./scripts";

const apiBase = runtimeApiBaseUrl;
const draftKey = "content-factory.creator-draft.v1";
function readDraft(): { selected: string[]; productId: string } {
  try {
    const value = JSON.parse(localStorage.getItem(draftKey) ?? "null");
    if (value && Array.isArray(value.selected) && value.selected.every((id: unknown) => typeof id === "string") && typeof value.productId === "string") return value;
  } catch { /* Storage can be disabled; the actual product remains on the server. */ }
  return { selected: [], productId: "" };
}

export function CreatorWorkspace({ products, studio, openProjects, openLibrary, openSettings }: {
  products: ReturnType<typeof useS4Products>; studio: ReturnType<typeof useS5Scripts>;
  openProjects: () => void; openLibrary: () => void; openSettings: () => void;
}) {
  const [initialDraft] = useState(readDraft);
  const [selected, setSelected] = useState<string[]>(initialDraft.selected);
  const [query, setQuery] = useState("");
  const [name, setName] = useState("");
  const [sku, setSku] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const file = files[0] ?? null;
  const createdId = useRef<string | null>(null);
  const [productDirty, setProductDirty] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editingProduct, setEditingProduct] = useState(false);
  const [detail, setDetail] = useState<ScriptSkillSummary | null>(null);
  const [batchMessage, setBatchMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [storageFailed, setStorageFailed] = useState(false);
  const lock = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);
  useUnsavedChanges(Boolean(file || name.trim() || sku.trim() || busy || storageFailed), "创作商品、模板选择与提交任务");
  const profile = products.profile;
  useEffect(() => {
    if (initialDraft.productId && !products.profile) void products.select(initialDraft.productId);
  }, [initialDraft.productId, products.select]);
  useEffect(() => {
    try {
      localStorage.setItem(draftKey, JSON.stringify({ selected, productId: profile?.product_id ?? initialDraft.productId }));
      setStorageFailed(false);
    } catch { setStorageFailed(true); }
  }, [selected, profile?.product_id, initialDraft.productId]);
  const availableIds = new Set(studio.templates.map(item => item.skill_id));
  const chosen = selected.filter(id => availableIds.has(id));
  const visible = studio.templates.filter(item => `${item.name} ${item.mechanism}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const imageSource = profile?.sources.find(source => source.managed && source.kind === "image" && source.asset_id);
  const toggle = (id: string) => setSelected(current => current.includes(id) ? current.filter(value => value !== id) : [...current, id]);
  function saveDraft(ids = chosen) {
    try {
      localStorage.setItem(draftKey, JSON.stringify({ selected: ids, productId: profile?.product_id ?? "" }));
      setBatchMessage("已保存商品选择与 Skill 勾选。未上传的图片和输入框内容不在草稿中，请先上传建档。");
    } catch { setBatchMessage("本机暂不能保存创作选择；商品档案和已提交任务仍保存在本机服务中。"); }
  }

  async function createProduct() {
    if (lock.current || productDirty || (!file && !createdId.current)) return;
    lock.current = true;
    setBusy(true);
    try {
      let current = createdId.current ? await fetchProduct(createdId.current) : await createProductRecord({ name: name.trim() || file!.name.replace(/\.[^.]+$/, ""), sku: sku.trim() || undefined, actor: "本机操作员" });
      createdId.current = current.product_id;
      for (let index = 0; index < files.length; index++) {
        const pendingFile = files[index]!;
        setBatchMessage(`正在保存商品图片 ${index + 1}/${files.length}：${pendingFile.name}`);
        current = (await uploadProductAsset(current, pendingFile, "本机操作员")).profile;
        setFiles(files.slice(index + 1));
      }
      if (!await products.select(current.product_id)) throw new Error("商品已保存，但重新读取失败；请点击继续读取，勿重复建品。");
      await products.refresh();
      createdId.current = null; setName(""); setSku("");
      if (fileInput.current) fileInput.current.value = "";
      setEditingProduct(true); setBatchMessage("商品与多张图片已保存。请识别或补充真实卖点并确认后生成脚本。");
      await studio.refresh();
    } catch (e) {
      setBatchMessage(`尚未完成，未上传图片已保留；重试会继续当前商品，不会另建一款。${e instanceof Error ? e.message : "保存失败"}`);
    } finally { lock.current = false; setBusy(false); }
  }
  async function generate() {
    if (lock.current || productDirty || !profile || !products.eligibility?.eligible || !chosen.length || !studio.readiness.gateway_configured) return;
    lock.current = true; setBusy(true); setBatchMessage("正在分别建立任务，请勿关闭窗口…");
    const productId = profile.product_id;
    try {
      const result = await submitSkillBatch(chosen, skill_id => studio.generate({ skill_id, product_id: productId, content_goal: "conversion", target_audience: "关注当前商品的抖音用户", version_count: 3 }));
      setSelected(current => current.filter(id => !result.succeeded.includes(id)));
      saveDraft(result.failed);
      setBatchMessage(`已建立 ${result.succeeded.length} 套独立任务。${result.failed.length ? `${result.failed.length} 套未确认成功，保留勾选；请先到作品项目核对任务，避免重复提交。` : "到作品项目查看结果，每套含 3 个备选，只选一版拍摄。"}`);
    } finally { lock.current = false; setBusy(false); }
  }

  return <div className="content creator-content">
    <header className="creator-heading"><div><h1 data-page-title tabIndex={-1}>创作工作台</h1><p>上传商品，选择脚本 Skill，分别生成拍摄稿与对应剪辑稿。</p></div><button type="button" className="secondary-button" onClick={openProjects}>查看作品项目</button></header>
    <div className="creator-layout"><div className="creator-main">
      <section className="creator-product" aria-labelledby="creator-product-title">
        <div className="creator-section-heading"><h2 id="creator-product-title">这次拍哪件商品？</h2><label>使用已有商品<select aria-label="使用已有商品" disabled={busy || productDirty || products.isLoading} value={profile?.product_id ?? ""} onChange={event => { if (event.target.value) guardUnsavedTransition(() => { void products.select(event.target.value); }); }}><option value="">请选择商品</option>{products.products.filter(item => item.status !== "archived").map(item => <option key={item.product_id} value={item.product_id}>{item.name} · {item.sku}</option>)}</select></label></div>
        {profile && <div className="creator-product-current">{imageSource && <img src={`${apiBase}/s4/products/${encodeURIComponent(profile.product_id)}/assets/${encodeURIComponent(imageSource.asset_id!)}`} alt={`${profile.name}商品图`} />}<div><h3>{profile.name}</h3><p>{profile.sku} · 第 {profile.revision} 版</p><div className="creator-facts">{profile.facts.filter(fact => fact.confirmed_by && fact.confirmed_at).map(fact => <span key={fact.id}>{fact.label}：{fact.value}</span>)}</div><p>{products.eligibility?.eligible ? "已确认卖点，可以写脚本" : products.eligibility?.blockers.join("；") || "正在检查商品信息"}</p><button className="secondary-button" type="button" disabled={busy} onClick={() => setEditingProduct(true)}>确认卖点 / 编辑资料</button></div></div>}
        <details className="creator-upload-section" open={!profile || Boolean(file) || Boolean(createdId.current)}><summary>{profile ? "上传另一件商品" : "上传商品图片"}</summary>
          <label className="creator-upload">选择商品图（可多选）<FileDropInput ref={fileInput} type="file" accept="image/jpeg,image/png,image/webp" multiple disabled={busy || productDirty} onChange={event => setFiles(Array.from(event.target.files ?? []))} /><small>{files.length ? `待保存 ${files.length} 张：${files.map(f => f.name).join("、")}` : "JPG、PNG 或 WebP。上传后确认可见卖点；款号可后补。"}</small></label>
          <div className="creator-product-fields"><label>商品名称<input value={name} disabled={busy || Boolean(createdId.current)} onChange={event => setName(event.target.value)} placeholder="留空使用图片文件名" /></label><label>款号（选填）<input value={sku} disabled={busy || Boolean(createdId.current)} onChange={event => setSku(event.target.value)} placeholder="可以后补" /></label><button type="button" className="primary-button" disabled={(!file && !createdId.current) || productDirty || busy || products.isSubmitting} onClick={() => void createProduct()}>{busy ? "处理中…" : (createdId.current ? "继续保存 / 重新读取本款" : "上传并保存商品")}</button></div>
          {createdId.current && <button className="secondary-button" type="button" disabled={busy || productDirty} onClick={async () => { if (productDirty) return; const id = createdId.current!; if (await products.select(id)) { createdId.current = null; setFiles([]); setName(""); setSku(""); setBatchMessage("已保留建立的商品和成功上传的图片；未上传选择已取消，可在本款资料区补充。"); } }}>保留已上传部分，取消剩余选择</button>}
        </details>
        <button type="button" className="secondary-button" disabled={busy || productDirty} onClick={() => setSheetOpen(true)}>导入商品表格（XLSX / CSV）</button>
        {profile && <ProductAssetsPanel key={profile.product_id} profile={profile} onDirty={setProductDirty} onRefresh={() => products.select(profile.product_id)} />}
        {products.actionMessage && <p role="status" className="creator-inline-message">{products.actionMessage}</p>}
        <p className="creator-policy">固定直播间 · 一名主播 · 固定机位与灯光 · 连续长镜头</p>
      </section>
      <section aria-labelledby="creator-skills-title"><div className="creator-section-heading"><div><h2 id="creator-skills-title">选择脚本 Skill</h2><p>仅使用已批准的模板；每个 Skill 单独出稿，不混合。</p></div><label>搜索模板<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="名称或方法" /></label></div>
        {studio.isLoading && !studio.templates.length ? <p role="status">正在读取模板…</p> : visible.length === 0 ? <div className="creator-empty"><h3>{query ? "没有匹配的模板" : "还没有可用的脚本 Skill"}</h3><p>先查看已有候选的来源证据，确认后再使用。新分析方法不会自动植入。</p><button type="button" className="secondary-button" onClick={openLibrary}>打开脚本 Skill 库</button></div> : <div className="creator-skill-grid">{visible.map(skill => <article className={`creator-skill-card${chosen.includes(skill.skill_id) ? " is-selected" : ""}`} key={skill.skill_id}><label><input type="checkbox" checked={chosen.includes(skill.skill_id)} disabled={busy} onChange={() => toggle(skill.skill_id)} /><strong>{skill.name}</strong></label><div className="skill-card-preview"><p>{skill.mechanism}</p><SkillCover skill={skill} onOpen={() => setDetail(skill)} /></div><small>{skill.distinct_video_count} 条来源视频 · {skill.occurrence_count} 次证据</small><footer><span>R{skill.skill_revision} · 已批准</span><button type="button" className="text-button" onClick={() => setDetail(skill)}>查看详情</button></footer></article>)}</div>}
      </section>
    </div><aside className="creator-summary" aria-label="本次创作摘要"><h2>本次创作</h2><small>已选商品</small><h3>{profile?.name ?? "请先上传或选择商品"}</h3><small>已选 {chosen.length} 个 Skill</small><ol>{studio.templates.filter(item => chosen.includes(item.skill_id)).map(item => <li key={item.skill_id}>{item.name}</li>)}</ol><p>每个 Skill 生成独立脚本包，含 3 个备选版本。选定一版交给主播，剪辑稿留在项目中。</p><button className="primary-button" type="button" disabled={busy || productDirty || products.isLoading || !products.eligibility?.eligible || !chosen.length || !studio.readiness.gateway_configured} onClick={() => void generate()}>{busy ? "处理中…" : `生成 ${chosen.length} 套脚本`}</button>{!studio.readiness.gateway_configured && <button type="button" className="text-button" onClick={openSettings}>先连接可用模型</button>}<button className="secondary-button" type="button" onClick={openProjects}>查看生成任务与脚本</button>{batchMessage && <p role="status">{batchMessage}</p>}{studio.actionMessageKind === "error" && studio.actionMessage && <p role="alert">{studio.actionMessage}</p>}<small>只使用已确认的商品信息。细节覆盖仅限同款，不中断主播原声。</small></aside></div>
    <div className="creator-draft-actions"><button className="secondary-button" type="button" disabled={busy || !profile || Boolean(file)} onClick={() => saveDraft()}>保存创作选择</button><small role="status">{storageFailed ? "本机无法自动保存选择，离开前请勿关闭窗口。" : "已建档商品与模板勾选自动保存在本机，切换页面后继续。未上传图片和填写内容请先建档。"}</small></div>
    {detail && <CreatorSkillDetail key={detail.skill_id} skill={detail} onClose={() => setDetail(null)} onSelect={() => { setSelected(current => [...new Set([...current, detail.skill_id])]); setDetail(null); }} />}
    {editingProduct && profile && <ModalDialog open title="确认商品卖点" description="只需商品图与至少一条有来源、经确认的可见事实。完整档案可以以后补。" onClose={() => guardUnsavedTransition(() => setEditingProduct(false))} footer={<><p role="status" aria-live="polite">{products.actionMessage}</p><button type="button" className="secondary-button" onClick={() => guardUnsavedTransition(() => setEditingProduct(false))}>返回创作工作台</button></>}><ProductEditor profile={profile} versions={products.versions} eligibility={products.eligibility} suggestions={products.suggestions} isSubmitting={products.isSubmitting || products.isSuggesting} onSave={async (draft, actor) => { const saved = await products.save(draft, actor); if (saved) await studio.refresh(); return saved; }} onStatus={products.changeStatus} onUpload={products.upload} onSuggest={products.suggest} onSuggestionsAccepted={products.clearSuggestions} /></ModalDialog>}
    {sheetOpen && <ProductSheetImport onClose={() => setSheetOpen(false)} onImported={() => products.refresh()} />}
  </div>;
}
