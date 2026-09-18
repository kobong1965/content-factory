import type { MediaTask } from "@content-factory/contracts";

import { taskStatusLabel, taskStepLabel } from "./media";

export function TaskQueue({ tasks }: { tasks: MediaTask[] }) {
  if (tasks.length === 0) {
    return (
      <div className="empty-state" role="status">
        <span className="empty-symbol" aria-hidden="true">空</span>
        <h3>还没有处理任务</h3>
        <p>从上方选择一条本地视频，任务会自动出现在这里。</p>
      </div>
    );
  }

  return (
    <ol className="task-list" aria-label="媒体处理任务">
      {tasks.map((task) => (
        <li className="task-row" key={task.task_id}>
          <div className="task-main">
            <div className="task-title-row">
              <h3 title={task.source_name}>{task.source_name}</h3>
              <span className={`task-status task-status-${task.status}`}>{taskStatusLabel(task.status)}</span>
            </div>
            <div className="task-progress" role="progressbar" aria-label={`${task.source_name}处理进度`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={task.progress}>
              <span style={{ width: `${task.progress}%` }} />
            </div>
            <p>{task.error ?? `${taskStepLabel(task.current_step)} · 第 ${task.attempt_count}/${task.max_attempts} 次`}</p>
          </div>
          <strong className="task-percent">{task.progress}%</strong>
        </li>
      ))}
    </ol>
  );
}
