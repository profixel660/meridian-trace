"use client";

/**
 * Alpha-17: AuthGate is now a no-op.
 *
 * Alpha-15 removed all backend ``Depends(require_session)`` gates so no
 * route returns 401, but this client-side guard kept redirecting to
 * /login whenever localStorage had no token. Net result: the user
 * still hit the TOTP login screen even though the product had no
 * server-side auth. Alpha-17 makes the gate a no-op until TOTP
 * enforcement is re-enabled at v0.3 readiness.
 *
 * The component is preserved (rather than deleted) so the existing
 * call sites under projects/[name]/page.tsx and setup/ready/page.tsx
 * keep compiling. Restoring auth = reverting this file.
 */
export function AuthGate() {
  return null;
}
