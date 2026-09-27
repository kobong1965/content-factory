# 0.1.33 渐变工作台与安装版素材路径修复

## 范围和原状

用户要求替换灰白旧界面为渐变背景，并修复截图项目 222 在 10% 的文件缺失错误。当前维护源码位于 `E:\Codex工作盘\projects\content-factory-public-0.1.30`，目录名不是软件版本。真实已安装入口为 `D:\桌面\ContentFactory\versions\0.1.31`；新构建并不自动改变该入口。

保留已有未提交的 0.1.32 工作流、Skill 导入、回收站/安全永久删除改动。本轮不修改模型接口、字幕语音策略或已审核成片；不自动重试付费项目。安装器既有未提交改动未覆盖。

## 已确认故障原因

真实项目的三条源片均存在，第一条 301,284,240 字节。复制目标路径长 240 字符；旧 `copy_atomic` 在原 SHA256 文件名后追加进程/线程编号及 `.partial`，临时文件路径达到 260 字符。本机 `LongPathsEnabled=0`。旧代码在真实 Windows 文件系统复现同样的 Errno 2。

修复：用同目录短独占临时文件后原子替换；复制失败清理残片，保留既有目标。新的剪辑分析目录使用项目/来源标识组合的稳定哈希，去除重复嵌套长 ID。原缓存、素材和项目记录不迁移、不删除。

实际用安装包自带 Python 3.12.14 读取真实第一条原片，向 E 盘 240 字符目标完成复制；源片与副本 SHA256 相同。证据 `E:\Codex工作盘\temp\media-path-033-packaged-probe\result.json`。私有素材副本不得公开发布。

## 视觉与交互

渐变铺满页面，海军蓝侧栏、白色编辑工作面、钴蓝操作。项目列表与详情重新组织；详情有状态及处理阶段，源文件列表、参数、错误恢复和匹配结果。新建项目保留上传排序/参数双区。

默认正文 17px/1.62，导航按钮 16px，辅助说明14–15px，标题26–28px，控件44px。实际浏览器 FontFaceSet 与 CDP 均确认 Noto Sans SC 加载并用于中文。原有大字号偏好兼容，但旧字号切换入口不恢复。

长失败信息变为可读原因与折叠技术详情，完整路径换行且不会撑出页面。未知错误不推断素材已删除。重点词颜色用色块加深色数值，不以白底黄字显示。

## 测试结果（最终浏览器第4轮及原生预览）

- Python：729 passed，259.45s。报告 `E:\Codex工作盘\temp\gradient033-core-01\results.xml`。
- 前端：28 文件、142 测试通过，TypeScript 无错误。
- 路径专项：新增4条测试先失败后通过；媒体/工作流相关68项通过，含真实FFmpeg。路径过深的安装式根目录下完成复制、代理、关键帧与result落盘。
- 浏览器：`E:\Codex工作盘\temp\gradient033-browser-04\evidence\results.json`，完整脚本退出0。创建三素材项目、调整顺序、503失败保留表单、保存重开一致且不自动启动；长错误折叠/展开/键盘可达；回收站恢复和二次删除仅针对合成测试文件；Skill包预览确认和重开；按款号筛选、指定批次跳转、ZIP文件内容有效。44张矩阵截图，共51张PNG。
- 视口：2560、1920、2048、1707、1440、800、390 CSS px；保留200%文字放大模拟。不是实际改变Windows系统缩放。
- 首轮暴露分析页隐式网格窄屏溢出，随后修复时暴露隐藏file input被width覆盖；均已修复，不删除断言。
- 第3轮自动流程通过后，视觉复核发现款号选中按钮hover时白字浅底；已修复选中按钮hover/focus的CSS覆盖，第4轮新增两态断言均为8.13:1，独立截图复核确认修复。
- 模型设置/版本入口仅只读导航，配置逐字节保持一致；没有点击检查更新、下载或安装，没有真实模型调用。
- 原生：最终构建 `E:\Codex工作盘\artifacts\test-builds\content-factory-0.1.33-gradient-final` 已实际启动，WebView加载 `http://tauri.localhost/index.html?cfPort=18769` 并连接隔离API。通过Windows窗口操作确认版本0.1.33、渐变与字体显示、打开新建表单双区、导航到素材分析/成片库以及J85筛选。原生选中款号深蓝白字正常。此轮未在原生窗口重新提交上传、下载、删除或模型任务。
- 测试仅永久删除了本次隔离目录生成的三条托管测试素材；合成源视频仍在evidence目录，未删除任何真实素材。
- Impeccable审查采用已有方向合同和实际截图；seed c197a70d已记录，但原始roll输出未另存留档、未独立核验；没有独立QUALITY BAR卡片。不补造过程证据。
- 独立视觉复核最终 disposition: ship，限本轮两项修复（证据边界、款号状态对比度）；独立代码复核在copy_atomic、稳定缓存标识、错误展示及对应测试范围未发现阻断项。不能将局部复核扩大为全部历史改动的安全审计。

最终桌面EXE SHA256：`85B04242E611E329E3C10B59F87BB1C98B78A07228F54305BD48BAA3B673F818`。隔离原生API健康响应 `build_id=content-factory-0.1.33`，服务接口版本 `version=0.1.0` 是不同字段，不代表桌面版本回退。

## 可重复命令

在维护工作副本使用 PowerShell 7：

```powershell
./scripts/verify-release-python.ps1 -PythonExecutable 'E:\Codex项目盘\男装编剪器\.venv\Scripts\python.exe' -RunRoot 'E:\Codex工作盘\temp\gradient033-core-NEW' -AdditionalPythonPath 'E:\Codex工作盘\runtimes\content-factory-release-crypto'
./scripts/verify-s5-dialog.ps1 -RunRoot 'E:\Codex工作盘\temp\gradient033-browser-NEW' -BrowserScript verify-workflow-032.cjs -PythonExecutable 'E:\Codex项目盘\男装编剪器\.venv\Scripts\python.exe' -AdditionalPythonPath 'E:\Codex工作盘\runtimes\content-factory-release-crypto'
```

NEW 必须是不存在的新目录。直接pytest时显式 PYTHONPATH 指向本工作副本的API/media/contracts，避免生产venv的editable路径。

## 部署与验证边界

此报告不宣称已重新完成用户项目222的剪辑，不把合成测试成片当商业质量验收。旧版failed状态保留，不能自动调用付费模型。真实系统125%/150%、同事干净环境均未复测。

已打开的0.1.33窗口是独立测试预览，数据为合成验收数据。生产快捷方式仍指向D盘0.1.31，尚未得到本轮原位部署确认，没有替换已安装程序或用户数据。

原位更新需同时部署新EXE和API/media模块，仅换EXE不能修复10%错误。保留0.1.31原版本，切换前备份SQLite及快捷方式并再次核对无运行任务。新建工作数据后回退旧数据库可能丢失新增工作，必须先保留回退时快照并人工确认恢复范围。
