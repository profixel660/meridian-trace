"use client";

import { useEffect } from "react";

import { isAuthenticated } from "@/lib/auth";
import { loginUrlForCurrentPath } from "@/lib/fetcher";

/**
 * Mount-time auth guard for client-side pages that require a session.
 *
 * Renders nothing visually — its single job is to check `isAuthenticated()`
 * on the browser and bounce to `/login?from=<current-path>` when the user
 * isn't signed in. Server-side renders fall through with no-op behaviour
 * so the SSR pass can still emit useful HTML for read-only consumers.
 *
 * The actual write-action calls are independently auth-gated by the
 * `meridianRequest` 401 interceptor — this gate is a UX nicety so the
 * user sees the login screen *before* they click "Build" / "Accept" and
 * get bounced mid-flow.
 */
export function AuthGate() {
  useEffect(() => {
    if (!isAuthenticated()) {
      window.location.assign(loginUrlForCurrentPath());
    }
  }, []);
  return null;
}
