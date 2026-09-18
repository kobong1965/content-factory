import { useEffect, useRef, useState } from 'react';
import { batchMediaUrl, fetchFootageBatches, importFootageBatch, reviewLabels, saveFootageReview, saveBatchProduct, sourceAtTime, type FootageBatch, type ReviewStatus } from './footageBatches';
import { guardUnsavedTransition, useUnsavedChanges } from './unsavedChanges';
import { SubtitleEditor } from './SubtitleEditor';

const time = (ms: number) => `${Math.floor(ms / 60000).toString().padStart(2, '0')}:${(ms / 1000 % 60).toFixed(2).padStart(5, '0')}`;

function BatchSkuField({ batch, busy, save }: { batch: FootageBatch; busy: boolean; save: (sku: string) => Promise<void> }) {
  const [sku, setSku] = useState(batch.sku ?? '');
  useUnsavedChanges(sku.trim() !== (batch.sku ?? ''), '成片款号');
  return <form className="footage-actions" onSubmit={e => { e.preventDefault(); void save(sku.trim()); }}>
    <label>本批商品款号<input value={sku} maxLength={100} onChange={e => setSku(e.target.value)} placeholder="未填写款号" /></label>
    <button className="secondary-button" disabled={busy || sku.trim() === (batch.sku ?? '')}>保存款号</button>
    <span>用于本批全部成片分类，不会更改审核结果。</span>
  </form>;
}

