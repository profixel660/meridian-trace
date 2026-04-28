"use client";

import { useEffect, useState } from "react";

import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { EmptyState } from "@/components/review/EmptyState";
import { FirstUseCallout } from "@/components/review/FirstUseCallout";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import { ToastHostProvider } from "@/components/review/ToastHost";
import {
  type ProjectCoverage,
  type QuarantinedItem,
} from "@/lib/api";
import { apiFetch } from "@/lib/fetcher";
import { useRuntimeProjectSlug } from "@/lib/useRuntimeProjectSlug";

import { QuarantineQueue } from "./QuarantineQueue";

export default function QuarantinePage() {
  const name = useRuntimeProjectSlug();

  const [items, setItems] = useState<QuarantinedItem[] | null>(null);
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
          apiFetch<QuarantinedItem[]>(
            `/projects/${encodeURIComponent(name)}/quarantine`,
          ),
          apiFetch<ProjectCoverage>(
            `/projects/${encodeURIComponent(name)}/coverage`,
          ).catch(() => null),
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
        title="Quarantine"
        subtitle="Deliverables held back from the master register pending your decision."
      >
        <div className="text-text-muted text-sm">Loading…</div>
      </ReviewLayout>
    );
  }

  return (
    <ToastHostProvider>
      <ReviewLayout
        projectName={name}
        title="Quarantine"
        subtitle="Deliverables held back from the master register pending your decision."
        counts={counts}
      >
        <FirstUseCallout
          routeKey={`projects/${name}/quarantine`}
          title="What is the Quarantine queue?"
        >
          <p>
            When the AI extracts a deliverable from a source document it runs
            it through a three-outcome gate. Anything that doesn&apos;t cleanly
            land INSIDE — BORDERLINE matches, low-confidence calls, or rows
            with raised flags — comes here for a human to decide.
          </p>
          <p>
            Use Accept to move the row onto the master register; Reject to
            remove it (kept in audit history); or Edit to refine wording or
            taxonomy before accepting. Source ref + flags help you check the
            AI&apos;s reasoning.
          </p>
        </FirstUseCallout>

        {listError ? (
          <ApiErrorPanel error={listError} />
        ) : loading || items === null ? (
          <div className="text-text-muted text-sm">Loading…</div>
        ) : items.length === 0 ? (
          <EmptyState
            title="No quarantined items"
            body={
              <>
                The quarantine queue is the AI&apos;s “I&apos;m not sure” pile.
                A deliverable lands here when the gate said BORDERLINE, the
                confidence was low, or a flag (e.g. <code>tbd_placeholder</code>,
                <code> definition_borderline</code>) was raised. Re-run
                extraction on a new source, or check{" "}
                <strong>Audit</strong> for OUTSIDE rows you might want to
                promote.
              </>
            }
            learnMoreHref="/glossary#quarantine"
          />
        ) : (
          <QuarantineQueue projectName={name} items={items} />
        )}
      </ReviewLayout>
    </ToastHostProvider>
  );
}
