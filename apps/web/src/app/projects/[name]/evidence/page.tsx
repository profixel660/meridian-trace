"use client";

import { use, useEffect, useState } from "react";

import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { FirstUseCallout } from "@/components/review/FirstUseCallout";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import { ToastHostProvider } from "@/components/review/ToastHost";
import { type ProjectCoverage } from "@/lib/api";
import {
  evidenceApi,
  type EvidencePackListItem,
} from "@/lib/apiClient/evidence";
import { apiFetch } from "@/lib/fetcher";

import { EvidencePanel } from "./EvidencePanel";

export default function EvidencePage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = use(params);

  const [packs, setPacks] = useState<EvidencePackListItem[] | null>(null);
  const [listError, setListError] = useState<unknown>(null);
  const [coverage, setCoverage] = useState<ProjectCoverage | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setListError(null);
    (async () => {
      try {
        const [list, cov] = await Promise.all([
          evidenceApi.list(name),
          apiFetch<ProjectCoverage>(
            `/projects/${encodeURIComponent(name)}/coverage`,
          ).catch(() => null),
        ]);
        if (!cancelled) {
          setPacks(list);
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

  return (
    <ToastHostProvider>
      <ReviewLayout
        projectName={name}
        title="Legal Evidence Pack"
        subtitle="Assemble a defensible audit-trail bundle suitable for hand-off to construction lawyers, claims teams, or opposing counsel."
        counts={counts}
      >
        <FirstUseCallout
          routeKey={`projects/${name}/evidence`}
          title="What is the Legal Evidence Pack?"
        >
          <p>
            The pack is a single zip containing the deliverables register,
            every LLM call (prompt + response + cost + model), every source
            document, full provenance links, schema notes, and a
            <code> cover.md</code> describing the contents. It&apos;s
            designed for hand-off in a dispute: a third-party reviewer can
            unzip it and reproduce every claim Meridian has made about the
            project.
          </p>
          <p>
            <strong>What it proves:</strong> that the deliverable register
            was assembled from these specific sources, with these specific
            LLM calls, on this date.{" "}
            <strong>What it does not prove:</strong> the underlying source
            documents are authoritative, or that the human reviewer&apos;s
            decisions were correct.{" "}
            <a
              href="/glossary#legal-evidence-pack"
              className="text-accent hover:underline"
            >
              Full definition →
            </a>
          </p>
        </FirstUseCallout>

        {listError ? (
          <ApiErrorPanel error={listError} />
        ) : loading || packs === null ? (
          <div className="text-text-muted text-sm">Loading…</div>
        ) : (
          <EvidencePanel projectName={name} packs={packs} />
        )}
      </ReviewLayout>
    </ToastHostProvider>
  );
}
