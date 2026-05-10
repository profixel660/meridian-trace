"use client";

import { useEffect, useRef, useState } from "react";

import {
  pipelineApi,
  type PipelinePhase,
  type PipelineStatus,
} from "@/lib/setupClient";

const POLL_INTERVAL_MS = 1500;
const SESSION_KEY = "meridian.setup.pipeline_job_id";

interface Props {
  projectSlug: string;
  /** Refetch coverage when the pipeline finishes successfully. */
  onDone?: () => void;
  /** Notified on every phase transition so the dashboard can dim KPIs / hide BaselineBanner. */
  onPhaseChange?: (phase: PipelinePhase) => void;
}

/**
 * Polls the alpha-25 pipeline endpoint and surfaces phase + per-source
 * progress + the current source filename verbatim. Renders nothing when
 * no job is in flight (terminal phase=done OR no job found at all).
 *
 * Job-id pickup priority:
 *   1. sessionStorage stash from the wizard's /setup/ready page
 *   2. fallback to GET /api/projects/<slug>/pipeline (latest)
 */
export function PipelineProgressTile({ projectSlug, onDone, onPhaseChange }: Props) {
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [resolved, setResolved] = useState(false);  // tile decided whether to render
  const pollHandle = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;
    let jobId: string | null = null;

    (async () => {
      try {
        jobId = window.sessionStorage.getItem(SESSION_KEY);
      } catch {
        jobId = null;
      }

      let initial: PipelineStatus | null = null;
      try {
        if (jobId) {
          initial = await pipelineApi.status(projectSlug, jobId);
        } else {
          initial = await pipelineApi.latest(projectSlug);
        }
      } catch {
        // 404 either way — no in-flight job. Render nothing.
        if (!cancelled) setResolved(true);
        return;
      }
      if (cancelled) return;
      setStatus(initial);
      onPhaseChange?.(initial.phase);
      setResolved(true);

      if (initial.phase === "done" || initial.phase === "failed") {
        if (initial.phase === "done") {
          try { window.sessionStorage.removeItem(SESSION_KEY); } catch {}
          onDone?.();
        }
        return;
      }

      // Begin polling.
      pollHandle.current = setInterval(async () => {
        try {
          const next = jobId
            ? await pipelineApi.status(projectSlug, jobId)
            : await pipelineApi.latest(projectSlug);
          if (cancelled) return;
          setStatus(next);
          onPhaseChange?.(next.phase);
          if (next.phase === "done" || next.phase === "failed") {
            if (pollHandle.current) {
              clearInterval(pollHandle.current);
              pollHandle.current = null;
            }
            if (next.phase === "done") {
              try { window.sessionStorage.removeItem(SESSION_KEY); } catch {}
              onDone?.();
            }
          }
        } catch {
          // transient — keep polling
        }
      }, POLL_INTERVAL_MS);
    })();

    return () => {
      cancelled = true;
      if (pollHandle.current) {
        clearInterval(pollHandle.current);
        pollHandle.current = null;
      }
    };
  }, [projectSlug, onDone, onPhaseChange]);

  if (!resolved) return null;
  if (!status || status.phase === "done") return null;

  if (status.phase === "failed") {
    return (
      <div className="rounded-lg border border-red-500/50 bg-red-500/5 p-4">
        <p className="text-sm font-medium text-red-300">
          ✗ Extraction failed
        </p>
        {status.error_message ? (
          <p className="mt-1 whitespace-pre-wrap font-mono text-xs text-red-200">
            {status.error_message}
          </p>
        ) : null}
        <button
          type="button"
          onClick={() => {
            const key = crypto.randomUUID();
            void pipelineApi
              .start(projectSlug, {}, { idempotencyKey: key })
              .then((res) => {
                try {
                  window.sessionStorage.setItem(SESSION_KEY, res.job_id);
                } catch {}
                window.location.reload();
              })
              .catch(() => {/* surface via reload */});
          }}
          className="mt-3 rounded-full border border-border px-3 py-1.5 text-xs text-text-primary hover:border-accent"
        >
          Try again
        </button>
      </div>
    );
  }

  if (status.phase === "bootstrap") {
    return (
      <div className="rounded-lg border border-accent/40 bg-accent/5 p-4">
        <div className="flex items-center gap-2">
          <span
            className="h-2 w-2 animate-pulse rounded-full bg-accent"
            aria-hidden
          />
          <p className="text-sm font-medium text-text-primary">
            Classifying your project's vocabulary…
          </p>
        </div>
        <p className="mt-1 text-xs text-text-muted">
          One LLM call to seed the trade / service / category taxonomies.
          Extraction starts next.
        </p>
      </div>
    );
  }

  if (status.phase === "conflicts") {
    return (
      <div className="rounded-lg border border-accent/40 bg-accent/5 p-4">
        <div className="flex items-center gap-2">
          <span
            className="h-2 w-2 animate-pulse rounded-full bg-accent"
            aria-hidden
          />
          <p className="text-sm font-medium text-text-primary">
            Detecting cross-source conflicts…
          </p>
        </div>
        <p className="mt-1 text-xs text-text-muted">
          One pass over the master register + audit log. Anything flagged
          lands in the master register's <code>conflict_summary</code> column
          (Excel) and the Conflicts review queue.
        </p>
      </div>
    );
  }

  // phase === "extract" (or "pending" — same render: spinner + "starting")
  const completed = status.extract_completed;
  const total = status.extract_total;
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;

  return (
    <div className="rounded-lg border border-accent/40 bg-accent/5 p-4">
      <p className="text-[11px] uppercase tracking-wide text-accent">
        Extracting now
      </p>
      <p className="mt-1 text-sm font-medium text-text-primary">
        {total > 0
          ? `${completed} of ${total} sources`
          : "Starting extraction…"}
      </p>
      <div
        className="mt-2 h-2 w-full overflow-hidden rounded-full bg-surface"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <div
          className="h-full bg-accent transition-[width]"
          style={{ width: `${pct}%` }}
        />
      </div>
      {status.current_source_filename ? (
        <p
          className="mt-2 truncate font-mono text-[11px] text-text-muted"
          title={status.current_source_filename}
        >
          {status.current_source_filename}
        </p>
      ) : null}
    </div>
  );
}
