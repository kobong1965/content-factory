import type { EditAudioAsset, EditClip, EditSettings } from "@content-factory/contracts";

export function EditInspector({ clip, settings, audio, updateClip, updateSettings }: {
  clip: EditClip | null; settings: EditSettings; audio: readonly EditAudioAsset[];
  updateClip: (changes: Partial<EditClip>) => void; updateSettings: (changes: Partial<EditSettings>) => void;
}) {
  const bgm = audio.filter((item) => item.kind === "bgm");
  return <aside className="edit-inspector">
    <header><h2>{clip ? `片段 ${clip.order} 参数` : "全局设置"}</h2></header>
    {clip && <fieldset><legend>片段调整</legend>
      <label>字幕<textarea value={clip.subtitle} onChange={(event) => updateClip({ subtitle: event.target.value })} /></label>
      <label>备注<textarea value={clip.note} onChange={(event) => updateClip({ note: event.target.value })} placeholder="只在工程中保留" /></label>
      <div className="inspector-grid">
        <label>源起点（毫秒）<input type="number" min="0" step="100" value={clip.source_start_ms} onChange={(event) => updateClip({ source_start_ms: Number(event.target.value) })} /></label>
        <label>源终点（毫秒）<input type="number" min="1" step="100" value={clip.source_end_ms} onChange={(event) => updateClip({ source_end_ms: Number(event.target.value) })} /></label>
        <label>裁切<select value={clip.crop_mode} onChange={(event) => updateClip({ crop_mode: event.target.value as EditClip["crop_mode"] })}><option value="fill">铺满竖屏</option><option value="fit">完整画面</option></select></label>
        <label>速度<input type="number" min="0.5" max="2" step="0.05" value={clip.speed} onChange={(event) => updateClip({ speed: Number(event.target.value) })} /></label>
        <label>焦点横向<input type="range" min="0" max="1" step="0.05" value={clip.focus_x} onChange={(event) => updateClip({ focus_x: Number(event.target.value) })} /></label>
        <label>焦点纵向<input type="range" min="0" max="1" step="0.05" value={clip.focus_y} onChange={(event) => updateClip({ focus_y: Number(event.target.value) })} /></label>
        <label>转场<select value={clip.transition} onChange={(event) => updateClip({ transition: event.target.value as EditClip["transition"], transition_ms: event.target.value === "cut" ? 0 : 120 })}><option value="cut">硬切</option><option value="fade">淡入淡出</option></select></label>
        <label>原声音量<input type="number" min="0" max="2" step="0.1" value={clip.original_volume} onChange={(event) => updateClip({ original_volume: Number(event.target.value) })} /></label>
      </div>
    </fieldset>}
    <fieldset><legend>整条成片</legend>
      <div className="inspector-grid">
        <label>色彩<select value={settings.color_preset} onChange={(event) => updateSettings({ color_preset: event.target.value as EditSettings["color_preset"] })}><option value="natural">自然</option><option value="bright">明亮</option><option value="warm">暖色</option><option value="cool">冷色</option></select></label>
        <label>BGM<select value={settings.bgm_asset_id ?? ""} onChange={(event) => updateSettings({ bgm_asset_id: event.target.value || null })}><option value="">不加 BGM</option>{bgm.map((item) => <option key={item.asset_id} value={item.asset_id}>{item.name}</option>)}</select></label>
        <label>BGM 音量<input type="range" min="0" max="1" step="0.01" value={settings.bgm_volume} onChange={(event) => updateSettings({ bgm_volume: Number(event.target.value) })} /></label>
        <label>字幕字号<input type="number" min="28" max="96" value={settings.subtitle_style.font_size} onChange={(event) => updateSettings({ subtitle_style: { ...settings.subtitle_style, font_size: Number(event.target.value) } })} /></label>
      </div>
      <label className="check-label"><input type="checkbox" checked={settings.subtitle_style.enabled} onChange={(event) => updateSettings({ subtitle_style: { ...settings.subtitle_style, enabled: event.target.checked } })} />烧录字幕</label>
      <label className="check-label"><input type="checkbox" checked={settings.reduce_noise} onChange={(event) => updateSettings({ reduce_noise: event.target.checked })} />原声降噪</label>
      <label className="check-label"><input type="checkbox" checked={settings.normalize_voice} onChange={(event) => updateSettings({ normalize_voice: event.target.checked })} />人声均衡</label>
      <label className="check-label"><input type="checkbox" checked={settings.auto_sound_effects} onChange={(event) => updateSettings({ auto_sound_effects: event.target.checked })} />自动轻音效</label>
    </fieldset>
    <div className="delivery-lock"><strong>固定交付</strong><span>1080 × 1920 · 30fps · H.264 + AAC</span><small>始终同时保留无字幕干净版。</small></div>
  </aside>;
}
