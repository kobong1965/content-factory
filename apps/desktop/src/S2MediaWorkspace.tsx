import { FileDropInput } from "./FileDropInput";
import { useRef, useState, type FormEvent } from "react";

import { completionPercent } from "./media";
import { ConfirmationDialog } from "./ConfirmationDialog";
import { TaskQueue } from "./TaskQueue";
import { WorkspaceHeader } from "./WorkspaceHeader";
import type { useS2Media } from "./useS2Media";

const capabilityLabels = [
  ["ffmpeg_ready", "视频转码"], ["ffprobe_ready", "视频信息读取"],
  ["whisper_filter_ready", "语音识别引擎"], ["whisper_model_ready", "本地语音模型"],
  ["queue_ready", "四路任务队列"],
] as const;

export function S2MediaWorkspace({ media, compact = false }: { media: ReturnType<typeof useS2Media>; compact?: boolean }) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [link, setLink] = useState("");
  const [showQueueConfirm, setShowQueueConfirm] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const realPercent = completionPercent(media.readiness.accepted_real_videos, media.readiness.required_real_videos);

  function handleFileSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selectedFile) setShowQueueConfirm(true);
  }

  async function confirmFileImport() {
    if (!selectedFile) return;
    const imported = await media.importFile(selectedFile);
    if (imported) {
      setSelectedFile(null);
      if (fileInput.current) fileInput.current.value = "";
      setShowQueueConfirm(false);
    }
  }

  function handleLinkSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (link.trim()) void media.checkLink(link.trim());
  }

  return <div className={`content ${compact ? 'simplified-upload' : ''}`}>
    {!compact && <WorkspaceHeader
      stage="S2 · 视频处理"
      title="本地媒体队列"
      description="在本机生成代理视频、语音文字、镜头边界和关键帧；原片不上云，中断任务可自动恢复。"
      current={<><strong>处理状态</strong><span>{media.readiness.pending_reason ?? "媒体处理能力与真实视频验收均已满足"}</span></>}
      metrics={[
        { label: "等待", value: media.readiness.pending_tasks, tone: media.readiness.pending_tasks ? "warning" : "default" },
        { label: "处理中", value: media.readiness.running_tasks, tone: media.readiness.running_tasks ? "warning" : "default" },
        { label: "已完成", value: media.readiness.completed_tasks, tone: "success" },
      ]}
      action={<button className="primary-button" type="button" onClick={() => document.getElementById("video-file")?.click()}>导入本机视频</button>}
    />}
    <ConfirmationDialog
      open={showQueueConfirm}
      title="确认加入本机处理队列"
      description="开始后会在本机生成代理视频、转写、镜头边界和关键帧；原片不会上传到云端。"
      confirmLabel="确认并开始处理"
      busy={media.isSubmitting}
      details={selectedFile && <><strong>{selectedFile.name}</strong><span>{(selectedFile.size / 1024 / 1024).toFixed(1)} MB</span></>}
      onClose={() => setShowQueueConfirm(false)}
      onConfirm={() => void confirmFileImport()}
    />

    <section className="import-grid compact-import-grid" aria-label="视频导入方式">
      <form className="import-card import-card-primary" onSubmit={handleFileSubmit}>
        <div><h2>选择电脑里的视频</h2><p>支持 MP4、MOV、MKV、AVI、WEBM、M4V。</p></div>
        <label className="file-picker" htmlFor="video-file"><span>{selectedFile?.name ?? "还没有选择文件"}</span><strong>选择视频</strong></label>
        <FileDropInput ref={fileInput} id="video-file" className="visually-hidden" type="file" accept="video/mp4,video/quicktime,video/x-matroska,video/x-msvideo,video/webm,.m4v" onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)} />
        <button className="primary-button" type="submit" disabled={!selectedFile || media.isSubmitting}>加入处理队列</button>
      </form>
      {!compact && <form className="import-card" onSubmit={handleLinkSubmit}>
        <div><h2>检查抖音链接</h2><p>只识别地址，不抓取平台内容；请使用有权使用的原视频。</p></div>
        <label htmlFor="douyin-link">抖音视频链接</label>
        <input id="douyin-link" type="url" value={link} placeholder="https://v.douyin.com/..." onChange={(event) => setLink(event.target.value)} />
        <button className="secondary-button" type="submit" disabled={!link.trim() || media.isSubmitting}>检查链接</button>
      </form>}
    </section>

    {media.actionMessage && <div className="action-message" role="status" aria-live="polite">{media.actionMessage}</div>}

    <section aria-labelledby="queue-title">
      <div className="section-heading">
        <h2 id="queue-title">处理队列</h2>
        <p className="section-copy">等待 {media.readiness.pending_tasks} · 处理中 {media.readiness.running_tasks} · 已完成 {media.readiness.completed_tasks}</p>
      </div>
      <TaskQueue tasks={media.tasks} />
    </section>

    {!compact && <section aria-labelledby="s2-acceptance-title">
      <div className="section-heading">
        <h2 id="s2-acceptance-title">工程能力与真实验收</h2>
        <span className={`section-state ${media.readiness.engineering_ready ? "state-ready" : "state-pending"}`}>{media.readiness.engineering_ready ? "工程已通过" : "工程待检查"}</span>
      </div>
      <div className="acceptance-grid">
        <article className="capability-card"><h3>本机处理能力</h3><ul>{capabilityLabels.map(([key, label]) => <li key={key}><span className={media.readiness[key] ? "capability-ready" : "capability-pending"} aria-hidden="true">{media.readiness[key] ? "已" : "待"}</span><strong>{label}</strong><small>{media.readiness[key] ? "已就绪" : "未就绪"}</small></li>)}</ul></article>
        <article className="real-validation-card">
          <div className="progress-heading"><span>真实男装视频</span><strong>{media.readiness.accepted_real_videos} / {media.readiness.required_real_videos}</strong></div>
          <div className="progress-track" role="progressbar" aria-label="真实视频验收进度" aria-valuemin={0} aria-valuemax={media.readiness.required_real_videos} aria-valuenow={media.readiness.accepted_real_videos}><span style={{ width: `${realPercent}%` }} /></div>
          <p>工程测试样片不计入这里。至少 9/10 条获授权真实视频跑通后，才算 S2 业务验收通过。</p>
        </article>
      </div>
    </section>}
  </div>;
}
