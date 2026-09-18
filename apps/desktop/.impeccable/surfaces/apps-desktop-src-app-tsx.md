---
version: 1
slug: "apps-desktop-src-app-tsx"
primary_target: "apps/desktop/src/App.tsx"
related_targets: ["apps/desktop/src/styles.css","apps/desktop/src/ui-scale.css","apps/desktop/src/ui-responsive.css","apps/desktop/src/gateway-settings.css"]
---

# App surface brief

- Scope: `apps/desktop/src/App.tsx` and all S2–S8 / CFG workspaces rendered inside it.
- Mode: Operate.
- Audience: 内部男装内容团队；长时间在 2K Windows 桌面端批量处理、审核和恢复任务。
- Job: 快速确认当前对象、证据、任务健康、可靠检查点与下一步操作，同时不丢失编辑或后台进度。
- Constraints: 保留现有功能、API、数据、状态、审核和本地/云端边界；正式实现以 Proposal v1.1 方案 A 为准。

## Direction contract

THESIS: 证据质检台把任务、9:16 原片、时间码结论和诊断同时放在工作面；拒绝重复 Hero 与卡片套卡片。

OWN-WORLD: 冷白工作面、深墨导航、松针绿主操作、锈橙证据；列轨、分隔线、清晰字号和克制圆角建立层级。

STORY: 用户先识别当前任务与阶段，再核对画面和分析证据，最后修订、恢复或确认结果。

FIRST VIEWPORT: 2K 下为 248px 导航、328px 任务、弹性主工作面、392px 检查器；唯一主操作位于页面头或粘性操作区。

FORM: user-approved Proposal v1.1 A；code-led；seed `user-approved-proposal-v1.1-a`；方案 B 只作为报告阅读模式。

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

- Unresolved: Windows 125%/150%、跨屏 DPI、200% 文字放大和正式业务流必须在落地后实测；不把视口模拟冒充系统缩放。
