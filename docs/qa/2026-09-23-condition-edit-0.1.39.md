# 0.1.39 失败项目修改条件后重新生成验收

## 范围

普通自动剪辑项目在方案或模型阶段失败后，可以打开“修改生成条件”，调整数量、成片时长范围、字幕字号/字体/特效、重点词颜色和放大倍数；保存条件会使旧方案失效但保留项目与原素材，保存并重新生成会使用新的 revision 重新进入后台队列。登记失败仍使用独立的“重试登记”，不覆盖已生成文件。

## 根因与修复

之前失败项目只有“重新尝试”，用户不能在项目内修正导致失败的条件。例如方案实际 16.486 秒、而条件要求 20—40 秒时，重新规划仍可能重复得到不合格方案。现在 PATCH 条件后由后端清空旧 plan、冻结 Skill 快照和分析摘要，递增 revision 并返回新的生成前检查；只有本地检查通过时才排队。失败方案和原素材不会被删除，也不会自动调用付费模型。

## 回归证据

- `services/api/tests/test_generation_gate.py` 与 `services/api/tests/test_auto_edit_projects.py`：25 passed；新增 API 回归验证失败项目 PATCH 后 revision、条件、旧方案清空和 generation_check。
- `apps/desktop/node_modules/.bin/vitest.cmd run --reporter=dot`：171 passed。
- `scripts/verify-generation-gate.cjs`（隔离根目录 `E:\Codex工作盘\temp\condition-edit-039-browser-04`）：10 项通过，包含“失败后修改条件并重新生成”，拦截 PATCH/retry，`paidCalls: 0`。
- `scripts/verify-core-acceptance.ps1 -RunRoot E:\Codex工作盘\temp\condition-edit-039-core-04 -AdditionalPythonPath E:\Codex工作盘\runtimes\content-factory-release-crypto`：Python 870 passed、桌面 154 passed、合同 17 passed、类型检查和前端构建通过。
- 原生构建：`E:\Codex工作盘\artifacts\test-builds\content-factory-0.1.39-condition-edit\content-factory-desktop.exe`，SHA-256 `5250371261465AD7D4CB8211F07DD24F5D27BD9EFA471C6B68B2AFCBC5285688`。
- 原位桌面验收：API `content-factory-0.1.39`，原生窗口显示版本 `0.1.39`、本机服务已连接和“修改生成条件”；快捷方式目标、参数、工作目录和图标路径保持不变。
- 用户资料 `E:\Codex工作盘\temp\gradient033-browser-04` 启动前后清单均为 233 项，inventory JSON 完全一致；没有点击真实失败项目的重新尝试，没有付费模型调用。

## 使用方式

在失败项目详情点击“修改生成条件”，改完后：

1. 点“保存生成条件”：只保存，旧方案作废；确认条件后再点“重新尝试”。
2. 点“保存并重新生成”：一次保存并重新排队；如果本地条件仍不满足，界面会留在编辑器并给出具体原因。

以截图中的案例为例，可把最短秒数从 20 调到 15，或把范围调整到确实能覆盖口播长度，再保存并重新生成。重新规划仍可能因模型方案质量失败，系统不会承诺每次都成功。
