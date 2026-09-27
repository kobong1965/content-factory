# 爆款内容工厂 0.1.44 Windows 便携版

这是经过本机验收的 Windows x64 便携版，适合使用中转 API 的电脑。解压后双击 `启动爆款内容工厂.vbs`，不会打开可见终端。

## 包含内容

- 0.1.44 桌面程序与当前自动剪辑、语音转写 API、语义字幕修复。
- 随包 Python 3.12 运行时和已验证的 Python 依赖。
- FFmpeg 与 ffprobe。
- 基础 Whisper 模型，用于本地媒体分析的基础转写能力。
- WebView2 与 VC++ x64 安装包（位于 `prerequisites`，仅在电脑缺少时安装）。

## 首次使用

1. 将整个目录解压到一个有写入权限的位置，不要只解压其中一个文件。
2. 双击 `启动爆款内容工厂.vbs`。
3. 在“模型连接设置”中添加图文模型和“语音转写模型”。
4. 语音模型需要实际支持 `/audio/transcriptions`，并返回 `words` 或带时间的 `segments`。本版会自动兼容不带 `/v1` 和带 `/v1` 的中转地址。
5. 自动剪辑项目的字幕模式选择“普通电影字幕 · 口播同步”或“自动混合三种模式”。

用户数据默认保存到 `%LOCALAPPDATA%\ContentFactory`，不会写入发布包目录，也不会覆盖其他安装版本。API Key 只保存在本机用户配置中，发布包不包含任何密钥、项目数据库、视频素材或缓存。

## 中转站示例

接口基地址可以填写：

```text
https://api.apikey.fan
```

软件会尝试 `/audio/transcriptions` 和 `/v1/audio/transcriptions`。请从中转站模型目录选择明确支持音频转写的模型，不能用只支持文字/图片的模型代替语音识别。

## 依赖与限制

- Windows x64；首次运行建议使用 Windows 10 22H2 或 Windows 11。
- 不包含用户的 API Key、素材、数据库、历史任务、内部 Skill 或本地 large-v3-turbo 模型。
- large-v3-turbo 约 1.5 GB，属于可选本地 ASR 组件；使用中转 API 时不需要它。
- Windows Defender 的“未知发布者”提示是因为当前发布包未做 Authenticode 代码签名；下载后请先校验 SHA-256。

## 完整性校验

同目录的 `SHA256SUMS.txt` 记录发布包内的关键文件哈希。GitHub Release 页面也会显示每个上传资产的 SHA-256 摘要。
