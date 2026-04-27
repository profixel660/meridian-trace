import { LoginForm } from "./LoginForm";

export const dynamic = "force-dynamic";

/**
 * Login page (server-component shell). Reads the optional `?from=...`
 * query so that on a successful login the form can hand the user back
 * to the page they tried to open before being bounced.
 *
 * The page itself is unauthenticated — `meridianRequest` allows-lists
 * anything under `/auth/...` and the global 401-redirect interceptor
 * skips this path so a stale token doesn't trap the user in a loop.
 */
export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ from?: string }>;
}) {
  const { from } = await searchParams;
  return (
    <div className="mx-auto max-w-md space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
          Sign in
        </h1>
        <p className="text-sm text-text-muted">
          Enter your TOTP code from the authenticator app you enrolled
          via{" "}
          <code className="rounded border border-border bg-surface px-1 py-0.5 text-xs text-text-primary">
            meridian auth enroll
          </code>
          . Lost your phone? Use a recovery code instead — they&apos;re the
          ones you saved at enrolment.
        </p>
      </header>
      <LoginForm fromPath={from ?? null} />
    </div>
  );
}
