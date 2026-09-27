# 自动剪辑项目 111 深度排查（0.1.41）

## 实际重试结果

已对现有项目 `auto_edit_52152156ed1c44ae891ab9d0ffb134f5` 执行一次用户要求的真实重试，未重复点击。项目包含 3 条各约 300 秒的素材，重试先逐条运行本地 ASR，随后进入 API 备用判断，最终仍在 10% 失败并保留项目参数与素材。

## 根因证据

1. 本地 ASR 进程真实启动，CPU 运行约数分钟，内存约 1.2GB；不是按钮假跑或素材读取失败。
2. 本地 worker 两次识别均触发“异常重复，重识别仍不合格”，对应日志：
   `E:\Codex工作盘\temp\gradient033-browser-04\s7\auto-edit\analysis\15d7880edca3ac82584a9cfa010cb0aa\natural-speech\speech-d7e3d2a84329402eb5babf3b81b821d3.error.log`
3. 模型连接中的 qwen3.8 配置用途只有 `analysis/script/material/video_review`，没有 `speech`；“原音识别（API 备用）”仍为未配置。
4. 使用不带真实 Key 的探针访问当前中转站的
   `POST https://maas.qianwenaiapi.com/compatible-mode/v1/audio/transcriptions`
   得到 HTTP 404。该中转模型接口只证明文字/图片能力，不能证明具备音频转写能力。

## 修复内容

- 前端合同版本号同步为 0.1.41，消除窗口显示 0.1.40、服务实际为 0.1.41 的不一致。
- 未明确配置 `speech` 路由时，不再把普通视频审核模型偷偷当成 ASR 模型发送音频。
- HTTP 404/405 显示为“未提供 /audio/transcriptions 音频转写接口”，不再笼统显示网络错误。
- API 热词字段兼容字符串和词列表，避免把字符串拆成单字发送。
- 当前运行服务健康检查：`content-factory-0.1.41`，数据 profile 保持 `0c3fd77a7126e453`。

## 验收

- 语音备用 + 自动剪辑专项：23 passed。
- 前端失败展示：6 passed；合同：17 passed；类型检查通过。
- 实际运行源码全量核心回归：889 个 Python 用例、157 个桌面行为用例、17 个合同用例全部通过；桌面类型检查与生产 Web 构建通过。隔离目录：`E:\Codex工作盘\t\active41`。
- 修复后的程序重新构建并替换到原安装目录，桌面快捷方式未改变，用户数据未替换。

要真正走 API，必须新增一个明确支持 `/audio/transcriptions` 且用途为“原音识别（API 备用）”的语音模型；当前 qwen3.8 中转配置不满足此条件。
