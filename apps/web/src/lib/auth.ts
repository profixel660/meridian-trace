/**
 * Browser-side bearer-token storage for the round-12 §3.2 auth flow.
 *
 * The Meridian API requires `Authorization: Bearer <token>` on POST/PUT/
 * DELETE routes. Tokens are minted by `POST /auth/login` and stored here
 * in `localStorage` so they survive page reloads. The session-expiry
 * timestamp is stored alongside so we can short-circuit obviously-stale
 * tokens client-side without a network round-trip.
 *
 * SECURITY NOTE: localStorage is XSS-readable. This is acceptable for
 * Meridian's threat model (single-user desktop, localhost-bound API per
 * CONTEXT.md §16) — the TOTP step is the real auth gate; the bearer is a
 * per-session convenience lock. Do NOT carry this pattern to a multi-
 * tenant deployment without re-evaluating against an httpOnly cookie.
 */

const TOKEN_KEY = "meridian.auth.token";
const EXPIRES_KEY = "meridian.auth.expires_at";

/** True only in a browser context (guards against SSR access). */
function hasStorage(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

/**
 * Return the stored bearer token, or `null` when absent / expired /
 * unreadable. Expiry is checked against `Date.now()` with a 30-second
 * skew to avoid race conditions at the boundary.
 */
export function getBearerToken(): string | null {
  if (!hasStorage()) return null;
  try {
    const token = window.localStorage.getItem(TOKEN_KEY);
    if (!token) return null;
    const expiresAt = window.localStorage.getItem(EXPIRES_KEY);
    if (expiresAt) {
      const expiryMs = Date.parse(expiresAt);
      if (!Number.isNaN(expiryMs) && expiryMs - 30_000 < Date.now()) {
        // Stale — drop it so callers don't keep trying.
        clearBearerToken();
        return null;
      }
    }
    return token;
  } catch {
    return null;
  }
}

/**
 * Persist a freshly-minted bearer + ISO-8601 expiry (the shape returned by
 * `POST /auth/login`).
 */
export function setBearerToken(token: string, expiresAt: string): void {
  if (!hasStorage()) return;
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
    window.localStorage.setItem(EXPIRES_KEY, expiresAt);
  } catch {
    // localStorage may be blocked (incognito + strict storage policy);
    // the request layer will surface the resulting 401 to the user.
  }
}

/**
 * Forget the bearer + expiry. Safe to call on every logout-button click —
 * idempotent and never throws.
 */
export function clearBearerToken(): void {
  if (!hasStorage()) return;
  try {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(EXPIRES_KEY);
  } catch {
    // ignore
  }
}

/** Return the stored expiry as an ISO-8601 string, or `null`. */
export function getExpiresAt(): string | null {
  if (!hasStorage()) return null;
  try {
    return window.localStorage.getItem(EXPIRES_KEY);
  } catch {
    return null;
  }
}

/**
 * Whole minutes until the stored token expires. Returns `null` when no
 * expiry is stored or the value is unparseable; floored at zero so a
 * just-expired token reads as `0`, not negative.
 */
export function minutesUntilExpiry(): number | null {
  const expiresAt = getExpiresAt();
  if (!expiresAt) return null;
  const expiryMs = Date.parse(expiresAt);
  if (Number.isNaN(expiryMs)) return null;
  return Math.max(0, Math.floor((expiryMs - Date.now()) / 60_000));
}

/** Cheap synchronous check used by client-side guards. */
export function isAuthenticated(): boolean {
  return getBearerToken() !== null;
}

/**
 * Wrap a `RequestInit` so the `Authorization: Bearer <token>` header is
 * injected when a live token exists. Existing headers on the input are
 * preserved; the bearer is only added when `Authorization` isn't already
 * set, so callers can override per-request if needed.
 */
export function withAuth(init?: RequestInit): RequestInit {
  const token = getBearerToken();
  if (!token) return init ?? {};
  const merged: RequestInit = { ...(init ?? {}) };
  const headers = new Headers(merged.headers ?? {});
  if (!headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  merged.headers = headers;
  return merged;
}
