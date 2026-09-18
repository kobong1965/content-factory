import { useCallback, useEffect, useMemo, useState } from "react";
import type { AnalysisReport, AnalysisTask, GatewayPurpose } from "@content-factory/contracts";

import {
  DEFAULT_GATEWAY_SETTINGS,
  DEFAULT_S3_READINESS,
  analysisPurposeCopy,
  cancelAnalysisTask,
  createAnalysis,
  fetchAnalysisReport,
  fetchAnalysisTasks,
  fetchGatewaySettings,
  fetchS3Readiness,
  reviewAnalysisReport,
  retryAnalysisTask,
  saveGatewaySettings,
  testGateway,
  updateAnalysisReport,
  type GatewayConnectionResult,
  type GatewayModelSaveInput,
} from "./analysis";
import { gatewayConnectionErrorMessage } from "./gatewayModelSettings";
import { useWorkspacePolling } from "./useWorkspacePolling";
import { createSerializedRefresh } from "./workspacePolling";

/**
 * Disconnect only the current view's read request. Task cancellation is a
 * separate, explicit POST action and must never be coupled to unmount/reload.
 */
export function disconnectAnalysisView(controller: AbortController): void {
  controller.abort();
}

export interface AnalysisReportBinding {
  taskId: string;
  report: AnalysisReport;
}

export interface AnalysisReportSelectionState {
  selectedTaskId: string | null;
  reportBinding: AnalysisReportBinding | null;
}

export function selectAnalysisTask(
  current: AnalysisReportSelectionState,
  taskId: string | null,
): AnalysisReportSelectionState {
  if (current.selectedTaskId === taskId) return current;
  return { selectedTaskId: taskId, reportBinding: null };
}

export function visibleAnalysisReport(current: AnalysisReportSelectionState): AnalysisReport | null {
  if (!current.selectedTaskId || current.reportBinding?.taskId !== current.selectedTaskId) return null;
  return current.reportBinding.report;
}

export function analysisReportMutationTarget(current: AnalysisReportSelectionState): AnalysisReportBinding | null {
  if (!current.selectedTaskId || current.reportBinding?.taskId !== current.selectedTaskId) return null;
  return current.reportBinding;
}

export function commitFetchedAnalysisReport(
  current: AnalysisReportSelectionState,
  taskId: string,
  report: AnalysisReport,
): AnalysisReportSelectionState {
  if (current.selectedTaskId !== taskId) return current;
  return { selectedTaskId: taskId, reportBinding: { taskId, report } };
}

export function commitMutatedAnalysisReport(
  current: AnalysisReportSelectionState,
  taskId: string,
  previousReport: AnalysisReport,
  report: AnalysisReport,
): AnalysisReportSelectionState {
  const target = analysisReportMutationTarget(current);
  if (!target || target.taskId !== taskId || target.report !== previousReport) return current;
  return { selectedTaskId: taskId, reportBinding: { taskId, report } };
}

function clearAnalysisReport(
  current: AnalysisReportSelectionState,
  taskId: string | null,
): AnalysisReportSelectionState {
  if (current.selectedTaskId !== taskId || current.reportBinding === null) return current;
  return { selectedTaskId: current.selectedTaskId, reportBinding: null };
}

