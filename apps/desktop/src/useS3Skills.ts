import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ViralSkillCandidate, ViralSkillCandidateSummary, ViralSkillView } from "@content-factory/contracts";

import {
  approveViralSkillCandidate,
  createSkillSelectionGuard,
  fetchViralSkill,
  fetchViralSkillCandidate,
  fetchViralSkillCandidates,
  fetchViralSkills,
  isViralSkillConflict,
  refreshViralSkillCandidates,
  setViralSkillStatus,
  summarizeSkillLibrary,
  type SkillCandidateApprovalInput,
  type SkillStatusInput,
} from "./viralSkills";

type SkillDetailState = "idle" | "loading" | "ready" | "error";
type SkillDetailSnapshot = Readonly<{
  candidate: ViralSkillCandidate;
  skill: ViralSkillView | null;
}>;

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function useS3Skills() {
  const [candidates, setCandidates] = useState<ViralSkillCandidateSummary[]>([]);
  const [skills, setSkills] = useState<ViralSkillView[]>([]);
  const [selectedCandidateId, setSelectedCandidateIdState] = useState("");
  const [candidate, setCandidate] = useState<ViralSkillCandidate | null>(null);
  const [skill, setSkill] = useState<ViralSkillView | null>(null);
  const [detailState, setDetailState] = useState<SkillDetailState>("idle");
  const [detailError, setDetailError] = useState<string | null>(null);
  const [conflictMessage, setConflictMessage] = useState<string | null>(null);
  const [isReloadingDetail, setIsReloadingDetail] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionMessageKind, setActionMessageKind] = useState<"status" | "error">("status");
  const selection = useRef(createSkillSelectionGuard());
  const submissionLocked = useRef(false);

  const selectCandidate = useCallback((candidateId: string) => {
    if (submissionLocked.current) return;
    if (selection.current.isCurrent(candidateId)) return;
    selection.current.select(candidateId);
    setSelectedCandidateIdState(candidateId);
    setConflictMessage(null);
    setDetailError(null);
    setActionMessage(null);
  }, []);

  const fetchDetailSnapshot = useCallback(async (candidateId: string, signal?: AbortSignal): Promise<SkillDetailSnapshot> => {
    const nextCandidate = await fetchViralSkillCandidate(candidateId, signal);
    const nextSkill = nextCandidate.linked_skill
      ? await fetchViralSkill(nextCandidate.linked_skill.skill_id, signal)
      : null;
    return { candidate: nextCandidate, skill: nextSkill };
  }, []);

  const commitDetailSnapshot = useCallback((candidateId: string, snapshot: SkillDetailSnapshot): boolean => {
    if (!selection.current.isCurrent(candidateId)) return false;
    // Candidate and linked formal Skill are committed atomically. A failed
    // linked-Skill request must never make an existing Skill look unapproved.
    setCandidate(snapshot.candidate);
    setSkill(snapshot.skill);
    setDetailState("ready");
    setDetailError(null);
    return true;
  }, []);

  const loadLists = useCallback(async (signal?: AbortSignal) => {
    const [nextCandidates, nextSkills] = await Promise.all([
      fetchViralSkillCandidates(signal),
      fetchViralSkills(signal),
    ]);
    setCandidates(nextCandidates);
    setSkills(nextSkills);
    const current = selection.current.current();
    const nextSelection = current && nextCandidates.some((item) => item.candidate_id === current)
      ? current
      : nextCandidates[0]?.candidate_id ?? "";
    if (nextSelection !== current) {
      selection.current.select(nextSelection);
      setSelectedCandidateIdState(nextSelection);
    }
    return nextSelection;
  }, []);

  const reloadSelectedDetail = useCallback(async () => {
    const targetCandidateId = selection.current.current();
    if (!targetCandidateId) return false;
    setIsReloadingDetail(true);
    setDetailError(null);
    try {
      const snapshot = await fetchDetailSnapshot(targetCandidateId);
      if (!commitDetailSnapshot(targetCandidateId, snapshot)) return false;
      setConflictMessage(null);
      setActionMessage("已载入最新证据与修订；你刚才填写的名称、机制和备注已保留，可核对后重新提交。");
      setActionMessageKind("status");
      return true;
    } catch (error) {
      if (!selection.current.isCurrent(targetCandidateId)) return false;
      const message = errorMessage(error, "Skill 最新证据读取失败");
      setDetailError(message);
      setActionMessage(message);
      setActionMessageKind("error");
      return false;
    } finally {
      setIsReloadingDetail(false);
    }
  }, [commitDetailSnapshot, fetchDetailSnapshot]);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const selectedAfterRefresh = await loadLists();
      if (selectedAfterRefresh) {
        const snapshot = await fetchDetailSnapshot(selectedAfterRefresh);
        commitDetailSnapshot(selectedAfterRefresh, snapshot);
      }
      setActionMessage(null);
    } catch (error) {
      const message = errorMessage(error, "爆点 Skill 库读取失败");
      setActionMessage(message);
      setActionMessageKind("error");
    } finally {
      setIsLoading(false);
    }
  }, [commitDetailSnapshot, fetchDetailSnapshot, loadLists]);

  useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true);
    void loadLists(controller.signal)
      .catch((error: unknown) => {
        if (isAbortError(error)) return;
        setActionMessage(errorMessage(error, "爆点 Skill 库读取失败"));
        setActionMessageKind("error");
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });
    return () => controller.abort();
  }, [loadLists]);

  useEffect(() => {
    if (!selectedCandidateId) {
      setCandidate(null);
      setSkill(null);
      setDetailState("idle");
      setDetailError(null);
      return;
    }
    const controller = new AbortController();
    setCandidate(null);
    setSkill(null);
    setDetailState("loading");
    setDetailError(null);
    setConflictMessage(null);
    void fetchDetailSnapshot(selectedCandidateId, controller.signal)
      .then((snapshot) => {
        if (!controller.signal.aborted) commitDetailSnapshot(selectedCandidateId, snapshot);
      })
      .catch((error: unknown) => {
        if (isAbortError(error) || !selection.current.isCurrent(selectedCandidateId)) return;
        const message = errorMessage(error, "Skill 证据读取失败");
        setCandidate(null);
        setSkill(null);
        setDetailState("error");
        setDetailError(message);
        setActionMessage(message);
        setActionMessageKind("error");
      });
    return () => controller.abort();
  }, [commitDetailSnapshot, fetchDetailSnapshot, selectedCandidateId]);

  const reconcile = useCallback(async () => {
    if (submissionLocked.current) return;
    submissionLocked.current = true;
    setIsSubmitting(true);
    try {
      const result = await refreshViralSkillCandidates();
      const selectedAfterRefresh = await loadLists();
      if (selectedAfterRefresh) {
        const snapshot = await fetchDetailSnapshot(selectedAfterRefresh);
        commitDetailSnapshot(selectedAfterRefresh, snapshot);
      }
      setConflictMessage(null);
      setActionMessage(`已重新核对所有已接受报告：${result.candidate_count ?? candidates.length} 个候选。`);
      setActionMessageKind("status");
    } catch (error) {
      setActionMessage(errorMessage(error, "Skill 候选刷新失败"));
      setActionMessageKind("error");
    } finally {
      submissionLocked.current = false;
      setIsSubmitting(false);
    }
  }, [candidates.length, commitDetailSnapshot, fetchDetailSnapshot, loadLists]);

  const approve = useCallback(async (input: SkillCandidateApprovalInput) => {
    const targetCandidateId = selection.current.current();
    if (!targetCandidateId || submissionLocked.current) return false;
    submissionLocked.current = true;
    setIsSubmitting(true);
    try {
      await approveViralSkillCandidate(targetCandidateId, input);
      await loadLists();
      if (selection.current.isCurrent(targetCandidateId)) {
        const snapshot = await fetchDetailSnapshot(targetCandidateId);
        commitDetailSnapshot(targetCandidateId, snapshot);
        setConflictMessage(null);
        setActionMessage(input.reuse_mode === "reuse" ? "已批准为正式 Skill，现在可供 S5 脚本使用。" : "已收录为避坑 Skill，不会进入脚本选择器。");
        setActionMessageKind("status");
      }
      return true;
    } catch (error) {
      if (selection.current.isCurrent(targetCandidateId)) {
        const message = errorMessage(error, "Skill 审核失败");
        if (isViralSkillConflict(error)) {
          setConflictMessage(`服务器上的候选或正式 Skill 已变更。当前填写内容已保留。${message}`);
        }
        setActionMessage(message);
        setActionMessageKind("error");
      }
      return false;
    } finally {
      submissionLocked.current = false;
      setIsSubmitting(false);
    }
  }, [commitDetailSnapshot, fetchDetailSnapshot, loadLists]);

  const changeStatus = useCallback(async (input: SkillStatusInput) => {
    const targetCandidateId = selection.current.current();
    const targetSkill = skill;
    if (!targetCandidateId || !targetSkill || submissionLocked.current) return false;
    submissionLocked.current = true;
    setIsSubmitting(true);
    try {
      await setViralSkillStatus(targetSkill.skill_id, input);
      await loadLists();
      if (selection.current.isCurrent(targetCandidateId)) {
        const snapshot = await fetchDetailSnapshot(targetCandidateId);
        commitDetailSnapshot(targetCandidateId, snapshot);
        setConflictMessage(null);
        setActionMessage(input.status === "disabled" ? "Skill 已停用，S5 不再提供它。" : "Skill 已重新启用。");
        setActionMessageKind("status");
      }
      return true;
    } catch (error) {
      if (selection.current.isCurrent(targetCandidateId)) {
        const message = errorMessage(error, "Skill 状态修改失败");
        if (isViralSkillConflict(error)) {
          setConflictMessage(`服务器上的正式 Skill 已变更。当前填写内容已保留。${message}`);
        }
        setActionMessage(message);
        setActionMessageKind("error");
      }
      return false;
    } finally {
      submissionLocked.current = false;
      setIsSubmitting(false);
    }
  }, [commitDetailSnapshot, fetchDetailSnapshot, loadLists, skill]);

  const counts = useMemo(() => summarizeSkillLibrary(candidates, skills), [candidates, skills]);

  return {
    candidates, skills, selectedCandidateId, candidate, skill, counts,
    isLoading, isSubmitting, detailState, detailError, conflictMessage, isReloadingDetail,
    actionMessage, actionMessageKind,
    setSelectedCandidateId: selectCandidate,
    refresh, reconcile, reloadSelectedDetail, approve, changeStatus,
  };
}
