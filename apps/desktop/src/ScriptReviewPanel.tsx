import { useState } from "react";
import type { ScriptPackage, ScriptRevision } from "@content-factory/contracts";

import { scriptReviewLabels, scriptSkillTrace, type ScriptContext } from "./scripts";
import { formatSkillTimecode } from "./viralSkills";

export function ScriptReviewPanel({
  script, context, revisions, dirty, isSubmitting, onReview,
}: {
  script: ScriptPackage;
  context: ScriptContext;
  revisions: readonly ScriptRevision[];
  dirty: boolean;
  isSubmitting: boolean;
  onReview: (status: "approved" | "rejected", reviewer: string, note: string) => Promise<boolean>;
}) {
  const reviewer = "本机";
  const [note, setNote] = useState("");
  const blocking = script.review.issues.some((issue) => issue.severity === "blocking");
  const skillTrace = scriptSkillTrace(context);

  return <section className="script-review-panel" aria-labelledby="script-review-title">
    <div className="script-review-intro">
      <h3 id="script-review-title">人工审核与输入追溯</h3>
      <p>批准前逐段核对商品事实、爆点证据、逐字口播、语气表情和同步动作。固定直播间规则不可绕过。</p>
    </div>
    <div className="script-input-trace">
      <div><span>商品输入</span><strong>{context.product.name}</strong><small>{context.product.sku} · 第 {script.product_revision} 版</small></div>
      <div><span>引用 Skill</span><strong>{skillTrace?.name ?? "旧脚本未冻结 Skill"}</strong><small>{skillTrace ? `R${skillTrace.revision} · ${skillTrace.distinctVideoCount} 条不同视频 · ${skillTrace.evidenceCount} 次证据` : `仅保留分析 R${script.source_analysis_revision}`}</small></div>
      <div><span>爆点机制</span><strong>{context.pattern.name}</strong><small>分析第 {script.source_analysis_revision} 版</small></div>
      <div><span>生成模型</span><strong>{script.generation.model}</strong><small>{script.generation.provider} · 原视频未上传</small></div>
      <div><span>拍摄方式</span><strong>{script.production_mode ? "固定直播间长镜头" : "旧版脚本"}</strong><small>{script.production_mode ? "单主播 · 固定机位 · 原声连续" : "建议重新生成后再批准"}</small></div>
    </div>
    {skillTrace && <details className="script-skill-provenance">
      <summary>查看引用 Skill 的代表来源</summary>
      {skillTrace.sources.length === 0 ? <p>这份冻结 Skill 没有可展示的来源摘要，批准前请回到 S3 核对。</p> : <ul>{skillTrace.sources.map((source, index) => <li key={`${source.videoId}-${source.startMs}-${index}`}>
        <div><strong>{source.sourceName}</strong><time>{source.startMs === null ? "时间码待补" : `${formatSkillTimecode(source.startMs)}—${formatSkillTimecode(source.endMs ?? source.startMs)}`}</time></div>
        <q>{source.dialogue || "无可引用原话"}</q>
        <small>动作：{source.action || "未单独标注"}</small>
      </li>)}</ul>}
      <p>这里显示生成当时冻结的 Skill 版本，后续 Skill 修订不会悄悄改写这份脚本。</p>
    </details>}
    <div className="machine-issues">
      <strong>机器检查</strong>
      {script.review.issues.length === 0 ? <p className="issue-clear">没有发现阻塞项，仍需人工核对。</p> : <ul>{script.review.issues.map((issue) => <li className={`issue-${issue.severity}`} key={issue.code}><b>{issue.severity === "blocking" ? "阻塞" : issue.severity === "warning" ? "注意" : "提示"}</b><span>{issue.message}</span></li>)}</ul>}
    </div>
    <div className="review-inputs">

      <label>审核意见<textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="批准可写确认重点；驳回必须写修改原因" /></label>
    </div>
    {dirty && <p className="review-warning" role="status">上方还有未保存修改，请先保存新版本再审核。</p>}
    <div className="review-actions">
      <span>当前：<b>{scriptReviewLabels[script.review.status]}</b></span>
      <button className="secondary-button danger-button" type="button" disabled={dirty || !reviewer.trim() || !note.trim() || isSubmitting} onClick={() => void onReview("rejected", reviewer.trim(), note)}>驳回修改</button>
      <button className="primary-button" type="button" disabled={dirty || blocking || !reviewer.trim() || isSubmitting} onClick={() => void onReview("approved", reviewer.trim(), note)}>批准为可拍摄</button>
    </div>
    <details className="script-history"><summary>查看 {revisions.length} 条版本记录</summary><ol>{revisions.map((item) => <li key={item.revision}><strong>第 {item.revision} 版</strong><em>{scriptReviewLabels[item.review_status]}</em><small>{new Date(item.created_at).toLocaleString("zh-CN")}</small></li>)}</ol></details>
  </section>;
}
