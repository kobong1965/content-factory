import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ScriptContentGoal,
  ScriptPackage,
  ScriptRevision,
  ScriptSummary,
  ScriptTask,
  S5Product,
} from "@content-factory/contracts";

import { createLatestRequestGuard, type LatestRequestCheck } from "./latestRequestGuard";
import {
  DEFAULT_S5_READINESS,
  createScriptGeneration,
  eligibleScriptSkills,
  fetchS5Products,
  fetchS5Readiness,
  fetchS5Templates,
  fetchScript,
  fetchScriptContext,
  fetchScriptRevisions,
  fetchScripts,
  fetchScriptTasks,
  retryScriptTask,
  reviewScript,
  saveScript,
  type ScriptContext,
  type ScriptSkillSummary,
} from "./scripts";
import { useWorkspacePolling } from "./useWorkspacePolling";
import { createSerializedRefresh } from "./workspacePolling";

export function useS5Scripts(pollingActive = true) {
  const [readiness, setReadiness] = useState(DEFAULT_S5_READINESS);
  const [templates, setTemplates] = useState<ScriptSkillSummary[]>([]);
  const [products, setProducts] = useState<S5Product[]>([]);
  const [tasks, setTasks] = useState<ScriptTask[]>([]);
  const [scripts, setScripts] = useState<ScriptSummary[]>([]);
  const [script, setScript] = useState<ScriptPackage | null>(null);
  const [context, setContext] = useState<ScriptContext | null>(null);
  const [revisions, setRevisions] = useState<ScriptRevision[]>([]);
  const [connectionState, setConnectionState] = useState<"connecting" | "live" | "offline">("connecting");
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionMessageKind, setActionMessageKind] = useState<"status" | "error">("status");
  const selectionRequests = useRef(createLatestRequestGuard<string>());

  const refresh = useMemo(() => createSerializedRefresh(async (signal?: AbortSignal) => {
    setConnectionState("connecting");
    try {
      const [nextReadiness, nextTemplates, nextProducts, nextTasks, nextScripts] = await Promise.all([
        fetchS5Readiness(signal), fetchS5Templates(signal), fetchS5Products(signal),
        fetchScriptTasks(signal), fetchScripts(signal),
      ]);
      setReadiness(nextReadiness);
      setTemplates(eligibleScriptSkills(nextTemplates));
      setProducts(nextProducts);
      setTasks(nextTasks);
      setScripts(nextScripts);
      setConnectionState("live");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setConnectionState("offline");
      setActionMessage(error instanceof Error ? error.message : "脚本工作台读取失败");
      setActionMessageKind("error");
    } finally {
      setIsLoading(false);
    }
  }), []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  const hasActiveTasks = tasks.some((task) => ["pending", "running", "retry_wait"].includes(task.status));
  useWorkspacePolling({ active: pollingActive, intervalMs: 1800, pollingRequired: hasActiveTasks, refresh });

  const select = useCallback(async (scriptId: string) => {
    const isCurrentSelection = selectionRequests.current.begin(scriptId);
    setIsLoading(true);
    try {
      const [nextScript, nextContext, nextRevisions] = await Promise.all([
        fetchScript(scriptId), fetchScriptContext(scriptId), fetchScriptRevisions(scriptId),
      ]);
      if (!isCurrentSelection()) return;
      setScript(nextScript);
      setContext(nextContext);
      setRevisions(nextRevisions);
      setActionMessage(null);
    } catch (error) {
      if (!isCurrentSelection()) return;
      setActionMessage(error instanceof Error ? error.message : "脚本读取失败");
      setActionMessageKind("error");
    } finally {
      if (isCurrentSelection()) setIsLoading(false);
    }
  }, []);

  const reloadSelected = useCallback(async (nextScript: ScriptPackage, isCurrentSelection: LatestRequestCheck) => {
    if (isCurrentSelection()) setScript(nextScript);
    const revisionsResult = await Promise.allSettled([
      fetchScriptRevisions(nextScript.script_id),
      refresh(),
    ]);
    if (isCurrentSelection() && revisionsResult[0].status === "fulfilled") setRevisions(revisionsResult[0].value);
  }, [refresh]);

  const generate = useCallback(async (input: {
    skill_id: string;
    product_id: string;
    content_goal: ScriptContentGoal;
    target_audience: string;
    version_count: 3 | 4 | 5;
  }) => {
    setIsSubmitting(true);
    try {
      await createScriptGeneration(input);
      await refresh();
      setActionMessage("脚本任务已建立。生成完成后会出现在下方脚本列表中。");
      setActionMessageKind("status");
      return true;
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "脚本任务建立失败");
      setActionMessageKind("error");
      return false;
    } finally {
      setIsSubmitting(false);
    }
  }, [refresh]);

  const retry = useCallback(async (taskId: string) => {
    setIsSubmitting(true);
    try {
      await retryScriptTask(taskId);
      await refresh();
      setActionMessage("失败任务已重新排队。");
      setActionMessageKind("status");
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "任务重试失败");
      setActionMessageKind("error");
    } finally {
      setIsSubmitting(false);
    }
  }, [refresh]);

  const save = useCallback(async (draft: ScriptPackage, actor: string) => {
    const belongsToSelection = selectionRequests.current.capture(draft.script_id);
    if (!belongsToSelection()) return false;
    const isCurrentSelection = selectionRequests.current.begin(draft.script_id);
    setIsSubmitting(true);
    try {
      const saved = await saveScript(draft, actor);
      await reloadSelected(saved, isCurrentSelection);
      if (isCurrentSelection()) setActionMessage(`人工修改已保存为第 ${saved.revision} 版，脚本已回到待审核。`);
      if (isCurrentSelection()) setActionMessageKind("status");
      return true;
    } catch (error) {
      if (isCurrentSelection()) setActionMessage(error instanceof Error ? error.message : "脚本保存失败");
      if (isCurrentSelection()) setActionMessageKind("error");
      return false;
    } finally {
      setIsSubmitting(false);
    }
  }, [reloadSelected]);

  const review = useCallback(async (status: "approved" | "rejected", reviewer: string, note: string) => {
    if (!script) return false;
    const belongsToSelection = selectionRequests.current.capture(script.script_id);
    if (!belongsToSelection()) return false;
    const isCurrentSelection = selectionRequests.current.begin(script.script_id);
    setIsSubmitting(true);
    try {
      const reviewed = await reviewScript(script, status, reviewer, note);
      await reloadSelected(reviewed, isCurrentSelection);
      if (isCurrentSelection()) setActionMessage(status === "approved" ? "脚本已批准为可拍摄。" : "脚本已驳回，修改后可以重新提交审核。");
      if (isCurrentSelection()) setActionMessageKind("status");
      return true;
    } catch (error) {
      if (isCurrentSelection()) setActionMessage(error instanceof Error ? error.message : "脚本审核失败");
      if (isCurrentSelection()) setActionMessageKind("error");
      return false;
    } finally {
      setIsSubmitting(false);
    }
  }, [reloadSelected, script]);

  return {
    readiness, templates, products, tasks, scripts, script, context, revisions,
    connectionState, isLoading, isSubmitting, actionMessage, actionMessageKind,
    refresh, select, generate, retry, save, review,
  };
}
