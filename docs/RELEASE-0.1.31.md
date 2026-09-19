# 爆款内容工厂 0.1.31

## 新功能

- “版本与更新”：GitHub 发布检查，稳定/测试通道，下载进度，Ed25519 更新清单验签与组件 SHA-256，忙碌时阻止安装，关闭窗口后安装并重开。保留旧版本目录，安装失败回到旧版。
- “模型连接设置”新增 APIKEY.FUN 余额与用量入口，固定打开 https://apikey.fun/dashboard，不向网页附带 API Key。
- 支持安装程序自动发现内部业务 Skill 包；首次启动事务导入，重复安装不覆盖本机改动，损坏包有明确提示且不阻止旧软件启动。
- 补全公开源码中之前遗漏的 Rust 源文件和 HiDPI manifest。

## 安装文件

将以下文件放在同一文件夹，双击安装程序，选择安装目录：

1. content-factory-0.1.31-setup.exe
2. content-factory-0.1.31-program.zip
3. delivery-manifest.json
4. [content-factory-0.1.30-speech-model.zip](https://github.com/kobong1965/content-factory/releases/download/v0.1.30/content-factory-0.1.30-speech-model.zip)
5. [content-factory-0.1.30-prerequisites.zip](https://github.com/kobong1965/content-factory/releases/download/v0.1.30/content-factory-0.1.30-prerequisites.zip)

后两个未变化的大组件复用 0.1.30 的已发布文件，无需重复下载已有副本。它们在本次本地完整交付目录中也已备齐。无需安装 Python、Node 或 FFmpeg。

内部同事可另收到 business-skills.cfskills、huashu-analysis.SKILL.md，以及单独的模型加密配置包。将配套文件放在安装程序旁会自动处理；这些私有文件不在公开 Release。

## 更新与边界

- 0.1.30 没有更新入口，首次需使用 0.1.31 安装程序；后续新版需提供有效的签名更新清单。
- 用户资料与程序版本目录分离。已有正式安装版升级保留本地资料；旧开发启动方式需要单独接管数据，不应直接当作同一路径升级。
- 本次不含 Windows Authenticode 代码签名。Ed25519 更新清单签名用于后续更新信任，不等于消除 Windows 对首次安装的未知发布者提示。
- 数据库备份在用户目录 cache/update-backups。保留旧程序不等于任意未来数据库迁移后都能直接降级；不宣称新版运行故障自动回滚。
- 内部 Skill 包只含批准的方法和文字证据快照，不含原视频及隐藏 S5 的完整旧分析报告链。
- 余额与用量通过服务商后台查看，不声称存在已接通的余额查询 API。可能需要登录服务商网站。
- 无新增付费模型调用。未以虚拟机或同事真实电脑验证；本机隔离安装测试与模拟更新测试分开记录。

## 已运行检查

689 项 Python 测试，138 项前端测试，TypeScript 与原生构建通过。浏览器测试覆盖入口、模拟更新状态、忙碌反馈与多窗口宽度；独立 Python 运行时健康检查和 SQLite 持久化通过。内部真实 13 个 Skill 导入、刷新与再次导入验证通过。详见源码 docs/qa/release-0.1.31-progress.md。
