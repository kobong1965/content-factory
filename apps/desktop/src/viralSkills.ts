import { runtimeApiBaseUrl } from './runtimeApi';
import type {
  ViralSkillCandidate,
  ViralSkillCandidateSummary,
  ViralSkillStatus,
  ViralSkillView,
} from "@content-factory/contracts";

const API_BASE_URL = runtimeApiBaseUrl;

export type SkillCandidateStatusFilter = "all" | "pending" | "reusable" | "avoid" | "disabled" | "update";
export type SkillCandidateCommonalityFilter = "all" | "common" | "single";
export type SkillCandidateFilter = Readonly<{
  query: string;
  status: SkillCandidateStatusFilter;
  commonality: SkillCandidateCommonalityFilter;
}>;

export type SkillCandidateApprovalInput = Readonly<{
  expected_candidate_revision: number;
  expected_skill_revision: number | null;
  reviewer: string;
  reuse_mode: "reuse" | "avoid";
  name: string;
  mechanism: string;
  note: string | null;
}>;

export type SkillStatusInput = Readonly<{
  expected_revision: number;
  status: ViralSkillStatus;
  reviewer: string;
  note: string | null;
}>;

export class ViralSkillApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ViralSkillApiError";
    this.status = status;
  }
}

export type SkillSelectionGuard = Readonly<{
  current: () => string;
  select: (candidateId: string) => void;
  isCurrent: (candidateId: string) => boolean;
}>;

export function createSkillSelectionGuard(initialCandidateId = ""): SkillSelectionGuard {
  let currentCandidateId = initialCandidateId;
  return {
    current: () => currentCandidateId,
    select: (candidateId) => { currentCandidateId = candidateId; },
    isCurrent: (candidateId) => currentCandidateId === candidateId,
  };
}

export function isViralSkillConflict(error: unknown): error is ViralSkillApiError {
  return error instanceof ViralSkillApiError && error.status === 409;
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
    // Keep a stable message for a non-JSON local service response.
  }
  throw new ViralSkillApiError(response.status, message);
}

function candidateMatchesStatus(candidate: ViralSkillCandidateSummary, status: SkillCandidateStatusFilter): boolean {
  if (status === "all") return true;
  if (status === "pending") return candidate.linked_skill === null;
  if (status === "update") return candidate.linked_skill?.update_available === true;
  if (status === "disabled") return candidate.linked_skill?.status === "disabled";
  if (status === "reusable") {
    return candidate.linked_skill?.eligibility.s5_eligible === true;
  }
  return candidate.linked_skill?.status === "approved" && candidate.linked_skill.reuse_mode === "avoid";
}

export function filterSkillCandidates(
  candidates: readonly ViralSkillCandidateSummary[],
  filter: SkillCandidateFilter,
): ViralSkillCandidateSummary[] {
  const query = filter.query.trim().toLocaleLowerCase();
  return candidates.filter((candidate) => {
    if (!candidateMatchesStatus(candidate, filter.status)) return false;
    if (filter.commonality === "common" && !candidate.is_common) return false;
    if (filter.commonality === "single" && candidate.is_common) return false;
    if (!query) return true;
    const searchable = [
      candidate.suggested_name,
      candidate.suggested_mechanism,
      ...candidate.steps.map((step) => step.description),
      ...candidate.necessary_conditions,
      ...candidate.failure_signals,
    ].join(" ").toLocaleLowerCase();
    return searchable.includes(query);
  });
}

export function summarizeSkillLibrary(
  candidates: readonly ViralSkillCandidateSummary[],
  skills: readonly ViralSkillView[],
): Readonly<{ pending: number; reusable: number; common: number }> {
  return {
    pending: candidates.filter((item) => item.linked_skill === null).length,
    reusable: skills.filter((item) => item.eligibility.s5_eligible).length,
    common: candidates.filter((item) => item.is_common).length,
  };
}

export function formatSkillTimecode(milliseconds: number | null): string {
  if (milliseconds === null) return "未定位";
  const seconds = Math.max(0, milliseconds) / 1000;
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;
}

export function fetchViralSkillCandidates(signal?: AbortSignal): Promise<ViralSkillCandidateSummary[]> {
  return fetch(`${API_BASE_URL}/s3/skill-candidates?include_inactive_linked=true`, { signal })
    .then(responseJson<ViralSkillCandidateSummary[]>);
}

export function fetchViralSkillCandidate(candidateId: string, signal?: AbortSignal): Promise<ViralSkillCandidate> {
  return fetch(`${API_BASE_URL}/s3/skill-candidates/${encodeURIComponent(candidateId)}`, { signal })
    .then(responseJson<ViralSkillCandidate>);
}

export function fetchViralSkills(signal?: AbortSignal): Promise<ViralSkillView[]> {
  return fetch(`${API_BASE_URL}/s3/skills`, { signal }).then(responseJson<ViralSkillView[]>);
}

export function fetchViralSkill(skillId: string, signal?: AbortSignal): Promise<ViralSkillView> {
  return fetch(`${API_BASE_URL}/s3/skills/${encodeURIComponent(skillId)}`, { signal }).then(responseJson<ViralSkillView>);
}

export function refreshViralSkillCandidates(): Promise<Record<string, number>> {
  return fetch(`${API_BASE_URL}/s3/skill-candidates/refresh`, { method: "POST" })
    .then(responseJson<Record<string, number>>);
}

export function approveViralSkillCandidate(
  candidateId: string,
  input: SkillCandidateApprovalInput,
): Promise<ViralSkillView> {
  return fetch(`${API_BASE_URL}/s3/skill-candidates/${encodeURIComponent(candidateId)}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }).then(responseJson<ViralSkillView>);
}

export function setViralSkillStatus(skillId: string, input: SkillStatusInput): Promise<ViralSkillView> {
  return fetch(`${API_BASE_URL}/s3/skills/${encodeURIComponent(skillId)}/status`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }).then(responseJson<ViralSkillView>);
}
