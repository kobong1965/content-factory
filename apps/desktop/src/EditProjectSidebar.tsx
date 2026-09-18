import type { EditProject } from "@content-factory/contracts";

import { formatEditDuration, projectStatusLabels } from "./editing";

export function EditProjectSidebar({ projects, selectedId, select }: {
  projects: readonly EditProject[]; selectedId: string | null; select: (id: string) => void;
}) {
  return <aside className="edit-project-sidebar" aria-label="剪辑工程">
    <header><h2>剪辑工程</h2><b>{projects.length}</b></header>
    {projects.length === 0 ? <div className="edit-side-empty"><strong>还没有工程</strong><span>先从素材已配齐的脚本建立一个。</span></div> : <ul>
      {projects.map((project) => <li key={project.project_id}>
        <button type="button" className={selectedId === project.project_id ? "edit-project-active" : ""} onClick={() => select(project.project_id)}>
          <span><strong>{project.variants[0]?.name ?? "未命名工程"}</strong><small>脚本第 {project.script_revision} 版 · 工程第 {project.revision} 版</small></span>
          <em className={`edit-status edit-status-${project.status}`}>{projectStatusLabels[project.status]}</em>
          <p>{project.variants.length} 个成片版本 · {formatEditDuration(project.variants[0]?.duration_ms ?? 0)}</p>
        </button>
      </li>)}
    </ul>}
  </aside>;
}
