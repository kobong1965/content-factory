import { describe, expect, it } from 'vitest';
import { generationBlockers, type AutoEditProject } from './autoEditing';
import { projectFailureSummary } from './projectPresentation';

const project = {
  status: 'draft', source: { duration_ms: 10_000 },
  settings: { duration_min_ms: 20_000, duration_max_ms: 40_000, target_count: 5 },
} as AutoEditProject;

describe('generation condition gate', () => {
  it('allows a short source to reach the target by repeating clips', () => {
    expect(generationBlockers(project)).toEqual([]);
  });
  it('keeps the existing 30-piece resource limit rather than consuming frames', () => {
    const short = {...project, source:{...project.source,duration_ms:5000},settings:{...project.settings,duration_min_ms:180000,duration_max_ms:180000}};
    expect(generationBlockers(short).join('')).toContain('30');
    expect(generationBlockers({...short,source:{...short.source,duration_ms:6000}})).toEqual([]);
  });
  it('explains historical overlap failures as the removed old rule', () => {
    const summary = projectFailureSummary('同一成片内的剪辑选段不能重叠');
    expect(summary.title).toContain('旧版');
    expect(summary.message).toContain('允许');
    expect(summary.message).toContain('重复');
    expect(summary.message).toContain('费用');
  });
  it('uses server file checks and does not block registration-only recovery', () => {
    const checked = { ...project, generation_check: { ready: false, blockers: ['第 1 条素材无法读取'] } };
    expect(generationBlockers(checked)).toEqual(['第 1 条素材无法读取']);
    expect(generationBlockers({ ...checked, status: 'failed', registration_checkpoint: {manifest_path:'m',manifest_sha256:'a'} })).toEqual([]);
  });
  it('allows reuse, exactly equal source duration and longer optional sources', () => {
    expect(generationBlockers({ ...project, sources: [{source_id:'long',file_name:'a',sha256:'a',duration_ms:20_000}] })).toEqual([]);
  });
  it('explains rejected plans instead of claiming a model connection failure', () => {
    const error = '第 5 条剪辑方案 clip_5 成片时长不在用户设置范围内：实际 30.036 秒，允许 10.000—30.000 秒；超过上限 0.036 秒。未进入成片渲染。';
    const summary = projectFailureSummary(error);
    expect(summary.title).toBe('剪辑方案不合格，已阻止生成成片');
    expect(summary.message).toContain('第 5 条');
    expect(summary.message).toContain('0.036');
    expect(summary.message).toContain('费用');
  });
  it('handles legacy duration errors without inventing a candidate or duration', () => {
    const summary = projectFailureSummary('剪辑方案成片时长不在用户设置范围内');
    expect(summary.title).toContain('已阻止');
    expect(summary.message).not.toContain('第 5 条');
  });
});
