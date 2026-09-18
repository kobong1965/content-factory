import type { S3Readiness } from "@content-factory/contracts";

export function AnalysisMethodNotice({ method }: { method?: S3Readiness["analysis_method"] }) {
  if (!method) return <p role="status">正在确认本机分析方法…</p>;
  if (method.status !== "ready") {
    return <p className="inline-warning" role={method.status === "invalid" ? "alert" : "status"}>{method.message}</p>;
  }
  return <div aria-label="本次分析方法">
    <p><strong>已启用 {method.title}</strong>（huashu-douyin-script）</p>
    <p>{method.dimensions.join(" · ")}</p>
    <p>新任务保存方法快照，结论关联原话、动作与时间码。复用建议遵守固定直播间长镜头；核对并批准后，才能用于商品脚本。</p>
  </div>;
}

export function analysisReportMethodLabel(purpose: string, promptVersion: string): string {
  if (purpose === "video_review") return `视频审核 · ${promptVersion}`;
  if (promptVersion === "1.3.0") return "Huashu 七维爆点拆解 · 适配版 1.0";
  return `基础爆点分析 · ${promptVersion}`;
}
