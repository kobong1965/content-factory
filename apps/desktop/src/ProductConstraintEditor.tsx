import type { ProductProfile, ShootingConstraints } from "@content-factory/contracts";

export function ProductConstraintEditor({
  profile, onChange,
}: {
  profile: ProductProfile;
  onChange: (profile: ProductProfile) => void;
}) {
  const constraints = profile.shooting_constraints;
  const update = (changes: Partial<ShootingConstraints>) => onChange({
    ...profile, shooting_constraints: { ...constraints, ...changes },
  });
  const listChange = (value: string) => update({ required_disclosures: value.split("\n") });

  return <fieldset className="product-section" disabled={profile.status === "archived"}>
    <legend>脚本边界</legend>
    <p>固定直播间生产方式已经锁定；这里只补充脚本不能越过的商品和品牌边界。</p>
    <div className="fixed-production-policy" role="note">
      <strong>固定直播间生产规则</strong>
      <span>主播 1 人 · 固定直播间 · 固定竖屏机位 · 固定灯光 · 连续长镜头</span>
      <small>允许后期覆盖同款裤腰、口袋、走线等细节；不得要求外拍、跟拍、移动机位或增加出镜人员。</small>
    </div>
    <div className="boundary-grid">
      <label>品牌语气（可选）<textarea value={constraints.brand_tone} onChange={(event) => update({ brand_tone: event.target.value })} placeholder="例如：像直播间真实推荐，直接、可信、不夸张" /></label>
      <label>禁用表达<textarea value={profile.forbidden_expressions.join("\n")} onChange={(event) => onChange({ ...profile, forbidden_expressions: event.target.value.split("\n") })} placeholder="每行一个，例如：全网最低" /></label>
      <label>无法证明的说法<textarea value={profile.unprovable_claims.join("\n")} onChange={(event) => onChange({ ...profile, unprovable_claims: event.target.value.split("\n") })} placeholder="每行一个，例如：显瘦十斤" /></label>
    </div>
    <details className="formal-product-fields"><summary>正式商品档案与合规补充（现在可不填）</summary><div className="constraint-grid">
      <label>必须披露<textarea value={constraints.required_disclosures.join("\n")} onChange={(event) => listChange(event.target.value)} placeholder="每行一个；没有也可以留空" /></label>
      <label><input type="checkbox" checked={!!profile.brand_boundary_confirmed_by} onChange={(event) => onChange({ ...profile, brand_boundary_confirmed_by: event.target.checked ? '本机' : null, brand_boundary_confirmed_at: event.target.checked ? profile.brand_boundary_confirmed_at : null })} />已确认品牌边界</label>
      <label><input type="checkbox" checked={!!constraints.confirmed_by} onChange={(event) => update({ confirmed_by: event.target.checked ? '本机' : null, confirmed_at: event.target.checked ? constraints.confirmed_at : null })} />已确认固定拍摄规则</label>
      <label>每日可用时间（分钟）<input type="number" min="0" max="1440" value={constraints.daily_available_minutes} onChange={(event) => update({ daily_available_minutes: Number(event.target.value) })} /></label>
      <label>单条预算（元）<input type="number" min="0" step="1" value={constraints.budget_cny} onChange={(event) => update({ budget_cny: Number(event.target.value) })} /></label>
      <label>目标最长时长（秒）<input type="number" min="1" step="1" value={constraints.max_duration_ms / 1000} onChange={(event) => update({ max_duration_ms: Math.round(Number(event.target.value) * 1000) })} /></label>
      <label className="check-label constraint-check"><input type="checkbox" checked={constraints.reusable_assets_allowed} onChange={(event) => update({ reusable_assets_allowed: event.target.checked })} />允许复用同款细节素材</label>
    </div></details>
  </fieldset>;
}