export function useS3Analysis(pollingActive = true) {
  const [readiness, setReadiness] = useState(DEFAULT_S3_READINESS);
  const [gateway, setGateway] = useState(DEFAULT_GATEWAY_SETTINGS);
  const [tasks, setTasks] = useState<AnalysisTask[]>([]);
  const [reportSelection, setReportSelection] = useState<AnalysisReportSelectionState>({
    selectedTaskId: null,
    reportBinding: null,
  });
  const [connectionState, setConnectionState] = useState<"connecting" | "live" | "offline">("connecting");
  const [actionNotice, setActionNotice] = useState<{ message: string; kind: "status" | "error" } | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const selectedTaskId = reportSelection.selectedTaskId;
  const report = visibleAnalysisReport(reportSelection);
  const setSelectedTaskId = useCallback((taskId: string | null) => {
    setReportSelection((current) => selectAnalysisTask(current, taskId));
  }, []);

  const refresh = useMemo(() => createSerializedRefresh(async (signal?: AbortSignal, quiet = false) => {
    if (!quiet) setConnectionState("connecting");
    try {
      const [nextReadiness, nextGateway, nextTasks] = await Promise.all([
        fetchS3Readiness(signal), fetchGatewaySettings(signal), fetchAnalysisTasks(signal),
      ]);
      setReadiness(nextReadiness);
      setGateway(nextGateway);
      setTasks(nextTasks);
      setReportSelection((current) => current.selectedTaskId
        ? current
        : selectAnalysisTask(
          current,
          nextTasks.find((task) => ["succeeded", "completed"].includes(task.status))?.task_id
            ?? nextTasks[0]?.task_id
            ?? null,
        ));
      setConnectionState("live");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setConnectionState("offline");
    }
  }), []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => disconnectAnalysisView(controller);
  }, [refresh]);

  const hasActiveTasks = tasks.some((task) => ["pending", "running", "retry_wait"].includes(task.status));
  useWorkspacePolling({
    active: pollingActive,
    intervalMs: hasActiveTasks ? 1600 : 8000,
    refresh: () => refresh(undefined, true),
  });

  const selectedTask = tasks.find((task) => task.task_id === selectedTaskId);
  const selectedTaskStatus = selectedTask?.status;
  const selectedTaskUpdatedAt = selectedTask?.updated_at;
  useEffect(() => {
    if (!selectedTaskId || !selectedTaskStatus || !["succeeded", "completed"].includes(selectedTaskStatus)) {
      setReportSelection((current) => clearAnalysisReport(current, selectedTaskId));
      return;
    }
    const requestedTaskId = selectedTaskId;
    const controller = new AbortController();
    void fetchAnalysisReport(requestedTaskId, controller.signal).then((nextReport) => {
      setReportSelection((current) => commitFetchedAnalysisReport(current, requestedTaskId, nextReport));
    }).catch((error: unknown) => {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setActionNotice({ message: error instanceof Error ? error.message : "报告读取失败", kind: "error" });
      }
    });
    return () => disconnectAnalysisView(controller);
  }, [selectedTaskId, selectedTaskStatus, selectedTaskUpdatedAt]);

  const saveGateway = useCallback(async (input: {
    models: GatewayModelSaveInput[];
    default_model_id: string;
    routing: Record<GatewayPurpose, string>;
  }) => {
    setIsSubmitting(true);
    setActionNotice({ message: "正在安全保存中转站设置…", kind: "status" });
    try {
      setGateway(await saveGatewaySettings(input));
      setActionNotice({ message: "中转站设置已保存，密钥不会在页面或接口中回显。", kind: "status" });
      await refresh(undefined, true);
      return true;
    } catch (error) {
      setActionNotice({ message: error instanceof Error ? error.message : "设置保存失败", kind: "error" });
      return false;
    } finally {
      setIsSubmitting(false);
    }
  }, [refresh]);

  const checkGateway = useCallback(async (modelId: string) => {
    setIsSubmitting(true);
    setActionNotice({ message: "正在实际连接中转站…", kind: "status" });
    try {
      const result = await testGateway(modelId);
      const scope = result.tested_modalities.includes("image") ? "图文多模态" : "文本";
      const requestTrace = result.endpoint_url ? ` 实际请求：POST ${result.endpoint_url}。` : "";
      setActionNotice({
        message: `${result.model} 的${scope}连接成功，耗时 ${result.latency_ms} 毫秒。${requestTrace}`,
        kind: "status",
      });
    } catch (error) {
      const model = gateway.models.find((item) => item.model_id === modelId);
      setActionNotice({
        message: model ? gatewayConnectionErrorMessage(error, model) : error instanceof Error ? error.message : "中转站连接失败",
        kind: "error",
      });
    } finally {
      setIsSubmitting(false);
    }
  }, [gateway.models]);

  const acceptGatewayConnection = useCallback(async (result: GatewayConnectionResult) => {
    setGateway(result.settings);
    setActionNotice({
      message: `模型已通过图文验证并保存，四类任务已自动分配。连接耗时 ${result.latency_ms} 毫秒。`,
      kind: "status",
    });
    await refresh(undefined, true);
  }, [refresh]);

  const clearActionMessage = useCallback(() => {
    setActionNotice(null);
  }, []);

  const startAnalysis = useCallback(async (input: Parameters<typeof createAnalysis>[0]) => {
    setIsSubmitting(true);
    const purposeCopy = analysisPurposeCopy[input.model_purpose];
    setActionNotice({ message: purposeCopy.creating, kind: "status" });
    try {
      const task = await createAnalysis(input);
      setSelectedTaskId(task.task_id);
      setActionNotice({ message: `${purposeCopy.queued}原视频不会上传到中转站。`, kind: "status" });
      await refresh(undefined, true);
      return true;
    } catch (error) {
      setActionNotice({ message: error instanceof Error ? error.message : "任务创建失败", kind: "error" });
      return false;
    } finally {
      setIsSubmitting(false);
    }
  }, [refresh]);

  const retry = useCallback(async (taskId: string) => {
    setIsSubmitting(true);
    setActionNotice({ message: "正在重新排队…", kind: "status" });
    try {
      await retryAnalysisTask(taskId);
      setSelectedTaskId(taskId);
      setActionNotice({ message: "失败任务已重新排队，将继续使用原来的本地素材。", kind: "status" });
      await refresh(undefined, true);
    } catch (error) {
      setActionNotice({ message: error instanceof Error ? error.message : "任务重试失败", kind: "error" });
    } finally {
      setIsSubmitting(false);
    }
  }, [refresh]);

  const cancel = useCallback(async (taskId: string) => {
    setIsSubmitting(true);
    setActionNotice({ message: "正在安全取消任务，已完成分段不会丢失…", kind: "status" });
    try {
      await cancelAnalysisTask(taskId);
      setSelectedTaskId(taskId);
      setActionNotice({ message: "任务已取消，现有检查点与诊断记录已保留。", kind: "status" });
      await refresh(undefined, true);
    } catch (error) {
      setActionNotice({ message: error instanceof Error ? error.message : "任务取消失败", kind: "error" });
    } finally {
      setIsSubmitting(false);
    }
  }, [refresh]);

  const saveConclusion = useCallback(async (text: string) => {
    const target = analysisReportMutationTarget(reportSelection);
    if (!target) {
      setActionNotice({ message: "当前报告与所选任务不一致，请重新选择任务后再保存。", kind: "error" });
      return;
    }
    setIsSubmitting(true);
    try {
      const summary = {
        ...target.report.summary,
        overall_conclusion: { ...target.report.summary.overall_conclusion, text },
      };
      const savedReport = await updateAnalysisReport(
        target.taskId,
        target.report.revision,
        { summary } as Partial<AnalysisReport>,
      );
      setReportSelection((current) => commitMutatedAnalysisReport(
        current,
        target.taskId,
        target.report,
        savedReport,
      ));
      setActionNotice({ message: "人工修订已保存，报告已回到待审核状态。", kind: "status" });
    } catch (error) {
      setActionNotice({ message: error instanceof Error ? error.message : "修订保存失败", kind: "error" });
    } finally {
      setIsSubmitting(false);
    }
  }, [reportSelection]);

  const review = useCallback(async (status: "reviewed" | "accepted", reviewer: string, note: string) => {
    const target = analysisReportMutationTarget(reportSelection);
    if (!target) {
      setActionNotice({ message: "当前报告与所选任务不一致，请重新选择任务后再审核。", kind: "error" });
      return;
    }
    setIsSubmitting(true);
    try {
      const reviewedReport = await reviewAnalysisReport(target.taskId, {
        expected_revision: target.report.revision, status, reviewer, note: note.trim() || null,
      });
      setReportSelection((current) => commitMutatedAnalysisReport(
        current,
        target.taskId,
        target.report,
        reviewedReport,
      ));
      setActionNotice({ message: status === "accepted" ? "报告已人工接受。" : "报告已完成复核。", kind: "status" });
    } catch (error) {
      setActionNotice({ message: error instanceof Error ? error.message : "审核失败", kind: "error" });
    } finally {
      setIsSubmitting(false);
    }
  }, [reportSelection]);

  return {
    readiness, gateway, tasks, selectedTaskId, report, connectionState,
    actionMessage: actionNotice?.message ?? null,
    actionMessageKind: actionNotice?.kind ?? "status",
    isSubmitting,
    refresh, setSelectedTaskId, saveGateway, checkGateway, acceptGatewayConnection, clearActionMessage, startAnalysis, retry, cancel, saveConclusion, review,
  };
}
