"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { ConfirmDialog } from "@/components/review/ConfirmDialog";
import { useToasts } from "@/components/review/ToastHost";
import { Tooltip, TooltipMore } from "@/components/review/Tooltip";
import type { TaxonomyProposal } from "@/lib/api";
import { explainQueueError, queueClient } from "@/lib/queueClient";

type Pending =
  | { kind: "confirm"; proposal: TaxonomyProposal }
  | { kind: "merge"; proposal: TaxonomyProposal; target: string }
  | { kind: "reject"; proposal: TaxonomyProposal };

const TABLE_LABEL: Record<TaxonomyProposal["table"], string> = {
  trade: "Trade",
  service: "Service",
  category: "Category",
};

// ── Round-15 auto-assessment styling. The pill is colour-coded *and*
// labelled — colour alone is not a discoverable signal (see UX
// discoverability rule). Tooltip carries the confidence + reasoning so the
// SME has the full context before clicking accept.
function llmPillClass(action: TaxonomyProposal["llm_recommended_action"]): string {
  switch (action) {
    case "confirm":
      return "border-emerald-500/40 bg-emerald-500/10 text-emerald-300";
    case "merge_into":
      return "border-amber-500/40 bg-amber-500/10 text-amber-300";
    case "defer_to_user":
      return "border-border bg-surface-elevated text-text-muted";
    default:
      // legacy null
      return "border-border bg-surface-elevated text-text-muted opacity-60";
  }
}

function llmPillLabel(p: TaxonomyProposal): string {
  switch (p.llm_recommended_action) {
    case "confirm":
      return "LLM: confirm";
    case "merge_into":
      return `LLM: merge → ${p.llm_merge_target ?? "?"}`;
    case "defer_to_user":
      return "LLM: defer";
    default:
      return "LLM: n/a";
  }
}

function formatConfidence(c: number | null | undefined): string {
  if (c == null) return "no confidence reported";
  return `${Math.round(c * 100)}% confidence`;
}

