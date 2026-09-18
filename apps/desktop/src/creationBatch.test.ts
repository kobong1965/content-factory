import { describe, expect, it } from "vitest";
import { submitSkillBatch } from "./creationBatch";

describe("independent Skill generation", () => {
  it("submits each unique Skill separately and preserves failed selections", async () => {
    const submitted: string[] = [];
    const result = await submitSkillBatch(["a", "b", "a", "c"], async (id) => {
      submitted.push(id);
      return id !== "b";
    });
    expect(submitted).toEqual(["a", "b", "c"]);
    expect(result).toEqual({ succeeded: ["a", "c"], failed: ["b"] });
  });
  it("retains uncertain submissions without automatically resending them", async () => {
    const result = await submitSkillBatch(["a", "b"], async (id) => {
      if (id === "a") throw new Error("connection interrupted");
      return true;
    });
    expect(result.failed).toEqual(["a"]);
    expect(result.succeeded).toEqual(["b"]);
  });
});
