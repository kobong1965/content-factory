import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { S3Readiness } from "@content-factory/contracts";
import { AnalysisMethodNotice, analysisReportMethodLabel } from "./AnalysisMethodNotice";

const method: NonNullable<S3Readiness["analysis_method"]> = {
  id: "huashu-douyin-script", title: "Huashu 七维爆点拆解", status: "ready",
  prompt_version: "1.3.0", dimensions: ["钩子分析", "分镜结构", "节奏设计", "视觉元素", "转化设计", "合规检查", "可复制要素"],
  message: "新爆点分析将使用七维拆解；旧任务和视频审核保持原方法。",
};

describe("analysis method provenance", () => {
  it("shows the confirmed method, seven dimensions and fixed-camera boundary", () => {
    const html = renderToStaticMarkup(<AnalysisMethodNotice method={method} />);
    expect(html).toContain("huashu-douyin-script");
    for (const dimension of method.dimensions) expect(html).toContain(dimension);
    expect(html).toContain("固定直播间长镜头");
  });
  it("does not present a missing or damaged installation as enabled", () => {
    const html = renderToStaticMarkup(<AnalysisMethodNotice method={{ ...method, status: "invalid", message: "版本校验失败，请恢复固定版本。" }} />);
    expect(html).toContain('role="alert"');
    expect(html).toContain("版本校验失败");
    expect(html).not.toContain("已启用");
    const missing = renderToStaticMarkup(<AnalysisMethodNotice method={{ ...method, status: "not_installed", message: "七维方法未安装；新任务使用原有基础分析方法。" }} />);
    expect(missing).toContain("基础分析方法");
    expect(missing).not.toContain("已启用");
    expect(renderToStaticMarkup(<AnalysisMethodNotice />)).toContain("正在确认");
  });
  it("labels each saved report from its frozen version, never from current readiness", () => {
    expect(analysisReportMethodLabel("analysis", "1.3.0")).toBe("Huashu 七维爆点拆解 · 适配版 1.0");
    expect(analysisReportMethodLabel("analysis", "1.2.0")).toBe("基础爆点分析 · 1.2.0");
    expect(analysisReportMethodLabel("video_review", "1.2.0")).toBe("视频审核 · 1.2.0");
    expect(analysisReportMethodLabel("analysis", "1.4.0")).not.toContain("Huashu");
  });
});
