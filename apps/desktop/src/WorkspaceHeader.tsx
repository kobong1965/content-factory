import type { ReactNode } from "react";

export type WorkspaceMetric = Readonly<{
  label: string;
  value: ReactNode;
  tone?: "default" | "success" | "warning" | "danger";
}>;

export function WorkspaceHeader({
  stage,
  title,
  description,
  current,
  metrics,
  action,
}: {
  stage: string;
  title: string;
  description: string;
  current?: ReactNode;
  metrics?: readonly WorkspaceMetric[];
  action?: ReactNode;
}) {
  return <header className="workspace-header" aria-label={`${stage}：${title}`}>
    <div className="workspace-heading">
      <h1 data-page-title tabIndex={-1}>{title}</h1>
      <p>{description}</p>
      {current && <div className="workspace-current">{current}</div>}
    </div>
    <div className="workspace-header-side">
      {metrics && metrics.length > 0 && <dl className="workspace-metrics">
        {metrics.map((metric) => <div className={`metric-${metric.tone ?? "default"}`} key={metric.label}>
          <dt>{metric.label}</dt><dd>{metric.value}</dd>
        </div>)}
      </dl>}
      {action && <div className="workspace-primary-action">{action}</div>}
    </div>
  </header>;
}
