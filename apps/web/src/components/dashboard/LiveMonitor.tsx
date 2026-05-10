"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { useEventStream, type MonitorEvent } from "@/lib/eventStream";

const COLLAPSE_KEY = "meridian.live_monitor.collapsed";

interface Props {
  projectSlug: string;
}

export function LiveMonitor({ projectSlug }: Props) {
  const stream = useEventStream(projectSlug);
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(COLLAPSE_KEY) === "1";
  });
  const [autoExpandedThisSession, setAutoExpandedThisSession] = useState(false);

  // Auto-expand on first activity event (one-shot per session)
  useEffect(() => {
    if (stream.events.length > 0 && !autoExpandedThisSession && collapsed) {
      setCollapsed(false);
      setAutoExpandedThisSession(true);
    }
  }, [stream.events.length, autoExpandedThisSession, collapsed]);

  const toggleCollapsed = () => {
    setCollapsed((c) => {
      const next = !c;
      try {
        window.localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
      } catch {
        // sessionStorage blocked — fine
      }
      return next;
    });
  };

  // Live age timer
  const ageRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    let raf: number;
    const tick = () => {
      if (ageRef.current) {
        if (stream.lastEventAt === null) {
          ageRef.current.textContent = "no events";
        } else {
          const sec = Math.max(0, Math.floor((Date.now() - stream.lastEventAt) / 1000));
          ageRef.current.textContent = sec < 60
            ? `${sec}s ago`
            : `${Math.floor(sec / 60)}m ago`;
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [stream.lastEventAt]);

  const dotColor = useMemo(() => {
    if (stream.status === "subscriber_limit") return "#6b7280";
    if (stream.lastEventAt === null) return "#6b7280";
    const age = Date.now() - stream.lastEventAt;
    if (age > 90_000) return "#ef4444";
    if (age > 30_000) return "#fbbf24";
    if (age < 2_000) return "#06b6d4";
    return "#34d399";
  }, [stream.status, stream.lastEventAt]);

  const currentActivity = useMemo(() => {
    const last = stream.events[0];
    if (!last) return "Idle";
    if (last.event === "triage.chunk.completed") {
      const ctx = last.ctx as { chunk_id?: string };
      return `Extracting · chunk · ${ctx.chunk_id ?? "?"}`;
    }
    if (last.event.startsWith("extraction.source.")) {
      return `Extracting · ${(last.ctx as { filename?: string }).filename ?? "source"}`;
    }
    if (last.event.startsWith("pipeline.")) {
      return `Pipeline · ${last.event.replace("pipeline.", "")}`;
    }
    return last.event;
  }, [stream.events]);

  // Subscriber limit state
  if (stream.status === "subscriber_limit") {
    return (
      <div className="sticky bottom-0 z-30 border-t border-border bg-surface-elevated px-4 py-2 text-xs">
        <div className="flex items-center gap-2 text-text-muted">
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{ background: "#6b7280" }}
            aria-hidden
          />
          <span>
            Monitor unavailable — server has{" "}
            {stream.subscriberLimit?.active ?? "?"} of{" "}
            {stream.subscriberLimit?.limit ?? "?"} active connections
          </span>
          <button
            onClick={stream.retry}
            type="button"
            className="ml-auto text-accent hover:underline"
            title="Retry connection"
          >
            ↻
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      role="log"
      aria-live="polite"
      className="sticky bottom-0 z-30 border-t border-border bg-gradient-to-b from-surface to-surface-elevated"
    >
      <div className="mx-auto max-w-6xl px-4">
        {/* Top row: always visible */}
        <button
          type="button"
          onClick={toggleCollapsed}
          aria-expanded={!collapsed}
          className="flex w-full items-center gap-2 py-2 text-xs"
        >
          <span
            className="h-1.5 w-1.5 rounded-full transition-colors"
            style={{
              background: dotColor,
              boxShadow: `0 0 6px ${dotColor}`,
              animation:
                stream.status === "live" && stream.lastEventAt !== null
                  ? "pulse 1.4s ease-in-out infinite"
                  : "none",
            }}
            aria-label={`Pipeline status indicator (${dotColor})`}
          />
          <span className="text-[11px] uppercase tracking-wide text-text-muted">
            {currentActivity}
          </span>
          {/* Bar (only when there's recent activity) */}
          {stream.events.length > 0 && (
            <span className="ml-2 h-1 flex-1 max-w-[120px] overflow-hidden rounded-full bg-border">
              <span
                className="block h-full"
                style={{
                  width: `${Math.min(100, stream.events.length / 2)}%`,
                  background: "linear-gradient(90deg, #3b82f6, #06b6d4)",
                  boxShadow: "inset 0 0 4px rgba(6,182,212,0.5)",
                  transition: "width 200ms ease-out",
                }}
              />
            </span>
          )}
          <span ref={ageRef} className="ml-auto font-mono text-[10px] text-text-muted">
            …
          </span>
          <span className="text-text-muted">{collapsed ? "↑" : "↓"}</span>
        </button>

        {/* Expanded tail */}
        {!collapsed && (
          <ol className="space-y-1 border-t border-border py-2 font-mono text-[10px] text-text-muted">
            {stream.events.slice(0, 5).map((ev, i) => (
              <li
                key={`${ev.ts}-${i}`}
                className="flex gap-2"
                style={{ opacity: 1 - i * 0.15 }}
              >
                <span style={{ color: levelColor(ev.level) }}>●</span>
                <span className="w-16 text-text-muted/70">
                  {fmtTime(ev.ts)}
                </span>
                <span className="text-text-primary">{ev.event}</span>
                <span className="ml-2 truncate text-text-muted">
                  {fmtCtx(ev.ctx)}
                </span>
              </li>
            ))}
            {stream.events.length === 0 && (
              <li className="text-text-muted/50">No events yet.</li>
            )}
          </ol>
        )}
      </div>
      <style jsx>{`
        @keyframes pulse {
          0%, 100% { opacity: 0.4; }
          50% { opacity: 1; }
        }
      `}</style>
    </div>
  );
}

function levelColor(level: MonitorEvent["level"]): string {
  switch (level) {
    case "info":    return "#06b6d4";
    case "warning": return "#fbbf24";
    case "error":   return "#ef4444";
    default:        return "#9ca3af";
  }
}

function fmtTime(ts: string): string {
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString(undefined, { hour12: false });
  } catch {
    return ts;
  }
}

function fmtCtx(ctx: Record<string, unknown>): string {
  return Object.entries(ctx)
    .filter(([k]) => k !== "timestamp")
    .slice(0, 3)
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)
    .join(" ");
}
