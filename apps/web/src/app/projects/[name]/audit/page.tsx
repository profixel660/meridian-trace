"use client";

import { useEffect, useState } from "react";

import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { EmptyState } from "@/components/review/EmptyState";
import { FirstUseCallout } from "@/components/review/FirstUseCallout";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import { ToastHostProvider } from "@/components/review/ToastHost";
import {
  type AuditItem,
  type ProjectCoverage,
} from "@/lib/api";
import { apiFetch } from "@/lib/fetcher";
import { useRuntimeProjectSlug } from "@/lib/useRuntimeProjectSlug";

import { AuditQueue } from "./AuditQueue";

export default function AuditPage() {
  const name = useRuntimeProjectSlug();

  const [items, setItems] = useState<AuditItem[] | null>(null);
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
        const auditPath = `/projects/${encodeURIComponent(name)}/audit`;
        const coveragePath = `/projects/${encodeURIComponent(name)}/coverage`;
        const [list, cov] = await Promise.all([
          apiFetch<AuditItem[]>(auditPath),
          apiFetch<ProjectCoverage>(coveragePath).catch(() => null),
        ]);
        if (!cancelled) {
          setItems(list);
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
        title="Audit (OUTSIDE candidates)"
        subtitle="Rows the gate ruled OUTSIDE the deliverable definition. Promote any that should actually be on the master register."
      >
        <div className="text-text-muted text-sm">Loading…</div>
      </ReviewLayout>
    );
  }

  return (
    <ToastHostProvider>
      <ReviewLayout
        projectName={name}
        title="Audit (OUTSIDE candidates)"
        subtitle="Rows the gate ruled OUTSIDE the deliverable definition. Promote any that should actually be on the master register."
        counts={counts}
      >
        <FirstUseCallout
          routeKey={`projects/${name}/audit`}
          title="What is the Audit queue?"
        >
          <p>
            Every chunk of source text the AI considers ends in one of three
            outcomes — INSIDE (becomes a deliverable), BORDERLINE (lands in
            Quarantine), or OUTSIDE (logged here). The Audit queue exists so
            nothing the AI looked at is invisible to you.
          </p>
          <p>
            If you spot something the AI dismissed that you actually want on
            the master register, click <strong>Promote</strong> and tag it with
            trade / service / category.
          </p>
        </FirstUseCallout>

        {listError ? (
          <ApiErrorPanel error={listError} />
        ) : loading || items === null ? (
          <div className="text-text-muted text-sm">Loading…</div>
        ) : items.length === 0 ? (
          <EmptyState
            title="No audit rows"
            body={
              <>
                Nothing in the OUTSIDE log yet. Items land here when the gate
                rules a candidate OUTSIDE the deliverable definition (for
                example, a generic clause about programme timing). Re-run
                extraction to populate.
              </>
            }
            learnMoreHref="/glossary#audit"
          />
        ) : (
          <AuditQueue projectName={name} items={items} />
        )}
      </ReviewLayout>
    </ToastHostProvider>
  );
}