export function TaxonomyQueue({
  projectName,
  proposals,
  canonicalByTable,
}: {
  projectName: string;
  proposals: TaxonomyProposal[];
  canonicalByTable: Record<string, string[]>;
}) {
  const router = useRouter();
  const toasts = useToasts();
  const [mergeTargets, setMergeTargets] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = useCallback(async () => {
    if (!pending) return;
    setBusy(true);
    try {
      if (pending.kind === "confirm") {
        await queueClient.confirmTaxonomy(projectName, {
          table: pending.proposal.table,
          value: pending.proposal.value,
        });
        toasts.success(
          `Confirmed "${pending.proposal.value}" as a canonical ${pending.proposal.table}.`,
        );
      } else if (pending.kind === "merge") {
        await queueClient.mergeTaxonomy(projectName, {
          table: pending.proposal.table,
          source_value: pending.proposal.value,
          target_value: pending.target,
        });
        toasts.success(
          `Merged "${pending.proposal.value}" → "${pending.target}".`,
        );
      } else {
        await queueClient.rejectTaxonomy(projectName, {
          table: pending.proposal.table,
          value: pending.proposal.value,
        });
        toasts.success(
          `Rejected proposal "${pending.proposal.value}".`,
        );
      }
      setPending(null);
      router.refresh();
    } catch (err) {
      toasts.error(explainQueueError(err), () => void submit());
    } finally {
      setBusy(false);
    }
  }, [pending, projectName, router, toasts]);

  return (
    <div className="space-y-4">
      <ul className="space-y-3">
        {proposals.map((p) => {
          const targets = canonicalByTable[p.table] ?? [];
          const target = mergeTargets[p.id] ?? "";
          return (
            <li
              key={p.id}
              className="rounded-md border border-border bg-surface-elevated p-4"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-3">
                  <span className="rounded-full border border-border px-2 py-0.5 text-[10px] uppercase tracking-wide text-text-muted">
                    {TABLE_LABEL[p.table]}
                  </span>
                  <h3 className="text-base font-semibold text-text-primary">
                    {p.value}
                  </h3>
                  <Tooltip
                    content={
                      <span>
                        <strong className="block text-text-primary">
                          In-use count
                        </strong>
                        <span className="mt-1 block text-text-muted">
                          Number of deliverables that currently reference this
                          taxonomy value. Merges cascade across all of them.
                        </span>
                        <TooltipMore href="/glossary#taxonomy-proposal" />
                      </span>
                    }
                  >
                    <button
                      type="button"
                      className="cursor-help rounded-full border border-border px-2 py-0.5 font-mono text-[10px] text-text-muted"
                    >
                      {p.in_use_count} in use
                    </button>
                  </Tooltip>

                  {/* Round-15 auto-assessment pill (DECISIONS.md §3.10).
                      Pill is labelled (not colour-only) and the tooltip carries
                      confidence + reasoning so the SME has full context. */}
                  <Tooltip
                    content={
                      <span>
                        <strong className="block text-text-primary">
                          LLM auto-assessment
                        </strong>
                        <span className="mt-1 block text-text-muted">
                          {p.llm_recommended_action == null ? (
                            <>
                              No recommendation available — this proposal
                              pre-dates the bootstrap auto-assessment pass.
                            </>
                          ) : (
                            <>
                              <span className="block">
                                Recommendation:{" "}
                                <strong className="text-text-primary">
                                  {p.llm_recommended_action === "merge_into"
                                    ? `merge into "${p.llm_merge_target ?? "?"}"`
                                    : p.llm_recommended_action === "confirm"
                                      ? "confirm as canonical"
                                      : "defer to your judgement"}
                                </strong>
                              </span>
                              <span className="block">
                                {formatConfidence(p.llm_confidence)}
                              </span>
                              {p.llm_reasoning ? (
                                <span className="mt-1 block italic">
                                  “{p.llm_reasoning}”
                                </span>
                              ) : null}
                            </>
                          )}
                        </span>
                        <TooltipMore href="/glossary#taxonomy-proposal" />
                      </span>
                    }
                  >
                    <button
                      type="button"
                      className={`inline-flex cursor-help items-center rounded-full border px-2 py-0.5 font-mono text-[10px] tracking-tight ${llmPillClass(
                        p.llm_recommended_action,
                      )}`}
                    >
                      {llmPillLabel(p)}
                    </button>
                  </Tooltip>
                </div>
                <span className="text-[11px] text-text-muted">
                  proposed {new Date(p.created_at).toLocaleDateString()} via{" "}
                  {p.source}
                </span>
              </div>

              <div className="mt-4 flex flex-wrap items-center gap-2">
                {/* Accept-LLM is the leftmost option when a usable
                    recommendation is present. Disabled (with tooltip) when
                    the LLM said 'defer_to_user' or the row is legacy. */}
                {p.llm_recommended_action === "confirm" ||
                (p.llm_recommended_action === "merge_into" && p.llm_merge_target) ? (
                  <button
                    type="button"
                    onClick={() => {
                      if (p.llm_recommended_action === "confirm") {
                        setPending({ kind: "confirm", proposal: p });
                      } else {
                        setPending({
                          kind: "merge",
                          proposal: p,
                          target: p.llm_merge_target ?? "",
                        });
                      }
                    }}
                    className="rounded-full bg-emerald-500/20 px-4 py-2 text-sm font-medium text-emerald-200 ring-1 ring-emerald-500/40 hover:bg-emerald-500/30"
                  >
                    Accept LLM recommendation
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => setPending({ kind: "confirm", proposal: p })}
                  className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90"
                >
                  Confirm
                </button>
                <div className="flex items-center gap-2">
                  <select
                    value={target}
                    onChange={(e) =>
                      setMergeTargets({
                        ...mergeTargets,
                        [p.id]: e.target.value,
                      })
                    }
                    className="rounded border border-border bg-background px-2 py-1.5 text-sm text-text-primary focus:border-accent focus:outline-none"
                  >
                    <option value="">Merge into…</option>
                    {targets
                      .filter((t) => t !== p.value)
                      .map((t) => (
                        <option key={t} value={t}>
                          {t}
                        </option>
                      ))}
                  </select>
                  <button
                    type="button"
                    disabled={!target}
                    onClick={() =>
                      setPending({ kind: "merge", proposal: p, target })
                    }
                    className="rounded-full border border-border px-4 py-2 text-sm text-text-primary hover:border-text-muted disabled:opacity-50"
                  >
                    Merge →
                  </button>
                </div>
                <button
                  type="button"
                  onClick={() => setPending({ kind: "reject", proposal: p })}
                  className="ml-auto rounded-full border border-red-500/50 bg-red-500/10 px-4 py-2 text-sm text-red-300 hover:bg-red-500/20"
                >
                  Reject
                </button>
              </div>
            </li>
          );
        })}
      </ul>

      <ConfirmDialog
        open={pending?.kind === "confirm"}
        title="Confirm taxonomy value?"
        body={
          <span>
            Adopting <strong>“{pending?.proposal.value}”</strong> as a
            canonical {pending?.proposal.table}. Future extractions will use
            it without further prompting.
          </span>
        }
        confirmLabel="Confirm value"
        destructive={false}
        busy={busy}
        onCancel={() => (busy ? undefined : setPending(null))}
        onConfirm={submit}
      />

      <ConfirmDialog
        open={pending?.kind === "merge"}
        title="Merge taxonomy value?"
        body={
          <span>
            <strong>“{pending?.kind === "merge" ? pending.proposal.value : ""}”</strong>{" "}
            will be folded into{" "}
            <strong>“{pending?.kind === "merge" ? pending.target : ""}”</strong>
            . All{" "}
            <strong>
              {pending?.kind === "merge"
                ? pending.proposal.in_use_count
                : 0}{" "}
              deliverable
              {pending?.kind === "merge" && pending.proposal.in_use_count === 1
                ? ""
                : "s"}
            </strong>{" "}
            currently using it will be repointed. The original value is
            soft-deleted and added to the target&apos;s synonyms.
          </span>
        }
        confirmLabel="Merge values"
        busy={busy}
        onCancel={() => (busy ? undefined : setPending(null))}
        onConfirm={submit}
      />

      <ConfirmDialog
        open={pending?.kind === "reject"}
        title="Reject taxonomy proposal?"
        body={
          <span>
            <strong>
              “{pending?.kind === "reject" ? pending.proposal.value : ""}”
            </strong>{" "}
            will be soft-deleted. If any deliverable still references it the
            backend will refuse — re-edit those rows first.
          </span>
        }
        confirmLabel="Reject proposal"
        busy={busy}
        onCancel={() => (busy ? undefined : setPending(null))}
        onConfirm={submit}
      />
    </div>
  );
}
