import type { EditClip, EditVariant } from "@content-factory/contracts";

import { formatEditDuration } from "./editing";

export function EditTimeline({ variant, selectedClipId, selectClip, moveClip }: {
  variant: EditVariant; selectedClipId: string | null; selectClip: (id: string) => void; moveClip: (id: string, direction: -1 | 1) => void;
}) {
  const total = Math.max(1, variant.duration_ms);
  return <section className="edit-timeline" aria-labelledby="timeline-title">
    <header><h3 id="timeline-title">画面时间线</h3><span>{variant.clips.length} 段 · {formatEditDuration(variant.duration_ms)}</span></header>
    <div className="timeline-ruler" aria-hidden="true"><span>0 秒</span><span>{formatEditDuration(total / 2)}</span><span>{formatEditDuration(total)}</span></div>
    <div className="timeline-track">
      {variant.clips.map((clip) => <ClipBlock key={clip.id} clip={clip} total={total} last={clip.order === variant.clips.length} active={clip.id === selectedClipId} select={() => selectClip(clip.id)} move={(direction) => moveClip(clip.id, direction)} />)}
    </div>
    {variant.warnings.length > 0 && <ul className="timeline-warnings">{variant.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
  </section>;
}

function ClipBlock({ clip, total, last, active, select, move }: {
  clip: EditClip; total: number; last: boolean; active: boolean; select: () => void; move: (direction: -1 | 1) => void;
}) {
  const width = Math.max(12, ((clip.timeline_end_ms - clip.timeline_start_ms) / total) * 100);
  return <article className={active ? "timeline-clip timeline-clip-active" : "timeline-clip"} style={{ flexBasis: `${width}%` }}>
    <button type="button" onClick={select} aria-pressed={active}>
      <b>{String(clip.order).padStart(2, "0")}</b><span>{clip.subtitle || "无字幕"}</span><small>{formatEditDuration(clip.timeline_end_ms - clip.timeline_start_ms)} · {clip.speed}×</small>
    </button>
    <div><button type="button" aria-label="前移片段" disabled={clip.order === 1} onClick={() => move(-1)}>←</button><button type="button" aria-label="后移片段" disabled={last} onClick={() => move(1)}>→</button></div>
  </article>;
}
