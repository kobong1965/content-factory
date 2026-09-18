# 0.1.30 程序组件说明

- 桌面：Tauri 2 / React；WebView2 使用微软离线安装组件。
- 后端：独立 CPython 3.12.14 / FastAPI / SQLite；ASR 使用另一套独立 Python，避免 NumPy/ONNX 版本冲突。Python 的许可文件随运行时保留。
- 媒体：Gyan FFmpeg 8.1.2 full build / FFprobe；GPLv3 许可和该构建的源码提交、编译选项链接位于 licenses/ffmpeg。FFmpeg 为独立子进程调用。
- 语音：Whisper tiny 与 large-v3-turbo 本地模型；CPU 运行，首次使用不下载模型。相关模型卡与 Whisper MIT 许可位于 licenses/whisper。
- OCR：现有 RapidOCR/ONNX 模型随 API Python 依赖提供，不新增无关 OCR 服务。
- UI 字体：Noto Sans SC 内嵌于桌面资源，OFL 许可随包提供。字幕字体依赖目标 Windows 中的雅黑/黑体/宋体/楷体；不重新分发微软字体。在没有这些中文字体的系统上，字体外观尚未验收。
- Huashu：安装时从作者固定提交获取并验证 SKILL.md；不运行上游脚本，不将未明确许可的文件镜像到公开发布包。
- Node、Rust、Visual Studio：只用于构建，同事不需要安装。
- API 配置：公开包没有真实 Key。内部配置包与口令分开交付，第一次输入口令后保存到目标用户的 Windows DPAPI 保护配置。

已有素材及分析数据库不在公开组件包中；内部数据迁移包仍需独立完成引用重定位和一致性检查，不能将原目录快照当作已完成的迁移包。
