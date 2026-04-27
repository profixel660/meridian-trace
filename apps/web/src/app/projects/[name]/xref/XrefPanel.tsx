"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { ConfirmDialog } from "@/components/review/ConfirmDialog";
import { EmptyState } from "@/components/review/EmptyState";
import {
  KeyboardShortcutSheet,
  type Shortcut,
} from "@/components/review/KeyboardShortcutSheet";
import { RowDetailDrawer } from "@/components/review/RowDetailDrawer";
import { useToasts } from "@/components/review/ToastHost";
import { Tooltip, TooltipMore } from "@/components/review/Tooltip";
import {
  xrefApi,
  type AnchorKind,
  type SweepResponse,
  type XrefFinding,
  type XrefOutcome,
  type XrefReportResponse,
} from "@/lib/apiClient/xref";
import { explainQueueError } from "@/lib/queueClient";

const SHORTCUTS: Shortcut[] = [
  { keys: "S", description: "Run sweep (dry-run preview)" },
  { keys: "P", description: "Run sweep and persist findings" },
  { keys: "L", description: "Toggle LLM-assist" },
  { keys: "Esc", description: "Close drawer / dialog" },
  { keys: "?", description: "Toggle this cheatsheet" },
];

const KNOWN_OUTCOMES: XrefOutcome[] = [
  "confirmed",
  "borderline",
  "external_reference",
  "rejected",
];

const KNOWN_ANCHOR_KINDS: AnchorKind[] = [
  "section",
  "standard",
  "drawing",
  "equipment_tag",
  "owner_id",
  "spec",
];

interface XrefPanelProps {
  projectName: string;
  report: XrefReportResponse | null;
}

type SortKey = "outcome" | "anchor_kind" | "raw_match" | "confidence";

