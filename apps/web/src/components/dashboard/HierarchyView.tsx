"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchHierarchy,
  type HierarchyEdge,
  type HierarchyRankedEntry,
  type HierarchyResponse,
} from "@/lib/apiClient/hierarchy";
import { useEventStream } from "@/lib/eventStream";

const VIEW_KEY = "meridian.hierarchy.view";
const LAST_RESOLVED_KEY = "meridian.conflicts.last_resolved_at";

type View = "flow" | "list";

interface Props {
  projectSlug: string;
}

export function HierarchyView({ projectSlug }: Props) {
  const [data, setData] = useState<HierarchyResponse | null>(null);
  const [view, setView] = useState<View>(() => {
    if (typeof window === "undefined") return "flow";
    return (window.localStorage.getItem(VIEW_KEY) as View) ?? "flow";
  });
  const [error, setError] = useState<unknown>(null);
  const stream = useEventStream(projectSlug);
  const [lastResolvedSeen, setLastResolvedSeen] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    try {
      setData(await fetchHierarchy(projectSlug));
      setError(null);
    } catch (err) {
      setError(err);
    }
  }, [projectSlug]);

  // Mount fetch + pipeline.done refetch trigger
  useEffect(() => { void refetch(); }, [refetch]);

  useEffect(() => {
    const last = stream.events[0];
    if (last?.event === "pipeline.done") {
      void refetch();
    }
  }, [stream.events, refetch]);

  // localStorage signal for cross-page conflict-resolved trigger
  useEffect(() => {
    const check = () => {
      try {
        const lr = window.localStorage.getItem(LAST_RESOLVED_KEY);
        if (lr && lr !== lastResolvedSeen) {
          setLastResolvedSeen(lr);
          void refetch();
        }
      } catch { /* sessionStorage blocked — fine */ }
    };
    check();
    window.addEventListener("focus", check);
    window.addEventListener("storage", check);
    return () => {
      window.removeEventListener("focus", check);
      window.removeEventListener("storage", check);
    };
  }, [lastResolvedSeen, refetch]);

  const setViewPersisted = (v: View) => {
    setView(v);
    try { window.localStorage.setItem(VIEW_KEY, v); } catch {}
  };

  if (error) {
    return (
      <section className="rounded-lg border border-border bg-surface-elevated p-4 text-sm text-text-muted">
        Hierarchy temporarily unavailable.
      </section>
    );
  }
  if (!data || data.resolved_count === 0) {
    // Suppress empty-state rendering entirely.
    return null;
  }

  return (
    <section
      id="hierarchy"
      className="rounded-lg border border-border bg-surface-elevated p-5"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-text-primary">
            Source-of-truth hierarchy
          </h2>
          <p className="text-xs text-text-muted">
            {data.resolved_count} resolved conflict
            {data.resolved_count === 1 ? "" : "s"}
          </p>
        </div>
        <fieldset className="flex gap-1 rounded-full border border-border p-1">
          <legend className="sr-only">View</legend>
          <ToggleBtn active={view === "flow"} onClick={() => setViewPersisted("flow")}>
            ● Flow
          </ToggleBtn>
          <ToggleBtn active={view === "list"} onClick={() => setViewPersisted("list")}>
            ◯ List
          </ToggleBtn>
        </fieldset>
      </header>

      <div className="mt-4">
        {view === "flow" ? (
          <SankeyDiagram edges={data.edges} projectSlug={projectSlug} />
        ) : (
          <RankedList entries={data.ranked} projectSlug={projectSlug} />
        )}
      </div>

      {data.same_class_conflicts.length > 0 && (
        <p className="mt-3 text-xs text-text-muted">
          <Link
            href={`/projects/${encodeURIComponent(projectSlug)}/conflict-register?same_class=true`}
            className="underline decoration-dotted"
          >
            Plus {data.same_class_conflicts.reduce((a, x) => a + x.count, 0)} conflict
            {data.same_class_conflicts.reduce((a, x) => a + x.count, 0) === 1 ? "" : "s"}
            {" "}within the same source class →
          </Link>
        </p>
      )}
      <p className="mt-1 text-[11px] text-text-muted">
        Auto-inferred from your resolved conflicts.{" "}
        <Link href="/glossary#source-of-truth-hierarchy" className="underline decoration-dotted">
          How does this work?
        </Link>
      </p>
    </section>
  );
}

