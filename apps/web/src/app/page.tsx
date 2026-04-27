import Link from "next/link";

import { StatusCard } from "@/components/StatusCard";
import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { meridianApi, type ProjectListItem } from "@/lib/api";

const FALLBACK_PROJECT = "syd2-shell-cd";

// Always render fresh — counts move as the backend processes documents.
export const dynamic = "force-dynamic";

export default async function ProjectsPage() {
  let projects: ProjectListItem[] | null = null;
  let listError: unknown = null;
  try {
    projects = await meridianApi.projects();
  } catch (err) {
    listError = err;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
          Projects
        </h1>
        <p className="mt-1 text-sm text-text-muted">
          Pick a project to drive its review queues, or open the master
          register. Counts refresh on each visit.
        </p>
      </div>

      {listError ? (
        <ApiErrorPanel error={listError} />
      ) : projects && projects.length > 0 ? (
        <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {projects.map((p) => (
            <li key={p.name}>
              <Link
                href={`/projects/${encodeURIComponent(p.name)}`}
                className="block rounded-lg border border-border bg-surface-elevated p-5 transition hover:border-accent/60"
              >
                <div className="flex items-baseline justify-between">
                  <h2 className="text-base font-semibold text-text-primary">
                    {p.name}
                  </h2>
                  <span className="text-[11px] uppercase tracking-wide text-text-muted">
                    open →
                  </span>
                </div>
                <p className="mt-1 text-xs text-text-muted">
                  Created {new Date(p.created_at).toLocaleDateString()}
                </p>
                <dl className="mt-4 grid grid-cols-2 gap-2 text-xs">
                  <Stat label="Sources" value={p.sources} />
                  <Stat label="On master" value={p.deliverables_master} />
                  <Stat
                    label="Questions pending"
                    value={p.questions_pending}
                    highlight={p.questions_pending > 0}
                  />
                  <Stat
                    label="Conflicts pending"
                    value={p.conflicts_pending}
                    highlight={p.conflicts_pending > 0}
                  />
                </dl>
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <FallbackProjectCard />
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  highlight = false,
}: {
  label: string;
  value: number;
  highlight?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between border-b border-border/60 pb-1">
      <dt className="text-text-muted">{label}</dt>
      <dd
        className={`font-mono ${
          highlight ? "text-accent" : "text-text-primary"
        }`}
      >
        {value}
      </dd>
    </div>
  );
}

async function FallbackProjectCard() {
  // No projects from the list endpoint — fall back to the smoke-test slug
  // so the original scaffold experience still works for first-time users.
  let body: React.ReactNode;
  try {
    const status = await meridianApi.projectStatus(FALLBACK_PROJECT);
    body = (
      <Link href={`/projects/${encodeURIComponent(FALLBACK_PROJECT)}`}>
        <StatusCard projectName={FALLBACK_PROJECT} status={status} />
      </Link>
    );
  } catch (err) {
    body = <ApiErrorPanel error={err} />;
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-text-muted">
        No projects returned from the API yet. Falling back to the smoke-test
        project so you can explore the UI.
      </p>
      {body}
    </div>
  );
}
