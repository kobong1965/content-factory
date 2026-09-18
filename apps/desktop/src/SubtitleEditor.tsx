import { runtimeApiBaseUrl } from './runtimeApi';
import { useEffect, useRef, useState } from 'react';
import { guardUnsavedTransition, useUnsavedChanges } from './unsavedChanges';
import './subtitle-editor.css';

type Word = { text: string; start_ms: number; end_ms: number; probability?: number };
type Cue = { start_ms: number; end_ms: number; text: string; words?: Word[]; uncertain?: boolean; original_text?: string };
type Document = { cues: Cue[]; x: number; y: number; font: string; size: number; effect: string; keywords?: string[]; keyword_color?: string; keyword_scale?: number; mode?: 'reveal' | 'sentence' | 'highlight'; hotwords?: string };
type CaptionEvent = { start_ms: number; end_ms: number; text: string; highlight?: string };
type Draft = { revision: number; document: Document; duration_ms: number };
type Suggestion = { index: number; text: string; reason: string; uncertain: boolean };
const fonts: Record<string, string> = { yahei: 'Microsoft YaHei', heiti: 'SimHei', songti: 'SimSun', kaiti: 'KaiTi' };

export function SubtitleEditor({ batchId, candidateId, onClose }: { batchId: string; candidateId: string; onClose: () => void }) {
  const base = `${runtimeApiBaseUrl}/s7/subtitle-editor/${encodeURIComponent(batchId)}/${encodeURIComponent(candidateId)}`;
  const [saved, setSaved] = useState<Draft>();
  const [doc, setDoc] = useState<Document>();
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [ready, setReady] = useState(false);
  const [now, setNow] = useState(0);
  const [width, setWidth] = useState(300);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [events, setEvents] = useState<CaptionEvent[]>([]);
  const [alignmentError, setAlignmentError] = useState('');
  const [speechResult, setSpeechResult] = useState<{ document: Document; original: Cue[]; engine: string }>();
  const [history, setHistory] = useState<{ original: Cue[]; versions: { revision: number; document: Document }[] }>();
  const cursor = useRef<{ index: number; offset: number } | undefined>(undefined);
  const player = useRef<HTMLVideoElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const lock = useRef(false);
  const stopAt = useRef<number | undefined>(undefined);
  const dirty = !!doc && JSON.stringify(doc) !== JSON.stringify(saved?.document);
  useUnsavedChanges(dirty || !!busy, '字幕编辑');
  async function request<T>(suffix = '', body?: unknown, method = 'POST'): Promise<T> {
    const response = await fetch(base + suffix, body === undefined ? undefined : { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!response.ok) {
      const value = await response.json().catch(() => ({}));
      throw new Error(typeof value.detail === 'string' ? value.detail : `字幕操作失败（${response.status}），修改已保留`);
    }
    return response.json() as Promise<T>;
  }
  async function act(label: string, operation: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(label); setError(''); setMessage('');
    try { await operation(); } catch (e) { setError(e instanceof Error ? e.message : '操作失败，修改已保留'); }
    finally { lock.current = false; setBusy(''); }
  }
  useEffect(() => { void act('读取字幕', async () => { const value = await request<Draft>(); setSaved(value); setDoc(value.document); }); }, [base]);
  useEffect(() => {
    if (!frame.current) return;
    const observer = new ResizeObserver(entries => { if (entries[0]) setWidth(entries[0].contentRect.width); });
    observer.observe(frame.current); return () => observer.disconnect();
  }, [doc !== undefined]);
  useEffect(() => {
    if (!doc) return;
    let cancelled = false;
    setEvents([]); setAlignmentError('');
    const timer = window.setTimeout(() => {
      void request<{ events: CaptionEvent[] }>('/events', { revision: saved?.revision ?? 0, document: doc }).then(result => { if (!cancelled) setEvents(result.events); }).catch(e => { if (!cancelled) setAlignmentError(e.message); });
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [doc, base]);
  useEffect(() => {
    if (!ready) return;
    let frameId = 0;
    const tick = () => {
      if (player.current) {
        if (stopAt.current !== undefined && player.current.currentTime * 1000 >= stopAt.current) {
          player.current.pause(); player.current.currentTime = Math.max(0, stopAt.current - 1) / 1000; stopAt.current = undefined;
        }
        setNow(player.current.currentTime * 1000);
      }
      frameId = requestAnimationFrame(tick);
    };
    frameId = requestAnimationFrame(tick); return () => cancelAnimationFrame(frameId);
  }, [ready]);
  function changeCue(index: number, change: Partial<Cue>) {
    setDoc(old => old && ({ ...old, cues: old.cues.map((cue, i) => i === index ? { ...cue, original_text: cue.original_text ?? cue.text, ...change, words: undefined } : cue) }));
    setSuggestions([]);
  }
  const active = doc?.cues.findIndex(cue => cue.start_ms <= now && now < cue.end_ms) ?? -1;
  const event = events.find(e => e.start_ms <= now && now < e.end_ms);
  const effectDuration = event ? event.end_ms - event.start_ms : 0;
  const fade = Math.min(100, Math.floor(effectDuration / 4));
  const pop = Math.min(120, Math.floor(effectDuration / 3));
  const sentenceMode = (doc?.mode ?? 'sentence') === 'sentence';
  const opacity = sentenceMode && doc?.effect === 'fade' && event && fade > 0 ? Math.max(0, Math.min(1, (now - event.start_ms) / fade, (event.end_ms - now) / fade)) : 1;
  const scale = sentenceMode && doc?.effect === 'pop' && event && pop > 0 ? .92 + .08 * Math.min(1, Math.max(0, now - event.start_ms) / pop) : 1;
  function splitCue(index: number) {
    const cue = doc?.cues[index]; if (!cue || !doc) return;
    const offset = cursor.current?.index === index ? cursor.current.offset : 0;
    if (offset <= 0 || offset >= cue.text.length) { setError('请先把文字光标放在要拆分的位置，再点击拆分。'); return; }
    let length = 0; const boundary = cue.words?.findIndex(word => { length += word.text.length; return length === offset; }) ?? -1;
    const leftWords = boundary >= 0 ? cue.words?.slice(0, boundary + 1) : undefined;
    const rightWords = boundary >= 0 ? cue.words?.slice(boundary + 1) : undefined;
    const at = rightWords?.[0]?.start_ms ?? Math.round((player.current?.currentTime ?? 0) * 1000);
    if (at <= cue.start_ms || at >= cue.end_ms) { setError('这句尚无对应字词时间。请播放到拆分处暂停，或先重新对齐。'); return; }
    const left = { ...cue, text: cue.text.slice(0, offset), end_ms: leftWords?.at(-1)?.end_ms ?? at, words: leftWords };
    const right = { ...cue, text: cue.text.slice(offset), start_ms: at, words: rightWords };
    setDoc({ ...doc, cues: [...doc.cues.slice(0, index), left, right, ...doc.cues.slice(index + 1)] }); setError('');
  }
  function captionText(text: string) {
    const words = (doc?.keywords ?? []).filter(Boolean).sort((a, b) => b.length - a.length);
    if (!words.length) return text;
    const escaped = words.map(word => word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    return text.split(new RegExp(`(${escaped.join('|')})`, 'g')).map((part, i) => words.includes(part) ? <span key={i} style={{ color: doc?.keyword_color ?? '#FFD400', fontSize: `${(doc?.keyword_scale ?? 1.3) * 100}%` }}>{part}</span> : part);
  }
  return <section className="subtitle-editor" aria-label="字幕编辑">
    <header><div><h2>字幕校对与位置</h2><p>短句跟随原声；逐字显示使用音频对齐时间。修改后生成新版，原版保留。</p></div><button disabled={!!busy} onClick={() => guardUnsavedTransition(onClose)}>返回成片审核</button></header>
    {error && <p role="alert">{error}</p>}{busy && <p role="status">{busy}…请稍候</p>}{message && <p role="status">{message}</p>}
    {!doc ? <button disabled={!!busy} onClick={() => void act('读取字幕', async () => { const value = await request<Draft>(); setSaved(value); setDoc(value.document); })}>重新读取</button> : <>
      <div className="subtitle-editor-grid">
        <aside>
          <div className="subtitle-preview" ref={frame}>
            {ready ? <video ref={player} controls src={`${base}/preview`} onError={() => setError('预览读取失败，请重新准备预览')} /> : <p>准备无字幕底片后，可边听边修改字幕和位置。</p>}
            {ready && event && <span className="subtitle-preview-caption" style={{ opacity, transform: `translate(-50%, -100%) scale(${scale})`, transformOrigin: 'center bottom', left: `${doc.x}%`, top: `${doc.y}%`, fontFamily: fonts[doc.font], fontSize: `${doc.size * width / 1080}px`, width: `${Math.min(doc.x, 100 - doc.x) * 2 - 10}%` }}>{event.highlight !== undefined ? <><span style={{ color: doc.keyword_color ?? '#FFD400' }}>{event.highlight}</span>{event.text.slice(event.highlight.length)}</> : captionText(event.text)}</span>}
          </div>
          <button disabled={!!busy} onClick={() => void act('准备无字幕预览', async () => { await request('/prepare', {}); setReady(true); })}>{ready ? '重新准备预览' : '准备视频预览'}</button>
          <fieldset disabled={!!busy} className="subtitle-controls"><legend>字幕位置与样式</legend>
            <label>显示方式<select aria-label="显示方式" value={doc.mode ?? 'sentence'} onChange={e => setDoc({ ...doc, mode: e.target.value as Document['mode'] })}><option value="reveal">短句 · 随口播逐字出现</option><option value="sentence">短句 · 整句显示</option><option value="highlight">短句 · 随口播逐字高亮</option></select></label>
            <label>水平位置 {doc.x}%<input aria-label="字幕水平位置" type="range" min="10" max="90" value={doc.x} onChange={e => setDoc({ ...doc, x: +e.target.value })} /></label>
            <label>垂直位置 {doc.y}%<input aria-label="字幕垂直位置" type="range" min="10" max="94" value={doc.y} onChange={e => setDoc({ ...doc, y: +e.target.value })} /></label>
            <label>字号 {doc.size}<input aria-label="字幕字号" type="range" min="32" max="120" value={doc.size} onChange={e => setDoc({ ...doc, size: +e.target.value })} /></label>
            <label>字体<select value={doc.font} onChange={e => setDoc({ ...doc, font: e.target.value })}><option value="yahei">微软雅黑</option><option value="heiti">黑体</option><option value="songti">宋体</option><option value="kaiti">楷体</option></select></label>
            <label>整句入场特效<select disabled={(doc.mode ?? 'sentence') !== 'sentence'} value={doc.effect} onChange={e => setDoc({ ...doc, effect: e.target.value })}><option value="none">无特效</option><option value="fade">轻柔淡入淡出</option><option value="pop">轻微弹入</option></select></label>
            <label>重点词（逗号分隔）<input value={(doc.keywords ?? []).join('，')} onChange={e => setDoc({ ...doc, keywords: e.target.value.split(/[,，]/) })} /></label>
            <label>重点词颜色<input type="color" value={doc.keyword_color ?? '#FFD400'} onChange={e => setDoc({ ...doc, keyword_color: e.target.value })} /></label>
            <p>逐字出现：只出现已说到的字。逐字高亮：当前短句完整显示，已说到的字变色。重点词颜色为固定强调，和语音高亮不同。</p>
          </fieldset>
        </aside>
        <div className="subtitle-timeline">
          <div className="subtitle-cue-actions"><button disabled={!!busy} onClick={() => void act('正在读取原音频并重新识别、分段', async () => { const result = await request<{ document: Document; original: Cue[]; engine: string }>('/speech', { revision: saved?.revision ?? 0, document: doc }); setSpeechResult(result); setMessage('原音频识别完成，请对照原文后采用。当前编辑未被覆盖。'); })}>原音频复核与短句分段</button>
          <button disabled={!!busy || !doc.cues.length} onClick={() => void act('正在按原音频重新对齐修改后的文字', async () => { const result = await request<{ document: Document }>('/speech?align=true', { revision: saved?.revision ?? 0, document: doc }); setDoc(result.document); setMessage('已按原音频重新计算字词时间并短句分段，请核听。'); })}>修改后重新对齐</button>
          <button disabled={!!busy} onClick={() => void act('读取原文与历史版本', async () => setHistory(await request('/history')))}>原文与历史版本</button></div>
          <label>服装热词（辅助识别，不作为事实）<input value={doc.hotwords ?? ''} placeholder="裤子 裤脚口 裤腰 裤长 弹力 尺码" onChange={e => setDoc({ ...doc, hotwords: e.target.value })} /></label>
          {alignmentError && <p role="status" className="subtitle-alignment-warning">{alignmentError}。不会用平均速度模拟逐字效果。</p>}
          {speechResult && <div className="subtitle-suggestion"><strong>原音频复核结果 · 尚未覆盖</strong><p>原字幕：{speechResult.original.map(c => c.text).join(' / ')}</p><p>重新识别：{speechResult.document.cues.map(c => c.text).join(' / ')}</p><p>标记待确认的片段请核听，数字和商品参数不自动认定。</p><button onClick={() => { setDoc(speechResult.document); setSpeechResult(undefined); }}>采用短句结果并继续核听</button><button onClick={() => setSpeechResult(undefined)}>保留当前字幕</button></div>}
          {history && <details open><summary>原始字幕与已保存版本</summary><p>{history.original.map(c => c.text).join(' / ')}</p><button disabled={!!busy} onClick={() => setDoc({ ...doc, cues: history.original, mode: 'sentence' })}>恢复原始文本到编辑区</button>{history.versions.map(v => <button disabled={!!busy} key={v.revision} onClick={() => setDoc(v.document)}>恢复版本 {v.revision} 到编辑区</button>)}</details>}
          <button disabled={!!busy || !doc.cues.length} onClick={() => void act('按上下文校对', async () => { const result = await request<{ suggestions: Suggestion[] }>('/suggest', { revision: saved?.revision, document: doc }); setSuggestions(result.suggestions); setMessage('建议未自动应用，请边听边核对。'); })}>AI 语境校对建议</button>
          <p>AI 依据识别文本与前后文推敲，不代表已经听清音频。尺码、价格、品牌等不确定内容请核听；不会自动覆盖原文。</p>
          <fieldset disabled={!!busy}>
            {!doc.cues.length && <p>暂无字幕，可添加一句后填写时间和口播内容。</p>}
            {doc.cues.map((cue, i) => { const suggestion = suggestions.find(s => s.index === i); return <div key={i} className={`subtitle-cue ${active === i ? 'is-active' : ''}`}>
              <div className="subtitle-cue-times"><strong>第 {i + 1} 句{active === i ? ' · 正在播放' : ''}</strong>
                <label>开始<input aria-label={`第${i + 1}句开始`} type="number" min="0" step="0.01" value={cue.start_ms / 1000} onChange={e => changeCue(i, { start_ms: Math.round(+e.target.value * 1000) })} /></label>
                <label>结束<input aria-label={`第${i + 1}句结束`} type="number" min="0" step="0.01" value={cue.end_ms / 1000} onChange={e => changeCue(i, { end_ms: Math.round(+e.target.value * 1000) })} /></label>
                <button disabled={!ready} onClick={() => { if (player.current) { player.current.currentTime = cue.start_ms / 1000; stopAt.current = cue.end_ms; void player.current.play().catch(() => setError('请点击视频播放按钮开始核听')); } }}>听这一句</button>
              </div>
              {cue.uncertain && <p className="subtitle-alignment-warning">待确认：含低置信度识别或数字、商品参数，请听这一句核实。</p>}
              <textarea aria-label={`第${i + 1}句字幕`} rows={2} maxLength={200} value={cue.text} onSelect={e => { cursor.current = { index: i, offset: e.currentTarget.selectionStart }; }} onChange={e => changeCue(i, { text: e.target.value })} />
              <button onClick={() => void act('正在携带前后音频复核这一句', async () => { setSpeechResult(await request(`/speech?cue_index=${i}`, { revision: saved?.revision ?? 0, document: doc })); })}>原音频复核这一句</button>
              {cue.original_text && cue.original_text !== cue.text && <small>修改前：{cue.original_text}</small>}
              <button onClick={() => splitCue(i)}>在文字光标处拆分</button>
              <div className="subtitle-cue-actions"><button disabled={i === doc.cues.length - 1} onClick={() => { const next = doc.cues[i + 1]; if (!next) return; setDoc({ ...doc, cues: [...doc.cues.slice(0, i), { ...cue, end_ms: next.end_ms, text: cue.text + next.text, words: cue.words && next.words ? [...cue.words, ...next.words] : undefined }, ...doc.cues.slice(i + 2)] }); setSuggestions([]); }}>与下一句合并</button><button onClick={() => { setDoc({ ...doc, cues: doc.cues.filter((_, n) => n !== i) }); setSuggestions([]); }}>删除这句</button></div>
              {suggestion && <div className="subtitle-suggestion"><p>{suggestion.uncertain ? '需核听：' : '建议：'}{suggestion.text}</p><p>{suggestion.reason}</p><button onClick={() => { changeCue(i, { text: suggestion.text }); }}>采用本句建议</button></div>}
            </div>; })}
            <button onClick={() => { const start = doc.cues.at(-1)?.end_ms ?? 0; setDoc({ ...doc, cues: [...doc.cues, { start_ms: start, end_ms: Math.min(start + 2000, saved?.duration_ms ?? start + 2000), text: '' }] }); setSuggestions([]); }}>添加一句</button>
          </fieldset>
        </div>
      </div>
      <footer><span>{dirty ? '有未保存修改' : `已保存 · 版本 ${saved?.revision ?? 0}`}</span><button disabled={!!busy} onClick={() => void act('保存草稿', async () => { const value = await request<Draft>('', { revision: saved?.revision, document: doc }, 'PUT'); setSaved(value); setDoc(value.document); setMessage('字幕草稿已保存，下次打开可继续编辑。'); })}>保存字幕草稿</button><button className="primary-button" disabled={!!busy || dirty || !saved?.revision} onClick={() => void act('生成字幕新版', async () => { await request('/render', { revision: saved?.revision, document: doc }); setMessage('新版已生成，请返回成片审核并刷新批次。原版和原审核未覆盖。'); })}>生成新版成片</button></footer>
    </>}
  </section>;
}