export function XrefPanel({ projectName, report }: XrefPanelProps) {
  const router = useRouter();
  const toasts = useToasts();

  const [llmAssist, setLlmAssist] = useState(false);
  const [confirmPersist, setConfirmPersist] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pageError, setPageError] = useState<unknown>(null);
  const [latestSweep, setLatestSweep] = useState<SweepResponse | null>(null);

  const [outcomeFilter, setOutcomeFilter] = useState<Set<XrefOutcome>>(
    new Set(),
  );
  const [anchorFilter, setAnchorFilter] = useState<Set<AnchorKind>>(new Set());
  const [sortKey, setSortKey] = useState<SortKey>("outcome");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [openFinding, setOpenFinding] = useState<XrefFinding | null>(null);

  const findings = report?.findings ?? [];

  // Derive outcome counts. Unknown outcome strings are bucketed under
  // borderline so reviewers always see them.
  const outcomeCounts = useMemo(() => {
    const counts: Record<string, number> = {
      confirmed: 0,
      borderline: 0,
      external_reference: 0,
      rejected: 0,
    };
    for (const f of findings) {
      const key = KNOWN_OUTCOMES.includes(f.outcome) ? f.outcome : "borderline";
      counts[key] = (counts[key] ?? 0) + 1;
    }
    return counts;
  }, [findings]);

  const filtered = useMemo(() => {
    let rows = findings;
    if (outcomeFilter.size > 0) {
      rows = rows.filter((r) => {
        const key = KNOWN_OUTCOMES.includes(r.outcome)
          ? r.outcome
          : "borderline";
        return outcomeFilter.has(key);
      });
    }
    if (anchorFilter.size > 0) {
      rows = rows.filter((r) => anchorFilter.has(r.anchor_kind));
    }
    const sorted = [...rows].sort((a, b) => {
      const av = String(a[sortKey] ?? "");
      const bv = String(b[sortKey] ?? "");
      const cmp = av.localeCompare(bv);
      return sortDir === "asc" ? cmp : -cmp;
    });
    return sorted;
  }, [findings, outcomeFilter, anchorFilter, sortKey, sortDir]);

  const runSweep = useCallback(
    async (dryRun: boolean) => {
      setBusy(true);
      setPageError(null);
      try {
        const res = await xrefApi.sweep(projectName, {
          dry_run: dryRun,
          llm_assist: llmAssist,
        });
        setLatestSweep(res);
        toasts.success(
          `Sweep ${dryRun ? "preview" : "persisted"} — ${res.findings_total} findings (confirmed ${res.confirmed} / borderline ${res.borderline} / rejected ${res.rejected}).`,
        );
        setConfirmPersist(false);
        if (!dryRun) router.refresh();
      } catch (err) {
        toasts.error(explainQueueError(err), () => void runSweep(dryRun));
        setPageError(err);
      } finally {
        setBusy(false);
      }
    },
    [projectName, llmAssist, toasts, router],
  );

  // Page-level shortcuts.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (confirmPersist || openFinding) return;
      const t = e.target as HTMLElement | null;
      if (
        t &&
        (t.tagName === "INPUT" ||
          t.tagName === "TEXTAREA" ||
          t.isContentEditable)
      )
        return;
      const k = e.key.toLowerCase();
      if (k === "s") {
        e.preventDefault();
        void runSweep(true);
      } else if (k === "p") {
        e.preventDefault();
        setConfirmPersist(true);
      } else if (k === "l") {
        e.preventDefault();
        setLlmAssist((v) => !v);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [confirmPersist, openFinding, runSweep]);

  const toggleOutcome = (o: XrefOutcome) => {
    setOutcomeFilter((prev) => {
      const next = new Set(prev);
      if (next.has(o)) next.delete(o);
      else next.add(o);
      return next;
    });
  };
  const toggleAnchor = (a: AnchorKind) => {
    setAnchorFilter((prev) => {
      const next = new Set(prev);
      if (next.has(a)) next.delete(a);
      else next.add(a);
      return next;
    });
  };
  const clickSort = (k: SortKey) => {
    if (k === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(k);
      setSortDir("asc");
    }
  };

  return (
    <div className="space-y-6">
      <KeyboardShortcutSheet shortcuts={SHORTCUTS} />

      <div className="rounded-md border border-border bg-surface-elevated p-4">
        <div className="flex flex-wrap items-center gap-3">
          <Tooltip
            content={
              <span>
                <strong className="block text-text-primary">
                  Dry-run preview
                </strong>
                <span className="mt-1 block text-text-muted">
                  Generates the CSV + Markdown report in memory without
                  writing to the DB or queueing borderlines for SME
                  review. Safe to run repeatedly.
                </span>
                <TooltipMore href="/glossary#cross-reference-sweep" />
              </span>
            }
          >
            <button
              type="button"
              onClick={() => void runSweep(true)}
              disabled={busy}
              className="rounded-full border border-border bg-surface px-4 py-2 text-sm text-text-primary hover:border-text-muted disabled:opacity-50"
            >
              {busy ? "Working…" : "Dry run (S)"}
            </button>
          </Tooltip>
          <Tooltip
            content={
              <span>
                <strong className="block text-text-primary">
                  Persist findings
                </strong>
                <span className="mt-1 block text-text-muted">
                  Writes findings to <code>cross_reference_sweep_result</code>
                  {" "}and adds borderline matches to the SME review queue.
                  Confirmation required.
                </span>
                <TooltipMore href="/glossary#cross-reference-sweep" />
              </span>
            }
          >
            <button
              type="button"
              onClick={() => setConfirmPersist(true)}
              disabled={busy}
              className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              Persist findings (P)
            </button>
          </Tooltip>
          <Tooltip
            content={
              <span>
                <strong className="block text-text-primary">
                  LLM-assist
                </strong>
                <span className="mt-1 block text-text-muted">
                  Off by default. Enable when many anchors are ambiguous
                  (e.g. abbreviated section refs, vendor names without
                  drawing context). Each ambiguous anchor becomes one
                  extra LLM call — costs scale with sweep size.
                </span>
              </span>
            }
          >
            <label className="ml-auto flex cursor-pointer items-center gap-2 text-xs text-text-muted">
              <input
                type="checkbox"
                checked={llmAssist}
                onChange={(e) => setLlmAssist(e.target.checked)}
              />
              LLM-assist (L) {llmAssist ? "on" : "off"}
            </label>
          </Tooltip>
        </div>
        {latestSweep ? (
          <p className="mt-3 text-xs text-text-muted">
            Last run: {new Date(latestSweep.finished_at).toLocaleString()} •{" "}
            {latestSweep.sources_scanned} sources •{" "}
            {latestSweep.deliverables_scanned} deliverables •{" "}
            {latestSweep.findings_total} findings
            {latestSweep.dry_run ? " (dry run, not persisted)" : ""}.
          </p>
        ) : null}
      </div>

      {pageError ? <ApiErrorPanel error={pageError} /> : null}

      {!report ? (
        <EmptyState
          title="No cross-reference sweep yet"
          body={
            <>
              Press <kbd className="rounded border border-border px-1">S</kbd>{" "}
              (or click <strong>Dry run</strong>) to preview what the sweep
              would find without writing to the DB. When you&apos;re happy,
              press <kbd className="rounded border border-border px-1">P</kbd>{" "}
              to persist — borderline findings will be queued for SME
              review.
            </>
          }
          learnMoreHref="/glossary#cross-reference-sweep"
        />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Counter
              label="Confirmed"
              n={outcomeCounts.confirmed}
              tone="ok"
            />
            <Counter
              label="Borderline"
              n={outcomeCounts.borderline}
              tone="warn"
            />
            <Counter
              label="External"
              n={outcomeCounts.external_reference}
              tone="info"
            />
            <Counter
              label="Rejected"
              n={outcomeCounts.rejected}
              tone="muted"
            />
          </div>

          <div className="rounded-md border border-border bg-surface-elevated p-3">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-text-muted">Outcome:</span>
              {KNOWN_OUTCOMES.map((o) => (
                <FilterPill
                  key={o}
                  label={o}
                  active={outcomeFilter.has(o)}
                  onClick={() => toggleOutcome(o)}
                  tone={outcomeTone(o)}
                />
              ))}
              <span className="ml-4 text-text-muted">Anchor:</span>
              {KNOWN_ANCHOR_KINDS.map((a) => (
                <FilterPill
                  key={a}
                  label={a}
                  active={anchorFilter.has(a)}
                  onClick={() => toggleAnchor(a)}
                  tone="neutral"
                />
              ))}
              {(outcomeFilter.size > 0 || anchorFilter.size > 0) && (
                <button
                  type="button"
                  onClick={() => {
                    setOutcomeFilter(new Set());
                    setAnchorFilter(new Set());
                  }}
                  className="ml-auto text-text-muted hover:text-text-primary"
                >
                  Clear filters
                </button>
              )}
            </div>
          </div>

          {filtered.length === 0 ? (
            <EmptyState
              title="No findings match the active filters"
              body={
                <>
                  Clear the filter pills above to see every finding from
                  the latest sweep.
                </>
              }
            />
          ) : (
            <table className="w-full overflow-hidden rounded-md border border-border bg-surface-elevated text-sm">
              <thead className="bg-surface text-left text-[11px] uppercase tracking-wide text-text-muted">
                <tr>
                  <SortableHeader
                    label="Outcome"
                    active={sortKey === "outcome"}
                    dir={sortDir}
                    onClick={() => clickSort("outcome")}
                    tooltip={{
                      title: "Outcome",
                      body: "confirmed = anchor resolved to a known project source. borderline = ambiguous (queued for SME review on persist). external_reference = legitimately points outside the project. rejected = noisy false positive.",
                      anchor: "/glossary#cross-reference-sweep",
                    }}
                  />
                  <SortableHeader
                    label="Anchor kind"
                    active={sortKey === "anchor_kind"}
                    dir={sortDir}
                    onClick={() => clickSort("anchor_kind")}
                    tooltip={{
                      title: "Anchor kind",
                      body: "What sort of cross-reference token was matched: section number, standards code, drawing ID, equipment tag, owner ID, or spec ref.",
                    }}
                  />
                  <SortableHeader
                    label="Raw match"
                    active={sortKey === "raw_match"}
                    dir={sortDir}
                    onClick={() => clickSort("raw_match")}
                    tooltip={{
                      title: "Raw match",
                      body: "The exact substring extracted from the source. Open the row drawer to see the surrounding excerpt.",
                    }}
                  />
                  <SortableHeader
                    label="Confidence"
                    active={sortKey === "confidence"}
                    dir={sortDir}
                    onClick={() => clickSort("confidence")}
                    tooltip={{
                      title: "Confidence",
                      body: "Per-finding confidence — high / medium / low.",
                      anchor: "/glossary#confidence",
                    }}
                  />
                  <th scope="col" className="px-3 py-2">
                    Detection
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((f, i) => (
                  <tr
                    key={`${f.from_source_id}-${f.raw_match}-${i}`}
                    onClick={() => setOpenFinding(f)}
                    className="cursor-pointer border-t border-border hover:bg-surface"
                  >
                    <td className="px-3 py-2">
                      <OutcomeBadge outcome={f.outcome} />
                    </td>
                    <td className="px-3 py-2 text-text-primary">
                      {f.anchor_kind}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-text-primary">
                      {f.raw_match}
                    </td>
                    <td className="px-3 py-2 text-xs text-text-muted">
                      {f.confidence}
                    </td>
                    <td className="px-3 py-2 text-xs text-text-muted">
                      {f.detection_method}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      <RowDetailDrawer
        open={openFinding !== null}
        title={openFinding?.raw_match ?? "Finding"}
        subtitle={
          openFinding
            ? `${openFinding.anchor_kind} • ${openFinding.detection_method}`
            : undefined
        }
        onClose={() => setOpenFinding(null)}
      >
        {openFinding ? (
          <dl className="space-y-3 text-sm">
            <Field label="Outcome">
              <OutcomeBadge outcome={openFinding.outcome} />
            </Field>
            <Field label="From source">
              <span className="font-mono text-xs">
                {openFinding.from_source_id}
              </span>
            </Field>
            <Field label="To source">
              <span className="font-mono text-xs">
                {openFinding.to_source_id ?? "—"}
              </span>
            </Field>
            <Field label="To deliverable">
              <span className="font-mono text-xs">
                {openFinding.to_deliverable_id ?? "—"}
              </span>
            </Field>
            <Field label="Raw match">
              <code className="rounded bg-background px-2 py-0.5 text-xs">
                {openFinding.raw_match}
              </code>
            </Field>
            <Field label="Normalised">
              <code className="rounded bg-background px-2 py-0.5 text-xs">
                {openFinding.normalised_match}
              </code>
            </Field>
            <Field label="Confidence">{openFinding.confidence}</Field>
            <Field label="Detection method">
              {openFinding.detection_method}
            </Field>
            <Field label="Reason">
              {openFinding.reason ?? "—"}
            </Field>
            {openFinding.review_question_id ? (
              <Field label="Review question">
                <span className="font-mono text-xs">
                  {openFinding.review_question_id}
                </span>
              </Field>
            ) : null}
          </dl>
        ) : null}
      </RowDetailDrawer>

      <ConfirmDialog
        open={confirmPersist}
        title="Persist sweep findings to the project DB?"
        destructive={false}
        body={
          <span>
            Findings will be written to{" "}
            <code>cross_reference_sweep_result</code>. Borderline findings
            will be added to the <strong>SME review queue</strong> for
            human triage. Confirmed and external_reference rows go in
            without further review; rejected rows are kept for the audit
            trail. Dry-run first if you want a preview.
          </span>
        }
        confirmLabel="Run + persist"
        busy={busy}
        onCancel={() => (busy ? undefined : setConfirmPersist(false))}
        onConfirm={() => void runSweep(false)}
      />
    </div>
  );
}

function outcomeTone(o: XrefOutcome): "ok" | "warn" | "info" | "muted" {
  if (o === "confirmed") return "ok";
  if (o === "borderline") return "warn";
  if (o === "external_reference") return "info";
  return "muted";
}

function OutcomeBadge({ outcome }: { outcome: XrefOutcome }) {
  const tone = outcomeTone(outcome);
  const cls =
    tone === "ok"
      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
      : tone === "warn"
        ? "border-amber-500/40 bg-amber-500/10 text-amber-300"
        : tone === "info"
          ? "border-sky-500/40 bg-sky-500/10 text-sky-300"
          : "border-border bg-surface-elevated text-text-muted";
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${cls}`}
    >
      {outcome}
    </span>
  );
}

function Counter({
  label,
  n,
  tone,
}: {
  label: string;
  n: number;
  tone: "ok" | "warn" | "info" | "muted";
}) {
  const colour =
    tone === "ok"
      ? "text-emerald-300"
      : tone === "warn"
        ? "text-amber-300"
        : tone === "info"
          ? "text-sky-300"
          : "text-text-muted";
  return (
    <div className="rounded-md border border-border bg-surface-elevated p-3">
      <div className="text-[11px] uppercase tracking-wide text-text-muted">
        {label}
      </div>
      <div className={`mt-1 font-mono text-2xl ${colour}`}>{n}</div>
    </div>
  );
}

function FilterPill({
  label,
  active,
  onClick,
  tone,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  tone: "ok" | "warn" | "info" | "muted" | "neutral";
}) {
  const baseCls = active
    ? tone === "ok"
      ? "border-emerald-500/60 bg-emerald-500/20 text-emerald-200"
      : tone === "warn"
        ? "border-amber-500/60 bg-amber-500/20 text-amber-200"
        : tone === "info"
          ? "border-sky-500/60 bg-sky-500/20 text-sky-200"
          : "border-accent/60 bg-accent/20 text-accent"
    : "border-border bg-surface-elevated text-text-muted hover:border-text-muted";
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full border px-2 py-0.5 text-[11px] ${baseCls}`}
    >
      {label}
    </button>
  );
}

function SortableHeader({
  label,
  active,
  dir,
  onClick,
  tooltip,
}: {
  label: string;
  active: boolean;
  dir: "asc" | "desc";
  onClick: () => void;
  tooltip: { title: string; body: string; anchor?: string };
}) {
  return (
    <th scope="col" className="px-3 py-2">
      <Tooltip
        content={
          <span>
            <strong className="block text-text-primary">{tooltip.title}</strong>
            <span className="mt-1 block text-text-muted">{tooltip.body}</span>
            {tooltip.anchor ? <TooltipMore href={tooltip.anchor} /> : null}
          </span>
        }
      >
        <button
          type="button"
          onClick={onClick}
          className={`cursor-pointer underline decoration-dotted ${
            active ? "text-text-primary" : ""
          }`}
        >
          {label}
          {active ? (dir === "asc" ? " ↑" : " ↓") : ""}
        </button>
      </Tooltip>
    </th>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="border-b border-border/60 pb-1">
      <dt className="text-[10px] uppercase tracking-wide text-text-muted">
        {label}
      </dt>
      <dd className="mt-1 text-sm text-text-primary">{children}</dd>
    </div>
  );
}
