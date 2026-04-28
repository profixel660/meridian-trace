"use client";

import { useEffect, useState } from "react";

/**
 * Alpha-13: red sticky banner that surfaces when the backend has
 * ``MERIDIAN_AUTH_DISABLED=1`` set and ``require_session`` is short-
 * circuited. The startup-time WARNING in ``api/main.py`` covers the
 * operator-reading-logs case; this covers the operator-using-the-GUI
 * case so a production deploy with the bypass still on cannot hide.
 *
 * Probes ``/setup/runtime`` (alpha-12 endpoint) once on mount. Polls
 * every 60s after that so a late env-var flip is reflected. Failure
 * to fetch (network down, backend restarting) silently hides the
 * banner — the failure surface is small enough that "no banner"
 * isn't a security regression.
 */
export function AuthDisabledBanner() {
  const [isDisabled, setIsDisabled] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const probe = async () => {
      try {
        const r = await fetch("/setup/runtime", {
          headers: { Accept: "application/json" },
        });
        if (!r.ok) return;
        const body = (await r.json()) as { auth_disabled?: boolean };
        if (!cancelled) setIsDisabled(Boolean(body.auth_disabled));
      } catch {
        // network blip — leave the banner state as-is
      }
    };
    void probe();
    const handle = window.setInterval(probe, 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, []);

  if (!isDisabled) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="border-b-2 border-red-500 bg-red-900/40 px-6 py-2 text-center text-xs font-semibold text-red-200"
    >
      ⚠ DEBUG MODE — auth disabled (MERIDIAN_AUTH_DISABLED=1). Never deploy a
      production build with this set.
    </div>
  );
}
