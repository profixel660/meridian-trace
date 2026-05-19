"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AuthGate } from "@/components/AuthGate";
import { ConflictsTile } from "@/components/dashboard/ConflictsTile";
import { HierarchyView } from "@/components/dashboard/HierarchyView";
import { PipelineProgressTile } from "@/components/dashboard/PipelineProgressTile";
import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import { StatusBadge } from "@/components/review/StatusBadge";
import { Tooltip, TooltipMore } from "@/components/review/Tooltip";
import { type ProjectCoverage } from "@/lib/api";
import { apiFetch } from "@/lib/fetcher";
import { type PipelinePhase } from "@/lib/setupClient";
import { useRuntimeProjectSlug } from "@/lib/useRuntimeProjectSlug";

export default function ProjectDashboardPage() {
  const name = useRuntimeProjectSlug();
  const [coverage, setCoverage] = useState<ProjectCoverage | null>(null);
  const [coverageError, setCoverageError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [pipelinePhase, setPipelinePhase] = useState<PipelinePhase | null>(null);

  const refetchCoverage = useCallback(() => {
    if (!name) return;
    setLoading(true);
    setCoverageError(null);
    (async () => {
      try {
        const result = await apiFetch<ProjectCoverage>(
          `/projects/${encodeURIComponent(name)}/coverage`,
        );
        setCoverage(result);
      } catch (err) {
        setCoverageError(err);
      } finally {
        setLoading(false);
      }
    })();
  }, [name]);

  useEffect(() => {
    if (!name) return;
    let cancelled = false;
    setLoading(true);
    setCoverageError(null);
    (async () => {
      try {
        const result = await apiFetch<ProjectCoverage>(
          `/projects/${encodeURIComponent(name)}/coverage`,
        );
        if (!cancelled) setCoverage(result);
      } catch (err) {
        if (!cancelled) setCoverageError(err);
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
        title="Loading…"
        subtitle="Project dashboard — coverage at a glance, plus quick access to every review queue."
      >
        <div className="text-text-muted text-sm">Loading…</div>
      </ReviewLayout>
    );
  }

  return (
    <ReviewLayout
      projectName={name}
      title={name}
      subtitle="Project dashboard — coverage at a glance, plus quick access to every review queue."
      counts={counts}
    >
      {/* Browser-side guard: bounce to /login if no live token. */}
      <AuthGate />
      {coverageError ? (
        <ApiErrorPanel error={coverageError} />
      ) : coverage ? (
        <DashboardBody
          projectName={name}
          coverage={coverage}
          pipelinePhase={pipelinePhase}
          onPipelinePhaseChange={setPipelinePhase}
          onPipelineDone={refetchCoverage}
        />
      ) : loading ? (
        <div className="text-text-muted text-sm">Loading…</div>
      ) : null}
    </ReviewLayout>
  );
}

function DashboardBody({
  projectName,
  coverage,
  pipelinePhase,
  onPipelinePhaseChange,
  onPipelineDone,
}: {
  projectName: string;
  coverage: ProjectCoverage;
  pipelinePhase: PipelinePhase | null;
  onPipelinePhaseChange: (phase: PipelinePhase) => void;
  onPipelineDone: () => void;
}) {
  const base = `/projects/${encodeURIComponent(projectName)}`;
  const pipelineActive =
    pipelinePhase !== null &&
    pipelinePhase !== "done" &&
    pipelinePhase !== "failed";

  const primaryCTA: "conflicts" | "quarantine" | "audit" | "questions" | "taxonomy" | null =
    coverage.conflicts.pending > 0 ? "conflicts" :
    coverage.deliverable_status.quarantined > 0 ? "quarantine" :
    coverage.audit.pending > 0 ? "audit" :
    coverage.questions.pending > 0 ? "questions" :
    coverage.taxonomy.pending_proposals > 0 ? "taxonomy" :
    null;

  return (
    <div className="space-y-8">
      <PipelineProgressTile
        projectSlug={projectName}
        onPhaseChange={onPipelinePhaseChange}
        onDone={onPipelineDone}
      />
      {/* Hide the BaselineBanner while the pipeline is running — the tile is
          the only signal the user needs in that window. */}
      {pipelineActive ? null : <BaselineBanner coverage={coverage} />}

      {!coverage.is_data_present ? (
        <section className="rounded-lg border border-accent/40 bg-accent/5 p-6">
          <h2 className="text-lg font-semibold text-text-primary">
            Welcome to your project
          </h2>
          <p className="mt-2 text-sm text-text-muted">
            No sources imported yet. Add some documents to get started —
            Meridian will extract requirements and group them by trade.
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <Link
              href="/setup/first-documents"
              className="rounded-full bg-accent px-5 py-2 text-sm font-medium text-white hover:opacity-90"
            >
              Add documents →
            </Link>
            <Link
              href="/glossary"
              className="rounded-full border border-border px-5 py-2 text-sm text-text-primary hover:border-accent"
            >
              What does Meridian do?
            </Link>
          </div>
        </section>
      ) : (
        <>
          {/* KPI tiles */}
          <section
            className={`grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4 transition-opacity ${
              pipelineActive ? "opacity-50" : ""
            }`}
          >
            <KpiCard
              label="Sources"
              value={coverage.sources_imported}
              sub={`${coverage.sources_extracted} extracted, ${coverage.sources_pending_extraction} pending`}
              href={`${base}/sources`}
            />
            <KpiCard
              label="Master register"
              value={coverage.deliverable_status.auto_approved + coverage.deliverable_status.user_accepted + coverage.deliverable_status.user_edited + coverage.deliverable_status.user_promoted}
              sub={`${coverage.deliverable_status.total} extracted total`}
              href={`${base}/master`}
            />
            <KpiCard
              label="Pending decisions"
              value={coverage.pending_decisions}
              sub="quarantine queue"
              highlight={coverage.pending_decisions > 0}
              href={`${base}/quarantine`}
            />
            <KpiCard
              label="Auto-route rate"
              value={`${coverage.auto_route_rate_pct.toFixed(1)}%`}
              sub={`${coverage.review_coverage_pct.toFixed(1)}% reviewed`}
              glossaryAnchor="/glossary#auto-route-rate"
            />
          </section>

          {/* Hierarchy view (alpha-26) */}
          {pipelineActive ? null : <HierarchyView projectSlug={projectName} />}

          {/* ConflictsTile (alpha-26) — prominent, above the queue grid */}
          {pipelineActive ? null : (
            <ConflictsTile
              projectSlug={projectName}
              pendingCount={coverage.conflicts.pending}
              resolvedCount={coverage.conflicts.resolved_count}
              isPrimaryCTA={primaryCTA === "conflicts"}
            />
          )}

          {/* Queue card grid (Quarantine/Audit/Questions/Taxonomy) */}
          {pipelineActive ? null : (
            <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <QueueCard
                href={`${base}/quarantine`}
                title="Quarantine"
                count={coverage.deliverable_status.quarantined}
                description="Deliverables held back from the master register pending your decision."
                isPrimaryCTA={primaryCTA === "quarantine"}
              />
              <QueueCard
                href={`${base}/audit`}
                title="Audit"
                count={coverage.audit.pending}
                description="Rejected candidates awaiting promote-or-reject."
                isPrimaryCTA={primaryCTA === "audit"}
              />
              <QueueCard
                href={`${base}/questions`}
                title="Questions"
                count={coverage.questions.pending}
                description="HITL questions that surfaced during extraction."
                isPrimaryCTA={primaryCTA === "questions"}
              />
              <QueueCard
                href={`${base}/taxonomy`}
                title="Taxonomy"
                count={coverage.taxonomy.pending_proposals}
                description="Proposed trade / service / category values needing approval."
                isPrimaryCTA={primaryCTA === "taxonomy"}
              />
            </section>
          )}

          {/* Recent activity */}
          <section>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-text-muted">
              Recent activity
            </h2>
            <dl className="mt-3 grid grid-cols-1 gap-2 text-sm sm:grid-cols-3">
              <Activity
                label="Last extraction"
                value={coverage.last_extraction_at}
              />
              <Activity
                label="Last LLM call"
                value={coverage.last_llm_call_at}
              />
              <Activity
                label="Last review action"
                value={coverage.last_review_action_at}
              />
            </dl>
          </section>
        </>
      )}
    </div>
  );
}

function BaselineBanner({ coverage }: { coverage: ProjectCoverage }) {
  if (!coverage.is_data_present) {
    // No documents yet — no opinion on trustworthiness. Suppress the banner
    // entirely; the welcome panel below handles the empty-project UX.
    return null;
  }
  if (coverage.is_baseline_trustworthy) {
    return (
      <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-4 text-sm text-emerald-200">
        <Tooltip
          content={
            <span>
              <strong className="block text-text-primary">
                Baseline trustworthy
              </strong>
              <span className="mt-1 block text-text-muted">
                Every queue is empty, every source has been extracted, and
                provenance is complete. The master register is safe to
                share with the project team as-is.
              </span>
              <TooltipMore href="/glossary#baseline-trustworthiness" />
            </span>
          }
        >
          <button
            type="button"
            className="cursor-help font-semibold underline decoration-dotted"
          >
            Baseline trustworthy
          </button>
        </Tooltip>{" "}
        — nothing waiting for review.
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4 text-sm text-amber-100">
      <div className="flex items-start gap-3">
        <StatusBadge label="needs review" variant="warn" explanation={{ title: "Baseline not yet trustworthy", body: "One or more blockers stand between the current state and a master register you can confidently hand to the project team." }} glossaryAnchor="/glossary#baseline-trustworthiness" />
        <div className="space-y-2">
          <p>
            The master register isn&apos;t yet baseline trustworthy. Work through
            the queues below to clear these blockers:
          </p>
          <ul className="list-disc space-y-1 pl-5 text-xs text-text-muted">
            {coverage.baseline_trust_blockers.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

function KpiCard({
  label,
  value,
  sub,
  href,
  highlight = false,
  glossaryAnchor,
}: {
  label: string;
  value: number | string;
  sub?: string;
  href?: string;
  highlight?: boolean;
  glossaryAnchor?: string;
}) {
  const inner = (
    <div
      className={`h-full rounded-lg border bg-surface-elevated p-4 transition ${
        href
          ? "cursor-pointer border-border hover:border-accent/60"
          : "border-border"
      }`}
    >
      <div className="text-[11px] uppercase tracking-wide text-text-muted">
        {glossaryAnchor ? (
          <Tooltip
            content={
              <span>
                <strong className="block text-text-primary">{label}</strong>
                <span className="mt-1 block text-text-muted">
                  Open the glossary for the full definition.
                </span>
                <TooltipMore href={glossaryAnchor} />
              </span>
            }
          >
            <button
              type="button"
              className="cursor-help underline decoration-dotted"
            >
              {label}
            </button>
          </Tooltip>
        ) : (
          label
        )}
      </div>
      <div
        className={`mt-2 font-mono text-2xl ${
          highlight ? "text-accent" : "text-text-primary"
        }`}
      >
        {value}
      </div>
      {sub ? <div className="mt-1 text-xs text-text-muted">{sub}</div> : null}
    </div>
  );
  return href ? <Link href={href}>{inner}</Link> : inner;
}

function QueueCard({
  href,
  title,
  count,
  description,
  isPrimaryCTA = false,
}: {
  href: string;
  title: string;
  count: number;
  description: string;
  isPrimaryCTA?: boolean;
}) {
  return (
    <Link
      href={href}
      className={`group block rounded-lg border bg-surface-elevated p-5 transition ${
        isPrimaryCTA
          ? "border-accent ring-2 ring-accent/30 hover:border-accent"
          : "border-border hover:border-accent/60"
      }`}
    >
      {isPrimaryCTA && (
        <span className="mb-2 inline-block rounded-full bg-accent px-2 py-0.5 text-[10px] uppercase tracking-wider text-white">
          Start here
        </span>
      )}
      <div className="flex items-baseline justify-between">
        <h3 className="text-base font-semibold text-text-primary">{title}</h3>
        <span
          className={`rounded-full px-2 py-0.5 font-mono text-[11px] ${
            count > 0
              ? "bg-accent/20 text-accent"
              : "bg-surface text-text-muted"
          }`}
        >
          {count} pending
        </span>
      </div>
      <p className="mt-2 text-sm text-text-muted">{description}</p>
      <p className="mt-3 text-xs text-accent group-hover:underline">
        Open queue →
      </p>
    </Link>
  );
}

function ToolCard({
  href,
  icon,
  title,
  description,
  glossaryAnchor,
}: {
  href: string;
  icon: string;
  title: string;
  description: string;
  glossaryAnchor: string;
}) {
  return (
    <div className="group relative rounded-lg border border-border bg-surface-elevated p-5 transition hover:border-accent/60">
      <div className="flex items-baseline justify-between gap-3">
        <div className="flex items-baseline gap-2">
          <span aria-hidden="true" className="text-lg">
            {icon}
          </span>
          <h3 className="text-base font-semibold text-text-primary">{title}</h3>
        </div>
        <Tooltip
          content={
            <span>
              <strong className="block text-text-primary">{title}</strong>
              <span className="mt-1 block text-text-muted">
                Open the glossary for the full definition.
              </span>
              <TooltipMore href={glossaryAnchor} />
            </span>
          }
        >
          <button
            type="button"
            className="cursor-help text-[10px] uppercase tracking-wide text-text-muted underline decoration-dotted"
          >
            What is this?
          </button>
        </Tooltip>
      </div>
      <p className="mt-2 text-sm text-text-muted">{description}</p>
      <Link
        href={href}
        className="mt-3 inline-block text-xs text-accent hover:underline"
      >
        Open tool →
      </Link>
    </div>
  );
}

function Activity({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="rounded border border-border bg-surface-elevated px-3 py-2">
      <dt className="text-xs uppercase tracking-wide text-text-muted">{label}</dt>
      <dd className="mt-1 font-mono text-xs text-text-primary">
        {value ? new Date(value).toLocaleString() : "—"}
      </dd>
    </div>
  );
}
