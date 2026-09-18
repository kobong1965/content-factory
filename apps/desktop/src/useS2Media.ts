import { useCallback, useEffect, useMemo, useState } from "react";
import type { MediaTask, S2Readiness } from "@content-factory/contracts";

import {
  DEFAULT_S2_READINESS,
  fetchMediaTasks,
  fetchS2Readiness,
  submitDouyinLink,
  uploadMedia,
} from "./media";
import { useWorkspacePolling } from "./useWorkspacePolling";
import { createSerializedRefresh } from "./workspacePolling";

export type ConnectionState = "connecting" | "live" | "offline";

export function useS2Media(pollingActive = true) {
  const [readiness, setReadiness] = useState<S2Readiness>(DEFAULT_S2_READINESS);
  const [tasks, setTasks] = useState<MediaTask[]>([]);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const refresh = useMemo(() => createSerializedRefresh(async (signal?: AbortSignal, quiet = false) => {
    if (!quiet) setConnectionState("connecting");
    try {
      const [nextReadiness, nextTasks] = await Promise.all([
        fetchS2Readiness(signal),
        fetchMediaTasks(signal),
      ]);
      setReadiness(nextReadiness);
      setTasks(nextTasks);
      setConnectionState("live");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setConnectionState("offline");
    }
  }), []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  const hasActiveTasks = tasks.some((task) => ["pending", "running", "retry_wait"].includes(task.status));
  useWorkspacePolling({
    active: pollingActive,
    intervalMs: hasActiveTasks ? 1500 : 8000,
    refresh: () => refresh(undefined, true),
  });

  const importFile = useCallback(async (file: File) => {
    setIsSubmitting(true);
    setActionMessage(`正在导入“${file.name}”…`);
    try {
      await uploadMedia(file);
      setActionMessage("视频已进入本地处理队列。原片不会上传到云端。");
      await refresh(undefined, true);
      return true;
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "导入失败，请重试");
      return false;
    } finally {
      setIsSubmitting(false);
    }
  }, [refresh]);

  const checkLink = useCallback(async (url: string) => {
    setIsSubmitting(true);
    setActionMessage("正在检查链接…");
    try {
      setActionMessage(await submitDouyinLink(url));
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "链接检查失败");
    } finally {
      setIsSubmitting(false);
    }
  }, []);

  return { readiness, tasks, connectionState, actionMessage, isSubmitting, refresh, importFile, checkLink };
}