function ToggleBtn({
  active, onClick, children,
}: {
  active: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-full px-3 py-1 text-xs ${
        active ? "bg-accent text-white" : "text-text-muted hover:text-text-primary"
      }`}
    >
      {children}
    </button>
  );
}

function SankeyDiagram({
  edges, projectSlug,
}: { edges: HierarchyEdge[]; projectSlug: string; }) {
  const layout = useMemo(() => buildSankeyLayout(edges), [edges]);
  const base = `/projects/${encodeURIComponent(projectSlug)}/conflict-register`;
  if (layout.lanes.length === 0) {
    return <p className="text-xs text-text-muted">No cross-class conflicts to graph.</p>;
  }
  return (
    <svg viewBox={`0 0 ${layout.width} ${layout.height}`} className="w-full" style={{ minHeight: 160 }}>
      {layout.lanes.map((lane, i) => (
        <g key={`L-${i}`}>
          <text x="6" y={lane.y + 4} fontSize="10" fill="#e5e7eb" fontFamily="ui-monospace, monospace">
            {lane.loser}
          </text>
          <text x={layout.width - 70} y={lane.y + 4} fontSize="10" fill="#e5e7eb" fontFamily="ui-monospace, monospace">
            {lane.winner}
          </text>
        </g>
      ))}
      {layout.ribbons.map((r, i) => (
        <Link key={`R-${i}`} href={`${base}?focus=${r.sample}`}>
          <path
            d={r.path}
            stroke={r.color}
            strokeWidth={r.width}
            fill="none"
            opacity="0.7"
          >
            <title>
              {r.winner} beats {r.loser} {r.wins} time{r.wins === 1 ? "" : "s"}
              {" "}({Math.round(r.winRate * 100)}% win rate)
            </title>
          </path>
        </Link>
      ))}
    </svg>
  );
}

function RankedList({
  entries, projectSlug,
}: { entries: HierarchyRankedEntry[]; projectSlug: string; }) {
  return (
    <ol className="space-y-2">
      {entries.map((e) => (
        <li key={e.class}>
          <Link
            href={`/projects/${encodeURIComponent(projectSlug)}/conflict-register?source_class=${encodeURIComponent(e.class)}`}
            className="flex items-center gap-3 rounded-md px-3 py-2 text-sm transition hover:bg-accent/5"
            style={{
              background: `linear-gradient(90deg, ${rankColor(e.rank)}26, transparent)`,
              borderLeft: `3px solid ${rankColor(e.rank)}`,
            }}
          >
            <span
              className="font-mono text-base font-bold"
              style={{ color: rankColor(e.rank), minWidth: 24 }}
            >
              {e.rank}
            </span>
            <span className="flex-1 text-text-primary">{e.class}</span>
            <span className="text-xs text-text-muted">
              {e.wins} win{e.wins === 1 ? "" : "s"} · {e.losses} loss
              {e.losses === 1 ? "" : "es"} · {Math.round(e.win_rate * 100)}%
            </span>
          </Link>
        </li>
      ))}
    </ol>
  );
}

function rankColor(rank: number): string {
  const palette = ["#3b82f6", "#06b6d4", "#10b981", "#a78bfa", "#9ca3af"];
  return palette[(rank - 1) % palette.length];
}

interface SankeyLayout {
  width: number;
  height: number;
  lanes: { winner: string; loser: string; y: number }[];
  ribbons: {
    path: string;
    width: number;
    color: string;
    sample: string;
    winner: string;
    loser: string;
    wins: number;
    winRate: number;
  }[];
}

function buildSankeyLayout(edges: HierarchyEdge[]): SankeyLayout {
  if (edges.length === 0) return { width: 360, height: 160, lanes: [], ribbons: [] };
  const losers = Array.from(new Set(edges.map((e) => e.loser_class))).sort();
  const winners = Array.from(new Set(edges.map((e) => e.winner_class))).sort();
  const width = 360;
  const height = Math.max(120, Math.max(losers.length, winners.length) * 30 + 30);
  const loserY = (cls: string) =>
    20 + losers.indexOf(cls) * ((height - 40) / Math.max(losers.length - 1, 1));
  const winnerY = (cls: string) =>
    20 + winners.indexOf(cls) * ((height - 40) / Math.max(winners.length - 1, 1));
  const maxWins = Math.max(...edges.map((e) => e.wins));
  const ribbons = edges.map((e) => ({
    path: `M 60 ${loserY(e.loser_class)} C ${width / 2} ${loserY(e.loser_class)}, ${width / 2} ${winnerY(e.winner_class)}, ${width - 80} ${winnerY(e.winner_class)}`,
    width: 2 + (e.wins / Math.max(maxWins, 1)) * 12,
    color: e.win_rate > 0.7 ? "#3b82f6" : e.win_rate > 0.4 ? "#06b6d4" : "#a78bfa",
    sample: e.sample_conflict_ids[0] ?? "",
    winner: e.winner_class,
    loser: e.loser_class,
    wins: e.wins,
    winRate: e.win_rate,
  }));
  const lanes = losers.map((loser, i) => ({
    winner: winners[i] ?? "",
    loser,
    y: loserY(loser),
  }));
  return { width, height, lanes, ribbons };
}
