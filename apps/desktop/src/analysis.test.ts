import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_GATEWAY_SETTINGS,
  DEFAULT_S3_READINESS,
  analysisProcessingPurpose,
  isAnalysisConclusionDirty,
  analysisPurposeCopy,
  apiErrorDetailMessage,
  analysisStatusLabel,
  analysisStepLabel,
  connectGatewayModel,
  createAnalysis,
  discoverGatewayModels,
  keyframeUrl,
} from "./analysis";
import {
  analysisReportMutationTarget,
  commitFetchedAnalysisReport,
  commitMutatedAnalysisReport,
  disconnectAnalysisView,
  selectAnalysisTask,
  visibleAnalysisReport,
  type AnalysisReportSelectionState,
} from "./useS3Analysis";
import type { AnalysisReport } from "@content-factory/contracts";

describe("S3 desktop analysis helpers", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("keeps offline and unconfigured states honest", () => {
    expect(DEFAULT_S3_READINESS.engineering_ready).toBe(false);
    expect(DEFAULT_S3_READINESS.business_ready).toBe(false);
    expect(DEFAULT_GATEWAY_SETTINGS.api_key_configured).toBe(false);
  });

  it("uses plain-language analysis progress", () => {
    expect(analysisStatusLabel("retry_wait")).toBe("准备重试");
    expect(analysisStepLabel("gateway")).toBe("模型深度理解");
    expect(analysisStatusLabel("cancelled")).toBe("已取消");
    expect(analysisStepLabel("segment")).toBe("分段分析中");
    expect(analysisStepLabel("summarizing")).toBe("汇总分析结论");
  });

  it("builds a local keyframe URL without exposing a filesystem path", () => {
    const url = keyframeUrl("analysis_task_a", "frame 1");
    expect(url).toContain("/s3/tasks/analysis_task_a/keyframes/frame%201");
    expect(url).not.toContain("E:\\");
  });

  it("keeps structured gateway diagnostics visible to the operator", () => {
    const message = apiErrorDetailMessage({
      diagnostic_code: "model_not_found",
      message: "模型标识不存在",
      endpoint_url: "https://relay.example/chat/completions",
      api_mode: "chat_completions",
      model: "gpt-5.6sol",
      suggestion: "请核对模型名中的连字符",
    }, 404);

    expect(message).toContain("模型标识不存在");
    expect(message).toContain("POST https://relay.example/chat/completions");
    expect(message).toContain("请核对模型名中的连字符");
  });

  it("keeps the selected AI task purpose in the create request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ task_id: "analysis_task_1" }), {
      status: 202,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await createAnalysis({
      media_task_id: "media_0123456789abcdef0123456789abcdef",
      model_purpose: "video_review",
      metric_snapshots: [],
      comments: [],
    });

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(request.body))).toMatchObject({ model_purpose: "video_review" });
    expect(analysisPurposeCopy.video_review.label).toBe("AI 视频审核");
    expect(analysisPurposeCopy.analysis.label).toBe("爆点研究智能体");
  });

  it("discovers models through the local service without changing the upstream model id", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      status: "ok",
      provider: "qwen",
      normalized_base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      catalog_source: "dashscope_models",
      models: [{
        upstream_model_id: "qwen3.7-plus",
        display_name: "Qwen 3.7 Plus",
        input_modalities: ["text", "image"],
        output_modalities: ["text"],
        supports_structured_output: true,
        capability_source: "provider_metadata",
      }],
      warnings: [],
      truncated: false,
      fetched_at: "2026-09-08T00:00:00Z",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await discoverGatewayModels({
      base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      api_key: "fixture-qwen-key",
    });

    expect(result.models[0]?.upstream_model_id).toBe("qwen3.7-plus");
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/s3/gateway/discover");
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(request.body))).toEqual({
      base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      api_key: "fixture-qwen-key",
    });
  });

  it("connects the exact model selected from discovery instead of rebuilding its name", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      status: "ok",
      connected_model_id: "model_qwen",
      latency_ms: 600,
      response_id: "resp_qwen",
      settings: DEFAULT_GATEWAY_SETTINGS,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await connectGatewayModel({
      base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      api_key: "fixture-qwen-key",
      upstream_model_id: "qwen3.7-plus",
    });

    expect(fetchMock.mock.calls[0]?.[0]).toContain("/s3/gateway/connect");
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(request.body))).toMatchObject({
      upstream_model_id: "qwen3.7-plus",
    });
    expect(String(request.body)).not.toContain("Qwen3.7-Plus");
  });

  it("reuses a saved credential without putting a secret in the discovery request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      status: "ok",
      provider: "openai_compatible",
      normalized_base_url: "https://relay.example/v1",
      catalog_source: "openai_models",
      models: [],
      warnings: [],
      truncated: false,
      fetched_at: "2026-09-08T00:00:00Z",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await discoverGatewayModels({
      base_url: "https://relay.example/v1",
      saved_model_id: "model_saved",
    });

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(request.body))).toEqual({
      base_url: "https://relay.example/v1",
      saved_model_id: "model_saved",
    });
    expect(String(request.body)).not.toContain("api_key");
  });

  it("marks manual model fallback explicitly instead of silently bypassing discovery", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      status: "ok",
      connected_model_id: "model_manual",
      latency_ms: 420,
      response_id: null,
      settings: DEFAULT_GATEWAY_SETTINGS,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await connectGatewayModel({
      base_url: "https://relay.example/v1",
      api_key: "fixture-relay-key",
      upstream_model_id: "relay-vision-model",
      manual_model_id: true,
    });

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(request.body))).toMatchObject({
      upstream_model_id: "relay-vision-model",
      manual_model_id: true,
    });
  });

  it("rejects ambiguous credential input before any request can leave the desktop", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(discoverGatewayModels({
      base_url: "https://relay.example/v1",
      api_key: "new-key-value",
      saved_model_id: "model_saved",
    })).rejects.toThrow("或复用同一服务地址");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uses report processing purpose for result labels and keeps legacy reports compatible", () => {
    expect(analysisProcessingPurpose({ purpose: "video_review" })).toBe("video_review");
    expect(analysisProcessingPurpose({ purpose: "analysis" })).toBe("analysis");
    expect(analysisProcessingPurpose({})).toBe("analysis");
  });

  it("disconnects a refreshed or closed view without cancelling the persisted task", () => {
    const controller = new AbortController();
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    disconnectAnalysisView(controller);

    expect(controller.signal.aborted).toBe(true);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("blocks review when the visible conclusion has not been saved", () => {
    expect(isAnalysisConclusionDirty("服务端结论", "用户刚修改的结论")).toBe(true);
    expect(isAnalysisConclusionDirty("服务端结论", " 服务端结论 ")).toBe(false);
  });

  it("clears the previous report immediately when the operator selects another task", () => {
    const reportA = { analysis_id: "analysis_a", revision: 1 } as AnalysisReport;
    const selectedA: AnalysisReportSelectionState = {
      selectedTaskId: "task_a",
      reportBinding: { taskId: "task_a", report: reportA },
    };

    const selectedB = selectAnalysisTask(selectedA, "task_b");

    expect(selectedB).toEqual({ selectedTaskId: "task_b", reportBinding: null });
    expect(visibleAnalysisReport(selectedB)).toBeNull();
    expect(analysisReportMutationTarget(selectedB)).toBeNull();
  });

  it("ignores a slow report response from the previously selected task", () => {
    const reportA = { analysis_id: "analysis_a", revision: 1 } as AnalysisReport;
    const selectedB: AnalysisReportSelectionState = {
      selectedTaskId: "task_b",
      reportBinding: null,
    };

    const afterLateA = commitFetchedAnalysisReport(selectedB, "task_a", reportA);

    expect(afterLateA).toBe(selectedB);
    expect(visibleAnalysisReport(afterLateA)).toBeNull();
  });

  it("commits a fetched report only to the task that requested it", () => {
    const reportB = { analysis_id: "analysis_b", revision: 3 } as AnalysisReport;
    const selectedB: AnalysisReportSelectionState = {
      selectedTaskId: "task_b",
      reportBinding: null,
    };

    const loadedB = commitFetchedAnalysisReport(selectedB, "task_b", reportB);

    expect(visibleAnalysisReport(loadedB)).toBe(reportB);
    expect(analysisReportMutationTarget(loadedB)).toEqual({ taskId: "task_b", report: reportB });
  });

  it("does not let an old save or review response overwrite the new selection", () => {
    const reportA = { analysis_id: "analysis_a", revision: 1 } as AnalysisReport;
    const savedA = { analysis_id: "analysis_a", revision: 2 } as AnalysisReport;
    const selectedA: AnalysisReportSelectionState = {
      selectedTaskId: "task_a",
      reportBinding: { taskId: "task_a", report: reportA },
    };
    const selectedB = selectAnalysisTask(selectedA, "task_b");

    const afterLateSave = commitMutatedAnalysisReport(selectedB, "task_a", reportA, savedA);

    expect(afterLateSave).toBe(selectedB);
    expect(analysisReportMutationTarget(afterLateSave)).toBeNull();
  });

  it("rejects a mismatched report before save or review can choose an API target", () => {
    const reportA = { analysis_id: "analysis_a", revision: 1 } as AnalysisReport;
    const corruptedSelection: AnalysisReportSelectionState = {
      selectedTaskId: "task_b",
      reportBinding: { taskId: "task_a", report: reportA },
    };

    expect(visibleAnalysisReport(corruptedSelection)).toBeNull();
    expect(analysisReportMutationTarget(corruptedSelection)).toBeNull();
  });
});
