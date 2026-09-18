import { useEffect, useState } from "react";
import type { EditVariant, RenderOutput } from "@content-factory/contracts";

import { formatEditDuration, outputResourceUrl, outputStatusLabels } from "./editing";

export function FinishedVideoPanel({ outputs, variants, busy, review }: {
  outputs: readonly RenderOutput[]; variants: readonly EditVariant[]; busy: boolean;
  review: (outputId: string, decision: "approved" | "rejected", reviewer: string, note: string) => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(outputs[0]?.output_id ?? null);
  const reviewer = "本机";
  const [note, setNote] = useState("");
  useEffect(() => { if (!outputs.some((item) => item.output_id === selectedId)) setSelectedId(outputs[0]?.output_id ?? null); }, [outputs, selectedId]);
  const selected = outputs.find((item) => item.output_id === selectedId) ?? null;
  const variant = variants.find((item) => item.id === selected?.variant_id);
  return <section className="finished-video-panel" aria-labelledby="finished-video-title">
    <header><h2 id="finished-video-title">成片验收</h2><p>这里通过的是成片，不等于已经发布；发布和数据回流属于 S8。</p></header>
    {outputs.length === 0 || !selected ? <div className="edit-inline-empty">成片渲染完成后会出现在这里。</div> : <div className="video-review-grid">
      <aside><ul>{outputs.map((output) => <li key={output.output_id}><button type="button" className={selected.output_id === output.output_id ? "output-active" : ""} onClick={() => setSelectedId(output.output_id)}><span><strong>{variants.find((item) => item.id === output.variant_id)?.name ?? output.media.filename}</strong><small>{formatEditDuration(output.media.duration_ms)} · {(output.media.size_bytes / 1024 / 1024).toFixed(1)} MB</small></span><em className={`edit-status edit-status-${output.status}`}>{outputStatusLabels[output.status]}</em></button></li>)}</ul></aside>
      <div className="finished-player"><video key={selected.output_id} controls preload="metadata" src={outputResourceUrl(selected, "video_ref")} /><div><strong>{variant?.name ?? selected.media.filename}</strong><span>{selected.media.width}×{selected.media.height} · {selected.media.fps.toFixed(2)}fps · {selected.media.video_codec.toUpperCase()}/{selected.media.audio_codec.toUpperCase()}</span></div></div>
      <div className="delivery-panel">
        <h3>交付文件</h3>
        <a href={outputResourceUrl(selected, "video_ref", true)}>下载字幕成片</a>
        <a href={outputResourceUrl(selected, "clean_video_ref", true)}>下载无字幕干净版</a>
        <a href={outputResourceUrl(selected, "subtitle_ref", true)}>下载 SRT 字幕</a>
        <a href={outputResourceUrl(selected, "project_ref", true)}>下载可编辑工程 JSON</a>
        <a href={outputResourceUrl(selected, "jianying_package_ref", true)}>下载剪映实验交接包</a>
        <small>剪映交接包不是原生草稿；稳定可编辑源是工程 JSON。</small>
        {selected.review.status === "pending" ? <div className="video-review-form">

          <label>意见<textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="驳回时必须填写原因" /></label>
          <div><button className="secondary-button danger-button" type="button" disabled={busy || !reviewer.trim() || !note.trim()} onClick={() => review(selected.output_id, "rejected", reviewer.trim(), note.trim())}>驳回修改</button><button className="primary-button" type="button" disabled={busy || !reviewer.trim()} onClick={() => review(selected.output_id, "approved", reviewer.trim(), note.trim())}>通过成片</button></div>
        </div> : <div className={`review-result review-result-${selected.review.status}`}><strong>{outputStatusLabels[selected.status]}</strong><span>{selected.review.note || "无补充意见"}</span></div>}
      </div>
    </div>}
  </section>;
}
