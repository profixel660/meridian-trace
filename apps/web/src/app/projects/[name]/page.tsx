"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";

import { AuthGate } from "@/components/AuthGate";
import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import { StatusBadge } from "@/components/review/StatusBadge";
import { Tooltip, TooltipMore } from "@/components/review/Tooltip";
import { type ProjectCoverage } from "@/lib/api";
import { apiFetch } from "@/lib/fetcher";

export default function ProjectDashboardPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = use(params);
  const [coverage, setCoverage] = useState<ProjectCoverage | null>(null);
  const [coverageError, setCoverageError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
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
        <DashboardBody projectName={name} coverage={coverage} />
      ) : loading ? (
        <div className="text-text-muted text-sm">Loading…</div>
      ) : null}
    </ReviewLayout>
  );
}

function DashboardBody({
  projectName,
  coverage,
}: {
  projectName: string;
  coverage: ProjectCoverage;
}) {
  const base = `/projects/${encodeURIComponent(projectName)}`;
  return (
    <div className="space-y-8">
      <BaselineBanner coverage={coverage} />

      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard
          label="Sources"
          value={coverage.sources_imported}
          sub={`${coverage.sources_extracted} extracted, ${coverage.sources_pending_extraction} pending`}
          href={`${base}/sources`}
        />
        <KpiCard
          label="On master register"
          value={coverage.deliverable_status.auto_approved + coverage.deliverable_status.user_accepted + coverage.deliverable_status.user_edited + coverage.deliverable_status.user_promoted}
          sub={`${coverage.deliverable_status.total} extracted total`}
          href={`${base}/master`}
        />
        <KpiCard
          label="Pending decisions"
          value={coverage.pending_decisions}
          sub="across all queues"
          highlight={coverage.pending_decisions > 0}
        />
        <KpiCard
          label="Auto-route rate"
          value={`${coverage.auto_route_rate_pct.toFixed(1)}%`}
          sub={`${coverage.review_coverage_pct.toFixed(1)}% reviewed`}
          glossaryAnchor="/glossary#auto-route-rate"
        />
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <QueueCard
          href={`${base}/quarantine`}
          title="Quarantine"
          count={coverage.deliverable_status.quarantined}
          description="Deliverables held back from the master register pending your decision. Accept, edit, or reject."
        />
        <QueueCard
          href={`${base}/audit`}
          title="Audit (OUTSIDE)"
          count={coverage.audit.pending}
          description="Candidates the gate ruled OUTSIDE the deliverable definition. Promote any that should actually be on the register."
        />
        <QueueCard
          href={`${base}/questions`}
          title="Questions"
          count={coverage.questions.pending}
          description="Open questions raised by the AI during extraction. Resolve to remove the qualifier from impacted rows."
        />
        <QueueCard
          href={`${base}/conflicts`}
          title="Conflicts"
          count={coverage.conflicts.pending}
          description="Two or more sources disagree about the same item. Pick the winning side, accept a hybrid, or reject both."
        />
        <QueueCard
          href={`${base}/taxonomy`}
          title="Taxonomy proposals"
          count={coverage.taxonomy.pending_proposals}
          description="New trade / service / category values the AI proposed. Confirm, merge into an existing value, or reject."
        />
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-text-muted">
          Hand-off &amp; integrity
        </h2>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <ToolCard
            href={`${base}/tender`}
            icon="📦"
            title="Tender Package Builder"
            description="Generate per-trade subcontractor packages (xlsx / md) from accepted deliverables."
            glossaryAnchor="/glossary#tender-package"
          />
          <ToolCard
            href={`${base}/evidence`}
            icon="⚖️"
            title="Legal Evidence Pack"
            description="Assemble a defensible audit-trail zip — sources, prompts, LLM calls, register — for legal hand-off."
            glossaryAnchor="/glossary#legal-evidence-pack"
          />
          <ToolCard
            href={`${base}/xref`}
            icon="🔗"
            title="Cross-reference sweep"
            description="Exhaustively scan documents for references to other project sources; queue ambiguous matches for SME review."
            glossaryAnchor="/glossary#cross-reference-sweep"
          />
        </div>
      </section>

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
    </div>
  );
}

function BaselineBanner({ coverage }: { coverage: ProjectCoverage }) {
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
}: {
  href: string;
  title: string;
  count: number;
  description: string;
}) {
  return (
    <Link
      href={href}
      className="group block rounded-lg border border-border bg-surface-elevated p-5 transition hover:border-accent/60"
    >
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
