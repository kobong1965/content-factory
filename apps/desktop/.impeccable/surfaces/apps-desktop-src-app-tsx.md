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
- Constraints: 保留功能与数据；2026-09-22 用户要求直接替换旧视觉为渐变背景版，优先于历史 Proposal A。

## Direction contract

THESIS: 渐变演播工作台，把项目、处理阶段、失败恢复与成片留在同一工作面，替换灰白页面与大段说明。

OWN-WORLD: 雾蓝到浅桃的大幅渐变、海军蓝导航、白色工作面、钴蓝操作；深色字、统一线性图标与分区页签。

STORY: 左侧选项目，右侧看状态和参数；保存与启动分开，完成后直达批次，失败展开诊断。

FIRST VIEWPORT: 224px 悬浮深色导航、320px 项目轨与弹性白色详情；紧凑标题与新建按钮。切换项目保持双区独立滚动，阶段条只显示实际状态。

FORM: code-led；候选4演播控制台；seed `c197a70d`；用户固定渐变背景与双区操作。

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

- Derivation: 1剪辑场记单、2布料样册、3商品陈列、4演播控制台、5唱片索引、6剪辑时间轨、7色彩校对册，跨纸张/空间/数字操作三个家族。
- Challenger verdicts: HyperCard declined（保留来回定位）；七段显示 declined（数字对齐）；舞台天幕 competitive（渐变拥有完整背景）；工业服装 declined（文字直述操作）；日式密集页 declined（紧凑分区但不缩小文字）；唱片目录 declined（统一批次索引）。用户明确直接实施，不追加方向选择阻塞修复。
- Unresolved: 真实 Windows 125%/150% 与跨屏 DPI 仍须单独验证；不把视口模拟冒充系统缩放。
- Evidence boundary: seed `c197a70d` 已记录，原始 roll 输出未另存留档，未独立核验；未提供独立 QUALITY BAR 卡片。最终复核依照用户明确要求、此合同和实际运行截图，不宣称额外批准。
