"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/review/ConfirmDialog";
import {
  KeyboardShortcutSheet,
  type Shortcut,
} from "@/components/review/KeyboardShortcutSheet";
import { StatusBadge } from "@/components/review/StatusBadge";
import { Tooltip, TooltipMore } from "@/components/review/Tooltip";
import { useToasts } from "@/components/review/ToastHost";
import type { ConflictItem, ConflictParty } from "@/lib/api";
import { explainQueueError, queueClient } from "@/lib/queueClient";

const SHORTCUTS: Shortcut[] = [
  { keys: "A", description: "Accept party A" },
  { keys: "R", description: "Reject both parties" },
  { keys: "S", description: "Skip (next conflict)" },
  { keys: "← / →", description: "Previous / next conflict" },
  { keys: "?", description: "Toggle this cheatsheet" },
];

type Pending =
  | { kind: "accept_a" | "accept_b"; party: ConflictParty }
  | { kind: "reject_both" };

export function ConflictsQueue({
  projectName,
  items,
}: {
  projectName: string;
  items: ConflictItem[];
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const toasts = useToasts();

  const [selectedIdx, setSelectedIdx] = useState(0);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const focusId = searchParams.get("focus");
    if (!focusId) return;
    const idx = items.findIndex((c) => c.id === focusId);
    if (idx >= 0) {
      setSelectedIdx(idx);
    } else if (items.length > 0) {
      // Conflict already resolved or superseded since the register loaded
      toasts.info(
        "This conflict has been resolved or superseded since you opened the register.",
      );
    }
  }, [searchParams, items, toasts]);

  const selected = items[selectedIdx] ?? null;
  const partyA = selected?.parties[0];
  const partyB = selected?.parties[1];

  const move = useCallback(
    (delta: number) => {
      setSelectedIdx((idx) => {
        const n = items.length;
        if (n === 0) return 0;
        return (idx + delta + n) % n;
      });
    },
    [items.length],
  );

  const submit = useCallback(async () => {
    if (!selected || !pending) return;
    setBusy(true);
    try {
      if (pending.kind === "reject_both") {
        await queueClient.resolveConflict(projectName, selected.id, {
          action: "reject_both",
        });
        toasts.success("Both parties rejected. Conflict resolved.");
      } else {
        await queueClient.resolveConflict(projectName, selected.id, {
          action: pending.kind,
          accept_party_id: pending.party.party_id,
        });
        toasts.success("Conflict resolved.");
      }
      try {
        window.localStorage.setItem(
          "meridian.conflicts.last_resolved_at",
          new Date().toISOString(),
        );
        // Also dispatch a storage-equivalent event for same-tab consumers
        window.dispatchEvent(new Event("storage"));
      } catch {
        // sessionStorage blocked — HierarchyView falls back to focus event
      }
      setPending(null);
      router.push(`/projects/${encodeURIComponent(projectName)}/conflict-register`);
    } catch (err) {
      toasts.error(explainQueueError(err), () => void submit());
    } finally {
      setBusy(false);
    }
  }, [selected, pending, projectName, router, toasts]);

  const onShortcut = useCallback(
    (key: string) => {
      if (pending) return;
      if (!selected) return;
      switch (key) {
        case "a":
          if (partyA) setPending({ kind: "accept_a", party: partyA });
          break;
        case "r":
          setPending({ kind: "reject_both" });
          break;
        case "s":
        case "ArrowRight":
          move(1);
          break;
        case "ArrowLeft":
          move(-1);
          break;
      }
    },
    [pending, selected, partyA, move],
  );

  return (
    <div className="space-y-4">
      <KeyboardShortcutSheet shortcuts={SHORTCUTS} onAction={onShortcut} />

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-surface-elevated px-4 py-2 text-xs text-text-muted">
        <span>
          {items.length} pending conflict{items.length === 1 ? "" : "s"} •
          Showing {Math.min(selectedIdx + 1, items.length)} of {items.length}
        </span>
        <span>
          Conflict kinds: cross-source content, responsibility, revision,
          document class, scope demarcation.
        </span>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_2fr]">
        <ul className="max-h-[70vh] space-y-1 overflow-y-auto rounded-md border border-border bg-surface-elevated p-2">
          {items.map((c, i) => (
            <li key={c.id}>
              <button
                type="button"
                onClick={() => setSelectedIdx(i)}
                className={`flex w-full flex-col gap-1 rounded-md border px-3 py-2 text-left text-sm transition ${
                  i === selectedIdx
                    ? "border-accent/60 bg-accent/10 text-text-primary"
                    : "border-transparent hover:border-border hover:bg-surface text-text-muted"
                }`}
              >
                <span className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
                  {c.kind}
                </span>
                <span className="line-clamp-2 leading-snug">
                  {c.parties[0]?.summary_or_text ?? "(no party text)"}
                </span>
                <span className="text-[10px] text-text-muted">
                  vs {c.parties.length - 1} other party{c.parties.length === 2 ? "" : "ies"}
                </span>
              </button>
            </li>
          ))}
        </ul>

        <div className="space-y-4 rounded-md border border-border bg-surface-elevated p-5">
          {selected ? (
            <>
              <div className="flex items-center justify-between">
                <h2 className="text-base font-semibold text-text-primary">
                  Conflict: {selected.kind}
                </h2>
                <Tooltip
                  content={
                    <span>
                      <strong className="block text-text-primary">
                        Most-onerous principle
                      </strong>
                      <span className="mt-1 block text-text-muted">
                        When sources disagree, Meridian flags the more
                        demanding interpretation as the safer default. You
                        can accept it, accept the other side, or reject both.
                      </span>
                      <TooltipMore href="/glossary#most-onerous" />
                    </span>
                  }
                >
                  <button
                    type="button"
                    className="cursor-help text-[11px] text-accent underline decoration-dotted"
                  >
                    Most onerous?
                  </button>
                </Tooltip>
              </div>

              {selected.most_onerous_reasoning ? (
                <p className="rounded border border-border bg-background p-3 text-xs text-text-muted">
                  <span className="font-semibold text-text-primary">
                    AI reasoning:{" "}
                  </span>
                  {selected.most_onerous_reasoning}
                </p>
              ) : null}

              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {selected.parties.map((p, i) => {
                  const isMostOnerous =
                    selected.most_onerous_party_id === p.party_id;
                  return (
                    <article
                      key={p.party_id}
                      className={`rounded-md border p-4 text-sm ${
                        isMostOnerous
                          ? "border-amber-500/50 bg-amber-500/5"
                          : "border-border bg-background"
                      }`}
                    >
                      <header className="flex items-center justify-between">
                        <span className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
                          Party {i === 0 ? "A" : "B"} • {p.party_kind}
                        </span>
                        {isMostOnerous ? (
                          <StatusBadge
                            label="most onerous"
                            variant="warn"
                            explanation={{
                              title: "Most onerous (AI suggestion)",
                              body: "The AI considers this side the safer / more demanding interpretation. You can still accept the other party.",
                            }}
                            glossaryAnchor="/glossary#most-onerous"
                          />
                        ) : null}
                      </header>
                      <p className="mt-2 whitespace-pre-wrap leading-snug text-text-primary">
                        {p.summary_or_text || "(no text)"}
                      </p>
                      {p.party_position ? (
                        <p className="mt-2 text-[11px] text-text-muted">
                          Position: <strong>{p.party_position}</strong>
                        </p>
                      ) : null}
                      <button
                        type="button"
                        onClick={() =>
                          setPending({
                            kind: i === 0 ? "accept_a" : "accept_b",
                            party: p,
                          })
                        }
                        className="mt-3 rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white hover:opacity-90"
                      >
                        Accept party {i === 0 ? "A" : "B"}
                      </button>
                    </article>
                  );
                })}
              </div>

              <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
                <button
                  type="button"
                  onClick={() => setPending({ kind: "reject_both" })}
                  className="rounded-full border border-red-500/50 bg-red-500/10 px-4 py-2 text-sm text-red-300 hover:bg-red-500/20"
                >
                  Reject both (R)
                </button>
                <button
                  type="button"
                  onClick={() => move(1)}
                  className="ml-auto rounded-full border border-border px-4 py-2 text-sm text-text-muted hover:text-text-primary"
                >
                  Skip (S) →
                </button>
              </div>
            </>
          ) : null}
        </div>
      </div>

      <ConfirmDialog
        open={pending?.kind === "accept_a" || pending?.kind === "accept_b"}
        title="Accept this party?"
        body={
          <span>
            Accepting <strong>party {pending?.kind === "accept_a" ? "A" : "B"}</strong>{" "}
            will keep that party&apos;s deliverable on the master register and
            mark the other party rejected. The conflict will close.
          </span>
        }
        confirmLabel="Accept party"
        destructive={false}
        busy={busy}
        onCancel={() => (busy ? undefined : setPending(null))}
        onConfirm={submit}
      />

      <ConfirmDialog
        open={pending?.kind === "reject_both"}
        title="Reject both parties?"
        body={
          <span>
            Both deliverables in this conflict will be marked rejected and
            removed from the master register. They&apos;ll remain in the audit
            trail.
          </span>
        }
        confirmLabel="Reject both"
        busy={busy}
        onCancel={() => (busy ? undefined : setPending(null))}
        onConfirm={submit}
      />
    </div>
  );
}
