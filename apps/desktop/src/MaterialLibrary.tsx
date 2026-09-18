import type { MaterialSummary } from "@content-factory/contracts";
import { recognitionLabels } from "./materials";

export function MaterialLibrary({ materials, selectedId, select }: {
  materials: readonly MaterialSummary[]; selectedId?: string; select: (id: string) => void;
}) {
  return <aside className="material-library" aria-label="素材库">
    <header><h2>素材库</h2><b>{materials.length}</b></header>
    {materials.length === 0 ? <div className="material-empty"><strong>还没有素材</strong><span>先从上方上传一段实拍视频。</span></div> : <ul>
      {materials.map((item) => <li key={item.material_id}>
        <button type="button" className={selectedId === item.material_id ? "material-selected" : ""} onClick={() => select(item.material_id)}>
          <span><strong>{item.original_name}</strong><small>{item.product_name} · {item.product_sku}</small></span>
          <em>{item.clip_count} 段</em>
          <p>{item.model_name} / {item.scene} / {item.batch}</p>
          <i className={`recognition-${item.recognition_status}`}>{recognitionLabels[item.recognition_status]}</i>
        </button>
      </li>)}
    </ul>}
  </aside>;
}
