# 0.1.31 更新、API 账单入口与 Skill 绑定规格（待确认）

## 目标与假设

基于已发布 0.1.30，保留素材分析、视频剪辑、成片库及所有个人数据。此次 API 需求解释为可保存并打开服务商的余额/用量后台地址，不假装实现尚未核实的余额查询 API。业务 Skill 只在内部配套包交付，不能公开发布。

## 现状证据

- React App.tsx 的侧栏仅显示 PROJECT_VERSION，没有更新流程。
- scripts/installed_launcher.py 首次只复制 bundled-skills/huashu-douyin-script/SKILL.md。
- s3_skills.py 使用 SQLite 保存人工批准的 Skill 快照及证据关联，不能用一个 SKILL.md 替代整个业务库。
- prepare-public-source.py 文件扩展名白名单遗漏 .rs，公开源码缺少 Tauri 原生实现；须从原有效源码核对恢复，不凭空重写。
- GitHub v0.1.30 当前是 prerelease，不能只请求 latest 后错误地声称没有版本。

## 技术与结构

Tauri 2 / React / TypeScript、Python FastAPI、SQLite；延用现有独立 Python API/ASR 运行时及分组件安装包。原生源代码在 apps/desktop/src-tauri，界面在 apps/desktop/src，后端在 services/api/src/content_factory_api，部署脚本在 scripts。

## 验收标准

### 软件更新

版本文字改为入口，展示当前版本、检查结果、新版说明、下载进度、重试、稍后安装。来源固定 kobong1965/content-factory；默认稳定通道，测试版需明确选择。下载前校验可信签名清单，下载后校验组件哈希；哈希不冒充代码签名。保留现有分组件交付，未变化模型不重复下载。分析/转写/剪辑/导出运行时禁止安装切换；开始安装前阻止新任务并备份数据库。新版本独立目录就绪后才切换入口，失败不覆盖旧版本、配置和用户数据。回退必须考虑数据库版本，不能直接用旧程序打开已迁移的新数据库。

### API 余额和使用情况

模型设置增加“服务商后台地址”和“查看余额与用量”。地址与推理 API 地址分开保存，允许不同连接独立设置；仅接受安全 HTTPS 网页地址，不接受内嵌用户名/密码，不追加密钥。经系统浏览器打开，登录由服务商处理，不收集浏览器凭据。不知道后台地址时保留待填写状态，不从域名猜路径。此版不显示未经服务商实际返回的余额数字。

### Skill 绑定

分别清点基础分析方法和已审核业务 Skill。导出应包含实际运行内容、版本、来源证据和必要资源，记录清单/哈希，转换绝对路径。仅复制关联必需文件，不复制整个运行数据库中的模型秘密和历史队列。独立内部 Skill 包由安装程序自动发现并导入；程序和包作为一套交付，不要求同事逐个安装。首次导入记录包标识，重复安装不重复导入，不覆盖同事编辑过的 Skill；冲突保留双方供确认。导入后在实际自动匹配接口验证可用，不只验证文件存在。第三方无明确再分发许可的内容不得放到公开 Release；内部交付范围与来源单列。

## 实施顺序（待确认）

1. 建立 0.1.31 隔离分支，核对并补全原生源文件及打包白名单。
2. 更新服务/入口与签名组件清单；补失败、忙碌、备份及恢复测试。
3. API 后台链接的独立持久化、安全打开及界面。
4. Skill 清点、可迁移导入导出、幂等与冲突保护。
5. 隔离真实安装、原生交互、重开与回归，生成公开程序包及内部 Skill 包。

## 命令与测试

从相应工作区运行：

```powershell
pnpm --filter @content-factory/desktop test -- --cache=false
pnpm --filter @content-factory/desktop typecheck
& ./scripts/verify-release-python.ps1 -PythonExecutable 'E:\Codex项目盘\男装编剪器\.venv\Scripts\python.exe' -RunRoot 'E:\Codex工作盘\temp\release-031-core-01' -AdditionalPythonPath 'E:\Codex工作盘\runtimes\content-factory-release-crypto'
& ./scripts/build-creator-desktop.ps1 -OutputRoot 'E:\Codex工作盘\artifacts\test-builds\content-factory-0.1.31-native-01'
```

以上为计划命令，尚未执行。每次 RunRoot/OutputRoot 使用新的目录。新行为先写失败用例再实现；测试网络更新用本地模拟与签名夹具，最终另测真实 GitHub 下载。账单链接需原生点击验证；Skill 需导入后重开和模型匹配输入验证。无虚拟机验收要求，诚实记录本机和同事电脑验证边界。

## 代码风格与边界

沿用 Python 类型标注、Path、显式校验和领域异常，例如 `def validate_package(path: Path) -> dict[str, object]: ...`；前端 TypeScript 不使用 any 掩盖状态。

- 始终：保护原安装、快捷方式、数据库、人工校对和模型设置，测试使用 E 盘隔离数据。
- 先确认：直接显示余额数字所需的新供应商 API 权限、业务 Skill 的公开分享、跨版本数据库迁移变更。
- 禁止：公开密钥/私有视频/内部配置；强退运行任务；仅靠哈希宣称发布者可信；覆盖旧版 Release。

## 待用户确认

确认 API 入口使用服务商网页、内部 Skill 与公开程序分包但安装时自动绑定的交付方式。需提供当前中转站实际余额/用量后台网址；可稍后提供，不影响其他实现。
