"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useState,
} from "react";

import { ConfirmDialog } from "@/components/review/ConfirmDialog";
import { KeyboardShortcutSheet } from "@/components/review/KeyboardShortcutSheet";

import { SHELL_COPY } from "./copy";
import { StepNav, type SetupStepKey } from "./StepNav";

interface SetupShellProps {
  /** Current step — drives the progress indicator. */
  step: SetupStepKey;
  /** Steps already completed (rendered with a tick + clickable). */
  completed?: SetupStepKey[];
  /** Where the "← Back" button goes. Omit to disable Back (welcome page). */
  backHref?: string;
  /**
   * Callback for the "Continue →" button. Pages own their own validation,
   * so the shell just calls this and the page does the work. Omit to
   * hide the Continue button entirely (welcome / ready pages have their
   * own primary CTA).
   */
  onContinue?: () => void;
  /** Whether the Continue button is currently enabled. */
  continueDisabled?: boolean;
  /** Override the Continue label (e.g. "Validate & save"). */
  continueLabel?: string;
  /** True while a long-running operation is mid-flight — disables nav. */
  busy?: boolean;
  /** Page body. */
  children: ReactNode;
}

/**
 * Shared chrome for every wizard page.
 *
 * Renders:
 *  - Header with the brand + step indicator + close (X) button
 *  - The page body inside a max-width prose column
 *  - Footer with Back / Continue buttons (page chooses what's visible)
 *
 * Keyboard:
 *  - `?` toggles the shortcut sheet (Discoverability rule #8 — keyboard
 *    discoverability — handled inside `KeyboardShortcutSheet`)
 *  - `Esc` opens the close-confirm dialog
 *  - `Enter` activates the Continue button when one is registered AND
 *    focus is not in a textarea/form-control. The page's primary input
 *    (e.g. the API key field) typically wires `Enter` to the same
 *    handler so it works whether or not the user has tabbed away.
 */
export function SetupShell({
  step,
  completed,
  backHref,
  onContinue,
  continueDisabled = false,
  continueLabel = SHELL_COPY.continue,
  busy = false,
  children,
}: SetupShellProps) {
  const router = useRouter();
  const [closeOpen, setCloseOpen] = useState(false);

  const handleClose = useCallback(() => {
    if (busy) return;
    setCloseOpen(true);
  }, [busy]);

  const handleConfirmClose = useCallback(() => {
    setCloseOpen(false);
    router.push("/");
  }, [router]);

  const handleBack = useCallback(() => {
    if (busy) return;
    if (backHref) router.push(backHref);
  }, [backHref, busy, router]);

  // `Esc` opens the close-confirm. `Enter` is forwarded to onContinue
  // when focus isn't in an editable element AND the button is enabled.
  useEffect(() => {
    const isEditable = (target: EventTarget | null) => {
      if (!(target instanceof HTMLElement)) return false;
      const tag = target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
      if (target.isContentEditable) return true;
      return false;
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !closeOpen) {
        e.preventDefault();
        handleClose();
        return;
      }
      // Enter triggers Continue only when focus is outside form fields —
      // form fields wire their own Enter handlers (e.g. "Validate & save"
      // on the API-key input) and we don't want to double-fire.
      if (
        e.key === "Enter" &&
        !isEditable(e.target) &&
        onContinue &&
        !continueDisabled &&
        !busy
      ) {
        e.preventDefault();
        onContinue();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [closeOpen, handleClose, onContinue, continueDisabled, busy]);

  return (
    <div className="min-h-[calc(100vh-7rem)]">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-border pb-4">
        <div className="flex items-center gap-4">
          <span className="text-xs uppercase tracking-wide text-text-muted">
            {SHELL_COPY.brand}
          </span>
          <Link
            href="/"
            className="text-xs text-text-muted hover:text-text-primary transition-colors"
          >
            My projects
          </Link>
        </div>
        <StepNav current={step} completed={completed} />
        <button
          type="button"
          onClick={handleClose}
          aria-label={SHELL_COPY.closeLabel}
          className="rounded border border-border px-2 py-1 text-xs text-text-muted hover:border-text-muted hover:text-text-primary"
        >
          ✕
        </button>
      </header>

      <section className="mx-auto max-w-[70ch] py-10">{children}</section>

      <footer className="mt-8 flex max-w-[70ch] mx-auto items-center justify-between border-t border-border pt-6">
        {backHref ? (
          <button
            type="button"
            onClick={handleBack}
            disabled={busy}
            className="rounded-full border border-border px-4 py-2 text-sm text-text-muted hover:border-text-muted hover:text-text-primary disabled:opacity-40"
          >
            {SHELL_COPY.back}
          </button>
        ) : (
          <span aria-hidden="true" />
        )}
        {onContinue ? (
          <button
            type="button"
            onClick={onContinue}
            disabled={continueDisabled || busy}
            className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
          >
            {busy ? "Working…" : continueLabel}
          </button>
        ) : (
          <span aria-hidden="true" />
        )}
      </footer>

      <ConfirmDialog
        open={closeOpen}
        title={SHELL_COPY.closeConfirm.title}
        body={SHELL_COPY.closeConfirm.body}
        confirmLabel={SHELL_COPY.closeConfirm.confirm}
        cancelLabel={SHELL_COPY.closeConfirm.cancel}
        destructive={false}
        onConfirm={handleConfirmClose}
        onCancel={() => setCloseOpen(false)}
      />

      <KeyboardShortcutSheet shortcuts={SHELL_COPY.shortcuts} />
    </div>
  );
}
