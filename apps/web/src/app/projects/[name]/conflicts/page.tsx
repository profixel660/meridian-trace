"use client";

import { useEffect, useState } from "react";

import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { EmptyState } from "@/components/review/EmptyState";
import { FirstUseCallout } from "@/components/review/FirstUseCallout";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import { ToastHostProvider } from "@/components/review/ToastHost";
import {
  type ConflictItem,
  type ProjectCoverage,
} from "@/lib/api";
import { apiFetch } from "@/lib/fetcher";
import { useRuntimeProjectSlug } from "@/lib/useRuntimeProjectSlug";

import { ConflictsQueue } from "./ConflictsQueue";

export default function ConflictsPage() {
  const name = useRuntimeProjectSlug();

  const [items, setItems] = useState<ConflictItem[] | null>(null);
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
          apiFetch<ConflictItem[]>(
            `/projects/${encodeURIComponent(name)}/conflicts?status=pending`,
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
        title="Conflicts"
        subtitle="Two or more sources disagree about the same item. Pick the winning side, accept a hybrid, or reject both."
      >
        <div className="text-text-muted text-sm">Loading…</div>
      </ReviewLayout>
    );
  }

  return (
    <ToastHostProvider>
      <ReviewLayout
        projectName={name}
        title="Conflicts"
        subtitle="Two or more sources disagree about the same item. Pick the winning side, accept a hybrid, or reject both."
        counts={counts}
      >
        <FirstUseCallout
          routeKey={`projects/${name}/conflicts`}
          title="What is the Conflicts queue?"
        >
          <p>
            A conflict is recorded whenever two sources cover the same
            deliverable but disagree — different responsibilities, different
            revisions, different document classes, or competing scope
            demarcations. The AI tags the most onerous side with reasoning, but
            the final call is yours.
          </p>
          <p>
            Use <strong>Accept A</strong> / <strong>Accept B</strong> to keep
            one party (the other is rejected); <strong>Reject both</strong> if
            neither is right; or open a row to record a hybrid resolution.
          </p>
        </FirstUseCallout>

        {listError ? (
          <ApiErrorPanel error={listError} />
        ) : loading || items === null ? (
          <div className="text-text-muted text-sm">Loading…</div>
        ) : items.length === 0 ? (
          <EmptyState
            title="No open conflicts"
            body={
              <>
                Nothing crossed between sources is in dispute. Conflicts are
                detected during extraction when the same deliverable surfaces
                in two sources with diverging values — for example, a
                Demarcation Schedule allocates a duct run to MEP but the BOD
                puts it on the architect.
              </>
            }
            learnMoreHref="/glossary#conflict"
          />
        ) : (
          <ConflictsQueue projectName={name} items={items} />
        )}
      </ReviewLayout>
    </ToastHostProvider>
  );
}
