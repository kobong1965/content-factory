import { FileDropInput } from "./FileDropInput";
import { useEffect, useState, type FormEvent } from "react";

import { MaterialEditor } from "./MaterialEditor";
import { MaterialLibrary } from "./MaterialLibrary";
import { ModalDialog } from "./ModalDialog";
import { ShootingTaskPanel } from "./ShootingTaskPanel";
import { WorkspaceHeader } from "./WorkspaceHeader";
import { importStatusLabels } from "./materials";
import { guardUnsavedTransition } from "./unsavedChanges";
import type { useS6Materials } from "./useS6Materials";

export function S6MaterialWorkspace({ center, openScripts, openProducts, openSettings }: {
  center: ReturnType<typeof useS6Materials>;
  openScripts: () => void; openProducts: () => void; openSettings: () => void;
}) {
  const [productId, setProductId] = useState("");
  const [scriptId, setScriptId] = useState("");
  const [materialRole, setMaterialRole] = useState<"host_take" | "detail">("host_take");
  const [shotDate, setShotDate] = useState(new Date().toISOString().slice(0, 10));
  const [batch, setBatch] = useState(`${new Date().toISOString().slice(0, 10)}-直播间`);
  const importedBy = "本机";
  const [note, setNote] = useState("");
  const [videos, setVideos] = useState<File[]>([]);
  const [showImport, setShowImport] = useState(false);

  useEffect(() => { if (!productId && center.products[0]) setProductId(center.products[0].product_id); }, [center.products, productId]);
  useEffect(() => {
    if (scriptId && center.scripts.find((item) => item.script_id === scriptId)?.product_id !== productId) setScriptId("");
  }, [center.scripts, productId, scriptId]);
  const scripts = center.scripts.filter((item) => item.product_id === productId);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (videos.length === 0) return;
    const ok = await center.uploadBatch(videos.map((video) => ({
      product_id: productId,
      source_script_id: scriptId,
      capture_role: materialRole,
      model_name: materialRole === "host_take" ? "主播 1 人" : "无人物（商品细节）",
      scene: "固定直播间",
      shot_date: shotDate,
      batch: batch.trim(),
      imported_by: importedBy.trim(),
      note: note.trim(),
      video,
    })));
    if (ok) { setVideos([]); setShowImport(false); }
  };

  return <div className="content material-content">
    <WorkspaceHeader
      stage="S6 · 素材中心"
      title="拍摄素材库"
      description="批量接收固定直播间的主播长镜头和同款细节镜头；本机切分、转写、归档并保留复用记录。"
      current={<><strong>当前素材</strong><span>{center.material?.file.original_name ?? "尚未选择"}</span></>}
      metrics={[
        { label: "素材 / 片段", value: `${center.readiness.material_count} / ${center.readiness.clip_count}` },
        { label: "处理中", value: center.readiness.pending_imports, tone: center.readiness.pending_imports ? "warning" : "default" },
        { label: "待补拍", value: center.readiness.pending_shoot_tasks, tone: center.readiness.pending_shoot_tasks ? "danger" : "success" },
      ]}
      action={<button className="primary-button" type="button" onClick={() => setShowImport(true)}>上传本次拍摄素材</button>}
    />

    {center.actionMessage && <div className="action-message" role="status" aria-live="polite">{center.actionMessage}</div>}
    <ModalDialog open={showImport} title="批量导入本次拍摄素材" description="固定直播间、固定机位和固定灯光已作为生产规则，无需每次重复填写。原视频只保存在本机。" onClose={() => setShowImport(false)}>
      <section className="material-upload-panel modal-create-panel" aria-label="素材导入参数">
      <div className="script-prerequisites">
        {center.products.length === 0 && <div><strong>没有可绑定商品</strong><span>先上传商品图，并确认至少一个真实卖点。</span><button className="text-button" type="button" onClick={openProducts}>打开商品资料</button></div>}
        {center.scripts.length === 0 && <div><strong>没有已批准脚本</strong><span>素材仍可先入库，之后再回来匹配。</span><button className="text-button" type="button" onClick={openScripts}>打开脚本编导</button></div>}
        {!center.readiness.gateway_configured && <div><strong>当前是人工标注模式</strong><span>本地处理不受影响；需要 AI 标签时再配置中转站。</span><button className="text-button" type="button" onClick={openSettings}>模型设置</button></div>}
      </div>
      <form className="material-upload-form modal-form" onSubmit={(event) => void submit(event)}>
        <label>商品<select value={productId} required onChange={(event) => setProductId(event.target.value)}><option value="">选择商品</option>{center.products.map((item) => <option key={item.product_id} value={item.product_id}>{item.name} · {item.sku}</option>)}</select></label>
        <label>对应脚本（可选）<select value={scriptId} onChange={(event) => setScriptId(event.target.value)}><option value="">暂不绑定</option>{scripts.map((item) => <option key={item.script_id} value={item.script_id}>{item.product_name} · 拍摄版“{item.selected_version_name}”</option>)}</select></label>
        <label>这批素材是什么<select value={materialRole} onChange={(event) => setMaterialRole(event.target.value as "host_take" | "detail")}><option value="host_take">主播连续长镜头（主素材）</option><option value="detail">同款商品细节（可复用）</option></select></label>
        <label>拍摄日期<input required type="date" value={shotDate} onChange={(event) => setShotDate(event.target.value)} /></label>
        <label>拍摄批次<input required value={batch} onChange={(event) => setBatch(event.target.value)} placeholder="例如：2026-09-07-开播前" /></label>

        <label className="material-file">视频文件（可多选）<FileDropInput required multiple type="file" accept="video/mp4,video/quicktime,video/x-matroska,video/x-msvideo,video/webm,video/x-m4v" onChange={(event) => setVideos(Array.from(event.target.files ?? []))} /><small>{videos.length ? `已选择 ${videos.length} 个视频` : "主播长镜头和细节镜头请分两批选择"}</small></label>
        <label className="material-note">备注（可选）<input value={note} onChange={(event) => setNote(event.target.value)} placeholder={materialRole === "host_take" ? "例如：脚本 A 连续录制" : "例如：裤腰、口袋、走线"} /></label>
        <div className="fixed-production-note" role="note"><strong>本批固定条件</strong><span>固定直播间 · 固定机位 · 固定灯光</span><small>{materialRole === "host_take" ? "保留主播连续口播原声" : "按当前商品归档，可供以后同款视频复用"}</small></div>
        <button className="primary-button" type="submit" disabled={center.isSubmitting || !productId || videos.length === 0 || !batch.trim() || !importedBy.trim()}>开始处理 {videos.length ? `${videos.length} 个视频` : ""}</button>
      </form>
      </section>
    </ModalDialog>

    {center.imports.length > 0 && <section className="material-imports material-import-status workbench-surface" aria-labelledby="recent-import-title"><div className="panel-heading"><h2 id="recent-import-title">最近导入</h2><span>{center.imports.length} 条</span></div><ul>{center.imports.slice(0, 5).map((task) => <li key={task.task_id}><span>{task.source_name}</span><i><b style={{ width: `${task.progress}%` }} /></i><em>{importStatusLabels[task.status]} · {task.progress}%</em>{task.status === "failed" && <button className="text-button" type="button" disabled={center.isSubmitting} onClick={() => void center.retry(task.task_id)}>重试</button>}</li>)}</ul></section>}

    <section className="material-workbench workbench-surface">
      <MaterialLibrary materials={center.materials} selectedId={center.material?.material_id} select={(id) => {
        if (center.material?.material_id === id) return;
        guardUnsavedTransition(() => { void center.selectMaterial(id); });
      }} />
      <div className="material-detail">{center.material ? <MaterialEditor material={center.material} usage={center.usage} busy={center.isSubmitting} gatewayConfigured={center.readiness.gateway_configured} save={(draft, actor) => void center.save(draft, actor)} recognize={() => void center.recognize(center.material!.material_id)} /> : <div className="material-detail-empty"><span className="empty-symbol">空</span><h2>选择一条素材查看片段</h2><p>你可以播放代理视频、修改标签、关闭复用，所有修改都会留下版本。</p></div>}</div>
    </section>

    <ShootingTaskPanel tasks={center.shootingTasks} scripts={center.scripts} materials={center.materials} busy={center.isSubmitting} confirm={(...args) => void center.confirm(...args)} release={(...args) => void center.release(...args)} />
  </div>;
}
