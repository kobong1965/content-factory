import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ProductFactSuggestion, ProductListItem, ProductProfile, ProductScriptEligibility, ProductStatus, ProductVersion,
} from "@content-factory/contracts";

import { createLatestRequestGuard, type LatestRequestCheck } from "./latestRequestGuard";
import {
  DEFAULT_S4_READINESS,
  changeProductStatus,
  createProduct,
  fetchProduct,
  fetchProductFactSuggestions,
  fetchProductScriptEligibility,
  fetchProducts,
  fetchProductVersions,
  fetchS4Readiness,
  prepareConfirmedProfile,
  saveProduct,
  uploadProductAsset,
} from "./products";

const CREATING_PRODUCT = Symbol("creating-product");

export function useS4Products() {
  const [readiness, setReadiness] = useState(DEFAULT_S4_READINESS);
  const [products, setProducts] = useState<ProductListItem[]>([]);
  const [profile, setProfile] = useState<ProductProfile | null>(null);
  const [versions, setVersions] = useState<ProductVersion[]>([]);
  const [eligibility, setEligibility] = useState<ProductScriptEligibility | null>(null);
  const [suggestions, setSuggestions] = useState<ProductFactSuggestion[]>([]);
  const [isSuggesting, setIsSuggesting] = useState(false);
  const [connectionState, setConnectionState] = useState<"connecting" | "live" | "offline">("connecting");
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const selectionRequests = useRef(createLatestRequestGuard<string | symbol>());

  const refresh = useCallback(async (query = "", status = "", signal?: AbortSignal) => {
    setConnectionState("connecting");
    try {
      const [nextReadiness, nextProducts] = await Promise.all([
        fetchS4Readiness(signal), fetchProducts(query, status, signal),
      ]);
      setReadiness(nextReadiness);
      setProducts(nextProducts);
      setConnectionState("live");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setConnectionState("offline");
      setActionMessage(error instanceof Error ? error.message : "商品资料读取失败");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh("", "", controller.signal);
    return () => controller.abort();
  }, [refresh]);

  const select = useCallback(async (productId: string) => {
    const isCurrentSelection = selectionRequests.current.begin(productId);
    setIsLoading(true);
    try {
      const [nextProfile, nextVersions, nextEligibility] = await Promise.all([
        fetchProduct(productId), fetchProductVersions(productId), fetchProductScriptEligibility(productId),
      ]);
      if (!isCurrentSelection()) return;
      setProfile(nextProfile);
      setVersions(nextVersions);
      setEligibility(nextEligibility);
      setSuggestions([]);
      setActionMessage(null);
      return true;
    } catch (error) {
      if (!isCurrentSelection()) return;
      setActionMessage(error instanceof Error ? error.message : "商品档案读取失败");
      return false;
    } finally {
      if (isCurrentSelection()) setIsLoading(false);
    }
  }, []);

  const reloadSelected = useCallback(async (nextProfile: ProductProfile, isCurrentSelection: LatestRequestCheck) => {
    if (isCurrentSelection()) setProfile(nextProfile);
    if (isCurrentSelection()) {
      const [nextVersions, nextEligibility] = await Promise.all([
        fetchProductVersions(nextProfile.product_id), fetchProductScriptEligibility(nextProfile.product_id),
      ]);
      if (isCurrentSelection()) {
        setVersions(nextVersions);
        setEligibility(nextEligibility);
      }
    }
    await refresh();
  }, [refresh]);

  const create = useCallback(async (input: { sku?: string; name: string; actor: string; file?: File | null }) => {
    let isCurrentSelection = selectionRequests.current.begin(CREATING_PRODUCT);
    setIsSubmitting(true);
    try {
      let created = await createProduct({ sku: input.sku, name: input.name, actor: input.actor });
      if (!isCurrentSelection()) {
        await refresh();
        return true;
      }
      isCurrentSelection = selectionRequests.current.begin(created.product_id);
      if (input.file) {
        try {
          const uploaded = await uploadProductAsset(created, input.file, input.actor);
          created = uploaded.profile;
        } catch (error) {
          await reloadSelected(created, isCurrentSelection);
          if (isCurrentSelection()) setActionMessage(
            `临时商品已经安全建立，但图片上传失败：${error instanceof Error ? error.message : "上传失败"}。可在右侧重新上传，不必再次建品。`,
          );
          return true;
        }
      }
      await reloadSelected(created, isCurrentSelection);
      if (input.file) {
        setIsSuggesting(true);
        try {
          const result = await fetchProductFactSuggestions(created.product_id);
          if (isCurrentSelection()) setSuggestions(result.suggestions);
          if (isCurrentSelection()) setActionMessage(result.suggestions.length
            ? `商品已建档，AI 找到 ${result.suggestions.length} 条可见卖点候选。确认后即可用于脚本。`
            : "商品已建档；图片中没有足够明确的可见卖点，请人工补一条真实事实。");
        } catch (error) {
          if (isCurrentSelection()) setActionMessage(`商品图片已安全保存；AI 识别暂未完成：${error instanceof Error ? error.message : "识别失败"}`);
        } finally {
          setIsSuggesting(false);
        }
      } else if (isCurrentSelection()) {
        setActionMessage("商品草稿已建立。上传图片并确认一条真实卖点后即可写脚本。");
      }
      return true;
    } catch (error) {
      if (isCurrentSelection()) setActionMessage(error instanceof Error ? error.message : "商品建立失败");
      return false;
    } finally {
      setIsSubmitting(false);
    }
  }, [refresh, reloadSelected]);

  const save = useCallback(async (draft: ProductProfile, actor: string) => {
    const belongsToSelection = selectionRequests.current.capture(draft.product_id);
    if (!belongsToSelection()) return false;
    const isCurrentSelection = selectionRequests.current.begin(draft.product_id);
    setIsSubmitting(true);
    try {
      const saved = await saveProduct(prepareConfirmedProfile(draft), actor);
      await reloadSelected(saved, isCurrentSelection);
      if (isCurrentSelection()) setSuggestions([]);
      if (isCurrentSelection()) setActionMessage(`商品资料已保存为第 ${saved.revision} 版。`);
      return true;
    } catch (error) {
      if (isCurrentSelection()) setActionMessage(error instanceof Error ? error.message : "商品保存失败");
      return false;
    } finally {
      setIsSubmitting(false);
    }
  }, [reloadSelected]);

  const changeStatus = useCallback(async (status: ProductStatus, actor: string) => {
    if (!profile) return false;
    const belongsToSelection = selectionRequests.current.capture(profile.product_id);
    if (!belongsToSelection()) return false;
    const isCurrentSelection = selectionRequests.current.begin(profile.product_id);
    setIsSubmitting(true);
    try {
      const updated = await changeProductStatus(profile, status, actor);
      await reloadSelected(updated, isCurrentSelection);
      if (isCurrentSelection()) setActionMessage(status === "active" ? "商品已转为正式商品；脚本仍只引用已确认事实。" : status === "archived" ? "商品已归档，资料和历史仍然保留。" : "商品已恢复为临时商品；确认一条真实卖点后仍可写脚本。");
      return true;
    } catch (error) {
      if (isCurrentSelection()) setActionMessage(error instanceof Error ? error.message : "商品状态修改失败");
      return false;
    } finally {
      setIsSubmitting(false);
    }
  }, [profile, reloadSelected]);

  const upload = useCallback(async (file: File, actor: string) => {
    if (!profile) return false;
    const belongsToSelection = selectionRequests.current.capture(profile.product_id);
    if (!belongsToSelection()) return false;
    const isCurrentSelection = selectionRequests.current.begin(profile.product_id);
    setIsSubmitting(true);
    try {
      const result = await uploadProductAsset(profile, file, actor);
      await reloadSelected(result.profile, isCurrentSelection);
      if (file.type.startsWith("image/") || /\.(jpe?g|png|webp)$/i.test(file.name)) {
        setIsSuggesting(true);
        try {
          const proposed = await fetchProductFactSuggestions(profile.product_id);
          if (isCurrentSelection()) setSuggestions(proposed.suggestions);
          if (isCurrentSelection()) setActionMessage(proposed.suggestions.length
            ? `图片已保存，AI 找到 ${proposed.suggestions.length} 条候选；请确认真实内容。`
            : "图片已保存，但没有识别出足够明确的可见卖点。");
        } catch (error) {
          if (isCurrentSelection()) setActionMessage(`图片已保存；AI 识别暂未完成：${error instanceof Error ? error.message : "识别失败"}`);
        } finally {
          setIsSuggesting(false);
        }
      } else if (isCurrentSelection()) {
        setActionMessage(result.duplicate ? "这份资料已存在，没有重复保存。" : "商品资料已复制到本机资料库。");
      }
      return true;
    } catch (error) {
      if (isCurrentSelection()) setActionMessage(error instanceof Error ? error.message : "资料上传失败");
      return false;
    } finally {
      setIsSubmitting(false);
    }
  }, [profile, reloadSelected]);

  const suggest = useCallback(async () => {
    if (!profile) return false;
    const belongsToSelection = selectionRequests.current.capture(profile.product_id);
    if (!belongsToSelection()) return false;
    setIsSuggesting(true);
    try {
      const result = await fetchProductFactSuggestions(profile.product_id);
      if (belongsToSelection()) setSuggestions(result.suggestions);
      if (belongsToSelection()) setActionMessage(result.suggestions.length
        ? `AI 找到 ${result.suggestions.length} 条可见卖点候选；不会自动写入，请人工确认。`
        : "当前图片没有识别出足够明确的可见卖点。");
      return true;
    } catch (error) {
      if (belongsToSelection()) setActionMessage(error instanceof Error ? error.message : "AI 卖点识别失败");
      return false;
    } finally {
      setIsSuggesting(false);
    }
  }, [profile]);

  return {
    readiness, products, profile, versions, eligibility, suggestions, isSuggesting,
    connectionState, isLoading, isSubmitting, actionMessage,
    refresh, select, create, save, changeStatus, upload, suggest,
    clearSuggestions: () => setSuggestions([]), setProfile,
  };
}
