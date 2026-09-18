import { useEffect, useRef, useState } from "react";

import {
  isWorkspacePollingEnabled,
  shouldRefreshWorkspacePolling,
  shouldScheduleWorkspacePolling,
  type WorkspacePollingState,
} from "./workspacePolling";

interface WorkspacePollingOptions {
  active: boolean;
  intervalMs: number;
  pollingRequired?: boolean;
  refresh: (signal: AbortSignal) => void | Promise<unknown>;
}

export function useWorkspacePolling({
  active,
  intervalMs,
  pollingRequired = true,
  refresh,
}: WorkspacePollingOptions): void {
  const [visible, setVisible] = useState(() => document.visibilityState === "visible");
  const previousState = useRef<WorkspacePollingState>({ active, visible });
  const pollingController = useRef<AbortController | null>(null);
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  useEffect(() => {
    const handleVisibilityChange = () => {
      const nextVisible = document.visibilityState === "visible";
      if (!nextVisible) pollingController.current?.abort();
      setVisible(nextVisible);
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, []);

  useEffect(() => {
    const current = { active, visible };
    const previous = previousState.current;
    previousState.current = current;

    if (!isWorkspacePollingEnabled(current)) {
      pollingController.current?.abort();
      pollingController.current = null;
      return;
    }

    const controller = new AbortController();
    pollingController.current = controller;
    if (shouldRefreshWorkspacePolling(previous, current)) void refreshRef.current(controller.signal);
    return () => {
      controller.abort();
      if (pollingController.current === controller) pollingController.current = null;
    };
  }, [active, visible]);

  useEffect(() => {
    if (!shouldScheduleWorkspacePolling({ active, visible }, pollingRequired)) return;
    const timer = window.setInterval(() => {
      const signal = pollingController.current?.signal;
      if (signal && !signal.aborted) void refreshRef.current(signal);
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [active, intervalMs, pollingRequired, visible]);
}
