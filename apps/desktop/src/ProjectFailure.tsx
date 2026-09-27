import { projectFailureSummary } from './projectPresentation';

export function ProjectFailure({ error, registrationRetry = false }: { error: string; registrationRetry?: boolean }) {
  const summary = registrationRetry
    ? { title: '成片已生成，登记未完成', message: '成片文件和剪辑方案已保留。点击“重试登记”仅校验并登记现有成片，不会重新分析或剪辑，也不会再次调用模型。' }
    : projectFailureSummary(error);
  return <section className="project-failure" role="alert">
    <strong>{summary.title}</strong>
    <p>{summary.message}</p>
    <details><summary>查看技术详情</summary><pre>{error}</pre></details>
  </section>;
}
