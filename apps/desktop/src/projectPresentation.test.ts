import { describe, expect, it } from 'vitest';
import { projectFailureSummary, projectStage } from './projectPresentation';

describe('project failure presentation', () => {
  it('does not claim missing source video for a failed processing copy', () => {
    const failure = projectFailureSummary('[Errno 2] No such file or directory: C:\\work\\originals\\xx.mp4.123.partial');
    expect(failure.title).toBe('视频处理文件无法访问');
    expect(failure.message).toContain('不一定是原素材丢失');
  });
  it('preserves unknown errors for the expandable diagnostic, not a guessed cause', () => {
    expect(projectFailureSummary('unexpected worker response').title).toBe('这次处理未完成');
  });
  it('distinguishes connection failures from disk failures', () => {
    expect(projectFailureSummary('API connection timeout').title).toBe('模型服务连接未完成');
  });
  it('explains that local speech recognition can use a configured API fallback', () => {
    const failure = projectFailureSummary('原音频识别/对齐失败，字幕未覆盖。请检查本地模型与运行时；日志已保存。');
    expect(failure.title).toBe('本地语音识别失败');
    expect(failure.message).toContain('/audio/transcriptions');
  });
  it('distinguishes an unavailable local model from an unavailable API fallback', () => {
    const failure = projectFailureSummary('本地语音识别失败；API 语音识别也不可用：API 语音识别请求失败（HTTP 404）');
    expect(failure.title).toBe('本地与 API 语音识别均不可用');
    expect(failure.message).toContain('仅支持文字/图片的模型不能替代语音转写');
  });
  it('does not advance draft or failed projects into a successful stage', () => {
    expect(projectStage('draft', 0)).toBe(0);
    expect(projectStage('failed', 10)).toBe(1);
    expect(projectStage('review', 100)).toBe(4);
  });
});
