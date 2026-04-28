"use client";

import { useEffect, useState } from "react";

import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { EmptyState } from "@/components/review/EmptyState";
import { FirstUseCallout } from "@/components/review/FirstUseCallout";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import { ToastHostProvider } from "@/components/review/ToastHost";
import {
  type MasterRow,
  type ProjectCoverage,
  type TaxonomyProposal,
} from "@/lib/api";
import { apiFetch } from "@/lib/fetcher";
import { useRuntimeProjectSlug } from "@/lib/useRuntimeProjectSlug";

import { TaxonomyQueue } from "./TaxonomyQueue";

export default function TaxonomyPage() {
  const name = useRuntimeProjectSlug();

  const [proposals, setProposals] = useState<TaxonomyProposal[] | null>(null);
  const [listError, setListError] = useState<unknown>(null);
  const [coverage, setCoverage] = useState<ProjectCoverage | null>(null);
  const [canonicalByTable, setCanonicalByTable] = useState<
    Record<string, string[]>
  >({
    trade: [],
    service: [],
    category: [],
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!name) return;
    let cancelled = false;
    setLoading(true);
    setListError(null);
    (async () => {
      try {
        const [props, cov] = await Promise.all([
          apiFetch<TaxonomyProposal[]>(
            `/projects/${encodeURIComponent(name)}/taxonomy/pending`,
          ),
          apiFetch<ProjectCoverage>(
            `/projects/${encodeURIComponent(name)}/coverage`,
          ).catch(() => null),
        ]);
        // Master register is the easiest place to harvest the canonical values
        // a reviewer can merge into. We just take the distinct non-null values.
        const master = await apiFetch<MasterRow[]>(
          `/projects/${encodeURIComponent(name)}/master`,
        ).catch(() => [] as MasterRow[]);
        const set = (axis: "trade" | "service" | "category") => {
          const values = new Set<string>();
          for (const r of master) {
            const v = r[axis];
            if (v) values.add(v);
          }
          return Array.from(values).sort();
        };
        if (!cancelled) {
          setProposals(props);
          setCoverage(cov);
          setCanonicalByTable({
            trade: set("trade"),
            service: set("service"),
            category: set("category"),
          });
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
        title="Taxonomy proposals"
        subtitle="New trade / service / category values the AI proposed. Confirm to add, merge into an existing value, or reject."
      >
        <div className="text-text-muted text-sm">Loading…</div>
      </ReviewLayout>
    );
  }

  return (
    <ToastHostProvider>
      <ReviewLayout
        projectName={name}
        title="Taxonomy proposals"
        subtitle="New trade / service / category values the AI proposed. Confirm to add, merge into an existing value, or reject."
        counts={counts}
      >
        <FirstUseCallout
          routeKey={`projects/${name}/taxonomy`}
          title="What is the Taxonomy queue?"
        >
          <p>
            Trade / service / category values are project-extensible but
            controlled. When extraction encounters a term not in the canonical
            list, it enters as a proposal — pending your decision.
          </p>
          <ul className="list-disc pl-5">
            <li>
              <strong>Confirm</strong> — adopt the new value as canonical.
            </li>
            <li>
              <strong>Merge</strong> — fold into an existing value (all
              referencing rows are repointed). Cascades many rows; confirm
              before applying.
            </li>
            <li>
              <strong>Reject</strong> — soft-delete. Refused if any
              deliverable still references the value.
            </li>
          </ul>
        </FirstUseCallout>

        {listError ? (
          <ApiErrorPanel error={listError} />
        ) : loading || proposals === null ? (
          <div className="text-text-muted text-sm">Loading…</div>
        ) : proposals.length === 0 ? (
          <EmptyState
            title="No pending taxonomy proposals"
            body={
              <>
                The trade / service / category lists are stable for now.
                Proposals appear when extraction encounters a value the
                project hasn&apos;t yet adopted.
              </>
            }
            learnMoreHref="/glossary#taxonomy-proposal"
          />
        ) : (
          <TaxonomyQueue
            projectName={name}
            proposals={proposals}
            canonicalByTable={canonicalByTable}
          />
        )}
      </ReviewLayout>
    </ToastHostProvider>
  );
}
