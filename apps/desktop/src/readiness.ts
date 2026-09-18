import { runtimeApiBaseUrl } from './runtimeApi';
import type { S1Readiness } from "@content-factory/contracts";

export const DEFAULT_S1_READINESS: S1Readiness = {
  stage: "S1",
  schema_version: "1.0.0",
  schema_count: 5,
  engineering_ready: true,
  business_ready: false,
  accepted_videos: 0,
  required_videos: 20,
  accepted_products: 0,
  required_products: 3,
  pending_reason: "待补 20 条真实视频和 3 款真实商品",
};

export function completionPercent(completed: number, required: number): number {
  if (required <= 0) return 0;
  return Math.min(100, Math.max(0, Math.round((completed / required) * 100)));
}

export function s1StatusLabel(readiness: S1Readiness): string {
  if (readiness.business_ready) return "S1 已完整验收";
  if (readiness.engineering_ready) return "工程已通过 · 真实资料待补";
  return "工程验收未通过";
}

export async function fetchS1Readiness(signal?: AbortSignal): Promise<S1Readiness> {
  const apiBaseUrl = runtimeApiBaseUrl;
  const response = await fetch(`${apiBaseUrl}/s1/readiness`, { signal });
  if (!response.ok) throw new Error(`S1 就绪接口返回 ${response.status}`);
  return (await response.json()) as S1Readiness;
}
