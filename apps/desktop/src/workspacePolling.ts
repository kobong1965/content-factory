export interface WorkspacePollingState {
  active: boolean;
  visible: boolean;
}

export function isWorkspacePollingEnabled(state: WorkspacePollingState): boolean {
  return state.active && state.visible;
}

export function shouldScheduleWorkspacePolling(
  state: WorkspacePollingState,
  pollingRequired: boolean,
): boolean {
  return pollingRequired && isWorkspacePollingEnabled(state);
}

export function shouldRefreshWorkspacePolling(
  previous: WorkspacePollingState,
  current: WorkspacePollingState,
): boolean {
  return !isWorkspacePollingEnabled(previous) && isWorkspacePollingEnabled(current);
}

export function createSerializedRefresh<Args extends unknown[], Result>(
  task: (...args: Args) => Promise<Result>,
): (...args: Args) => Promise<Result> {
  let inFlight: Promise<Result> | null = null;
  let trailing: {
    args: Args;
    promise: Promise<Result>;
    resolve: (value: Result) => void;
    reject: (reason: unknown) => void;
  } | null = null;

  const start = (args: Args): Promise<Result> => {
    let request: Promise<Result>;
    try {
      request = task(...args);
    } catch (error) {
      return Promise.reject(error);
    }

    inFlight = request;
    const clear = () => {
      if (inFlight !== request) return;
      inFlight = null;
      const next = trailing;
      trailing = null;
      if (next) void start(next.args).then(next.resolve, next.reject);
    };
    void request.then(clear, clear);
    return request;
  };

  return (...args: Args) => {
    if (!inFlight) return start(args);
    if (trailing) {
      trailing.args = args;
      return trailing.promise;
    }

    let resolve!: (value: Result) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<Result>((onResolve, onReject) => {
      resolve = onResolve;
      reject = onReject;
    });
    trailing = { args, promise, resolve, reject };
    return promise;
  };
}
