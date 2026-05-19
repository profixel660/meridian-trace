"use client";

import { useEffect, useState } from "react";

import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { EmptyState } from "@/components/review/EmptyState";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import {
  API_BASE,
  type MasterRow,
  type ProjectCoverage,
} from "@/lib/api";
import { apiFetch } from "@/lib/fetcher";
import { useRuntimeProjectSlug } from "@/lib/useRuntimeProjectSlug";

import { MasterTable } from "./MasterTable";

export default function MasterPage() {
  const name = useRuntimeProjectSlug();

  const [rows, setRows] = useState<MasterRow[] | null>(null);
  const [listError, setListError] = useState<unknown>(null);
  const [coverage, setCoverage] = useState<ProjectCoverage | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!name) return;
    let cancelled = false;
    setLoading(true);
    setListError(null);
    (async () => {
      try {
        const [list, cov] = await Promise.all([
          apiFetch<MasterRow[]>(
            `/projects/${encodeURIComponent(name)}/master`,
          ),
          apiFetch<ProjectCoverage>(
            `/projects/${encodeURIComponent(name)}/coverage`,
          ).catch(() => null),
        ]);
        if (!cancelled) {
          setRows(list);
          setCoverage(cov);
        }
      } catch (err) {
        if (!cancelled) setListError(err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [name]);

  const counts = coverage
    ? {
        quarantine: coverage.deliverable_status.quarantined,
        audit: coverage.audit.pending,
        questions: coverage.questions.pending,
        conflicts: coverage.conflicts.pending,
        taxonomy: coverage.taxonomy.pending_proposals,
      }
    : undefined;

  if (!name) {
    return (
      <ReviewLayout
        projectName=""
        title="Master register"
        subtitle="Read-only view of every deliverable currently on the master register. Filter by trade / service / category."
      >
        <div className="text-text-muted text-sm">Loading…</div>
      </ReviewLayout>
    );
  }

  return (
    <ReviewLayout
      projectName={name}
      title="Master register"
      subtitle="Read-only view of every deliverable currently on the master register. Filter by trade / service / category."
      counts={counts}
      actions={
        rows && rows.length > 0 ? (
          <a
            href={`${API_BASE}/projects/${encodeURIComponent(name)}/export.xlsx`}
            className="rounded-full border border-border px-4 py-2 text-sm text-text-primary hover:border-text-muted"
          >
            Download Excel
          </a>
        ) : (
          <span
            className="rounded-full border border-border px-4 py-2 text-sm text-text-muted cursor-not-allowed opacity-50"
            title="Run an extraction first to generate register rows"
          >
            Download Excel
          </span>
        )
      }
    >
      {listError ? (
        <ApiErrorPanel error={listError} />
      ) : loading || rows === null ? (
        <div className="text-text-muted text-sm">Loading…</div>
      ) : rows.length === 0 ? (
        <EmptyState
          title="Master register is empty"
          body={
            <>
              Nothing has been auto-approved or accepted yet. Import a source
              and run extraction, then review the Quarantine queue to populate
              the register.
            </>
          }
          learnMoreHref="/glossary#master-register"
        />
      ) : (
        <MasterTable rows={rows} />
      )}
    </ReviewLayout>
  );
}
