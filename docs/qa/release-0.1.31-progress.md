# 0.1.31 更新、账单入口、内部 Skill 导入

用户于 2026-09-19 确认：固定使用 https://apikey.fun/dashboard；按已确认规格实施。

## 已实现

- 原生固定地址打开服务商后台，不携带 API Key。余额/用量以服务商页面为准，无伪造本地余额。
- GitHub 版本检查、稳定/测试通道、进度、签名清单和文件校验；退出后安装；忙碌保护、数据库备份、安装失败重开旧版。
- 使用已有 cryptography 的 Ed25519，不新增更新框架依赖。签名私钥仅在 E 盘受限私有目录，源码只含公钥。
- 同目录内部 Skill 包自动导入；事务、幂等、保留本地修改、导入证据独立于本机分析队列。第三方方法可从内部文件离线装入并验固定哈希。
- 修复公开源码白名单漏掉 .rs/.xml；保留 PerMonitorV2。

## 测试记录（持续追加）

- 第一轮全部 Python：686 通过；报告 E:/Codex工作盘/temp/release031-core01/results.xml。
- 前端：138 通过；TypeScript 通过。
- 复核专项：32 通过，包含失败父任务/待处理子段不阻塞更新、坏内部包不阻止启动、安装失败/篡改不替换旧程序。
- 浏览器：账单 URL、版本入口、测试版默认关闭、模拟检查/下载/忙碌反馈、960/1280/1536/1920/2560 宽度。证据 E:/Codex工作盘/temp/update031-browser01/evidence。不是系统缩放实测，也不是实际升级下载。
- 独立运行时：HTTP 健康、导入、SQLite 持久化通过；Python 3.12.14。

## 明确边界

最终产物验证（2026-09-19）：全部 Python 689 通过，前端 138 通过；最终浏览器证据位于 E:/Codex工作盘/temp/update031-browser-final/evidence。最终 NSIS 安装到含中文和空格的隔离目录，退出码 0；安装后自带 Python 启动服务成功，原生窗口显示 0.1.31 和“本机服务已连接”。实际目标数据库只读检查批准 Skill 为 13，重复启动导入数为 0。测试模式没有替换生产快捷方式。原生点击自动化未形成可靠完成证据，账单和更新交互以浏览器专项为准；不冒充已实测未来版本完整升级。

- 无 Windows Authenticode 代码签名；Ed25519 更新清单签名不等于操作系统代码签名。
- 旧0.1.30没有更新入口，首次需安装0.1.31。开发/旧启动目录不会冒充可自动安装版。
- 安装失败可重开旧版；尚不宣称新版本运行一段时间后的逻辑故障或不兼容数据库迁移自动回滚。备份在用户 cache/update-backups，旧程序版本目录保留。
- 内部 Skill 为当前批准快照及文字证据，不包含原视频、历史队列和隐藏 S5 完整报告链。
- 未新增收费模型调用；没测同事电脑、重启电脑或真实系统缩放。用户已取消虚拟机验收要求。
- 原生产安装、快捷方式和业务数据库尚未替换。

## 重复验证命令

运行 scripts/verify-release-python.ps1（新 E 盘 RunRoot），桌面 Vitest 与 tsc。界面运行 scripts/verify-s5-dialog.ps1 -BrowserScript verify-update-billing.cjs，可显式提供 PythonExecutable 和 AdditionalPythonPath。构建用 build-creator-desktop.ps1，组件用 package-delivery.py，签名用 sign-update.py（私钥路径不得指向源码或公开成品）。
