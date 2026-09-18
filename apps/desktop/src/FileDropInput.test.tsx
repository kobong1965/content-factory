import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FileDropInput, validateFiles } from "./FileDropInput";

const jpg = new File(["image"], "商品正面.JPG", { type: "image/jpeg" });
const png = new File(["detail"], "口袋细节.png", { type: "image/png" });

describe("shared file upload selection validation", () => {
  it("accepts multiple matching files without silently discarding any", () => {
    expect(validateFiles([jpg, png], "image/jpeg,image/png", true)).toBeNull();
  });

  it("rejects a multi-file drop in single-file mode with an actionable message", () => {
    expect(validateFiles([jpg, png], "image/*", false)).toEqual(expect.any(String));
    expect(validateFiles([jpg, png], "image/*", false)).toMatch(/一|1|单/);
  });

  it("accepts case-insensitive extensions even if the OS omitted MIME type", () => {
    expect(validateFiles([new File(["sheet"], "J85资料.XLSX")], ".xlsx,.csv", false)).toBeNull();
  });

  it("rejects every batch containing an unsupported file rather than dropping it silently", () => {
    const invalid = new File(["video"], "主播.mp4", { type: "video/mp4" });
    expect(validateFiles([jpg, invalid], "image/*", true)).toEqual(expect.any(String));
  });

  it("does not confuse extension substrings with supported formats", () => {
    expect(validateFiles([new File(["bad"], "商品.jpg.exe")], ".jpg,.png", true)).not.toBeNull();
  });

  it("preserves the native input's name, accept, multiple, disabled and accessible label", () => {
    const markup = renderToStaticMarkup(<FileDropInput aria-label="商品图片" name="product_images" accept="image/*" multiple disabled onChange={() => undefined} />);
    expect(markup).toMatch(/<input\b[^>]*type="file"/);
    expect(markup).toContain('name="product_images"');
    expect(markup).toContain('accept="image/*"');
    expect(markup).toContain('aria-label="商品图片"');
    expect(markup).toMatch(/<input\b[^>]*multiple/);
    expect(markup).toMatch(/<input\b[^>]*disabled/);
  });
});
