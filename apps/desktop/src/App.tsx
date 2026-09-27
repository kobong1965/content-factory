import { useEffect, useState } from 'react';
import { PROJECT_NAME, PROJECT_VERSION } from '@content-factory/contracts';
import { GatewaySettings } from './GatewaySettings';
import { ServiceLinks } from './ServiceLinks';
import { SoftwareUpdates } from './SoftwareUpdates';
import { S2MediaWorkspace } from './S2MediaWorkspace';
import { S3AnalysisWorkspace } from './S3AnalysisWorkspace';
import { FinishedLibrary } from './FinishedLibrary';
import { AutoEditWorkspace } from './AutoEditWorkspace';
import { SkillPackagePanel } from './SkillPackagePanel';
import { StudioIcon } from './StudioIcon';
import { useS2Media } from './useS2Media';
import { useS3Analysis } from './useS3Analysis';
import { persistUiScale, readUiScale, type UiScale } from './uiPreferences';
import { guardUnsavedTransition, useUnsavedChanges, useUnsavedChangesBeforeUnload } from './unsavedChanges';
import './simplified-workspace.css';

type Workspace = 'analysis' | 'editing' | 'finished' | 'settings' | 'updates';
type AnalysisStep = 'upload' | 'analysis';
export function App() {
  const [workspace, setWorkspace] = useState<Workspace>('editing');
  const [analysisStep, setAnalysisStep] = useState<AnalysisStep>('upload');
  // Keep the approved comfortable baseline; the old compact toggle made the
  // 2K layout too small. The preference remains readable for older sessions.
  const [uiScale] = useState<UiScale>(() => readUiScale() === 'large' ? 'large' : 'comfortable');
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const [reviewRequested, setReviewRequested] = useState(0);
  const [targetBatch, setTargetBatch] = useState<string | null>(null);
  const media = useS2Media(workspace === 'analysis');
  const analysis = useS3Analysis(workspace === 'analysis' && analysisStep === 'analysis');
  useUnsavedChanges(settingsDirty, '模型设置');
  useUnsavedChangesBeforeUnload();
  useEffect(() => persistUiScale(uiScale), [uiScale]);
  const navigate = (next: Workspace) => guardUnsavedTransition(() => { setTargetBatch(null); setReviewRequested(0); setWorkspace(next); });
  const connection = media.connectionState;
  const openReview = (batchId?: string) => guardUnsavedTransition(() => { setTargetBatch(batchId ?? null); setWorkspace('editing'); setReviewRequested(value => value + 1); });
  const openLibrary = (batchId: string) => guardUnsavedTransition(() => { setTargetBatch(batchId); setWorkspace('finished'); });
  return <div className="app-shell studio-shell">
    <a className="skip-link" href="#workspace-content">跳到主内容</a>
    <aside className="sidebar" aria-label="项目导航">
      <div className="brand-lockup"><img className="brand-mark" src="/brand-logo.png" alt="" /><div className="brand-copy"><strong>{PROJECT_NAME}</strong><span>内容制作工作室</span></div></div>
      <nav aria-label="主要工作区">{([{id:'analysis',label:'素材分析'}, {id:'editing',label:'视频剪辑'}, {id:'finished',label:'成片素材库'}] as const).map(item => <button key={item.id} type="button" className={'nav-item ' + (workspace === item.id ? 'nav-item-active' : '')} aria-current={workspace === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}><StudioIcon name={item.id}/>{item.label}</button>)}</nav>
      <div className="sidebar-note"><button type="button" className="text-button" aria-current={workspace === 'settings' ? 'page' : undefined} onClick={() => navigate('settings')}><StudioIcon name="settings"/>模型连接设置</button><button type="button" className="text-button" aria-current={workspace === 'updates' ? 'page' : undefined} onClick={() => navigate('updates')}><StudioIcon name="updates"/><span>版本 {PROJECT_VERSION}<small>检查更新</small></span></button></div>
    </aside>
    <main id="workspace-content" data-workspace={workspace}>
      <header className="topbar"><div className="topbar-location"><strong>{workspace === 'analysis' ? '素材分析' : workspace === 'editing' ? '视频剪辑' : workspace === 'finished' ? '成片素材库' : workspace === 'updates' ? '版本与更新' : '模型连接设置'}</strong><small>{workspace === 'analysis' ? '把对标方法审核为剪辑 Skill' : workspace === 'editing' ? '多项目自动排队与成片审核' : workspace === 'updates' ? '检查版本、下载与安全安装' : workspace === 'settings' ? '模型连接、余额与用量' : '按批次制作与管理'}</small></div><div className="topbar-actions">
        <span className={'connection-status connection-status-' + connection} role="status">{connection === 'live' ? '本机服务已连接' : connection === 'offline' ? '本机服务未启动' : '正在连接本机服务'}</span>
        <button className="secondary-button" onClick={() => { setRefreshToken(v => v + 1); void media.refresh(); void analysis.refresh(); }}><StudioIcon name="refresh"/>刷新</button>
      </div></header>
      {workspace === 'analysis' && <><div className="content simplified-heading"><h1 data-page-title tabIndex={-1}>对标素材与剪辑方法</h1><nav className="simplified-steps" aria-label="素材分析步骤">{([{id:'upload',label:'上传对标 / Skill 库'}, {id:'analysis',label:'深度分析与 Skill 审核'}] as const).map((item,i)=><button className="secondary-button" key={item.id} aria-current={analysisStep === item.id ? 'step' : undefined} onClick={() => guardUnsavedTransition(() => setAnalysisStep(item.id))}>{i+1}. {item.label}</button>)}</nav></div>
        {analysisStep === 'upload' && <S2MediaWorkspace media={media} compact afterUpload={<SkillPackagePanel />} />}
        {analysisStep === 'analysis' && <S3AnalysisWorkspace analysis={analysis} mediaTasks={media.tasks} openSettings={() => navigate('settings')} compact />}
      </>}
      {workspace === 'editing' && <AutoEditWorkspace reviewRequested={reviewRequested} initialBatchId={targetBatch ?? undefined} openLibrary={openLibrary} />}
      {workspace === 'finished' && <FinishedLibrary refreshToken={refreshToken} openReview={openReview} initialBatchId={targetBatch} />}
      {workspace === 'settings' && <><ServiceLinks /><GatewaySettings analysis={analysis} onDirtyChange={setSettingsDirty} /></>}
      {workspace === 'updates' && <SoftwareUpdates />}
    </main>
  </div>;
}
