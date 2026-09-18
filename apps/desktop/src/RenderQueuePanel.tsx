import type { RenderTask } from "@content-factory/contracts";

import { renderStatusLabels } from "./editing";

export function RenderQueuePanel({ tasks, busy, retry }: {
  tasks: readonly RenderTask[]; busy: boolean; retry: (taskId: string) => void;
}) {
  return <section className="render-queue-panel" aria-labelledby="render-queue-title">
    <header><h2 id="render-queue-title">本机渲染队列</h2><p>最多同时渲染 2 条；意外中断后会自动恢复。</p></header>
    {tasks.length === 0 ? <div className="edit-inline-empty">还没有渲染任务。保存工程后选择一个版本开始渲染。</div> : <ul>
      {tasks.slice(0, 8).map((task) => <li key={task.task_id}>
        <div><strong>{renderStatusLabels[task.status]}</strong><span>{task.current_step} · 第 {task.attempt_count}/{task.max_attempts} 次</span></div>
        <i><b style={{ width: `${task.progress}%` }} /></i><em>{task.progress}%</em>
        {task.error && <small>{task.error}</small>}
        {task.status === "failed" && <button className="secondary-button" type="button" disabled={busy} onClick={() => retry(task.task_id)}>重试</button>}
      </li>)}
    </ul>}
  </section>;
}
