import type { ScriptPackage, ScriptVersion } from "@content-factory/contracts";
import { formatScriptTime, scriptReviewLabels } from "./scripts";

export function shootingScriptText(productName: string, script: ScriptPackage, version: ScriptVersion): string {
  const lines = [productName, `拍摄稿：${version.name}`, `脚本修订：${script.revision} · ${scriptReviewLabels[script.review.status]}`,
    "固定直播间，一名主播，固定机位与灯光，连续长镜头。只拍选定的一版。", ""];
  for (const shot of version.shots) {
    lines.push(`${formatScriptTime(shot.start_ms)} — ${formatScriptTime(shot.end_ms)}`,
      `口播：${shot.voiceover}`, `画面：${shot.visual}`, `动作：${shot.action}`,
      `语气：${shot.delivery?.tone || "未标注"}`, `语速：${shot.delivery?.pacing || "未标注"}`,
      `重读：${shot.delivery?.emphasis || "未标注"}`, `停顿：${shot.delivery?.pause || "未标注"}`,
      `表情：${shot.performance?.expression || "未标注"}`, `目光：${shot.performance?.eye_line || "未标注"}`,
      `身体动作：${shot.performance?.body_action || shot.action}`, `商品动作：${shot.performance?.product_action || "未标注"}`, "");
  }
  lines.push("后期剪辑稿保留在软件中。同款细节仅作可选覆盖，不中断主播连续原声。");
  return lines.join("\n");
}

export function downloadShootingScript(productName: string, script: ScriptPackage, version: ScriptVersion) {
  const url = URL.createObjectURL(new Blob(["\uFEFF", shootingScriptText(productName, script, version)], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `${productName}-${version.name}-拍摄稿.txt`.replace(/[<>:"/\\|?*\x00-\x1f]/g, "_");
  document.body.append(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
