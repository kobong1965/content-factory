import { runtimeApiBaseUrl } from './runtimeApi';
import { useEffect, useRef, useState } from 'react';
import { invoke, isTauri } from '@tauri-apps/api/core';
import './desktop-downloads.css';

const API = runtimeApiBaseUrl;

export function DesktopDownloads() {
  const [status, setStatus] = useState('');
  const [path, setPath] = useState('');
  const [error, setError] = useState(false);
  const lock = useRef(false);
  useEffect(() => {
    if (!isTauri()) return;
    const onClick = (event: MouseEvent) => {
      const anchor = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>('a[href]') : null;
      if (!anchor) return;
      const url = new URL(anchor.href);
      if (url.origin !== new URL(API).origin || url.searchParams.get('download') !== 'true') return;
      event.preventDefault();
      if (lock.current) return;
      lock.current = true; setError(false); setPath(''); setStatus('正在保存文件到本机，请稍候…');
      void (async () => {
        try {
          const response = await fetch(`${API}/local-exports`, {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({resource: url.pathname}),
          });
          const data = await response.json() as {detail?: string; path?: string};
          if (!response.ok || !data.path) throw new Error(data.detail || `保存失败（${response.status}），请重试`);
          setPath(data.path); setStatus('文件已保存');
        } catch (e) {
          setError(true); setStatus(e instanceof Error ? e.message : '保存失败，请重试');
        } finally { lock.current = false; }
      })();
    };
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, []);
  if (!status) return null;
  return <aside className="desktop-download-status" aria-label="下载状态">
    <p role={error ? 'alert' : 'status'}>{status}</p>
    {path && <><p className="desktop-download-path">{path}</p><button className="secondary-button" onClick={() => void invoke('open_download_folder').catch(() => { setError(true); setStatus('文件已保存，但文件夹未能打开。请按显示的路径查找。'); })}>打开下载文件夹</button></>}
    {!lock.current && <button className="text-button" onClick={() => {setStatus(''); setPath('');}}>关闭提示</button>}
  </aside>;
}
