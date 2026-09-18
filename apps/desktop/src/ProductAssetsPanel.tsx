import { runtimeApiBaseUrl } from './runtimeApi';
import { useEffect, useRef, useState } from "react";
import type { ProductProfile } from "@content-factory/contracts";
import { FileDropInput } from "./FileDropInput";
import { fetchProduct, uploadProductAsset } from "./products";
import { useUnsavedChanges } from "./unsavedChanges";

const base = runtimeApiBaseUrl;
export interface ProductWorkspace { revision: number; notes: string; selected_asset_ids: string[]; primary_asset_id: string | null }
export async function productRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}/s4/${path}`, init);
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : `操作失败（${response.status}），输入已保留。`);
  return value as T;
}
export function ProductAssetsPanel({ profile, onRefresh, onDirty }: { profile: ProductProfile; onRefresh: () => Promise<unknown>; onDirty: (dirty: boolean) => void }) {
  const [workspace, setWorkspace] = useState<ProductWorkspace | null>(null);
  const [dirty, setDirty] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const lock = useRef(false);
  const pending = dirty || files.length > 0 || busy;
  useUnsavedChanges(pending, "商品资料与素材选择");
  useEffect(() => { onDirty(pending); return () => onDirty(false); }, [pending, onDirty]);
  useEffect(() => {
    let cancelled = false;
    productRequest<ProductWorkspace>(`products/${encodeURIComponent(profile.product_id)}/workspace`).then(value => { if (!cancelled) setWorkspace(value); }).catch(e => { if (!cancelled) setMessage(String(e.message)); });
    return () => { cancelled = true; };
  }, [profile.product_id]);
  function update(value: Partial<ProductWorkspace>) { setWorkspace(current => current ? { ...current, ...value } : current); setDirty(true); }
  async function save() {
    if (!workspace || lock.current) return;
    lock.current = true; setBusy(true);
    try {
      const { revision, ...data } = workspace;
      const saved = await productRequest<ProductWorkspace>(`products/${encodeURIComponent(profile.product_id)}/workspace`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...data, expected_revision: revision }) });
      setWorkspace(saved); setDirty(false); setMessage("资料与勾选已保存。下次选这个商品即可继续使用；取消勾选不会删除文件。");
    } catch (e) { setMessage(e instanceof Error ? e.message : "保存失败，内容已保留"); }
    finally { lock.current = false; setBusy(false); }
  }
  async function upload() {
    if (lock.current || !files.length) return;
    lock.current = true; setBusy(true); const pendingFiles = [...files];
    let completed = 0;
    try {
      let current = await fetchProduct(profile.product_id);
      for (const file of pendingFiles) {
        setMessage(`正在保存 ${completed + 1}/${pendingFiles.length}：${file.name}`);
        const result = await uploadProductAsset(current, file, "本机操作员");
        current = result.profile; completed++;
        setFiles(pendingFiles.slice(completed));
      }
      setMessage(`已保存 ${completed} 个文件。勾选需要复用的素材，再保存选择。`);
    } catch (e) { setMessage(`已保存 ${completed} 个；未完成文件保留，可重试。${e instanceof Error ? e.message : "上传失败"}`); }
    finally { try { await onRefresh(); } finally { lock.current = false; setBusy(false); } }
  }
  return <section className="product-assets" aria-label="本款资料与素材">
    <h3>本款资料与素材</h3><p>只上传一次，之后选择这个款、勾选已保存素材即可。商品资料先保存，事实确认仍在“确认卖点”中完成。</p>
    <label>补充商品资料<textarea aria-label="补充商品资料" disabled={!workspace || busy} maxLength={20000} value={workspace?.notes ?? ""} onChange={e => update({ notes: e.target.value })} placeholder="粘贴商品说明、尺码表文字、供货资料等。不确定的参数写待确认。" /></label>
    <label>添加同款图片、细节视频或资料<FileDropInput aria-label="添加同款资料" accept=".jpg,.jpeg,.png,.webp,.mp4,.mov,.pdf" multiple disabled={busy} onChange={e => setFiles(Array.from(e.target.files ?? []))} /></label>
    {files.length > 0 && <><ul>{files.map((f, i) => <li key={`${f.name}-${i}`}>{f.name}</li>)}</ul><div className="asset-actions"><button type="button" className="primary-button" disabled={busy} onClick={() => void upload()}>保存 {files.length} 个文件</button><button type="button" className="secondary-button" disabled={busy} onClick={() => setFiles([])}>取消本次选择</button></div></>}
    <div className="product-asset-grid">{profile.sources.filter(s => s.managed && s.asset_id).map(source => {
      const url = `${base}/s4/products/${encodeURIComponent(profile.product_id)}/assets/${encodeURIComponent(source.asset_id!)}`;
      return <article key={source.asset_id}>{source.kind === "image" ? <img src={url} alt={source.label} loading="lazy" /> : source.kind === "video" ? <video src={url} controls preload="none" aria-label={source.label} /> : <a href={url} target="_blank" rel="noreferrer">查看资料文件</a>}
        <label><input type="checkbox" disabled={!workspace || busy} checked={workspace?.selected_asset_ids.includes(source.asset_id!) ?? false} onChange={e => update({ selected_asset_ids: e.target.checked ? [...new Set([...(workspace?.selected_asset_ids ?? []), source.asset_id!])] : workspace!.selected_asset_ids.filter(id => id !== source.asset_id) })} />本次使用：{source.label}</label>
        {source.kind === "image" && <label><input type="radio" name={`primary-${profile.product_id}`} disabled={!workspace || busy} checked={workspace?.primary_asset_id === source.asset_id} onChange={() => update({ primary_asset_id: source.asset_id! })} />设为主图</label>}
      </article>;
    })}</div>
    <div className="asset-actions"><button className="primary-button" type="button" disabled={!workspace || busy || !dirty} onClick={() => void save()}>保存资料与素材选择</button><span>{workspace?.selected_asset_ids.length ?? 0} 个素材已勾选{dirty ? " · 尚未保存" : ""}</span></div>
    {message && <p role="status">{message}</p>}
  </section>;
}
