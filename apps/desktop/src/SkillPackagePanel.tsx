import { useEffect, useRef, useState } from 'react';
import { FileDropInput } from './FileDropInput';
import { ViralSkillLibrary } from './ViralSkillLibrary';
import { runtimeApiBaseUrl as base } from './runtimeApi';
import { useUnsavedChanges } from './unsavedChanges';

type Skill = { skill_id: string; name: string; mechanism: string; revision: number; status?: string; reuse_mode?: string };
type Preview = { package_id: string; skills: Skill[]; source_videos_included: boolean };
async function response<T>(res: Response): Promise<T> {
  const value = await res.json();
  if (!res.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'Skill 包操作失败，请检查文件后重试');
  return value as T;
}

export function SkillPackagePanel() {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [review, setReview] = useState(false);
  const lock = useRef(false);
  useUnsavedChanges(Boolean(preview), '待确认的 Skill 导入');
  useEffect(() => {
    const controller = new AbortController();
    fetch(`${base}/s3/skills`, { signal: controller.signal }).then(response<Skill[]>).then(setSkills)
      .catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : '读取 Skill 失败'); });
    return () => controller.abort();
  }, [revision]);
  async function action(work: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); setMessage('');
    try { await work(); } catch (e) { setError(e instanceof Error ? e.message : '操作失败，待确认内容已保留'); }
    finally { lock.current = false; setBusy(false); }
  }
  const reusable = skills.filter(s => s.status === 'approved' && s.reuse_mode === 'reuse');
  return <section className="skill-package-panel" aria-label="剪辑 Skill 库">
    <header className="section-heading"><h2>已分析的剪辑 Skill <span className="assistive">{reusable.length} 个可用</span></h2><button className="secondary-button" onClick={() => setReview(!review)}>{review ? '收起方法审核' : '管理 / 审核方法'}</button></header>
    <div className="skill-package-upload"><label>导入 Codex 整理的 Skill 包<FileDropInput accept=".cfskills,.zip" disabled={busy} onChange={e => {
      const file = e.target.files?.[0]; e.target.value = '';
      if (file) void action(async () => { const body = new FormData(); body.append('package', file); setPreview(await response<Preview>(await fetch(`${base}/s3/skill-packages/preview`, { method:'POST', body }))); });
    }} /></label><p className="assistive">支持 .cfskills，或包含 references/**/software-skill-current.json 的 Codex ZIP（ZIP 内的视频和关键帧只作证据，不会写入 Skill 包）。先查看方法，再确认加入；不执行包内脚本。单独的 SKILL.md 请先整理成带审核证据的包。</p></div>
    {error && <p role="alert" className="workspace-alert is-error">{error}</p>}{message && <p role="status" className="workspace-alert">{message}</p>}
    {busy && <p role="status">正在校验 Skill 包…</p>}
    {preview && <div className="skill-import-review"><h3>待确认 · {preview.skills.length} 个方法</h3><p>此包包含方法与文字证据，不包含原视频。重复方法保留本机版本。</p><ul>{preview.skills.map(s => <li key={s.skill_id}><strong>{s.name}</strong><p>{s.mechanism}</p></li>)}</ul><div className="asset-actions"><button className="primary-button" disabled={busy} onClick={() => void action(async () => {
      const result = await response<{imported:number;preserved:string[]}>(await fetch(`${base}/s3/skill-packages/approve`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({package_id:preview.package_id,confirmed:true})}));
      setMessage(`已加入 ${result.imported} 个方法；重复或本机已修改的方法不会覆盖。`); setPreview(null); setRevision(v => v+1);
    })}>确认加入剪辑方法库</button><button className="secondary-button" disabled={busy} onClick={() => setPreview(null)}>取消导入</button></div></div>}
    {reusable.length === 0 ? <p className="empty-state">还没有可用方法。导入已整理的 Skill 包，或分析对标视频并审核后，智能体才能自动选用。</p> : <div className="skill-method-list">{reusable.map(s => <article key={s.skill_id}><div><h3>{s.name}</h3><small>已审核 · v{s.revision}</small></div><p>{s.mechanism}</p></article>)}</div>}
    {review && <ViralSkillLibrary key={revision} />}
  </section>;
}
