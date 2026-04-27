/**
 * Browser-side fetch wrapper that:
 *   1. Injects `Authorization: Bearer <token>` from `lib/auth.ts`
 *   2. Resolves URLs against `NEXT_PUBLIC_MERIDIAN_API`
 *   3. On a 401, clears the stale token and redirects to
 *      `/login?from=<current-path>` so the user lands back where they
 *      started after a successful re-auth.
 *
 * Use this for any *new* client-side fetch you write. Existing apiClient
 * modules (`tender.ts`, `evidence.ts`, `xref.ts`, `queueClient.ts`) flow
 * through `meridianRequest` in `lib/api.ts`, which itself uses
 * `withAuth(...)` and surfaces 401s as `MeridianApiError(401)` —
 * `handleUnauthorizedRedirect` below is the single place that decides
 * what to do with that 401 inside a client component.
 */

import { MeridianApiError, meridianRequest } from "./api";
import { clearBearerToken } from "./auth";

/**
 * Build a `?from=...` URL pointing at the current location, used so
 * `/login` can hand the user back after a successful re-auth.
 */
export function loginUrlForCurrentPath(): string {
  if (typeof window === "undefined") return "/login";
  const from = `${window.location.pathname}${window.location.search}`;
  return `/login?from=${encodeURIComponent(from)}`;
}

/**
 * Centralised 401 reaction: drop the stale token and bounce to /login.
 * Idempotent — safe to call from multiple in-flight requests landing
 * 401 simultaneously (the second `assign` is a no-op if we've already
 * left the page).
 */
export function handleUnauthorizedRedirect(): void {
  if (typeof window === "undefined") return;
  clearBearerToken();
  window.location.assign(loginUrlForCurrentPath());
}

/**
 * Browser-side fetch helper. Calls the typed API; on 401 it bounces to
 * `/login` rather than letting the error surface as a generic
 * ApiErrorPanel (which would leave the user staring at a confusing
 * "Meridian API returned 401" message instead of being able to log in).
 *
 * Any other error bubbles up as `MeridianApiError` so the calling
 * component can render its own ApiErrorPanel + retry affordance.
 */
export async function apiFetch<T>(
  path: string,
  init?: RequestInit & { revalidate?: number },
): Promise<T> {
  try {
    return await meridianRequest<T>(path, init);
  } catch (err) {
    if (err instanceof MeridianApiError && err.status === 401) {
      handleUnauthorizedRedirect();
      // We've initiated navigation; throw something the caller can
      // safely swallow but that prevents downstream `.then(...)` paths
      // from operating on undefined data.
      throw err;
    }
    throw err;
  }
}
