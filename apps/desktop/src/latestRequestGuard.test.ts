import { describe, expect, it } from "vitest";

import { createLatestRequestGuard } from "./latestRequestGuard";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

describe("latest request guard", () => {
  it("prevents an older response from replacing a newer selection", async () => {
    const guard = createLatestRequestGuard<string>();
    const older = deferred<string>();
    const newer = deferred<string>();
    const committed: string[] = [];

    const load = async (selection: string, request: Promise<string>) => {
      const isLatest = guard.begin(selection);
      const value = await request;
      if (isLatest()) committed.push(value);
    };

    const olderLoad = load("older", older.promise);
    const newerLoad = load("newer", newer.promise);
    newer.resolve("newer selection");
    await newerLoad;
    older.resolve("older selection");
    await olderLoad;

    expect(committed).toEqual(["newer selection"]);
  });

  it("invalidates a mutation when another object is selected", () => {
    const guard = createLatestRequestGuard<string>();
    guard.begin("product-a");
    const belongsToSelectedProduct = guard.capture("product-a");

    expect(belongsToSelectedProduct()).toBe(true);

    guard.begin("product-b");

    expect(belongsToSelectedProduct()).toBe(false);
    expect(guard.capture("product-a")()).toBe(false);
    expect(guard.capture("product-b")()).toBe(true);
  });
});
