import { describe, expect, it } from "vitest";

import { UI_SCALE_STORAGE_KEY, normalizeUiScale } from "./uiPreferences";

describe("desktop reading scale preference", () => {
  it("defaults unknown persisted values to the approved comfortable scale", () => {
    expect(normalizeUiScale(null)).toBe("comfortable");
    expect(normalizeUiScale("compact")).toBe("comfortable");
  });

  it("keeps the approved large reading scale and a stable storage key", () => {
    expect(normalizeUiScale("large")).toBe("large");
    expect(UI_SCALE_STORAGE_KEY).toBe("content-factory.ui-scale");
  });
});
