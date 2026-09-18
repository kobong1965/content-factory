import { useEffect, useRef } from "react";

export const UNSAVED_CHANGES_FALLBACK_LABEL = "当前内容";

type ConfirmUnsavedChanges = (message: string) => boolean;
export type DraftTransitionEffect = "preserves-draft" | "replaces-draft";

export interface UnsavedChangesRegistry {
  update(token: symbol, dirty: boolean, label: string): void;
  remove(token: symbol): void;
  hasChanges(): boolean;
  labels(): string[];
  confirmMessage(): string;
}

export function createUnsavedChangesRegistry(): UnsavedChangesRegistry {
  const entries = new Map<symbol, string>();

  return {
    update(token, dirty, label) {
      if (!dirty) {
        entries.delete(token);
        return;
      }
      entries.set(token, label.trim() || UNSAVED_CHANGES_FALLBACK_LABEL);
    },
    remove(token) {
      entries.delete(token);
    },
    hasChanges() {
      return entries.size > 0;
    },
    labels() {
      return [...new Set(entries.values())];
    },
    confirmMessage() {
      const descriptions = [...new Set(entries.values())].map((label) => `「${label}」`).join("、");
      return `${descriptions || `「${UNSAVED_CHANGES_FALLBACK_LABEL}」`}还有未保存的修改。继续离开或切换可能丢失这些修改，仍要继续吗？`;
    },
  };
}

export const unsavedChangesRegistry = createUnsavedChangesRegistry();

export function preventUnloadWhenDirty(
  event: Pick<BeforeUnloadEvent, "preventDefault" | "returnValue">,
  registry: UnsavedChangesRegistry = unsavedChangesRegistry,
): boolean {
  if (!registry.hasChanges()) return false;
  event.preventDefault();
  event.returnValue = "";
  return true;
}

export function runGuardedTransition(
  transition: () => void,
  confirm: ConfirmUnsavedChanges = (message) => window.confirm(message),
  registry: UnsavedChangesRegistry = unsavedChangesRegistry,
): boolean {
  if (registry.hasChanges() && !confirm(registry.confirmMessage())) return false;
  transition();
  return true;
}

export function runDraftTransition(
  transition: () => void,
  effect: DraftTransitionEffect,
  confirm: ConfirmUnsavedChanges = (message) => window.confirm(message),
  registry: UnsavedChangesRegistry = unsavedChangesRegistry,
): boolean {
  if (effect === "preserves-draft") {
    transition();
    return true;
  }
  return runGuardedTransition(transition, confirm, registry);
}

export function useUnsavedChanges(dirty: boolean, label: string): void {
  const token = useRef(Symbol(label));

  useEffect(() => {
    unsavedChangesRegistry.update(token.current, dirty, label);
    return () => unsavedChangesRegistry.remove(token.current);
  }, [dirty, label]);
}

export function useUnsavedChangesBeforeUnload(): void {
  useEffect(() => {
    const handleBeforeUnload = (event: BeforeUnloadEvent) => { preventUnloadWhenDirty(event); };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, []);
}

export function guardUnsavedTransition(transition: () => void): boolean {
  return runGuardedTransition(transition);
}
