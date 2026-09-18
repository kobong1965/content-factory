export const UI_SCALE_STORAGE_KEY = "content-factory.ui-scale";

export type UiScale = "comfortable" | "large";

export function normalizeUiScale(value: string | null | undefined): UiScale {
  return value === "large" ? "large" : "comfortable";
}

export function readUiScale(): UiScale {
  if (typeof window === "undefined") return "comfortable";
  return normalizeUiScale(window.localStorage.getItem(UI_SCALE_STORAGE_KEY));
}

export function persistUiScale(scale: UiScale): void {
  if (typeof document !== "undefined") {
    document.documentElement.dataset.uiScale = scale;
  }
  if (typeof window !== "undefined") {
    window.localStorage.setItem(UI_SCALE_STORAGE_KEY, scale);
  }
}
