import { describe, expect, it, vi } from "vitest";

import {
  createUnsavedChangesRegistry,
  preventUnloadWhenDirty,
  runDraftTransition,
  runGuardedTransition,
  UNSAVED_CHANGES_FALLBACK_LABEL,
} from "./unsavedChanges";

describe("unsaved changes navigation guard", () => {
  it("取消离开时不执行切换，并保留当前未保存记录", () => {
    const registry = createUnsavedChangesRegistry();
    const editor = Symbol("商品资料");
    const transition = vi.fn();
    const confirm = vi.fn(() => false);

    registry.update(editor, true, "商品资料");

    expect(runGuardedTransition(transition, confirm, registry)).toBe(false);
    expect(transition).not.toHaveBeenCalled();
    expect(registry.hasChanges()).toBe(true);
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("商品资料"));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("未保存"));
  });

  it("确认离开时执行一次切换", () => {
    const registry = createUnsavedChangesRegistry();
    const transition = vi.fn();
    const confirm = vi.fn(() => true);

    registry.update(Symbol("剪辑工程"), true, "剪辑工程");

    expect(runGuardedTransition(transition, confirm, registry)).toBe(true);
    expect(transition).toHaveBeenCalledOnce();
    expect(confirm).toHaveBeenCalledOnce();
  });

  it("成功保存后变为清洁状态，后续切换不再提示", () => {
    const registry = createUnsavedChangesRegistry();
    const editor = Symbol("脚本修改");
    const transition = vi.fn();
    const confirm = vi.fn(() => false);

    registry.update(editor, true, "脚本修改");
    registry.update(editor, false, "脚本修改");

    expect(runGuardedTransition(transition, confirm, registry)).toBe(true);
    expect(transition).toHaveBeenCalledOnce();
    expect(confirm).not.toHaveBeenCalled();
  });

  it("同一份内存草稿内切换视图时不询问丢弃确认", () => {
    const registry = createUnsavedChangesRegistry();
    const transition = vi.fn();
    const confirm = vi.fn(() => false);

    registry.update(Symbol("脚本修改"), true, "脚本修改");

    expect(runDraftTransition(transition, "preserves-draft", confirm, registry)).toBe(true);
    expect(transition).toHaveBeenCalledOnce();
    expect(confirm).not.toHaveBeenCalled();
    expect(registry.hasChanges()).toBe(true);
  });

  it("替换当前实体草稿时仍保留未保存确认", () => {
    const registry = createUnsavedChangesRegistry();
    const transition = vi.fn();
    const confirm = vi.fn(() => false);

    registry.update(Symbol("剪辑工程"), true, "剪辑工程");

    expect(runDraftTransition(transition, "replaces-draft", confirm, registry)).toBe(false);
    expect(transition).not.toHaveBeenCalled();
    expect(confirm).toHaveBeenCalledOnce();
  });

  it("任一未保存编辑器都会触发关闭窗口保护", () => {
    const registry = createUnsavedChangesRegistry();
    const analysis = Symbol("深度分析结论");
    const cleanEvent = { preventDefault: vi.fn(), returnValue: "unchanged" };
    const dirtyEvent = { preventDefault: vi.fn(), returnValue: "unchanged" };

    expect(registry.hasChanges()).toBe(false);
    expect(preventUnloadWhenDirty(cleanEvent, registry)).toBe(false);
    expect(cleanEvent.preventDefault).not.toHaveBeenCalled();
    registry.update(analysis, true, "深度分析结论");
    expect(registry.hasChanges()).toBe(true);
    expect(preventUnloadWhenDirty(dirtyEvent, registry)).toBe(true);
    expect(dirtyEvent.preventDefault).toHaveBeenCalledOnce();
    expect(dirtyEvent.returnValue).toBe("");
    registry.remove(analysis);
    expect(registry.hasChanges()).toBe(false);
  });

  it("多个未保存来源使用去重后的中文名称，空名称有明确兜底", () => {
    const registry = createUnsavedChangesRegistry();

    registry.update(Symbol("one"), true, "素材标签");
    registry.update(Symbol("two"), true, "素材标签");
    registry.update(Symbol("three"), true, "  ");

    expect(registry.labels()).toEqual(["素材标签", UNSAVED_CHANGES_FALLBACK_LABEL]);
    expect(registry.confirmMessage()).toContain(`「素材标签」、「${UNSAVED_CHANGES_FALLBACK_LABEL}」`);
  });
});
