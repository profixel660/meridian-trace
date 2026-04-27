"use client";

import { useEffect, useState, type ReactNode } from "react";

interface FirstUseCalloutProps {
  /** Stable key used in localStorage; usually the route. */
  routeKey: string;
  title: string;
  children: ReactNode;
}

/**
 * Dismissible "what am I looking at?" banner shown on first visit per
 * route. Persists `meridian:dismissed:<routeKey>` in localStorage.
 *
 * Discoverability rule #4: every queue page renders one of these.
 */
export function FirstUseCallout({
  routeKey,
  title,
  children,
}: FirstUseCalloutProps) {
  const storageKey = `meridian:dismissed:${routeKey}`;
  // Render hidden until we've checked localStorage to avoid SSR/CSR flicker.
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    try {
      if (window.localStorage.getItem(storageKey) !== "1") {
        setVisible(true);
      }
    } catch {
      // localStorage blocked — fall back to always-show.
      setVisible(true);
    }
  }, [storageKey]);

  if (!visible) return null;

  const dismiss = () => {
    try {
      window.localStorage.setItem(storageKey, "1");
    } catch {
      // ignore
    }
    setVisible(false);
  };

  return (
    <aside
      role="note"
      className="rounded-lg border border-accent/40 bg-accent/5 p-4 text-sm leading-relaxed text-text-primary"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <h2 className="text-sm font-semibold">{title}</h2>
          <div className="text-text-muted">{children}</div>
          <p className="pt-1 text-[11px] text-text-muted">
            Press <kbd className="rounded border border-border px-1">?</kbd> for
            keyboard shortcuts on this screen.
          </p>
        </div>
        <button
          type="button"
          onClick={dismiss}
          className="shrink-0 rounded border border-border px-2 py-1 text-xs text-text-muted hover:border-text-muted hover:text-text-primary"
          aria-label="Dismiss this introduction"
        >
          Got it
        </button>
      </div>
    </aside>
  );
}
