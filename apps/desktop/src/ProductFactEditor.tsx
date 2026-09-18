import type { ProductFact, ProductProfile } from "@content-factory/contracts";

const fieldLabels = {
  fabric: "面料", fit: "版型", size: "尺码", color: "颜色", price: "价格",
  activity: "活动", feature: "功能卖点", care: "护理", other: "其他",
} as const;

function nextId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "")}`;
}

export function ProductFactEditor({
  profile, actor, onChange,
}: {
  profile: ProductProfile;
  actor: string;
  onChange: (profile: ProductProfile) => void;
}) {
  const setFacts = (facts: readonly ProductFact[]) => onChange({ ...profile, facts });
  const updateFactClaim = (id: string, changes: Partial<ProductFact>) => onChange({
    ...profile,
    facts: profile.facts.map((fact) => fact.id === id ? {
      ...fact,
      ...changes,
      confirmed_by: "",
      confirmed_at: "",
    } : fact),
    selling_point_fact_ids: profile.selling_point_fact_ids.filter((factId) => factId !== id),
  });
  const removeFact = (id: string) => onChange({
    ...profile,
    facts: profile.facts.filter((fact) => fact.id !== id),
    selling_point_fact_ids: profile.selling_point_fact_ids.filter((factId) => factId !== id),
  });
  const addFact = () => setFacts([...profile.facts, {
    id: nextId("fact"), field: "feature", label: "核心卖点", value: "", unit: null,
    source_type: "manual_confirmed", source_id: null, source_ref: "人工确认",
    confirmed_by: "", confirmed_at: "",
  }]);
  const toggleSellingPoint = (id: string, checked: boolean) => onChange({
    ...profile,
    selling_point_fact_ids: checked
      ? [...new Set([...profile.selling_point_fact_ids, id])]
      : profile.selling_point_fact_ids.filter((factId) => factId !== id),
  });
  const toggleConfirmed = (fact: ProductFact, checked: boolean) => onChange({
    ...profile,
    facts: profile.facts.map((item) => item.id === fact.id ? {
      ...item,
      source_ref: checked && !item.source_ref.trim() && item.source_type === "manual_confirmed"
        ? "人工现场确认"
        : item.source_ref,
      confirmed_by: checked ? (actor.trim() || "本机操作员") : "",
      confirmed_at: checked ? new Date().toISOString() : "",
    } : item),
    selling_point_fact_ids: checked
      ? profile.selling_point_fact_ids
      : profile.selling_point_fact_ids.filter((factId) => factId !== fact.id),
  });

  return <fieldset className="product-section fact-section" disabled={profile.status === "archived"}>
    <legend>事实与核心卖点</legend>
    <p>这里只保存你已经核对过的事实。首版只需要确认一条真实卖点即可写脚本，其他资料以后再补。</p>
    {profile.facts.length === 0 && <div className="product-inline-empty">还没有已确认事实。可以让 AI 从商品图提出候选，或手工添加一条真实卖点。</div>}
    <div className="fact-list">
      {profile.facts.map((fact, index) => <article className="fact-row" key={fact.id}>
        <div className="fact-row-head">
          <strong>事实 {index + 1}</strong>
          <label className="check-label"><input type="checkbox" checked={Boolean(fact.confirmed_by.trim() && fact.confirmed_at)} disabled={!fact.value.trim()} onChange={(event) => toggleConfirmed(fact, event.target.checked)} />已核对，允许写入脚本</label>
          <label className="check-label" title={fact.confirmed_at ? "" : "请先核对并确认这条事实"}><input type="checkbox" checked={profile.selling_point_fact_ids.includes(fact.id)} disabled={!fact.confirmed_at} onChange={(event) => toggleSellingPoint(fact.id, event.target.checked)} />作为核心卖点</label>
          <button className="text-button danger-text" type="button" onClick={() => removeFact(fact.id)}>移除</button>
        </div>
        <div className="fact-grid">
          <label>类别<select value={fact.field} onChange={(event) => updateFactClaim(fact.id, { field: event.target.value as ProductFact["field"] })}>{Object.entries(fieldLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
          <label>显示名称<input value={fact.label} onChange={(event) => updateFactClaim(fact.id, { label: event.target.value })} /></label>
          <label className="fact-value">确认内容<input value={fact.value} onChange={(event) => updateFactClaim(fact.id, { value: event.target.value })} placeholder="只写图片可见或人工核对过的信息" /></label>
          <label>单位<input value={fact.unit ?? ""} onChange={(event) => updateFactClaim(fact.id, { unit: event.target.value.trim() || null })} placeholder="可留空" /></label>
          <label>来源方式<select value={fact.source_type} onChange={(event) => updateFactClaim(fact.id, { source_type: event.target.value as ProductFact["source_type"], source_id: event.target.value === "manual_confirmed" ? null : fact.source_id })}><option value="manual_confirmed">人工确认</option><option value="official_detail_page">官方详情页</option><option value="supplier_document">供应资料</option><option value="media_evidence">图片或视频证据</option></select></label>
          <label>关联资料<select value={fact.source_id ?? ""} onChange={(event) => updateFactClaim(fact.id, { source_id: event.target.value || null })}><option value="">不关联文件</option>{profile.sources.map((source) => <option value={source.id} key={source.id}>{source.label}</option>)}</select></label>
          <label className="fact-value">来源说明<input value={fact.source_ref} onChange={(event) => updateFactClaim(fact.id, { source_ref: event.target.value })} placeholder="人工确认可留空；图片证据会自动关联" /></label>
          <div className="fact-confirmation"><span>确认记录</span><strong>{fact.confirmed_at ? `${fact.confirmed_by} · 已确认` : "待人工核对"}</strong></div>
        </div>
      </article>)}
    </div>
    <button className="secondary-button compact-button" type="button" onClick={addFact}>添加一条事实</button>
  </fieldset>;
}
