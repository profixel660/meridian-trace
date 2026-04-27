"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

type Tone = "info" | "success" | "error";

interface Toast {
  id: number;
  tone: Tone;
  message: string;
  /** Optional retry handler — renders a "Retry" affordance. */
  onRetry?: () => void;
}

interface ToastApi {
  push: (toast: Omit<Toast, "id">) => void;
  success: (msg: string) => void;
  error: (msg: string, onRetry?: () => void) => void;
  info: (msg: string) => void;
}

const ToastCtx = createContext<ToastApi | null>(null);

/**
 * Provider + visual host. Wrap a queue page (or its layout) once and
 * call `useToasts()` from any child to surface action feedback.
 */
export function ToastHostProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const remove = useCallback((id: number) => {
    setToasts((xs) => xs.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (t: Omit<Toast, "id">) => {
      const id = nextId.current++;
      setToasts((xs) => [...xs, { ...t, id }]);
      // Errors stay until dismissed; info/success fade after 4s.
      if (t.tone !== "error") {
        setTimeout(() => remove(id), 4000);
      }
    },
    [remove],
  );

  const api = useMemo<ToastApi>(
    () => ({
      push,
      success: (message) => push({ tone: "success", message }),
      info: (message) => push({ tone: "info", message }),
      error: (message, onRetry) => push({ tone: "error", message, onRetry }),
    }),
    [push],
  );

  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex max-w-sm flex-col gap-2">
        {toasts.map((t) => (
          <ToastCard key={t.id} toast={t} onClose={() => remove(t.id)} />
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

function ToastCard({
  toast,
  onClose,
}: {
  toast: Toast;
  onClose: () => void;
}) {
  const colour =
    toast.tone === "error"
      ? "border-red-500/50 bg-red-950/80 text-red-100"
      : toast.tone === "success"
        ? "border-emerald-500/50 bg-emerald-950/80 text-emerald-100"
        : "border-border bg-surface-elevated text-text-primary";
  return (
    <div
      role={toast.tone === "error" ? "alert" : "status"}
      className={`pointer-events-auto rounded-md border px-4 py-3 text-sm shadow-lg ${colour}`}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="leading-snug">{toast.message}</p>
        <button
          type="button"
          onClick={onClose}
          aria-label="Dismiss"
          className="text-xs opacity-70 hover:opacity-100"
        >
          ✕
        </button>
      </div>
      {toast.onRetry ? (
        <button
          type="button"
          onClick={() => {
            toast.onRetry?.();
            onClose();
          }}
          className="mt-2 rounded border border-current px-2 py-0.5 text-xs"
        >
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function useToasts(): ToastApi {
  const ctx = useContext(ToastCtx);
  if (!ctx) {
    // Fallback no-op API so a misuse outside the provider doesn't crash.
    return {
      push: () => {},
      success: () => {},
      error: () => {},
      info: () => {},
    };
  }
  return ctx;
}

// Auto-dismiss delay isn't user-configurable, but expose for tests.
export const TOAST_AUTO_DISMISS_MS = 4000;
