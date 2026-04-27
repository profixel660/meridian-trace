import type { ProjectStatus } from "@/lib/api";

interface StatusCardProps {
  projectName: string;
  status: ProjectStatus;
}

const LABELS: Record<keyof ProjectStatus, string> = {
  sources: "Source documents",
  deliverables_total: "Deliverables (total)",
  deliverables_master: "Deliverables (master register)",
  audit_outside: "Audit records (outside-of-scope)",
  questions_pending: "Questions pending",
  extraction_jobs: "Extraction jobs",
  llm_calls: "LLM calls",
};

export function StatusCard({ projectName, status }: StatusCardProps) {
  const entries = Object.entries(LABELS) as Array<
    [keyof ProjectStatus, string]
  >;
  return (
    <div className="rounded-lg border border-border bg-surface-elevated p-6 shadow-sm">
      <div className="mb-4 flex items-baseline justify-between">
        <h2 className="text-base font-semibold text-text-primary">
          {projectName}
        </h2>
        <span className="text-xs uppercase tracking-wide text-text-muted">
          status
        </span>
      </div>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
        {entries.map(([key, label]) => (
          <div
            key={key}
            className="flex items-center justify-between border-b border-border/60 py-1.5 text-sm last:border-b-0"
          >
            <dt className="text-text-muted">{label}</dt>
            <dd className="font-mono text-text-primary">{status[key]}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
