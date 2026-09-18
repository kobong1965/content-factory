import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import type { ScriptContentGoal } from "@content-factory/contracts";

import { ScriptEditor } from "./ScriptEditor";
import { ModalDialog } from "./ModalDialog";
import { WorkspaceHeader } from "./WorkspaceHeader";
import { reconcileSelectedProductId, reconcileSelectedSkillId, scriptGoalLabels, scriptReviewLabels, scriptStepLabels, scriptTaskLabels } from "./scripts";
import { S5SkillPicker } from "./S5SkillPicker";
import { guardUnsavedTransition } from "./unsavedChanges";
import type { useS5Scripts } from "./useS5Scripts";

export function S5ScriptWorkspace({
  studio, openAnalysis, openProducts, openSettings,
}: {
  studio: ReturnType<typeof useS5Scripts>;
  openAnalysis: () => void;
  openProducts: () => void;
  openSettings: () => void;
}) {
  const [skillId, setSkillId] = useState("");
  const [productId, setProductId] = useState("");
  const [goal, setGoal] = useState<ScriptContentGoal>("conversion");
  const [audience, setAudience] = useState("关注版型、上身效果和真实商品细节的男装消费者");
  const [versionCount, setVersionCount] = useState<3 | 4 | 5>(3);
  const [showCreate, setShowCreate] = useState(false);
  const [generationAttempted, setGenerationAttempted] = useState(false);
  const generationInFlight = useRef(false);
  const createFormId = useId();
  const feedbackId = useId();

  useEffect(() => {
    setProductId((current) => reconcileSelectedProductId(current, studio.products));
  }, [studio.products]);

  useEffect(() => {
    setSkillId((current) => reconcileSelectedSkillId(current, studio.templates));
  }, [studio.templates]);

  const generate = async (event: FormEvent) => {
    event.preventDefault();
    if (!readyToGenerate || studio.isSubmitting || generationInFlight.current) return;
    const selectedSkill = studio.templates.find((item) => item.skill_id === skillId);
    const selectedProduct = studio.products.find((item) => item.product_id === productId);
    if (!selectedSkill || !selectedProduct) return;
    generationInFlight.current = true;
    setGenerationAttempted(true);
    try {
      const created = await studio.generate({
        skill_id: selectedSkill.skill_id, product_id: selectedProduct.product_id, content_goal: goal,
        target_audience: audience.trim(), version_count: versionCount,
      });
      if (created) setShowCreate(false);
    } finally {
      generationInFlight.current = false;
    }
  };

  const readyToGenerate = studio.readiness.gateway_configured && Boolean(
    studio.templates.some((item) => item.skill_id === skillId)
    && studio.products.some((item) => item.product_id === productId)
    && audience.trim(),
  );
  const generationHint = !studio.readiness.gateway_configured ? "请先连接可用于脚本生成的模型。"
    : !studio.templates.some((item) => item.skill_id === skillId) ? "请选择一个已批准的爆点 Skill。"
    : !studio.products.some((item) => item.product_id === productId) ? "请在表单中选择自有商品。"
    : !audience.trim() ? "请填写目标人群。"
    : "已就绪，生成后可选择其中一版拍摄。";
  const generationError = generationAttempted && !studio.isSubmitting && studio.actionMessageKind === "error"
    ? studio.actionMessage : null;
  const leaveCreate = (navigate: () => void) => {
    setShowCreate(false);
    navigate();
  };

  return <div className="content script-content">
    <WorkspaceHeader
      stage="S5 · 脚本编导"
      title="脚本生产台"
      description="把已批准的爆点 Skill 适配到当前商品真实卖点，生成 3—5 个固定直播间长镜头脚本，再逐段核对。"
      current={<><strong>当前脚本</strong><span>{studio.script ? `${studio.context?.product.name ?? studio.script.script_id} · 第 ${studio.script.revision} 版` : "尚未选择"}</span></>}
      metrics={[
        { label: "可用 Skill", value: studio.readiness.available_templates },
        { label: "待审核", value: studio.readiness.pending_review_scripts, tone: studio.readiness.pending_review_scripts ? "warning" : "default" },
        { label: "可拍摄", value: studio.readiness.approved_scripts, tone: "success" },
      ]}
      action={<button className="primary-button" type="button" onClick={() => { setGenerationAttempted(false); setShowCreate(true); }}>生成新脚本</button>}
    />

    {!showCreate && studio.actionMessage && <div className={`action-message${studio.actionMessageKind === "error" ? " action-message-error" : ""}`} role={studio.actionMessageKind === "error" ? "alert" : "status"} aria-live={studio.actionMessageKind === "error" ? "assertive" : "polite"}>{studio.actionMessage}</div>}

    <ModalDialog open={showCreate} title="建立脚本生成任务" description="生成时冻结当前分析与商品版本，后续变化不会悄悄改写这份脚本。" onClose={() => setShowCreate(false)} footer={<>
      <div className="modal-submit-feedback" id={feedbackId}>
        {generationError ? <p role="alert">{generationError}</p> : <p role="status">{studio.isSubmitting ? "正在建立任务，请勿重复提交…" : generationHint}</p>}
      </div>
      <button className="primary-button" type="submit" form={createFormId} aria-describedby={feedbackId} disabled={!readyToGenerate || studio.isSubmitting}>{studio.isSubmitting ? "正在提交…" : `生成 ${versionCount} 版脚本`}</button>
    </>}>
      <section className="script-create-panel modal-create-panel" aria-label="脚本生成参数">
        <div className="script-prerequisites">
        {!studio.readiness.gateway_configured && <div><strong>中转站未配置</strong><span>先填写地址、模型和 API Key。</span><button className="text-button" type="button" onClick={() => leaveCreate(openSettings)}>打开模型设置</button></div>}
        {studio.templates.length === 0 && <div><strong>没有可用爆点 Skill</strong><span>先在爆点研究中接受报告，再对候选做“批准并用于脚本”的人工确认。</span><button className="text-button" type="button" onClick={() => leaveCreate(openAnalysis)}>打开爆点研究</button></div>}
        {studio.products.length === 0 && <div><strong>没有可写脚本的商品</strong><span>上传商品图并确认至少一条真实卖点即可，不必先补齐全部档案。</span><button className="text-button" type="button" onClick={() => leaveCreate(openProducts)}>打开商品资料</button></div>}
      </div>
      <form id={createFormId} className="script-create-form modal-form" onSubmit={(event) => void generate(event)}>
        <S5SkillPicker skills={studio.templates} selectedSkillId={skillId} onChange={setSkillId} />
        <label>自有商品<select value={productId} onChange={(event) => setProductId(event.target.value)} disabled={studio.products.length === 0}><option value="">请选择已确认卖点的商品</option>{studio.products.map((product) => <option value={product.product_id} key={product.product_id}>{product.name} · {product.sku}{product.status === "draft" ? " · 临时商品" : ""}</option>)}</select></label>
        <label>内容目标<select value={goal} onChange={(event) => setGoal(event.target.value as ScriptContentGoal)}>{Object.entries(scriptGoalLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <label>版本数量<select value={versionCount} onChange={(event) => setVersionCount(Number(event.target.value) as 3 | 4 | 5)}><option value={3}>3 个版本</option><option value={4}>4 个版本</option><option value={5}>5 个版本</option></select></label>
        <label className="script-audience">目标人群<input value={audience} onChange={(event) => setAudience(event.target.value)} placeholder="例如：需要通勤显利落、在意垂感的 25—40 岁男性" /></label>
      </form>
      <div className="fixed-production-note" role="note"><strong>脚本固定规则</strong><span>单主播 · 固定直播间 · 固定机位 · 固定灯光 · 连续长镜头</span><small>模型必须把口播、语气、停顿、表情、目光和动作逐段写清；细节镜头只作为可选覆盖画面。</small></div>
      </section>
    </ModalDialog>

    <section className="script-workbench workbench-surface" aria-label="脚本任务和编辑工作台">
      <aside className="script-library">
        <div className="panel-heading"><h2>生成任务</h2><span>{studio.tasks.length}</span></div>
        {studio.tasks.length === 0 ? <div className="script-side-empty">还没有生成任务</div> : <ul className="script-task-list">{studio.tasks.map((task) => <li key={task.task_id}><div><strong>{scriptTaskLabels[task.status]}</strong><span>{scriptStepLabels[task.current_step]} · {task.progress}%</span></div><i><b style={{ width: `${task.progress}%` }} /></i>{task.error && <small>{task.error}</small>}{task.status === "failed" && <button className="text-button" type="button" disabled={studio.isSubmitting} onClick={() => void studio.retry(task.task_id)}>修好设置后重试</button>}</li>)}</ul>}

        <div className="panel-heading script-list-heading"><h2>脚本包</h2><span>{studio.scripts.length}</span></div>
        {studio.isLoading && studio.scripts.length === 0 ? <div className="script-side-empty" aria-busy="true">正在读取脚本…</div> : studio.scripts.length === 0 ? <div className="script-side-empty">生成完成的脚本会出现在这里</div> : <ul className="script-package-list">{studio.scripts.map((item) => <li key={item.script_id}><button type="button" className={studio.script?.script_id === item.script_id ? "script-package-active" : ""} onClick={() => {
          if (studio.script?.script_id === item.script_id) return;
          guardUnsavedTransition(() => { void studio.select(item.script_id); });
        }}><span><strong>{item.product_name}</strong><small>{item.product_sku} · {item.pattern_name}</small></span><em className={`script-review-status review-${item.review_status}`}>{scriptReviewLabels[item.review_status]}</em><p>{item.version_count} 个版本 · 第 {item.revision} 版</p></button></li>)}</ul>}
      </aside>

      <div className="script-detail">
        {studio.script && studio.context ? <ScriptEditor script={studio.script} context={studio.context} revisions={studio.revisions} isSubmitting={studio.isSubmitting} onSave={studio.save} onReview={studio.review} /> : <div className="script-empty"><span className="empty-symbol" aria-hidden="true">空</span><h2>选择一份脚本</h2><p>脚本生成完成后，从左侧打开它，逐版本核对并人工批准。</p></div>}
      </div>
    </section>
  </div>;
}
