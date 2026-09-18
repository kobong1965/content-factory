import { useRef, useState } from "react";
import { FileDropInput } from "./FileDropInput";
import { ModalDialog } from "./ModalDialog";
import { productRequest } from "./ProductAssetsPanel";
interface Preview { columns: string[]; rows: string[][]; sheet_names: string[]; header_row: number }
export function ProductSheetImport({ onClose, onImported }: { onClose: () => void; onImported: () => Promise<void> }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [nameColumn, setNameColumn] = useState(0);
  const [skuColumn, setSkuColumn] = useState(-1);
  const [selected, setSelected] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [completed, setCompleted] = useState(false);
  const lock = useRef(false);
  async function read(sheet?: string) {
    if (!file || lock.current) return;
    lock.current = true; setBusy(true); setPreview(null); setCompleted(false);
    try {
      const body = new FormData(); body.set("upload", file); if (sheet) body.set("sheet_name", sheet);
      const result = await productRequest<Preview>("product-sheet/preview", { method: "POST", body });
      setPreview(result); setSelected([]);
      setNameColumn(Math.max(0, result.columns.findIndex(c => /^(商品名称|商品名|名称)/.test(c)))); setSkuColumn(result.columns.findIndex(c => c.includes("款号")));
      setMessage(`读取 ${result.rows.length} 行；请勾选要导入的真实商品，示例不要勾选。`);
    } catch (e) { setMessage(e instanceof Error ? e.message : "无法读取表格"); }
    finally { lock.current = false; setBusy(false); }
  }
  async function submit() {
    if (!preview || lock.current || completed) return;
    lock.current = true; setBusy(true);
    try {
      const rows = selected.map(i => ({ name: preview.rows[i]![nameColumn] ?? "", sku: preview.rows[i]![skuColumn] ?? "", notes: `来源表格：${file?.name}\n` + preview.columns.map((column, j) => `${column || `第${j + 1}列`}：${preview.rows[i]![j] ?? ""}`).join("\n") }));
      const result = await productRequest<unknown[]>("product-sheet/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rows }) });
      setCompleted(true); setMessage(`已保存 ${result.length} 个商品资料草稿，未把表格内容自动确认为卖点。请选择商品补图并确认事实。`); await onImported();
    } catch (e) { setMessage(e instanceof Error ? e.message : "导入失败，选择已保留"); }
    finally { lock.current = false; setBusy(false); }
  }
  return <ModalDialog open title="导入商品表格" description="支持 XLSX / CSV，最大 8 MB。先预览再确认，不覆盖已有款号。" onClose={() => { if (!busy) onClose(); }} footer={<><p role="status">{message}</p><button type="button" className="secondary-button" disabled={busy} onClick={onClose}>关闭</button><button type="button" className="primary-button" disabled={busy || completed || !selected.length || selected.length > 200} onClick={() => void submit()}>保存勾选的 {selected.length} 个商品</button></>}>
    <FileDropInput aria-label="商品表格" accept=".xlsx,.csv" disabled={busy} onChange={e => { setFile(e.target.files?.[0] ?? null); setPreview(null); setSelected([]); setCompleted(false); }} />
    <button type="button" className="secondary-button" disabled={!file || busy} onClick={() => void read()}>读取表格预览</button>
    <p>表格内图片不会自动提取，文件夹路径也不会触发扫描；图片、视频请在对应商品下拖入。款号建议设为文本，保留前导零。</p>
    {preview && <><label>切换工作表<select disabled={busy} defaultValue="" onChange={e => void read(e.target.value)}><option value="" disabled>选择工作表</option>{preview.sheet_names.map(n => <option key={n}>{n}</option>)}</select></label><label>商品名称列<select value={nameColumn} disabled={busy || completed} onChange={e => setNameColumn(+e.target.value)}>{preview.columns.map((c, i) => <option key={i} value={i}>{c || `第${i + 1}列`}</option>)}</select></label><label>款号列<select value={skuColumn} disabled={busy || completed} onChange={e => setSkuColumn(+e.target.value)}><option value={-1}>没有款号，稍后补</option>{preview.columns.map((c, i) => <option key={i} value={i}>{c || `第${i + 1}列`}</option>)}</select></label>
      <div className="sheet-table"><table><thead><tr><th>选择</th>{preview.columns.map((c, i) => <th key={i}>{c}</th>)}</tr></thead><tbody>{preview.rows.map((row, i) => <tr key={i}><td><input aria-label={`导入第${i + 1}行`} type="checkbox" disabled={busy || completed} checked={selected.includes(i)} onChange={e => setSelected(ids => e.target.checked ? [...ids, i] : ids.filter(id => id !== i))} /></td>{row.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody></table></div></>}
  </ModalDialog>;
}
