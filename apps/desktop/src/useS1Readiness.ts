import { useCallback, useEffect, useState } from "react";
import type { S1Readiness } from "@content-factory/contracts";

import { DEFAULT_S1_READINESS, fetchS1Readiness } from "./readiness";

type ConnectionState = "connecting" | "live" | "offline";

export function useS1Readiness() {
  const [readiness, setReadiness] = useState<S1Readiness>(DEFAULT_S1_READINESS);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setConnectionState("connecting");
    try {
      setReadiness(await fetchS1Readiness(signal));
      setConnectionState("live");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setConnectionState("offline");
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  return { readiness, connectionState, refresh };
}
