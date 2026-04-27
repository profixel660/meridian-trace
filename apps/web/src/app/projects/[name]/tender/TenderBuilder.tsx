"use client";

import { useCallback, useState } from "react";

import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import {
  KeyboardShortcutSheet,
  type Shortcut,
} from "@/components/review/KeyboardShortcutSheet";
import { RowDetailDrawer } from "@/components/review/RowDetailDrawer";
import { useToasts } from "@/components/review/ToastHost";
import { Tooltip, TooltipMore } from "@/components/review/Tooltip";
import {
  tenderApi,
  type TenderBuildResponse,
  type TradeCount,
} from "@/lib/apiClient/tender";
import { explainQueueError } from "@/lib/queueClient";

const SHORTCUTS: Shortcut[] = [
  { keys: "B", description: "Open Build dialog for the selected trade" },
  { keys: "↑ / ↓", description: "Move selection between trades" },
  { keys: "Esc", description: "Close the Build dialog" },
  { keys: "?", description: "Toggle this cheatsheet" },
];

interface TenderBuilderProps {
  projectName: string;
  trades: TradeCount[];
  defaultOutputDir: string | null;
}

export function TenderBuilder({
  projectName,
  trades,
  defaultOutputDir,
}: TenderBuilderProps) {
  const toasts = useToasts();

  const [selectedIdx, setSelectedIdx] = useState(0);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [format, setFormat] = useState<"xlsx" | "md">("xlsx");
  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState<TenderBuildResponse | null>(
    null,
  );
  const [pageError, setPageError] = useState<unknown>(null);

  const selected = trades[selectedIdx] ?? null;

  const onBuild = useCallback(async () => {
    if (!selected) return;
    setBusy(true);
    setPageError(null);
    try {
      const result = await tenderApi.build(projectName, {
        trade: selected.trade,
        format,
      });
      setLastResult(result);
      toasts.success(
        `Built ${format.toUpperCase()} tender package for "${selected.trade}".`,
      );
      setDrawerOpen(false);
    } catch (err) {
      // Toast for the immediate feedback, panel for persistent diagnosis.
      toasts.error(explainQueueError(err), () => void onBuild());
      setPageError(err);
    } finally {
      setBusy(false);
    }
  }, [selected, projectName, format, toasts]);

  const openBuildDialog = useCallback(() => {
    if (!selected) return;
    setFormat("xlsx");
    setDrawerOpen(true);
  }, [selected]);

  const onShortcut = useCallback(
    (key: string) => {
      if (drawerOpen) return;
      if (key === "ArrowRight" || key === "ArrowDown" || key === "s") {
        setSelectedIdx((idx) => (idx + 1) % Math.max(trades.length, 1));
      } else if (key === "ArrowLeft" || key === "ArrowUp") {
        setSelectedIdx(
          (idx) =>
            (idx - 1 + Math.max(trades.length, 1)) %
            Math.max(trades.length, 1),
        );
      } else if (key === "b") {
        openBuildDialog();
      }
    },
    [drawerOpen, trades.length, openBuildDialog],
  );

  // The shortcut sheet only forwards a/r/e/s/←/→. Wire `b` ourselves.
  // (Following the conflicts queue precedent — see ConflictsQueue.tsx.)
  const handleBuildKey = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key.toLowerCase() === "b") openBuildDialog();
    },
    [openBuildDialog],
  );

  return (
    <div
      className="space-y-4"
      onKeyDown={handleBuildKey}
    >
      <KeyboardShortcutSheet shortcuts={SHORTCUTS} onAction={onShortcut} />

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-surface-elevated px-4 py-2 text-xs text-text-muted">
        <span>
          {trades.length} trade{trades.length === 1 ? "" : "s"} ready to
          package
          {defaultOutputDir ? (
            <>
              {" "}• default output:{" "}
              <code className="text-text-primary">{defaultOutputDir}</code>
            </>
          ) : null}
        </span>
        <span>
          Tip: press{" "}
          <kbd className="rounded border border-border px-1">B</kbd> to build
          the selected trade,{" "}
          <kbd className="rounded border border-border px-1">?</kbd> for all
          shortcuts.
        </span>
      </div>

      {pageError ? <ApiErrorPanel error={pageError} /> : null}

      <table className="w-full overflow-hidden rounded-md border border-border bg-surface-elevated text-sm">
        <thead className="bg-surface text-left text-[11px] uppercase tracking-wide text-text-muted">
          <tr>
            <th scope="col" className="px-4 py-2">
              <Tooltip
                content={
                  <span>
                    <strong className="block text-text-primary">Trade</strong>
                    <span className="mt-1 block text-text-muted">
                      Trade name as stored in the project taxonomy. Rows
                      labelled <code>(unassigned)</code> indicate accepted
                      deliverables that still need a trade tag — review
                      them in the master register before issuing.
                    </span>
                    <TooltipMore href="/glossary#taxonomy-proposal" />
                  </span>
                }
              >
                <button type="button" className="cursor-help underline decoration-dotted">
                  Trade
                </button>
              </Tooltip>
            </th>
            <th scope="col" className="px-4 py-2 text-right">
              <Tooltip
                content={
                  <span>
                    <strong className="block text-text-primary">
                      Deliverable count
                    </strong>
                    <span className="mt-1 block text-text-muted">
                      Number of <em>accepted</em> deliverables in this
                      trade. Quarantined and rejected rows are excluded.
                    </span>
                    <TooltipMore href="/glossary#deliverable-status" />
                  </span>
                }
              >
                <button type="button" className="cursor-help underline decoration-dotted">
                  Accepted deliverables
                </button>
              </Tooltip>
            </th>
            <th scope="col" className="px-4 py-2 text-right">
              <Tooltip
                content={
                  <span>
                    <strong className="block text-text-primary">Build</strong>
                    <span className="mt-1 block text-text-muted">
                      Open the build dialog (xlsx or md). Building a tender
                      package is purely an assembly step — no LLM calls,
                      no DB writes to deliverable status.
                    </span>
                    <TooltipMore href="/glossary#tender-package" />
                  </span>
                }
              >
                <button type="button" className="cursor-help underline decoration-dotted">
                  Action
                </button>
              </Tooltip>
            </th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => {
            const isSelected = i === selectedIdx;
            const unassigned = t.trade === "(unassigned)";
            return (
              <tr
                key={t.trade}
                onClick={() => setSelectedIdx(i)}
                className={`cursor-pointer border-t border-border transition ${
                  isSelected
                    ? "bg-accent/10"
                    : "hover:bg-surface"
                }`}
              >
                <td className="px-4 py-2 text-text-primary">
                  {unassigned ? (
                    <span className="text-amber-300">{t.trade}</span>
                  ) : (
                    t.trade
                  )}
                </td>
                <td className="px-4 py-2 text-right font-mono text-text-primary">
                  {t.deliverable_count}
                </td>
                <td className="px-4 py-2 text-right">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      setSelectedIdx(i);
                      setFormat("xlsx");
                      setDrawerOpen(true);
                    }}
                    disabled={unassigned}
                    title={
                      unassigned
                        ? "Resolve unassigned rows in the master register first."
                        : "Open build dialog (B)"
                    }
                    className="rounded-full bg-accent px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    Build
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {lastResult ? (
        <div className="rounded-md border border-emerald-500/40 bg-emerald-500/5 p-4 text-sm text-emerald-100">
          <p className="font-semibold">
            Built {lastResult.format.toUpperCase()} package for{" "}
            <span className="text-emerald-50">{lastResult.trade}</span>.
          </p>
          <p className="mt-1 text-xs text-text-muted">Saved to:</p>
          <pre className="mt-1 overflow-x-auto rounded border border-border bg-background px-3 py-2 text-xs text-text-primary">
            {lastResult.output_path}
          </pre>
          <p className="mt-2 text-[11px] text-text-muted">
            Files persist on the server. Copy the path above to share, or
            re-run from this page if the deliverables register has changed.
          </p>
        </div>
      ) : null}

      <RowDetailDrawer
        open={drawerOpen}
        title={
          selected
            ? `Build tender package — ${selected.trade}`
            : "Build tender package"
        }
        subtitle={
          selected
            ? `${selected.deliverable_count} accepted deliverables will be included.`
            : undefined
        }
        onClose={() => (busy ? undefined : setDrawerOpen(false))}
        footer={
          <div className="flex items-center justify-end gap-3">
            <button
              type="button"
              onClick={() => setDrawerOpen(false)}
              disabled={busy}
              className="rounded-full border border-border px-4 py-2 text-sm text-text-muted hover:border-text-muted hover:text-text-primary disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onBuild}
              disabled={busy}
              className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {busy ? "Building…" : "Confirm build"}
            </button>
          </div>
        }
      >
        <div className="space-y-4">
          <p className="text-xs text-text-muted">
            This is an assembly step — no LLM calls, no deliverable-status
            changes. The file is written under the project&apos;s tender
            output directory and the path is returned in the success toast.
          </p>
          <fieldset className="space-y-2">
            <legend className="text-xs uppercase tracking-wide text-text-muted">
              Output format
            </legend>
            <label className="flex items-start gap-3 rounded border border-border bg-background p-3">
              <input
                type="radio"
                name="format"
                value="xlsx"
                checked={format === "xlsx"}
                onChange={() => setFormat("xlsx")}
                className="mt-1"
              />
              <span>
                <span className="block text-sm text-text-primary">
                  Excel (.xlsx)
                </span>
                <span className="block text-xs text-text-muted">
                  Multi-sheet workbook (cover, deliverables, standards,
                  flags). Default — what subcontractors typically expect.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-3 rounded border border-border bg-background p-3">
              <input
                type="radio"
                name="format"
                value="md"
                checked={format === "md"}
                onChange={() => setFormat("md")}
                className="mt-1"
              />
              <span>
                <span className="block text-sm text-text-primary">
                  Markdown (.md)
                </span>
                <span className="block text-xs text-text-muted">
                  Single text file — useful for diffing, version control,
                  or embedding in a longer issue brief.
                </span>
              </span>
            </label>
          </fieldset>
          {defaultOutputDir ? (
            <div>
              <p className="text-[11px] uppercase tracking-wide text-text-muted">
                Output directory
              </p>
              <pre className="mt-1 overflow-x-auto rounded border border-border bg-background px-3 py-2 text-xs text-text-primary">
                {defaultOutputDir}
              </pre>
              <p className="mt-1 text-[11px] text-text-muted">
                Server-side path — the operator can copy from here after
                build.
              </p>
            </div>
          ) : null}
        </div>
      </RowDetailDrawer>
    </div>
  );
}
