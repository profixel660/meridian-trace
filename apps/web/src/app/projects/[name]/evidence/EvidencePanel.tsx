"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { ConfirmDialog } from "@/components/review/ConfirmDialog";
import { EmptyState } from "@/components/review/EmptyState";
import {
  KeyboardShortcutSheet,
  type Shortcut,
} from "@/components/review/KeyboardShortcutSheet";
import { useToasts } from "@/components/review/ToastHost";
import { Tooltip, TooltipMore } from "@/components/review/Tooltip";
import {
  evidenceApi,
  type EvidenceBuildResponse,
  type EvidencePackListItem,
} from "@/lib/apiClient/evidence";
import { explainQueueError } from "@/lib/queueClient";

const SHORTCUTS: Shortcut[] = [
  { keys: "B", description: "Open Build Pack confirmation" },
  { keys: "Esc", description: "Close any open dialog" },
  { keys: "?", description: "Toggle this cheatsheet" },
];

interface EvidencePanelProps {
  projectName: string;
  packs: EvidencePackListItem[];
}

export function EvidencePanel({ projectName, packs }: EvidencePanelProps) {
  const router = useRouter();
  const toasts = useToasts();

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [lastBuild, setLastBuild] = useState<EvidenceBuildResponse | null>(
    null,
  );
  const [pageError, setPageError] = useState<unknown>(null);

  const onBuild = useCallback(async () => {
    setBusy(true);
    setPageError(null);
    try {
      const result = await evidenceApi.build(projectName);
      setLastBuild(result);
      toasts.success(
        `Built evidence pack — ${result.deliverable_count} deliverables, ${result.llm_call_count} LLM calls, ${result.source_count} sources.`,
      );
      setConfirmOpen(false);
      router.refresh();
    } catch (err) {
      toasts.error(explainQueueError(err), () => void onBuild());
      setPageError(err);
    } finally {
      setBusy(false);
    }
  }, [projectName, toasts, router]);

  // Page-level shortcut: `b` opens the Build dialog.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (confirmOpen) return;
      const t = e.target as HTMLElement | null;
      if (
        t &&
        (t.tagName === "INPUT" ||
          t.tagName === "TEXTAREA" ||
          t.isContentEditable)
      ) {
        return;
      }
      if (e.key.toLowerCase() === "b") {
        e.preventDefault();
        setConfirmOpen(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [confirmOpen]);

  const onCopyPath = useCallback(
    async (path: string) => {
      try {
        await navigator.clipboard.writeText(path);
        toasts.success("Pack path copied to clipboard.");
      } catch {
        toasts.error(
          "Couldn't copy. Select the path text and copy manually.",
        );
      }
    },
    [toasts],
  );

  return (
    <div className="space-y-6">
      <KeyboardShortcutSheet shortcuts={SHORTCUTS} />

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-surface-elevated px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-text-primary">
            Build a new evidence pack
          </h2>
          <p className="mt-1 text-xs text-text-muted">
            Snapshots the project&apos;s current state into a standalone
            zip. Re-runnable any time — packs are timestamped, never
            overwritten.
          </p>
        </div>
        <Tooltip
          content={
            <span>
              <strong className="block text-text-primary">
                Build cost: zero LLM calls
              </strong>
              <span className="mt-1 block text-text-muted">
                This is a pure assembly step — it reads the project DB,
                gathers source files + LLM call records, and zips them.
                No additional model spend, no DB writes to deliverable
                status. Safe to re-run.
              </span>
              <TooltipMore href="/glossary#legal-evidence-pack" />
            </span>
          }
        >
          <button
            type="button"
            onClick={() => setConfirmOpen(true)}
            disabled={busy}
            className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            Build new pack (B)
          </button>
        </Tooltip>
      </div>

      {pageError ? <ApiErrorPanel error={pageError} /> : null}

      {lastBuild ? (
        <div className="rounded-md border border-emerald-500/40 bg-emerald-500/5 p-4 text-sm text-emerald-100">
          <p className="font-semibold">
            Pack ready — {lastBuild.deliverable_count} deliverables /{" "}
            {lastBuild.llm_call_count} LLM calls /{" "}
            {lastBuild.source_count} sources.
          </p>
          <pre className="mt-2 overflow-x-auto rounded border border-border bg-background px-3 py-2 text-xs text-text-primary">
            {lastBuild.pack_path}
          </pre>
          {lastBuild.incomplete_provenance_ids.length > 0 ? (
            <details className="mt-2 text-xs text-amber-200">
              <summary className="cursor-pointer">
                ⚠ {lastBuild.incomplete_provenance_ids.length} deliverable(s)
                with incomplete provenance — included anyway, but flagged
                in <code>cover.md</code>.
              </summary>
              <ul className="mt-1 list-disc pl-5 font-mono text-[11px] text-text-muted">
                {lastBuild.incomplete_provenance_ids.slice(0, 20).map((i) => (
                  <li key={i}>{i}</li>
                ))}
                {lastBuild.incomplete_provenance_ids.length > 20 ? (
                  <li>
                    …and {lastBuild.incomplete_provenance_ids.length - 20}{" "}
                    more
                  </li>
                ) : null}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}

      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-text-muted">
          Previously generated packs
        </h2>
        {packs.length === 0 ? (
          <EmptyState
            title="No evidence packs yet"
            body={
              <>
                Build your first pack with the button above. Packs are
                timestamped <code>.zip</code> files written to{" "}
                <code>&lt;projects_dir&gt;/&lt;slug&gt;.evidence/</code>.
                Older packs are kept for reference — nothing is overwritten.
              </>
            }
            learnMoreHref="/glossary#legal-evidence-pack"
          />
        ) : (
          <table className="w-full overflow-hidden rounded-md border border-border bg-surface-elevated text-sm">
            <thead className="bg-surface text-left text-[11px] uppercase tracking-wide text-text-muted">
              <tr>
                <th scope="col" className="px-4 py-2">
                  <Tooltip
                    content={
                      <span>
                        <strong className="block text-text-primary">
                          Filename
                        </strong>
                        <span className="mt-1 block text-text-muted">
                          Timestamped zip name. Format:
                          {" "}<code>evidence-&lt;slug&gt;-&lt;ISO timestamp&gt;.zip</code>.
                        </span>
                      </span>
                    }
                  >
                    <button type="button" className="cursor-help underline decoration-dotted">
                      Filename
                    </button>
                  </Tooltip>
                </th>
                <th scope="col" className="px-4 py-2 text-right">
                  <Tooltip
                    content={
                      <span>
                        <strong className="block text-text-primary">Size</strong>
                        <span className="mt-1 block text-text-muted">
                          Pack size on disk. Larger packs typically mean
                          more sources or more LLM call history.
                        </span>
                      </span>
                    }
                  >
                    <button type="button" className="cursor-help underline decoration-dotted">
                      Size
                    </button>
                  </Tooltip>
                </th>
                <th scope="col" className="px-4 py-2">
                  <Tooltip
                    content={
                      <span>
                        <strong className="block text-text-primary">
                          Modified
                        </strong>
                        <span className="mt-1 block text-text-muted">
                          When the pack was written. Snapshots project
                          state at this moment.
                        </span>
                      </span>
                    }
                  >
                    <button type="button" className="cursor-help underline decoration-dotted">
                      Modified
                    </button>
                  </Tooltip>
                </th>
                <th scope="col" className="px-4 py-2 text-right">
                  Action
                </th>
              </tr>
            </thead>
            <tbody>
              {packs.map((p) => (
                <tr key={p.path} className="border-t border-border">
                  <td className="px-4 py-2 font-mono text-xs text-text-primary">
                    {p.filename}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs text-text-muted">
                    {formatBytes(p.size_bytes)}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-text-muted">
                    {new Date(p.modified_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => onCopyPath(p.path)}
                      className="rounded-full border border-border px-3 py-1 text-xs text-text-primary hover:border-text-muted"
                      title="Copy server-side path to clipboard"
                    >
                      Copy path
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="space-y-3 rounded-md border border-border bg-surface-elevated p-4">
        <h2 className="text-sm font-semibold text-text-primary">
          Verify a pack
        </h2>
        <p className="text-xs text-text-muted">
          Re-verifying a pack&apos;s integrity (manifest + checksums) is
          available via the CLI. We deliberately keep verify out of the web
          surface — running it here would require exposing arbitrary server
          file paths or accepting browser uploads of large zips, both of
          which add risk without operator benefit.
        </p>
        <pre className="overflow-x-auto rounded border border-border bg-background px-3 py-2 text-xs text-text-primary">
          meridian evidence verify &lt;path-to-pack.zip&gt;
        </pre>
        <p className="text-[11px] text-text-muted">
          Run from the same machine as the FastAPI backend. Exit code 0 =
          intact; non-zero = tampered or incomplete. Use the{" "}
          <button
            type="button"
            onClick={() =>
              packs[0]
                ? onCopyPath(packs[0].path)
                : toasts.info("No packs yet — build one first.")
            }
            className="text-accent hover:underline"
          >
            most recent pack path
          </button>{" "}
          for a quick check.
        </p>
      </section>

      <ConfirmDialog
        open={confirmOpen}
        title="Build a Legal Evidence Pack?"
        destructive={false}
        body={
          <span>
            This will assemble a complete audit zip including all LLM
            calls, prompts, sources, and the deliverables register. Pure
            assembly — no LLM calls, no changes to deliverable status.
            <br />
            <br />
            The pack will be written to the project&apos;s{" "}
            <code>.evidence/</code> directory and listed below for copy /
            hand-off.
          </span>
        }
        confirmLabel="Build pack"
        busy={busy}
        onCancel={() => (busy ? undefined : setConfirmOpen(false))}
        onConfirm={onBuild}
      />
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}
