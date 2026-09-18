import { useEffect, useState } from 'react';
import { batchMediaUrl, fetchFinishedLibrary, fetchFootageBatches, reviewLabels } from './footageBatches';
import { finishedFolders } from './finishedFolders';
import { archiveUrl, libraryApi, libraryOrganization, organizeLibrary, type LibraryEntry, type LibraryOrganization } from './libraryManagement';
import './finished-library.css';

export function FinishedLibrary({ openReview, refreshToken = 0 }: { openReview: () => void; refreshToken?: number }) {
  const [folders, setFolders] = useState<ReturnType<typeof finishedFolders>>([]);
  const [organization, setOrganization] = useState<LibraryOrganization>({ revision: 0, entries: {} });
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [trash, setTrash] = useState(false);
  const [query, setQuery] = useState('');
  const [group, setGroup] = useState('');
  const [sort, setSort] = useState('newest');
  const [checked, setChecked] = useState<string[]>([]);
  const [editor, setEditor] = useState<{ keys: string[]; mode: 'title' | 'group'; value: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string[] | null>(null);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError('');
    Promise.all([fetchFinishedLibrary(controller.signal), fetchFootageBatches(controller.signal), libraryOrganization(controller.signal)]).then(([data, batches, metadata]) => {
      if (controller.signal.aborted) return;
      setFolders(finishedFolders(data, batches.batches)); setOrganization(metadata);
      if (data.unavailable.length) setError(`${data.unavailable.length} 条历史成片文件需要恢复，请在审核页检查。`);
    }).catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : '成片读取失败，请重试'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [revision, refreshToken]);
  const meta = (key: string) => organization.entries[key] ?? {};
  const name = (key: string, fallback: string) => meta(key).title ?? fallback;
  const folder = folders.find(f => f.id === selected);
  const change = async (keys: string[], value: LibraryEntry) => {
    if (busy) return;
    setBusy(true); setError('');
    try {
      setOrganization(await organizeLibrary(organization.revision, keys, value));
      setEditor(null); setConfirmDelete(null); setChecked([]);
      setMessage(value.deleted === true ? '已移入回收站，可随时恢复；原始录播和审核记录保留。' : value.deleted === false ? '已恢复。' : '整理已保存。');
    } catch (e) { setError(e instanceof Error ? e.message : '操作失败，内容已保留'); }
    finally { setBusy(false); }
  };
  const enter = (id: string | null) => { setSelected(id); setChecked([]); setQuery(''); setEditor(null); setConfirmDelete(null); };
  const toggle = (key: string) => setChecked(current => current.includes(key) ? current.filter(k => k !== key) : [...current, key]);
  const isVisible = (key: string, title: string) => Boolean(meta(key).deleted) === trash && name(key, title).toLocaleLowerCase().includes(query.toLocaleLowerCase());
  const visibleFolders = folders.filter(f => isVisible(f.id, f.title) && (!group || meta(f.id).group === group));
  if (sort === 'name') visibleFolders.sort((a, b) => name(a.id, a.title).localeCompare(name(b.id, b.title), 'zh-CN'));
  const items = folder?.items.filter(i => meta(folder.id).deleted ? name(`${i.batch_id}/${i.candidate.id}`, i.candidate.title).includes(query) : isVisible(`${i.batch_id}/${i.candidate.id}`, i.candidate.title)) ?? [];
  if (sort === 'name') items.sort((a, b) => name(`${a.batch_id}/${a.candidate.id}`, a.candidate.title).localeCompare(name(`${b.batch_id}/${b.candidate.id}`, b.candidate.title), 'zh-CN'));
  const visibleKeys = folder ? items.map(i => `${i.batch_id}/${i.candidate.id}`) : visibleFolders.map(f => f.id);
  const choose = (keys: string[], mode: 'title' | 'group', value = '') => { setEditor({keys, mode, value}); setConfirmDelete(null); };
  const actions = (key: string, title: string) => <div className="asset-actions">
    {trash ? <button className="secondary-button" disabled={busy || Boolean(folder && meta(folder.id).deleted)} onClick={() => void change([key], { deleted: false })}>恢复</button> : <>
      <button className="text-button" disabled={busy} onClick={() => choose([key], 'title', name(key, title))}>重命名</button>
      <button className="text-button danger-text" disabled={busy} onClick={() => setConfirmDelete([key])}>删除</button>
    </>}
  </div>;
  return <div className="content finished-library">
    <header className="creator-heading"><div><h1 data-page-title tabIndex={-1}>{folder ? name(folder.id, folder.title) : trash ? '成片回收站' : '成片素材库'}</h1><p>{folder ? `本批 ${items.length} 条成片，审核状态独立保留。` : `每批剪辑结果一个文件夹 · 共 ${visibleFolders.length} 批`}</p></div><div className="asset-actions"><button className="secondary-button" disabled={loading || busy} onClick={() => setRevision(v => v + 1)}>刷新成片</button><button className="secondary-button" onClick={openReview}>检查 / 审核成片</button></div></header>
    <div className="library-toolbar">
      {selected !== null && <button className="secondary-button" onClick={() => enter(null)}>返回全部批次</button>}
      <button className="secondary-button" aria-pressed={trash} onClick={() => { setTrash(!trash); enter(null); setGroup(''); }}>{trash ? '返回成片库' : '回收站'}</button>
      <input aria-label="搜索成片" placeholder="搜索批次或成片名称" value={query} onChange={e => {setQuery(e.target.value); setChecked([]);}} />
      {!folder && <select aria-label="筛选分类" value={group} onChange={e => {setGroup(e.target.value); setChecked([]);}}><option value="">全部分类</option>{[...new Set(Object.values(organization.entries).map(e => e.group).filter(Boolean))].map(g => <option key={g}>{g}</option>)}</select>}
      <select aria-label="排列方式" value={sort} onChange={e => setSort(e.target.value)}><option value="newest">最新批次优先</option><option value="name">按名称排列</option></select>
      {folder && !trash && <a className="primary-button" href={archiveUrl(folder.id)}>下载整批 ZIP</a>}
    </div>
    {error && <p role="alert" className="footage-error">{error}</p>}
    {message && <p role="status">{message}</p>}
    {trash && <p className="assistive">这里可以恢复删除的批次和文件。删除不会清理磁盘原文件，也不会删除原始录播。查看单条已删除成片，请进入对应批次。</p>}
    {loading ? <p role="status">正在读取成片批次…</p> : <>
      <div className="library-toolbar"><label><input type="checkbox" checked={visibleKeys.length > 0 && visibleKeys.every(k => checked.includes(k))} onChange={e => setChecked(e.target.checked ? visibleKeys : [])} />全选当前列表</label><span>已选 {checked.length} 项</span>
        {checked.length > 0 && (trash ? <button disabled={busy || Boolean(folder && meta(folder.id).deleted)} className="secondary-button" onClick={() => void change(checked, { deleted: false })}>恢复所选</button> : <><button disabled={busy} className="secondary-button" onClick={() => setConfirmDelete(checked)}>删除所选</button>{!folder && <button disabled={busy} className="secondary-button" onClick={() => choose(checked, 'group')}>设置分类</button>}</>)}
      </div>
      {editor && <form className="library-inline-editor" onSubmit={e => {e.preventDefault(); void change(editor.keys, {[editor.mode]: editor.value});}}><label>{editor.mode === 'title' ? '新名称' : '分类名称（例如款号 J85）'}<input autoFocus required={editor.mode === 'title'} maxLength={editor.mode === 'title' ? 100 : 80} value={editor.value} onChange={e => setEditor({...editor, value:e.target.value})} /></label><button className="primary-button" disabled={busy}>保存整理</button><button type="button" className="secondary-button" disabled={busy} onClick={() => setEditor(null)}>取消</button></form>}
      {confirmDelete && <div role="alert" className="library-inline-editor"><p>将选中的 {confirmDelete.length} 项移入回收站？可恢复，原始录播不受影响。</p><button className="secondary-button danger-text" disabled={busy} onClick={() => void change(confirmDelete, {deleted:true})}>确认移入回收站</button><button className="secondary-button" disabled={busy} onClick={() => setConfirmDelete(null)}>取消</button></div>}
      {selected === null && <div className="batch-folder-list">{visibleFolders.map(f => <article className="library-folder-card" key={f.id}>
        <label className="library-selection"><input type="checkbox" aria-label={`选择批次 ${name(f.id,f.title)}`} checked={checked.includes(f.id)} onChange={() => toggle(f.id)} />{meta(f.id).group || '未分类'}</label>
        <button type="button" className="batch-folder" onClick={() => enter(f.id)} aria-label={`打开批次 ${name(f.id,f.title)}`}><svg width="40" height="36" viewBox="0 0 40 36" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M3 10V5h12l4 5h18v22H3Z"/><path d="M3 14h34"/></svg><span><strong>{name(f.id,f.title)}</strong><small>{f.items.filter(i => !meta(`${f.id}/${i.candidate.id}`).deleted).length} 条成片 · {f.items.filter(i => i.candidate.review_status === 'approved' && !meta(`${f.id}/${i.candidate.id}`).deleted).length} 条审核通过</small></span></button>
        <div className="asset-actions">{!trash && <><a className="secondary-button" href={archiveUrl(f.id)}>下载整批</a><button className="text-button" onClick={() => choose([f.id], 'group', meta(f.id).group ?? '')}>分类</button></>}{actions(f.id,f.title)}</div>
      </article>)}</div>}
      {trash && !folder && folders.filter(f => !meta(f.id).deleted && f.items.some(i => meta(`${f.id}/${i.candidate.id}`).deleted)).map(f => <button key={f.id} className="secondary-button" onClick={() => enter(f.id)}>查看“{name(f.id,f.title)}”中删除的成片</button>)}
      {folder && <div className="finished-grid finished-group">{items.map(({batch_id,candidate,video_url,subtitle_url}) => {
        const key = `${batch_id}/${candidate.id}`;
        return <article key={key} className="finished-item"><label className="library-selection"><input type="checkbox" aria-label={`选择成片 ${name(key,candidate.title)}`} checked={checked.includes(key)} onChange={() => toggle(key)} />选择</label><video controls preload="none" poster={video_url ? undefined : batchMediaUrl(batch_id,candidate.id,'cover')} src={video_url ? libraryApi+video_url : batchMediaUrl(batch_id,candidate.id,'video')} onError={() => setError('成片文件无法播放，请到审核页检查文件。')} /><h3>{name(key,candidate.title)}</h3><p>{(candidate.duration_ms/1000).toFixed(1)} 秒 · {reviewLabels[candidate.review_status]}</p><div className="asset-actions">{!trash && <><a className="primary-button" href={video_url ? libraryApi+video_url+'?download=true' : batchMediaUrl(batch_id,candidate.id,'video',true)}>下载成片</a><a href={subtitle_url ? libraryApi+subtitle_url+'?download=true' : batchMediaUrl(batch_id,candidate.id,'subtitle',true)}>字幕文件</a></>}{actions(key,candidate.title)}</div></article>;
      })}</div>}
      {!visibleKeys.length && <p className="empty-state">当前列表没有匹配的文件。</p>}
    </>}
  </div>;
}
