import { useEffect, useState } from 'react';
import { PROJECT_VERSION } from '@content-factory/contracts';
import { runtimeApiBaseUrl } from './runtimeApi';

type UpdateState = {phase: string; current_version?: string; version?: string; message: string; notes?: string; downloaded: number; total: number};

export function SoftwareUpdates() {
  const [state, setState] = useState<UpdateState>({phase:'idle', message:'', downloaded:0, total:0});
  const [preview, setPreview] = useState(false);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  async function read() {
    const response = await fetch(`${runtimeApiBaseUrl}/software-updates`);
    if (!response.ok) throw new Error('无法读取更新状态，请检查本机服务。');
    setState(await response.json());
  }
  useEffect(() => {
    let active = true;
    const refresh = () => { if (active) void read().catch(e => { if (active) setError(String(e.message)); }); };
    refresh(); const timer = window.setInterval(refresh, 1500);
    return () => {active=false;window.clearInterval(timer);};
  }, []);
  async function act(action: string) {
    if (submitting) return;
    setSubmitting(true);setError('');
    try {
      const response = await fetch(`${runtimeApiBaseUrl}/software-updates/${action}${action==='check'?`?preview=${preview}`:''}`, {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'更新操作失败，请重试。');
      setState(data);
    } catch(e) {setError(e instanceof Error?e.message:'更新操作失败，请重试。');}
    finally {setSubmitting(false);}
  }
  const busy = submitting || ['checking','downloading','installing'].includes(state.phase);
  return <section className="content" aria-label="版本与更新">
    <h1>版本与更新</h1><p>当前版本 {state.current_version || PROJECT_VERSION} · Windows 桌面版</p>
    <p>更新来源：kobong1965/content-factory。仅安装通过签名清单校验的程序，不覆盖你的素材、Skill 和模型设置。</p>
    <label><input type="checkbox" checked={preview} disabled={busy} onChange={e=>setPreview(e.target.checked)} />包含测试版本（默认只检查稳定版）</label>
    <div className="editor-actions"><button type="button" className="secondary-button" disabled={busy} onClick={()=>void act('check')}>检查更新</button>
      {state.phase==='available'&&<button type="button" className="primary-button" disabled={busy} onClick={()=>void act('download')}>下载 {state.version}</button>}
      {state.phase==='ready'&&<><button type="button" className="primary-button" disabled={busy} onClick={()=>void act('install')}>安装更新</button><span>也可以稍后再安装，下载文件会保留。</span></>}
    </div>
    {state.message&&<p role="status">{state.message}</p>}
    {state.phase==='downloading'&&<><progress value={state.downloaded} max={state.total||1} aria-label="更新下载进度"/><p>{(state.downloaded/1024**2).toFixed(1)} / {(state.total/1024**2).toFixed(1)} MB</p></>}
    {state.phase==='installing'&&<p>请关闭本软件窗口。后台将在退出后安装并重新打开，当前数据已备份；此时不要关闭电脑。</p>}
    {error&&<p role="alert">{error}</p>}
    {state.notes&&<details open><summary>更新说明 · {state.version}</summary><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',fontFamily:'inherit'}}>{state.notes}</pre></details>}
  </section>;
}
