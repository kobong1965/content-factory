import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MaterialAsset, MaterialImportTask, MaterialSummary, MaterialUsage, ShootingTask } from "@content-factory/contracts";

import { createLatestRequestGuard } from "./latestRequestGuard";
import {
  DEFAULT_S6_READINESS, confirmMaterialMatch, fetchMaterial, fetchMaterialImports, fetchMaterialUsage,
  fetchMaterials, fetchS6Products, fetchS6Readiness, fetchS6Scripts, fetchShootingTasks,
  recognizeMaterial, releaseMaterialMatch, retryMaterialImport, saveMaterial, uploadMaterial,
  type MaterialUploadInput, type S6Product,
} from "./materials";
import type { S6ScriptOption } from "@content-factory/contracts";
import { useWorkspacePolling } from "./useWorkspacePolling";
import { createSerializedRefresh } from "./workspacePolling";

export function useS6Materials(pollingActive = true) {
  const [readiness, setReadiness] = useState(DEFAULT_S6_READINESS);
  const [products, setProducts] = useState<S6Product[]>([]);
  const [scripts, setScripts] = useState<S6ScriptOption[]>([]);
  const [imports, setImports] = useState<MaterialImportTask[]>([]);
  const [materials, setMaterials] = useState<MaterialSummary[]>([]);
  const [shootingTasks, setShootingTasks] = useState<ShootingTask[]>([]);
  const [material, setMaterial] = useState<MaterialAsset | null>(null);
  const [usage, setUsage] = useState<MaterialUsage[]>([]);
  const [connectionState, setConnectionState] = useState<"connecting" | "live" | "offline">("connecting");
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const selectionRequests = useRef(createLatestRequestGuard<string>());

  const refresh = useMemo(() => createSerializedRefresh(async (signal?: AbortSignal) => {
    setConnectionState("connecting");
    try {
      const results = await Promise.all([
        fetchS6Readiness(signal), fetchS6Products(signal), fetchS6Scripts(signal), fetchMaterialImports(signal),
        fetchMaterials(signal), fetchShootingTasks(signal),
      ]);
      setReadiness(results[0]); setProducts(results[1]); setScripts(results[2]); setImports(results[3]);
      setMaterials(results[4]); setShootingTasks(results[5]); setConnectionState("live");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setConnectionState("offline");
      setActionMessage(error instanceof Error ? error.message : "素材中心读取失败");
    } finally {
      setIsLoading(false);
    }
  }), []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  const hasActiveImports = imports.some((task) => ["pending", "running", "retry_wait"].includes(task.status));
  useWorkspacePolling({ active: pollingActive, intervalMs: 1800, pollingRequired: hasActiveImports, refresh });

  const selectMaterial = useCallback(async (materialId: string) => {
    const isCurrentSelection = selectionRequests.current.begin(materialId);
    setIsLoading(true);
    try {
      const [profile, history] = await Promise.all([fetchMaterial(materialId), fetchMaterialUsage(materialId)]);
      if (!isCurrentSelection()) return;
      setMaterial(profile); setUsage(history); setActionMessage(null);
    } catch (error) {
      if (!isCurrentSelection()) return;
      setActionMessage(error instanceof Error ? error.message : "素材读取失败");
    } finally { if (isCurrentSelection()) setIsLoading(false); }
  }, []);

  const upload = useCallback(async (input: MaterialUploadInput) => {
    setIsSubmitting(true);
    try {
      await uploadMaterial(input); await refresh(); setActionMessage("视频已交给本机处理，完成后会自动进入素材库。"); return true;
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : "素材上传失败"); return false;
    } finally { setIsSubmitting(false); }
  }, [refresh]);

  const uploadBatch = useCallback(async (inputs: readonly MaterialUploadInput[]) => {
    if (inputs.length === 0) return false;
    setIsSubmitting(true);
    let accepted = 0;
    const errors: string[] = [];
    try {
      for (const input of inputs) {
        try {
          await uploadMaterial(input);
          accepted += 1;
        } catch (error) {
          errors.push(`${input.video.name}：${error instanceof Error ? error.message : "上传失败"}`);
        }
      }
      await refresh();
      if (errors.length === 0) {
        setActionMessage(`${accepted} 个视频已交给本机并行处理，完成后会自动进入素材库。`);
        return true;
      }
      setActionMessage(`已接收 ${accepted} 个，${errors.length} 个未接收。${errors.slice(0, 2).join("；")}`);
      return false;
    } finally {
      setIsSubmitting(false);
    }
  }, [refresh]);

  const retry = useCallback(async (taskId: string) => {
    setIsSubmitting(true);
    try { await retryMaterialImport(taskId); await refresh(); setActionMessage("失败任务已重新排队。"); }
    catch (error) { setActionMessage(error instanceof Error ? error.message : "任务重试失败"); }
    finally { setIsSubmitting(false); }
  }, [refresh]);

  const save = useCallback(async (draft: MaterialAsset, actor: string) => {
    const belongsToSelection = selectionRequests.current.capture(draft.material_id);
    if (!belongsToSelection()) return false;
    const isCurrentSelection = selectionRequests.current.begin(draft.material_id);
    setIsSubmitting(true);
    try {
      const saved = await saveMaterial(draft, actor);
      if (isCurrentSelection()) setMaterial(saved);
      await refresh();
      if (isCurrentSelection()) setActionMessage(`素材标签已保存为第 ${saved.revision} 版。`);
      return true;
    } catch (error) { if (isCurrentSelection()) setActionMessage(error instanceof Error ? error.message : "素材保存失败"); return false; }
    finally { setIsSubmitting(false); }
  }, [refresh]);

  const recognize = useCallback(async (materialId: string) => {
    const belongsToSelection = selectionRequests.current.capture(materialId);
    if (!belongsToSelection()) return;
    const isCurrentSelection = selectionRequests.current.begin(materialId);
    setIsSubmitting(true);
    try {
      const saved = await recognizeMaterial(materialId);
      if (isCurrentSelection()) setMaterial(saved);
      await refresh();
      if (isCurrentSelection()) setActionMessage("关键帧标签已重新识别。");
    }
    catch (error) { if (isCurrentSelection()) setActionMessage(error instanceof Error ? error.message : "关键帧识别失败"); }
    finally { setIsSubmitting(false); }
  }, [refresh]);

  const confirm = useCallback(async (scriptId: string, shotId: string, materialId: string, clipId: string, actor: string) => {
    setIsSubmitting(true);
    try {
      const next = await confirmMaterialMatch(scriptId, { script_shot_id: shotId, material_id: materialId, clip_id: clipId, actor });
      setShootingTasks((items) => items.map((item) => item.script_id === scriptId ? next : item));
      await refresh(); setActionMessage(next.status === "ready_for_edit" ? "这份脚本的素材已补齐，可以进入剪辑。" : "素材匹配已确认。");
    } catch (error) { setActionMessage(error instanceof Error ? error.message : "素材匹配失败"); }
    finally { setIsSubmitting(false); }
  }, [refresh]);

  const release = useCallback(async (scriptId: string, shotId: string, actor: string) => {
    setIsSubmitting(true);
    try { await releaseMaterialMatch(scriptId, shotId, actor); await refresh(); setActionMessage("已撤销匹配，镜头重新进入补拍清单。"); }
    catch (error) { setActionMessage(error instanceof Error ? error.message : "撤销匹配失败"); }
    finally { setIsSubmitting(false); }
  }, [refresh]);

  return {
    readiness, products, scripts, imports, materials, shootingTasks, material, usage,
    connectionState, isLoading, isSubmitting, actionMessage,
    refresh, selectMaterial, upload, uploadBatch, retry, save, recognize, confirm, release,
  };
}
