"use client";

import { use, useEffect, useState } from "react";

import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { FirstUseCallout } from "@/components/review/FirstUseCallout";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import { ToastHostProvider } from "@/components/review/ToastHost";
import { type ProjectCoverage } from "@/lib/api";
import {
  xrefApi,
  type XrefReportResponse,
} from "@/lib/apiClient/xref";
import { apiFetch } from "@/lib/fetcher";

import { XrefPanel } from "./XrefPanel";

export default function XrefPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = use(params);

  const [report, setReport] = useState<XrefReportResponse | null>(null);
  const [listError, setListError] = useState<unknown>(null);
  const [coverage, setCoverage] = useState<ProjectCoverage | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setListError(null);
    (async () => {
      try {
        const [rep, cov] = await Promise.all([
          xrefApi.report(name),
          apiFetch<ProjectCoverage>(
            `/projects/${encodeURIComponent(name)}/coverage`,
          ).catch(() => null),
        ]);
        if (!cancelled) {
          setReport(rep);
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
        title="Cross-reference sweep"
        subtitle="Exhaustively scan every ingested document for textual references to other project sources."
        counts={counts}
      >
        <FirstUseCallout
          routeKey={`projects/${name}/xref`}
          title="What is a cross-reference sweep?"
        >
          <p>
            The sweep walks every authoritative source in the project and
            extracts <em>cross-reference anchors</em> — section numbers,
            standards, drawing IDs, equipment tags, owner IDs, spec
            references — then resolves each anchor to another project
            source where possible.
          </p>
          <p>
            Outcomes are three-way: <strong>confirmed</strong> (anchor
            resolved to a known project source),{" "}
            <strong>borderline</strong> (ambiguous — added to the SME
            review queue when persisted), and{" "}
            <strong>external_reference</strong> (legitimately points
            outside the project, e.g. to a published standard).{" "}
            <strong>Rejected</strong> findings are noisy false positives
            the deterministic pass discards.
          </p>
        </FirstUseCallout>

        {listError ? (
          <ApiErrorPanel error={listError} />
        ) : loading && report === null ? (
          <div className="text-text-muted text-sm">Loading…</div>
        ) : (
          <XrefPanel projectName={name} report={report} />
        )}
      </ReviewLayout>
    </ToastHostProvider>
  );
}