export function FootageBatchPanel({ compact = false }: { compact?: boolean }) {
  const [batches, setBatches] = useState<FootageBatch[]>([]);
  const [batchId, setBatchId] = useState('');
  const [candidateId, setCandidateId] = useState('');
  const [note, setNote] = useState('');
  const actor = "本机";
  const [manifest, setManifest] = useState('');
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [editingSubtitles, setEditingSubtitles] = useState(false);
  const [sourceSecond, setSourceSecond] = useState<number | null>(null);
  const player = useRef<HTMLVideoElement>(null);
  const original = useRef<HTMLVideoElement>(null);
  const batch = batches.find(b => b.id === batchId) ?? batches[0];
  const candidate = batch?.candidates.find(c => c.id === candidateId) ?? batch?.candidates[0];
  const dirty = !!candidate && note !== candidate.review_note;
  useUnsavedChanges(dirty, '成片审核意见');

  useEffect(() => {
    const controller = new AbortController();
    fetchFootageBatches(controller.signal).then(result => {
      setBatches(result.batches); setNote(result.batches[0]?.candidates[0]?.review_note ?? '');
    }).catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : '批次读取失败'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);

  const act = async (operation: () => Promise<void>) => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); setMessage('');
    try { await operation(); } catch (e) { setError(e instanceof Error ? e.message : '操作失败，输入已保留'); }
    finally { lock.current = false; setBusy(false); }
  };
  const save = (status: ReviewStatus) => act(async () => {
    if (!batch || !candidate || !actor.trim()) return;
    const updated = await saveFootageReview(batch, candidate, status, note, actor.trim());
    setBatches(previous => previous.map(b => b.id === updated.id ? updated : b));
    setNote(updated.candidates.find(c => c.id === candidate.id)?.review_note ?? note);
    setMessage('审核已保存，重新打开仍可继续查看。');
  });
  const refresh = () => act(async () => {
    const result = await fetchFootageBatches(); setBatches(result.batches);
    // Explicit refresh updates the conflict revision but never discards the user's draft.
    if (!dirty) setNote(result.batches.find(b => b.id === batch?.id)?.candidates.find(c => c.id === candidate?.id)?.review_note ?? result.batches[0]?.candidates[0]?.review_note ?? '');
    setMessage('已刷新批次；未保存的意见保留在输入框中。');
  });
  const jump = (second: number) => {
    player.current?.pause(); setSourceSecond(second);
    if (original.current) original.current.currentTime = second;
  };
  if (editingSubtitles && batch && candidate) return <SubtitleEditor batchId={batch.id} candidateId={candidate.id} onClose={() => { setEditingSubtitles(false); void refresh(); }} />;
  return <section className="footage-batches workbench-surface" aria-label="实拍剪辑批次">
    <header className="footage-batch-heading"><div><h2>实拍素材剪辑 · 成片审核</h2><p>先有素材也能进入审核。每版保留选段、对标依据与主播原声。</p></div><button className="secondary-button" disabled={busy} onClick={() => void refresh()}>刷新批次</button></header>
    {!compact && <details className="footage-import"><summary>导入已有剪辑批次</summary><form onSubmit={e => { e.preventDefault(); guardUnsavedTransition(() => { void act(async () => {
      const item = await importFootageBatch(manifest.trim()); setBatches(prev => [item, ...prev.filter(b => b.id !== item.id)]);
      setBatchId(item.id); setCandidateId(item.candidates[0]?.id ?? ''); setNote(item.candidates[0]?.review_note ?? ''); setSourceSecond(null); setMessage('批次已导入，原有审核记录保留。');
    }); }); }}><label>批次文件路径<input value={manifest} onChange={e => setManifest(e.target.value)} placeholder="粘贴剪辑结果文件夹内 batch.json 的完整路径" /></label><button className="secondary-button" disabled={busy || !manifest.trim()}>导入并检查</button></form></details>}
    {error && <p role="alert" className="footage-error">{error}</p>}{message && <p role="status">{message}</p>}
    {loading ? <p role="status">正在读取剪辑批次…</p> : !batch || !candidate ? <p>还没有实拍剪辑批次。完成素材分析与剪辑后，成片及其依据会在这里审核。原脚本工程可在“脚本剪辑”中继续使用。</p> : <>
      <label className="footage-batch-select">当前批次<select value={batch.id} disabled={busy} onChange={e => guardUnsavedTransition(() => {
        const next = batches.find(b => b.id === e.target.value); setBatchId(e.target.value); setCandidateId(''); setNote(next?.candidates[0]?.review_note ?? ''); setSourceSecond(null); setError(''); setMessage('');
      })}>{batches.map(b => <option key={b.id} value={b.id}>{b.title} · {b.candidates.length} 版</option>)}</select></label>
      {!compact && <BatchSkuField key={batch.id} batch={batch} busy={busy} save={sku => act(async () => { const updated = await saveBatchProduct(batch, sku); setBatches(previous => previous.map(b => b.id === updated.id ? updated : b)); setMessage('款号已保存，已通过的成片分类同步更新。'); })} />}
      <div className="footage-version-list" aria-label="成片版本">{batch.candidates.map(c => <button key={c.id} aria-pressed={c.id === candidate.id} disabled={busy} onClick={() => guardUnsavedTransition(() => { setCandidateId(c.id); setNote(c.review_note); setSourceSecond(null); setError(''); setMessage(''); })}>
        <img src={batchMediaUrl(batch.id, c.id, 'cover')} alt={`${c.title}的原素材封面`} loading="lazy" /><span><strong>{c.title}</strong><small>{(c.duration_ms / 1000).toFixed(1)} 秒 · {reviewLabels[c.review_status]}</small></span>
      </button>)}</div>
      <div className="footage-review-grid">
        <div className="footage-player"><video ref={player} key={`${batch.id}/${candidate.id}`} controls preload="metadata" poster={batchMediaUrl(batch.id, candidate.id, 'cover')} src={batchMediaUrl(batch.id, candidate.id, 'video')} onError={() => setError('成片读取失败。请检查结果文件是否移动或变化。')} />
          <div className="footage-actions"><button className="secondary-button" onClick={() => jump(sourceAtTime(candidate.clips, player.current?.currentTime ?? 0))}>回看此刻原片</button><button className="secondary-button" onClick={() => guardUnsavedTransition(() => { player.current?.pause(); setEditingSubtitles(true); })}>编辑字幕与位置</button><a className="secondary-button" href={batchMediaUrl(batch.id, candidate.id, 'video', true)}>下载这版视频</a><a href={batchMediaUrl(batch.id, candidate.id, 'subtitle', true)}>下载字幕</a></div>
        </div>
        <div className="footage-review-details"><h3>{candidate.title}</h3><p>{candidate.hook}</p><p>借鉴对标：{candidate.benchmark_refs.join('、')}</p>
          <details><summary>本批分析结论</summary><p>{batch.analysis_summary}</p></details>
          <h3>原片选段</h3><p className="footage-source-name">{candidate.source_path.split(/[\\/]/).at(-1)}</p>
          <div className="footage-actions">{candidate.clips.map((c, i) => <button key={`${i}/${c.start_ms}`} className="secondary-button" onClick={() => jump(c.start_ms / 1000)}>第 {i + 1} 段 · {time(c.start_ms)}—{time(c.end_ms)}</button>)}</div>
          {sourceSecond !== null && <div className="footage-original"><p>原片定位：{time(sourceSecond * 1000)}</p><video ref={original} key={`${batch.id}/${candidate.id}/source`} controls preload="metadata" src={batchMediaUrl(batch.id, candidate.id, 'source')} onLoadedMetadata={e => { e.currentTarget.currentTime = sourceSecond; }} onPlay={() => player.current?.pause()} /><button className="text-button" onClick={() => setSourceSecond(null)}>收起原片</button></div>}
          <h3>审核提醒</h3><ul>{candidate.review_notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
          <label htmlFor="footage-review-note">审核意见</label><textarea id="footage-review-note" value={note} maxLength={20000} onChange={e => setNote(e.target.value)} placeholder="记录要保留的版本，或写下需要调整的时间点与字幕。" rows={4} />

          <div className="footage-actions"><button className="secondary-button" disabled={busy || !actor.trim()} onClick={() => void save('pending')}>保存意见 · 待审核</button><button className="secondary-button" disabled={busy || !actor.trim()} onClick={() => void save('changes_requested')}>标记需要修改</button><button className="primary-button" disabled={busy || !actor.trim()} onClick={() => void save('approved')}>审核通过</button></div>
          <p>审核通过仅保存你的判断，不会自动发布或投放。</p>
        </div>
      </div>
    </>}
  </section>;
}
