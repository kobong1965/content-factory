# 爆款内容工厂 0.1.30 安装说明

这是 Windows 64 位部署验证套件，不需要虚拟机，也不需要安装 Python、Node 或 FFmpeg 开发工具。

1. 保持以下四个文件在同一个文件夹，不要手动解压 ZIP：
   - content-factory-0.1.30-setup.exe
   - content-factory-0.1.30-program.zip
   - content-factory-0.1.30-speech-model.zip
   - content-factory-0.1.30-prerequisites.zip
2. 若公司另给了 model-config.cfcfg，将它也放在这个文件夹。交付口令应从内部渠道单独获取，不随公开下载提供。
3. 双击 setup，选择安装位置，等待安装完成。首次安装需要联网获取固定版本分析方法；如果缺少微软运行组件，安装程序会自动处理，VC++ 组件可能请求 Windows 系统授权。
4. 从桌面“爆款内容工厂”打开。若有内部配置包，首次输入交付口令即可导入模型设置，不必逐条填写 API Key。已存在的模型配置不会被覆盖。
5. 确认右上角显示“本机服务已连接”。然后使用“素材分析 / 视频剪辑 / 成片素材库”。这是空白工作资料环境，历史素材不会随公开安装套件出现。

新资料默认保存在本机用户的 AppData/Local/ContentFactory。导出视频在该资料目录下的 exports 文件夹，可通过软件打开下载目录。不要把自己的资料目录发到公开 GitHub。

建议 Windows 11 x64、至少 16GB 内存，程序盘预留 12GB 以上，视频素材空间另外计算。可以使用 CPU，不强制要求独立显卡。运行云端模型仍需网络与有效账号，费用从已配置的模型服务账户扣除。

安装失败时查看所选安装目录中的 installation-error.txt。启动失败时软件会提示诊断文件位置。反馈问题前不要发送 API Key 或交付口令。

此版本完成了本机隔离安装验证，未做另一台电脑或重启验收；应用内自动更新和历史资料自动迁移尚未完成。安装器没有 Windows 发布者代码签名，SHA256SUMS.txt 仅用于检查文件完整性。完整范围见 DEPLOYMENT-TESTS.md。
