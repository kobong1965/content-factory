import { describe, expect, it } from "vitest";

import {
  createSerializedRefresh,
  isWorkspacePollingEnabled,
  shouldRefreshWorkspacePolling,
  shouldScheduleWorkspacePolling,
} from "./workspacePolling";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

describe("workspace polling policy", () => {
  it("polls only while its workspace is active and the page is visible", () => {
    expect(isWorkspacePollingEnabled({ active: true, visible: true })).toBe(true);
    expect(isWorkspacePollingEnabled({ active: false, visible: true })).toBe(false);
    expect(isWorkspacePollingEnabled({ active: true, visible: false })).toBe(false);
    expect(shouldScheduleWorkspacePolling({ active: true, visible: true }, false)).toBe(false);
    expect(shouldScheduleWorkspacePolling({ active: true, visible: true }, true)).toBe(true);
  });

  it("refreshes immediately when a workspace or hidden page becomes active again", () => {
    expect(shouldRefreshWorkspacePolling(
      { active: false, visible: true },
      { active: true, visible: true },
    )).toBe(true);
    expect(shouldRefreshWorkspacePolling(
      { active: true, visible: false },
      { active: true, visible: true },
    )).toBe(true);
    expect(shouldRefreshWorkspacePolling(
      { active: true, visible: true },
      { active: true, visible: true },
    )).toBe(false);
    expect(shouldRefreshWorkspacePolling(
      { active: true, visible: true },
      { active: true, visible: false },
    )).toBe(false);
  });
});

describe("serialized refresh", () => {
  it("serializes overlapping refreshes into one trailing request", async () => {
    const firstCompletion = deferred<void>();
    const secondCompletion = deferred<void>();
    const thirdCompletion = deferred<void>();
    const completions = [firstCompletion, secondCompletion, thirdCompletion];
    let calls = 0;
    let concurrent = 0;
    let maxConcurrent = 0;
    const refresh = createSerializedRefresh(async () => {
      const completion = completions[calls]!;
      calls += 1;
      concurrent += 1;
      maxConcurrent = Math.max(maxConcurrent, concurrent);
      await completion.promise;
      concurrent -= 1;
    });

    const first = refresh();
    const overlapping = refresh();
    const alsoOverlapping = refresh();

    expect(alsoOverlapping).toBe(overlapping);
    expect(calls).toBe(1);

    firstCompletion.resolve();
    await first;
    await Promise.resolve();

    expect(calls).toBe(2);

    secondCompletion.resolve();
    await overlapping;

    const afterCompletion = refresh();
    expect(calls).toBe(3);
    thirdCompletion.resolve();
    await afterCompletion;

    expect(maxConcurrent).toBe(1);
  });
});
