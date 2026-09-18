export const PROJECT_NAME = "爆款内容工厂";
export const PROJECT_ALIAS = "男装编剪器";
export const PROJECT_VERSION = "0.1.30";

export const jobStatuses = [
  "pending_analysis",
  "analyzed",
  "script_review",
  "ready_to_shoot",
  "pending_shoot",
  "assets_uploaded",
  "pending_edit",
  "video_review",
  "approved",
  "published",
] as const;

export type JobStatus = (typeof jobStatuses)[number];

export type ServiceHealth = Readonly<{
  service: "api" | "media-worker";
  status: "ok";
  version: string;
}>;

export const s0Modules = [
  {
    id: "desktop",
    name: "Windows 桌面端",
    detail: "Tauri 2 + React + TypeScript",
  },
  {
    id: "api",
    name: "业务 API",
    detail: "FastAPI 服务边界与健康检查",
  },
  {
    id: "media",
    name: "本地媒体工作器",
    detail: "FFmpeg、ASR、OCR 与镜头处理",
  },
  {
    id: "contracts",
    name: "共享数据契约",
    detail: "统一版本、任务状态和服务响应",
  },
  {
    id: "archive",
    name: "项目资料归档",
    detail: "需求、计划书与高保真原型已固化",
  },
] as const;

export type S0Module = (typeof s0Modules)[number];

export * from "./s1";
export * from "./s2";
export * from "./s3";
export * from "./s4";
export * from "./s5";
export * from "./s6";
export * from "./s7";
export * from "./s8";
export * from "./skills";
