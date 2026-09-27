# 原音频识别 API 备用通道验收（0.1.41）

## 这次修复的故障

本地字幕识别在模型未安装、运行时不可用或输出明显重复/乱码时，会安全停止，不生成伪造字幕。此前该错误被界面归类为一般模型连接失败，用户也没有可用的备用路径。

## 处理规则

1. 优先使用本地 ASR，保留原有本地模型和逐词时间戳能力。
2. 本地 ASR 因模型缺失、运行时故障、超时或重复/乱码被拒绝时，若已配置模型连接，则调用兼容 OpenAI 的 `/audio/transcriptions` 接口。
3. API 返回的 `words` 或 `segments` 时间统一转换为毫秒；没有可用时间信息时判为失败，不均分时间、不伪造字幕。
4. 本地和 API 都失败时，项目保留参数和素材，错误明确写出两条路径的结果；API Key 不写入错误信息和日志。
5. 新连接可以单独指定“原音识别（API 备用）”路由；旧连接仍兼容使用已有的视频复核路由作为备用。

## 自动化证据

- `services/api/tests/test_speech_fallback_040.py`: 4 passed
  - 本地 ASR 失败后转 API；
  - API 也失败时给出组合错误且不伪造成功；
  - API 逐词/分段时间归一化。
- `services/api/tests/test_speech_fallback_040.py services/api/tests/test_auto_edit_projects.py`: 21 passed
- `apps/desktop` Vitest: 30 files / 157 tests passed
- `apps/desktop` TypeScript typecheck passed
- 核心回归：Python 888 passed，桌面 156 passed，合同 17 passed，类型检查和生产构建通过；隔离目录为 `E:\Codex工作盘\temp\speech-api-fallback-0.1.41-core-final2`。

本轮没有点击真实生产项目的“重新尝试”，也没有调用付费 API；真实 API 只有在用户在模型连接设置中配置并验证后，且本地识别不可用时才会作为备用路径使用。
