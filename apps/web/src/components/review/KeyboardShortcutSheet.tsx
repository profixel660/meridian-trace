"use client";

import { useEffect, useState } from "react";

export interface Shortcut {
  keys: string;
  description: string;
}

interface KeyboardShortcutSheetProps {
  /** Per-screen shortcut definitions. */
  shortcuts: Shortcut[];
  /** Optional handler invoked when each per-row key fires. The hook
   *  ignores keypresses while focus is in an input/textarea/contenteditable. */
  onAction?: (key: string) => void;
}

const ROW_ACTION_KEYS = new Set([
  "a",
  "r",
  "e",
  "s",
  "ArrowLeft",
  "ArrowRight",
]);

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (target.isContentEditable) return true;
  return false;
}

/**
 * Renders nothing visible until `?` is pressed; then shows the cheatsheet
 * overlay (Discoverability rule #8). Also forwards row-action keys
 * (a/r/e/s/←/→) to the supplied `onAction` callback when focus is not in
 * an input.
 */
export function KeyboardShortcutSheet({
  shortcuts,
  onAction,
}: KeyboardShortcutSheetProps) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isEditableTarget(e.target)) return;
      if (e.key === "?") {
        e.preventDefault();
        setOpen((v) => !v);
        return;
      }
      if (e.key === "Escape" && open) {
        e.preventDefault();
        setOpen(false);
        return;
      }
      if (onAction && ROW_ACTION_KEYS.has(e.key)) {
        onAction(e.key);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onAction]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4"
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-md rounded-lg border border-border bg-surface p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-baseline justify-between">
          <h2 className="text-base font-semibold text-text-primary">
            Keyboard shortcuts
          </h2>
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="text-xs text-text-muted hover:text-text-primary"
          >
            Esc
          </button>
        </div>
        <dl className="mt-4 space-y-2 text-sm">
          {shortcuts.map((s) => (
            <div
              key={s.keys}
              className="flex items-center justify-between gap-4"
            >
              <dt className="text-text-muted">{s.description}</dt>
              <dd>
                <kbd className="rounded border border-border bg-surface-elevated px-2 py-0.5 font-mono text-xs text-text-primary">
                  {s.keys}
                </kbd>
              </dd>
            </div>
          ))}
        </dl>
        <p className="mt-4 border-t border-border pt-3 text-xs text-text-muted">
          Tip: shortcuts are paused while you&apos;re typing in a field.
        </p>
      </div>
    </div>
  );
}
