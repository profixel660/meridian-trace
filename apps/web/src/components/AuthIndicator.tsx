"use client";

import { useCallback, useEffect, useState } from "react";

import { meridianRequest } from "@/lib/api";
import {
  clearBearerToken,
  getBearerToken,
  minutesUntilExpiry,
} from "@/lib/auth";

/**
 * Header chip showing auth state plus a logout affordance.
 *
 * Three visual states:
 *   - signed-in   → "TOTP user · expires in N min · [Logout]"
 *   - signed-out  → "[Sign in]" linking to /login
 *   - hydrating   → empty placeholder (avoids SSR/CSR mismatch)
 *
 * Refreshes the minutes-remaining display every 30 seconds so the user
 * sees the timer count down without reloading the page.
 */
export function AuthIndicator() {
  const [hydrated, setHydrated] = useState(false);
  const [hasToken, setHasToken] = useState(false);
  const [minsLeft, setMinsLeft] = useState<number | null>(null);

  const refresh = useCallback(() => {
    const token = getBearerToken();
    setHasToken(token !== null);
    setMinsLeft(token !== null ? minutesUntilExpiry() : null);
  }, []);

  useEffect(() => {
    setHydrated(true);
    refresh();
    const id = window.setInterval(refresh, 30_000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const onLogout = useCallback(async () => {
    const token = getBearerToken();
    // Best-effort server-side revoke — swallow failures so a stale token
    // or backend hiccup never blocks the user from clearing their session.
    if (token) {
      try {
        await meridianRequest("/auth/logout", {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        });
      } catch {
        // ignore — we still want to clear locally and bounce.
      }
    }
    clearBearerToken();
    window.location.assign("/login");
  }, []);

  if (!hydrated) {
    // Reserve space so the header doesn't reflow once we know the state.
    return (
      <span
        aria-hidden="true"
        className="inline-block h-6 w-32 rounded border border-border"
      />
    );
  }

  if (!hasToken) {
    return (
      <a
        href="/login"
        className="rounded border border-border px-2 py-1 text-xs text-text-muted hover:border-accent hover:text-accent"
      >
        Sign in
      </a>
    );
  }

  return (
    <span
      title={
        minsLeft !== null
          ? `Bearer token expires in approximately ${minsLeft} minute${minsLeft === 1 ? "" : "s"}.`
          : "Signed in."
      }
      className="inline-flex items-center gap-2 rounded border border-border px-2 py-1 text-xs text-text-muted"
    >
      <span className="inline-block h-2 w-2 rounded-full bg-emerald-400" />
      <span>
        TOTP user
        {minsLeft !== null ? (
          <>
            <span aria-hidden="true"> · </span>
            <span>expires in {minsLeft} min</span>
          </>
        ) : null}
      </span>
      <button
        type="button"
        onClick={onLogout}
        className="ml-1 rounded border border-border px-2 py-0.5 text-[11px] text-text-muted hover:border-text-muted hover:text-text-primary"
      >
        Logout
      </button>
    </span>
  );
}
