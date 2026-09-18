import { useEffect, useState } from 'react';
import { PROJECT_NAME, PROJECT_VERSION } from '@content-factory/contracts';
import { GatewaySettings } from './GatewaySettings';
import { S2MediaWorkspace } from './S2MediaWorkspace';
import { S3AnalysisWorkspace } from './S3AnalysisWorkspace';
import { FinishedLibrary } from './FinishedLibrary';
import { AutoEditWorkspace } from './AutoEditWorkspace';
import { useS2Media } from './useS2Media';
import { useS3Analysis } from './useS3Analysis';
import { persistUiScale, readUiScale, type UiScale } from './uiPreferences';
import { guardUnsavedTransition, useUnsavedChanges, useUnsavedChangesBeforeUnload } from './unsavedChanges';
import './simplified-workspace.css';

type Workspace = 'analysis' | 'editing' | 'finished' | 'settings';
type AnalysisStep = 'upload' | 'analysis';
export function App() {
  const [workspace, setWorkspace] = useState<Workspace>('editing');
  const [analysisStep, setAnalysisStep] = useState<AnalysisStep>('upload');
  const [uiScale, setUiScale] = useState<UiScale>(readUiScale);
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const [reviewRequested, setReviewRequested] = useState(0);
  const media = useS2Media(workspace === 'analysis');
  const analysis = useS3Analysis(workspace === 'analysis' && analysisStep === 'analysis');
  useUnsavedChanges(settingsDirty, '模型设置');
  useUnsavedChangesBeforeUnload();
  useEffect(() => persistUiScale(uiScale), [uiScale]);
  const navigate = (next: Workspace) => guardUnsavedTransition(() => setWorkspace(next));
  const connection = media.connectionState;
  const openReview = () => guardUnsavedTransition(() => { setWorkspace('editing'); setReviewRequested(value => value + 1); });
  return <div className="app-shell">
    <a className="skip-link" href="#workspace-content">跳到主内容</a>
    <aside className="sidebar" aria-label="项目导航">
      <div className="brand-lockup"><img className="brand-mark" src="/brand-logo.png" alt="" /><div className="brand-copy"><strong>{PROJECT_NAME}</strong><span>素材剪辑工作台</span></div></div>
      <nav aria-label="主要工作区">{([{id:'analysis',label:'素材分析'}, {id:'editing',label:'视频剪辑'}, {id:'finished',label:'成片素材库'}] as const).map(item => <button key={item.id} type="button" className={'nav-item ' + (workspace === item.id ? 'nav-item-active' : '')} aria-current={workspace === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}>{item.label}</button>)}</nav>
      <div className="sidebar-note"><button type="button" className="text-button" onClick={() => navigate('settings')}>模型连接设置</button><small>版本 {PROJECT_VERSION}</small></div>
    </aside>
    <main id="workspace-content" data-workspace={workspace}>
      <header className="topbar"><div className="topbar-location"><strong>{workspace === 'analysis' ? '素材分析' : workspace === 'editing' ? '视频剪辑' : workspace === 'finished' ? '成片素材库' : '模型连接设置'}</strong><small>{workspace === 'analysis' ? '把对标方法审核为剪辑 Skill' : workspace === 'editing' ? '多项目自动排队与成片审核' : '按批次制作与管理'}</small></div><div className="topbar-actions">
        <div className="ui-scale-switch" role="group" aria-label="界面字号"><button aria-pressed={uiScale === 'comfortable'} onClick={() => setUiScale('comfortable')}>舒适</button><button aria-pressed={uiScale === 'large'} onClick={() => setUiScale('large')}>大字号</button></div>
        <span className={'connection-status connection-status-' + connection} role="status">{connection === 'live' ? '本机服务已连接' : connection === 'offline' ? '本机服务未启动' : '正在连接本机服务'}</span>
        <button className="secondary-button" onClick={() => { setRefreshToken(v => v + 1); void media.refresh(); void analysis.refresh(); }}>刷新</button>
      </div></header>
      {workspace === 'analysis' && <><div className="content simplified-heading"><h1 data-page-title tabIndex={-1}>对标素材分析</h1><p>导入优秀对标视频，完成深度分析和人工审核后，将方法保存为剪辑 Skill。</p><nav className="simplified-steps" aria-label="素材分析步骤">{([{id:'upload',label:'导入与处理'}, {id:'analysis',label:'深度分析与 Skill 审核'}] as const).map((item,i)=><button className="secondary-button" key={item.id} aria-current={analysisStep === item.id ? 'step' : undefined} onClick={() => guardUnsavedTransition(() => setAnalysisStep(item.id))}>{i+1}. {item.label}</button>)}</nav></div>
        {analysisStep === 'upload' && <S2MediaWorkspace media={media} compact />}
        {analysisStep === 'analysis' && <S3AnalysisWorkspace analysis={analysis} mediaTasks={media.tasks} openSettings={() => navigate('settings')} compact />}
      </>}
      {workspace === 'editing' && <AutoEditWorkspace reviewRequested={reviewRequested} />}
      {workspace === 'finished' && <FinishedLibrary refreshToken={refreshToken} openReview={openReview} />}
      {workspace === 'settings' && <GatewaySettings analysis={analysis} onDirtyChange={setSettingsDirty} />}
    </main>
  </div>;
}
