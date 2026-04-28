"use client";

import { useEffect, useState } from "react";

import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { EmptyState } from "@/components/review/EmptyState";
import { FirstUseCallout } from "@/components/review/FirstUseCallout";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import { ToastHostProvider } from "@/components/review/ToastHost";
import { type ProjectCoverage } from "@/lib/api";
import {
  tenderApi,
  type TradeListResponse,
  type TenderOutputDirResponse,
} from "@/lib/apiClient/tender";
import { apiFetch } from "@/lib/fetcher";
import { useRuntimeProjectSlug } from "@/lib/useRuntimeProjectSlug";

import { TenderBuilder } from "./TenderBuilder";

export default function TenderPage() {
  const name = useRuntimeProjectSlug();

  const [trades, setTrades] = useState<TradeListResponse | null>(null);
  const [outputDir, setOutputDir] = useState<TenderOutputDirResponse | null>(
    null,
  );
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
        const [tradeList, outDir, cov] = await Promise.all([
          tenderApi.trades(name),
          tenderApi.outputDir(name).catch(() => null),
          apiFetch<ProjectCoverage>(
            `/projects/${encodeURIComponent(name)}/coverage`,
          ).catch(() => null),
        ]);
        if (!cancelled) {
          setTrades(tradeList);
          setOutputDir(outDir);
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
        title="Tender Package Builder"
        subtitle="Generate per-trade subcontractor packages from the accepted-deliverables register."
      >
        <div className="text-text-muted text-sm">Loading…</div>
      </ReviewLayout>
    );
  }

  return (
    <ToastHostProvider>
      <ReviewLayout
        projectName={name}
        title="Tender Package Builder"
        subtitle="Generate per-trade subcontractor packages from the accepted-deliverables register."
        counts={counts}
      >
        <FirstUseCallout
          routeKey={`projects/${name}/tender`}
          title="What is a tender package?"
        >
          <p>
            A tender package is a per-trade hand-off file (xlsx or md) you
            issue to subcontractors during procurement. Meridian assembles
            it from <strong>accepted</strong> deliverables only —
            quarantined, rejected, and audit-only rows are excluded — and
            adds a cover sheet with project metadata, source documents,
            applicable standards, flag summary, and any items still flagged
            for review.
          </p>
          <p>
            Pick a trade below to see the deliverable count and kick off a
            build. Files are written to{" "}
            <code className="text-text-primary">
              {outputDir?.default_output_dir ??
                "<projects_dir>/<slug>.tenders/"}
            </code>
            ; copy the path from the success toast to share with the team.
          </p>
        </FirstUseCallout>

        {listError ? (
          <ApiErrorPanel error={listError} />
        ) : loading || trades === null ? (
          <div className="text-text-muted text-sm">Loading…</div>
        ) : trades.trades.length === 0 ? (
          <EmptyState
            title="No accepted deliverables yet"
            body={
              <>
                The tender builder needs at least one accepted deliverable
                with a trade tag. Work the <strong>Quarantine</strong> queue
                to clear ambiguous rows, then come back. Rows tagged{" "}
                <code>(unassigned)</code> in the master register also won&apos;t
                appear here until they have a trade.
              </>
            }
            learnMoreHref="/glossary#tender-package"
          />
        ) : (
          <TenderBuilder
            projectName={name}
            trades={trades.trades}
            defaultOutputDir={outputDir?.default_output_dir ?? null}
          />
        )}
      </ReviewLayout>
    </ToastHostProvider>
  );
}
