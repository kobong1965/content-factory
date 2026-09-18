import { useEffect, useState } from "react";
import type { Publication } from "@content-factory/contracts";

export function BusinessClosureCard({ publication, snapshotCount, busy, confirm }: {
  publication: Publication; snapshotCount: number; busy: boolean;
  confirm: (publicationId: string, expectedRevision: number, actor: string, note: string) => Promise<unknown>;
}) {
  const actor = "本机";
  const [note, setNote] = useState("");
  useEffect(() => { setNote(""); }, [publication.publication_id]);
  const review = publication.business_review ?? { status: "pending" as const, reviewed_by: null, reviewed_at: null, note: "" };
  if (review.status === "confirmed") {
    return <aside className="business-closure business-closure-done">
      <strong>业务复盘已确认</strong>
      <span>{review.reviewed_at ? new Date(review.reviewed_at).toLocaleString("zh-CN") : ""}</span>
      <p>{review.note}</p>
      {publication.fixture_data && <small>这是隔离样例，不计入真实闭环。</small>}
    </aside>;
  }
  return <aside className="business-closure">
    <div><strong>最后一关：由业务人员确认闭环</strong><span>{snapshotCount}/2 个不同时刻快照</span></div>
    {snapshotCount < 2 ? <p>再保存 {2 - snapshotCount} 个已确认快照后，才能确认这次复盘已经完成。</p> : <div className="business-closure-form">

      <label>复盘确认说明<textarea value={note} maxLength={1000} placeholder="确认已看过数据、结论和下一步测试" onChange={(event) => setNote(event.target.value)} /></label>
      <button className="primary-button" type="button" disabled={busy || !actor.trim() || !note.trim()} onClick={() => void confirm(publication.publication_id, publication.revision, actor.trim(), note.trim())}>确认本次业务闭环</button>
      {publication.fixture_data && <small>可用于界面验收，但不会计入真实闭环。</small>}
    </div>}
  </aside>;
}
