"use client";

import { useEffect, type ReactNode } from "react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  /** Plain-English summary of what will happen. */
  body: ReactNode;
  /** Label for the destructive primary action (e.g. "Reject"). */
  confirmLabel: string;
  /** Label for the cancel button. Defaults to "Cancel". */
  cancelLabel?: string;
  /** True for red destructive accent. Defaults to true (this dialog is only for hard actions). */
  destructive?: boolean;
  /** Called when the user clicks confirm. */
  onConfirm: () => void;
  /** Called on cancel, backdrop click, or Escape. */
  onCancel: () => void;
  /** Disables the confirm button while an async action is mid-flight. */
  busy?: boolean;
}

/**
 * Modal confirmation for destructive or hard-to-reverse actions
 * (Reject deliverable, Reject taxonomy, Merge taxonomy, …).
 *
 * Discoverability rule #6: every such action surfaces explicit copy
 * naming what will happen, plus counts where relevant (caller passes
 * those into `body`).
 */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  cancelLabel = "Cancel",
  destructive = true,
  onConfirm,
  onCancel,
  busy = false,
}: ConfirmDialogProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, busy, onCancel]);

  if (!open) return null;

  const confirmCls = destructive
    ? "bg-red-600 text-white hover:bg-red-500"
    : "bg-accent text-white hover:opacity-90";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4"
      onClick={() => (busy ? undefined : onCancel())}
    >
      <div
        className="w-full max-w-md rounded-lg border border-border bg-surface p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2
          id="confirm-dialog-title"
          className="text-base font-semibold text-text-primary"
        >
          {title}
        </h2>
        <div className="mt-3 text-sm leading-relaxed text-text-muted">
          {body}
        </div>
        <div className="mt-6 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-full border border-border px-4 py-2 text-sm text-text-muted hover:border-text-muted hover:text-text-primary disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className={`rounded-full px-4 py-2 text-sm font-medium ${confirmCls} disabled:opacity-50`}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
