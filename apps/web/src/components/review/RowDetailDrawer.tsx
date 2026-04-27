"use client";

import { useEffect, type ReactNode } from "react";

interface RowDetailDrawerProps {
  open: boolean;
  title: string;
  /** Optional secondary line under the title (e.g. source filename). */
  subtitle?: ReactNode;
  /** Body content — typically a definition list of fields. */
  children: ReactNode;
  /** Footer slot for action buttons. */
  footer?: ReactNode;
  onClose: () => void;
}

/**
 * Slide-out side panel for full-row inspection. Shared by quarantine,
 * audit, and conflict pages so the row picker pattern is consistent.
 */
export function RowDetailDrawer({
  open,
  title,
  subtitle,
  children,
  footer,
  onClose,
}: RowDetailDrawerProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="row-drawer-title"
      className="fixed inset-0 z-30 flex justify-end bg-black/40"
      onClick={onClose}
    >
      <aside
        className="flex h-full w-full max-w-xl flex-col border-l border-border bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-4 border-b border-border px-6 py-4">
          <div className="space-y-1">
            <h2
              id="row-drawer-title"
              className="text-base font-semibold text-text-primary"
            >
              {title}
            </h2>
            {subtitle ? (
              <div className="text-xs text-text-muted">{subtitle}</div>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close detail panel"
            className="rounded border border-border px-2 py-1 text-xs text-text-muted hover:border-text-muted hover:text-text-primary"
          >
            Close (Esc)
          </button>
        </header>
        <div className="flex-1 overflow-y-auto px-6 py-4 text-sm text-text-primary">
          {children}
        </div>
        {footer ? (
          <footer className="border-t border-border px-6 py-4">{footer}</footer>
        ) : null}
      </aside>
    </div>
  );
}
