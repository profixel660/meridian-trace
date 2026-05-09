"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { StatusCard } from "@/components/StatusCard";
import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { type ProjectListItem, type ProjectStatus } from "@/lib/api";
import { apiFetch } from "@/lib/fetcher";
import type { SetupState } from "@/lib/setupClient";

const FALLBACK_PROJECT = "syd2-shell-cd";

export default function ProjectsPage() {
  const router = useRouter();
  const [projects, setProjects] = useState<ProjectListItem[] | null>(null);
  const [listError, setListError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  // Round-17: gate the projects grid behind a setup-state check so a
  // brand-new install lands on /setup rather than an empty projects list.
  const [setupChecked, setSetupChecked] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // Fetch projects ONCE and share between the gate-softening check
      // and the existing render.
      let listResult: ProjectListItem[] | null = null;
      try {
        listResult = await apiFetch<ProjectListItem[]>("/projects");
      } catch (e) {
        if (!cancelled) setListError(e);
      }
      if (cancelled) return;

      // Setup gate: bounce to /setup ONLY if no projects exist on disk.
      // The state file's complete-flag is the gate for first-install; once
      // the operator has any real project, that gate is past — bouncing
      // them is hostile (alpha-25 §8).
      try {
        const setupState = await apiFetch<SetupState>("/setup/state");
        if (cancelled) return;
        if (setupState.complete === false && (listResult ?? []).length === 0) {
          router.replace("/setup");
          return;
        }
      } catch {
        // /setup/state unreachable — fall through to whatever projects we have.
      }
      if (cancelled) return;
      setSetupChecked(true);
      if (listResult !== null) setProjects(listResult);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

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

      {!setupChecked ? (
        <div className="text-text-muted text-sm">Checking setup…</div>
      ) : loading ? (
        <div className="text-text-muted text-sm">Loading…</div>
      ) : listError ? (
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

function FallbackProjectCard() {
  // No projects from the list endpoint — fall back to the smoke-test slug
  // so the original scaffold experience still works for first-time users.
  const [status, setStatus] = useState<ProjectStatus | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await apiFetch<ProjectStatus>(
          `/projects/${encodeURIComponent(FALLBACK_PROJECT)}/status`,
        );
        if (!cancelled) setStatus(result);
      } catch (e) {
        if (!cancelled) setErr(e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  let body: React.ReactNode;
  if (loading) {
    body = <div className="text-text-muted text-sm">Loading…</div>;
  } else if (err) {
    body = <ApiErrorPanel error={err} />;
  } else if (status) {
    body = (
      <Link href={`/projects/${encodeURIComponent(FALLBACK_PROJECT)}`}>
        <StatusCard projectName={FALLBACK_PROJECT} status={status} />
      </Link>
    );
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
