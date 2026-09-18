import { runtimeApiBaseUrl } from './runtimeApi';
import type {
  ProductListItem,
  ProductFactSuggestion,
  ProductProfile,
  ProductScriptEligibility,
  ProductStatus,
  ProductVersion,
  S4Readiness,
} from "@content-factory/contracts";

const API_BASE_URL = runtimeApiBaseUrl;

export const DEFAULT_S4_READINESS: S4Readiness = {
  stage: "S4",
  engineering_ready: false,
  contract_version: "1.1.0",
  total_products: 0,
  draft_products: 0,
  active_products: 0,
  archived_products: 0,
  script_eligible_products: 0,
  accepted_real_products: 0,
  required_real_products: 3,
  business_ready: false,
  pending_reason: "本地接口未连接",
};

export const productStatusLabels: Record<ProductStatus, string> = {
  draft: "临时商品",
  active: "正式商品",
  archived: "已归档",
};

export function lines(value: string): string[] {
  return [...new Set(value.split(/\r?\n|，|,/).map((item) => item.trim()).filter(Boolean))];
}

export function joined(values: readonly string[]): string {
  return values.join("\n");
}

export function completenessPercent(ratio: number): number {
  return Math.max(0, Math.min(100, Math.round(ratio * 100)));
}

function validationMessage(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return null;
  const first = detail[0] as { loc?: unknown[]; msg?: string } | undefined;
  if (!first?.msg) return null;
  const field = first.loc?.slice(1).join(" → ");
  return field ? `${field}：${first.msg}` : first.msg;
}

async function responseJson<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;
  let message = `本地接口返回 ${response.status}`;
  try {
    const payload = (await response.json()) as { detail?: unknown };
    message = validationMessage(payload.detail) ?? message;
  } catch {
    // Keep the stable fallback for non-JSON failures.
  }
  throw new Error(message);
}

export function fetchS4Readiness(signal?: AbortSignal): Promise<S4Readiness> {
  return fetch(`${API_BASE_URL}/s4/readiness`, { signal }).then(responseJson<S4Readiness>);
}

export function fetchProducts(query = "", status = "", signal?: AbortSignal): Promise<ProductListItem[]> {
  const parameters = new URLSearchParams();
  if (query.trim()) parameters.set("query", query.trim());
  if (status) parameters.set("status", status);
  const suffix = parameters.size ? `?${parameters}` : "";
  return fetch(`${API_BASE_URL}/s4/products${suffix}`, { signal }).then(responseJson<ProductListItem[]>);
}

export function fetchProduct(productId: string, signal?: AbortSignal): Promise<ProductProfile> {
  return fetch(`${API_BASE_URL}/s4/products/${encodeURIComponent(productId)}`, { signal }).then(responseJson<ProductProfile>);
}

export function fetchProductVersions(productId: string, signal?: AbortSignal): Promise<ProductVersion[]> {
  return fetch(`${API_BASE_URL}/s4/products/${encodeURIComponent(productId)}/versions`, { signal }).then(responseJson<ProductVersion[]>);
}

export function createProduct(input: { sku?: string; name: string; actor: string }): Promise<ProductProfile> {
  return fetch(`${API_BASE_URL}/s4/products`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  }).then(responseJson<ProductProfile>);
}

export function fetchProductScriptEligibility(
  productId: string, signal?: AbortSignal,
): Promise<ProductScriptEligibility> {
  return fetch(`${API_BASE_URL}/s4/products/${encodeURIComponent(productId)}/script-eligibility`, { signal })
    .then(responseJson<ProductScriptEligibility>);
}

export function fetchProductFactSuggestions(productId: string): Promise<{
  product_id: string;
  product_revision: number;
  model_profile_id: string;
  model: string;
  suggestions: ProductFactSuggestion[];
  saved: false;
  notice: string;
}> {
  return fetch(`${API_BASE_URL}/s4/products/${encodeURIComponent(productId)}/suggestions`, { method: "POST" })
    .then(responseJson<{
      product_id: string;
      product_revision: number;
      model_profile_id: string;
      model: string;
      suggestions: ProductFactSuggestion[];
      saved: false;
      notice: string;
    }>);
}

export function saveProduct(profile: ProductProfile, actor: string): Promise<ProductProfile> {
  const {
    revision, sku, name, sources, facts, selling_point_fact_ids, forbidden_expressions,
    unprovable_claims, brand_boundary_confirmed_by, brand_boundary_confirmed_at, shooting_constraints,
  } = profile;
  return fetch(`${API_BASE_URL}/s4/products/${encodeURIComponent(profile.product_id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      expected_revision: revision, actor, sku, name, sources, facts, selling_point_fact_ids,
      forbidden_expressions, unprovable_claims, brand_boundary_confirmed_by,
      brand_boundary_confirmed_at, shooting_constraints,
    }),
  }).then(responseJson<ProductProfile>);
}

export function changeProductStatus(
  profile: ProductProfile, status: ProductStatus, actor: string,
): Promise<ProductProfile> {
  return fetch(`${API_BASE_URL}/s4/products/${encodeURIComponent(profile.product_id)}/status`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_revision: profile.revision, status, actor }),
  }).then(responseJson<ProductProfile>);
}

export function uploadProductAsset(
  profile: ProductProfile, file: File, actor: string,
): Promise<{ profile: ProductProfile; duplicate: boolean }> {
  const form = new FormData();
  form.set("expected_revision", String(profile.revision));
  form.set("actor", actor);
  form.set("upload", file);
  return fetch(`${API_BASE_URL}/s4/products/${encodeURIComponent(profile.product_id)}/assets`, {
    method: "POST", body: form,
  }).then(responseJson<{ profile: ProductProfile; duplicate: boolean }>);
}

export function prepareConfirmedProfile(profile: ProductProfile): ProductProfile {
  const now = new Date().toISOString();
  const brandBy = profile.brand_boundary_confirmed_by?.trim() || null;
  const shootingBy = profile.shooting_constraints.confirmed_by?.trim() || null;
  return {
    ...profile,
    brand_boundary_confirmed_by: brandBy,
    brand_boundary_confirmed_at: brandBy ? profile.brand_boundary_confirmed_at ?? now : null,
    shooting_constraints: {
      ...profile.shooting_constraints,
      models: ["主播 1 人"],
      locations: ["固定直播间"],
      equipment: ["固定直播间竖屏机位（不移动）"],
      lights: ["固定直播间灯光"],
      reusable_assets_allowed: true,
      max_duration_ms: profile.shooting_constraints.max_duration_ms || 60_000,
      confirmed_by: shootingBy,
      confirmed_at: shootingBy ? profile.shooting_constraints.confirmed_at ?? now : null,
    },
  };
}
