import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { EditProject, RenderOutput } from "@content-factory/contracts";

import {
  DEFAULT_S7_READINESS, createEditProject, fetchEditAudio, fetchEditProjects, fetchEligibleScripts,
  fetchRenderOutputs, fetchRenderTasks, fetchS7Readiness, retryProjectRender, reviewRenderOutput,
  saveEditProject, startProjectRender, uploadEditAudio,
} from "./editing";
import { useWorkspacePolling } from "./useWorkspacePolling";
import { createSerializedRefresh } from "./workspacePolling";

export function useS7Editing(pollingActive = true) {
  const [readiness, setReadiness] = useState(DEFAULT_S7_READINESS);
  const [eligibleScripts, setEligibleScripts] = useState<Awaited<ReturnType<typeof fetchEligibleScripts>>>([]);
  const [projects, setProjects] = useState<Awaited<ReturnType<typeof fetchEditProjects>>>([]);
  const [tasks, setTasks] = useState<Awaited<ReturnType<typeof fetchRenderTasks>>>([]);
  const [outputs, setOutputs] = useState<Awaited<ReturnType<typeof fetchRenderOutputs>>>([]);
  const [audio, setAudio] = useState<Awaited<ReturnType<typeof fetchEditAudio>>>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [connectionState, setConnectionState] = useState<"connecting" | "live" | "offline">("connecting");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const refreshFailedRef = useRef(false);

  const refresh = useMemo(() => createSerializedRefresh(async (signal?: AbortSignal) => {
    setConnectionState("connecting");
    try {
      const values = await Promise.all([
        fetchS7Readiness(signal), fetchEligibleScripts(signal), fetchEditProjects(signal),
        fetchRenderTasks(signal), fetchRenderOutputs(signal), fetchEditAudio(signal),
      ]);
      setReadiness(values[0]); setEligibleScripts(values[1]); setProjects(values[2]);
      setTasks(values[3]); setOutputs(values[4]); setAudio(values[5]); setConnectionState("live");
      setSelectedId((current) => current ?? values[2][0]?.project_id ?? null);
      if (refreshFailedRef.current) {
        refreshFailedRef.current = false;
        setActionMessage(null);
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      refreshFailedRef.current = true;
      setConnectionState("offline");
      setActionMessage(error instanceof Error ? error.message : "剪辑工作台读取失败");
    }
  }), []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  const hasActiveTasks = tasks.some((item) => ["pending", "running", "retry_wait"].includes(item.status));
  useWorkspacePolling({ active: pollingActive, intervalMs: 1500, pollingRequired: hasActiveTasks, refresh });

  const perform = useCallback(async <T,>(action: () => Promise<T>, success: string): Promise<T | null> => {
    setIsSubmitting(true);
    try { const value = await action(); await refresh(); setActionMessage(success); return value; }
    catch (error) { setActionMessage(error instanceof Error ? error.message : "操作失败"); return null; }
    finally { setIsSubmitting(false); }
  }, [refresh]);

  const create = (scriptId: string, actor: string) => perform(async () => {
    const result = await createEditProject(scriptId, actor); setSelectedId(result.project.project_id); return result;
  }, "剪辑工程已建立，当前拍摄版已排好初始时间线和字幕。");
  const save = (project: EditProject, actor: string) => perform(async () => {
    const result = await saveEditProject(project, actor); setSelectedId(result.project_id); return result;
  }, "剪辑调整已保存为新版本。");
  const render = (project: EditProject, variantId: string) => perform(
    () => startProjectRender(project, variantId), "已进入本机渲染队列，最多同时处理两条。",
  );
  const retry = (taskId: string) => perform(() => retryProjectRender(taskId), "失败任务已重新排队。");
  const review = (outputId: string, decision: "approved" | "rejected", reviewer: string, note: string) => perform(
    () => reviewRenderOutput(outputId, decision, reviewer, note), decision === "approved" ? "成片已通过。" : "成片已驳回，可修改工程后重新渲染。",
  );
  const uploadAudio = (input: Parameters<typeof uploadEditAudio>[0]) => perform(
    () => uploadEditAudio(input), "音频已复制到本机托管目录。",
  );

  const project = useMemo(() => projects.find((item) => item.project_id === selectedId) ?? null, [projects, selectedId]);
  const projectOutputs = useMemo(() => outputs.filter((item) => item.project_id === selectedId), [outputs, selectedId]);
  const projectTasks = useMemo(() => tasks.filter((item) => item.project_id === selectedId), [tasks, selectedId]);
  const latestOutput = (variantId: string): RenderOutput | undefined => projectOutputs.find((item) => item.variant_id === variantId);

  return {
    readiness, eligibleScripts, projects, tasks: projectTasks, outputs: projectOutputs, audio, project,
    selectedId, connectionState, isSubmitting, actionMessage,
    refresh, selectProject: setSelectedId, create, save, render, retry, review, uploadAudio, latestOutput,
  };
}
