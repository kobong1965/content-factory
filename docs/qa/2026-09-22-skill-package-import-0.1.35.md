# Skill 包导入修复验收（0.1.35）

## 问题

素材分析页上方的入口只接受视频。把 `SKILL.md` 拖到这里会按视频 MIME/扩展名校验失败；这不是 Skill 包上传入口。

下方入口原来只允许不超过 32 MiB 的结构化 `.cfskills`，并且 ZIP 必须包含一个 `.cfskills`。此前的 Codex 整理包同时带有原视频、关键帧和 12 个案例，约 733 MiB，因此在读取阶段被拒绝。直接上传单独的 `SKILL.md` 也缺少已审核候选、来源时间码和 Skill 版本，不能安全导入。

## 修复

- 下方入口仍保留 32 MiB 的最终 `.cfskills` 限制，避免把媒体塞进运行时数据库。
- 支持不超过 1 GiB 的 Codex 证据 ZIP，采用分块写入，避免一次性把大文件读进内存。
- ZIP 只读取 `references/**/software-skill-current.json`（兼容 `software-skill.json`），忽略视频、图片、Markdown、脚本和其他媒体；不解压、不执行包内脚本。
- 将已审核且可复用的 Skill 规范化为紧凑 `.cfskills`，自动生成完整候选来源记录，保留 12 条 Skill 的名称、版本、证据步骤和来源时间码。
- Windows 下先关闭 ZIP 文件句柄，再覆盖临时上传文件，避免 `WinError 32`。
- 上方视频入口继续只接受 MP4/MOV/MKV/AVI/WEBM/M4V；单独的 `SKILL.md` 请先放入带审核证据的 Codex ZIP，或使用下方生成的 `.cfskills`。

## 可直接导入的包

已从原始证据包生成不含视频的运行时 Skill 包：

`E:\我的SKILL包\大平视频-统一对标脚本Skill-可直接导入-20260922.cfskills`

- 文件大小：349,293 bytes
- Skill 数量：12
- 包内内容校验（导入 package_id）：`1c246fa3e22c386f7eaba9cf394d91a31a09f8b13dfcf237a4d1fcf8bcd93bc4`
- 文件 SHA-256：`c4c39664ce057499c1748084c1ffb3d46e0c1f5d4e41a051219304943640da93`
- 原视频/关键帧：不包含

在软件的“素材分析”页面，把这个文件拖到“导入 Codex 整理的 Skill 包”区域，先查看 12 条方法，再点“确认加入剪辑方法库”。

## 验收证据

- `services/api/tests/test_skill_archive_035.py`：用大于 32 MiB 且含视频/关键帧的 ZIP 验证预览 → 生成小包 → 确认导入；同时验证单独 `SKILL.md` 和缺少 JSON 的媒体 ZIP 会被拒绝。
- `services/api/tests/test_skill_bundle.py`、`test_skill_import_032.py`：原有 `.cfskills` 导入、损坏包拒绝、重复导入保护。
- 后端专项：13 项通过。
- 前端类型检查通过；桌面端 29 个测试文件、146 项通过。
- 版本：0.1.35（仅补丁版本升级，未替换用户数据）。
