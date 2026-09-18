# 爆款内容工厂

Windows 桌面端直播素材分析与剪辑工具，使用 Tauri 2、React、TypeScript、Python/FastAPI、SQLite 与 FFmpeg。

主要工作区：素材分析、视频剪辑、成片素材库。内容分析结论需要证据与人工审核；不承诺流量或成交效果。

## Windows 分发

发布入口：https://github.com/kobong1965/content-factory/releases

0.1.30 为部署验证版本。公开分发文件不包含真实 API Key、个人素材、历史数据库或内部模型配置。将安装程序和同版本的三个 ZIP 配套文件放在同一个文件夹，再运行安装程序。不要手动解压或复制内部文件。

安装程序检查组件 SHA-256、自动解压 Python/媒体工具/本地模型、准备 WebView2 与 VC++ 运行库，并创建桌面快捷方式。可选择安装位置；各版本分别保存，不覆盖既有用户数据。程序未做 Windows 发布者代码签名；SHA-256 仅证明文件完整性，不能替代发布者身份验证。

Huashu 分析方法由安装程序从作者公开仓库的固定提交读取并校验，只使用固定分析提示，不运行上游脚本。由于未确认上游再分发许可，此文件不镜像到本公开仓库；首次安装需要能访问 raw.githubusercontent.com。下载失败则安装不完成并保留旧版本。

团队内部可额外提供 `model-config.cfcfg`，安装时同目录自动识别。首次启动输入单独收到的交付口令，配置随即用本机 Windows 用户凭据重新保护。已有模型配置不会被覆盖。没有内部包的用户需自行配置模型服务；网络与模型服务费用归所用服务账户。

建议 Windows 11 x64、16 GB 或更多内存，程序盘预留 12 GB 以上以及另计的视频工作空间。运行模式为 CPU，不要求独立显卡；这些是部署建议，并非所有配置实测后的最低要求。Windows 10 22H2 x64 尚未实机验收；32 位与 ARM64 不在本次分发范围。

本机隔离测试不等于全新同事电脑验收。虚拟机验证已按项目负责人要求取消。完整自动更新、失败回退与业务数据迁移的完成状态以每个 Release 的实际说明为准。

## 开发与构建

需要 Node.js 24+、pnpm（见 package.json）、Python 3.12，以及构建 Tauri 的 Rust/MSVC 工具链。Node 与 Rust 仅供开发，不是同事运行条件。

- 开发入口：`scripts/dev-tauri.ps1`。
- 桌面资源/原生构建：`scripts/build-creator-desktop.ps1 -OutputRoot <新的构建目录>`。
- 独立 Python/ASR/工具装配：`scripts/stage-windows-runtime.ps1`，所有输入路径与 Python 来源校验值均显式传入。
- 配套 ZIP：`scripts/package-delivery.py --stage <运行目录> --output <新输出目录>`。
- 安装器：`scripts/windows-installer.nsi`，指定 VERSION、OUTPUT、PYTHON、WORKER、MANIFEST 后使用 NSIS 编译。
- Python 回归：`scripts/verify-release-python.ps1 -PythonExecutable <Python路径> -RunRoot <新隔离目录>`。
- 前端回归：在 `apps/desktop` 执行 `pnpm exec vitest run --cache=false`。

历史开发脚本遵守项目 E 盘存储规则，包含开发目录默认值。安装版通过 `scripts/installed_launcher.py` 显式重设所有用户数据、媒体工具和模型路径；不能用开发启动脚本替代正式安装入口。

## 数据与回滚

同事新增数据保存在其 `%LOCALAPPDATA%\ContentFactory`。每个安装版本位于选择的程序目录 `versions/<版本>`；保留旧版本不等于任意数据库都可降级。不要将已被新版修改的数据库直接交给旧版写入。卸载/清理程序文件前自行备份工作数据。

公开源码不包含内部视频/分析数据库。测试素材生成入口：`create-s2-fixture.ps1`、`create-s3-ocr-fixture.py`、`create-s8-metric-fixture.py`，位于 scripts。
