import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ProjectFailure } from './ProjectFailure';

describe('project registration failure', () => {
  it('explains preserved outputs and registration-only retry without losing diagnostic disclosure', () => {
    const html = renderToStaticMarkup(<ProjectFailure error="422: 第 4 条内部编号无效" registrationRetry />);
    expect(html).toContain('成片已生成，登记未完成');
    expect(html).toContain('重试登记');
    expect(html).toContain('不会重新分析或剪辑');
    expect(html).not.toContain('重新调用模型并产生费用');
    expect(html).toContain('role="alert"');
    expect(html).toContain('<details><summary>查看技术详情</summary>');
    expect(html).toContain('422: 第 4 条内部编号无效');
  });

  it('retains the paid retry warning for ordinary failures without a checkpoint', () => {
    const html = renderToStaticMarkup(<ProjectFailure error="unexpected worker response" />);
    expect(html).toContain('重新尝试可能重新调用模型并产生费用');
    expect(html).not.toContain('成片已生成');
  });
});
