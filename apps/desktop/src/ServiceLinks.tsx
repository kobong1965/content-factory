import { useState } from 'react';
import { invoke, isTauri } from '@tauri-apps/api/core';

export const BILLING_URL = 'https://apikey.fun/dashboard';

export function ServiceLinks() {
  const [error, setError] = useState('');
  async function openBilling() {
    setError('');
    try {
      if (isTauri()) await invoke('open_service_dashboard');
      else if (!window.open(BILLING_URL, '_blank', 'noopener,noreferrer')) {
        // Browsers may return null with noopener even when the tab opens.
        setError('如后台未打开，请使用下方地址在浏览器打开。');
      }
    } catch { setError('浏览器未能打开，请复制下方地址重试。'); }
  }
  return <section className="content" aria-label="API 余额与用量">
    <h2>APIKEY.FUN · 余额与用量</h2>
    <p>在服务商后台查看真实余额、请求记录及 Token 消耗。可能需要登录中转站账号；本软件不会传递 API Key 或代管网页登录信息。</p>
    <button className="secondary-button" type="button" onClick={() => void openBilling()}>查看余额与用量</button>
    <p><a href={BILLING_URL} target="_blank" rel="noopener noreferrer" onClick={event => { if (isTauri()) { event.preventDefault(); void openBilling(); } }}>{BILLING_URL}</a></p>
    {error && <p role="alert">{error}</p>}
  </section>;
}
