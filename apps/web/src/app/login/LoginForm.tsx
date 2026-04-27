"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState, type FormEvent } from "react";

import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { FirstUseCallout } from "@/components/review/FirstUseCallout";
import { Tooltip, TooltipMore } from "@/components/review/Tooltip";
import { MeridianApiError, meridianRequest } from "@/lib/api";
import { setBearerToken } from "@/lib/auth";

type Mode = "totp" | "recovery";

interface LoginResponse {
  bearer_token: string;
  expires_at: string;
}

/**
 * Three-outcome login state machine — `valid` (success, navigates away),
 * `invalid_credentials` (401), or `rate_limited` (429). Anything else
 * (network / 5xx) lands in `pageError` and renders an `ApiErrorPanel`
 * so the operator gets actionable next steps.
 */
type LoginOutcome =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "invalid_credentials" }
  | { kind: "rate_limited"; retryAfterSeconds: number | null };

interface LoginFormProps {
  /**
   * Optional `?from=` query value — where to redirect after a successful
   * login. Validated to be a same-origin path before use.
   */
  fromPath: string | null;
}

/** Defensive: only honour `from` if it points at a same-origin path. */
function safeRedirectTarget(from: string | null): string {
  if (!from) return "/projects";
  // Reject schemes / hosts entirely — accept only relative paths.
  if (from.startsWith("//") || from.startsWith("http")) return "/projects";
  if (!from.startsWith("/")) return "/projects";
  // Reject the login path itself to avoid bounce loops.
  if (from === "/login" || from.startsWith("/login?")) return "/projects";
  return from;
}

export function LoginForm({ fromPath }: LoginFormProps) {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("totp");
  const [code, setCode] = useState("");
  const [outcome, setOutcome] = useState<LoginOutcome>({ kind: "idle" });
  const [pageError, setPageError] = useState<unknown>(null);

  const submitting = outcome.kind === "submitting";

  const onSubmit = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      const trimmed = code.trim();
      if (!trimmed) return;
      setPageError(null);
      setOutcome({ kind: "submitting" });

      const body =
        mode === "totp"
          ? { totp_code: trimmed }
          : { recovery_code: trimmed };

      try {
        const result = await meridianRequest<LoginResponse>("/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        setBearerToken(result.bearer_token, result.expires_at);
        const target = safeRedirectTarget(fromPath);
        // Use a hard navigation — `router.push` doesn't refresh the
        // server-component tree, and the dashboard is dynamic.
        window.location.assign(target);
        return;
      } catch (err) {
        if (err instanceof MeridianApiError) {
          if (err.status === 401) {
            setOutcome({ kind: "invalid_credentials" });
            return;
          }
          if (err.status === 429) {
            setOutcome({ kind: "rate_limited", retryAfterSeconds: 300 });
            return;
          }
        }
        setPageError(err);
        setOutcome({ kind: "idle" });
      }
    },
    [code, mode, fromPath],
  );

  return (
    <div className="space-y-4">
      <FirstUseCallout
        routeKey="/login"
        title="What is TOTP?"
      >
        <p>
          A 6-digit code from an authenticator app (Google Authenticator,
          1Password, Authy, etc.) that rotates every 30 seconds. Meridian
          uses TOTP because it works fully offline — no SSO provider, no
          phone number, no shared secret in flight.
        </p>
        <p className="pt-2">
          Don&apos;t have a code yet? Run{" "}
          <code className="rounded border border-border px-1 py-0.5 text-xs">
            meridian auth enroll
          </code>{" "}
          from a terminal — it shows a QR code you scan once.
        </p>
      </FirstUseCallout>

      <div
        role="tablist"
        aria-label="Credential type"
        className="flex gap-1 rounded-md border border-border bg-surface-elevated p-1 text-xs"
      >
        <ModeTab
          active={mode === "totp"}
          onClick={() => {
            setMode("totp");
            setCode("");
            setOutcome({ kind: "idle" });
          }}
          label="TOTP code"
        />
        <ModeTab
          active={mode === "recovery"}
          onClick={() => {
            setMode("recovery");
            setCode("");
            setOutcome({ kind: "idle" });
          }}
          label="Recovery code"
        />
      </div>

      <form onSubmit={onSubmit} className="space-y-3">
        <label className="block text-xs uppercase tracking-wide text-text-muted">
          <Tooltip
            content={
              <span>
                <strong className="block text-text-primary">
                  {mode === "totp" ? "TOTP code" : "Recovery code"}
                </strong>
                <span className="mt-1 block text-text-muted">
                  {mode === "totp"
                    ? "6 digits from your authenticator app. Codes rotate every 30 seconds — if it just changed, wait for the next rotation."
                    : "One of the ABCD-EFGH-IJKL codes you saved at enrolment. Each one works exactly once."}
                </span>
                <TooltipMore href="/glossary#totp" />
              </span>
            }
          >
            <button type="button" className="cursor-help underline decoration-dotted">
              {mode === "totp" ? "TOTP code" : "Recovery code"}
            </button>
          </Tooltip>
        </label>
        <input
          // `name="totp"` keeps autofill out (not "username"/"password") since
          // the value is single-use. Disable autocomplete for the same reason.
          name="totp"
          type="text"
          inputMode={mode === "totp" ? "numeric" : "text"}
          autoComplete="off"
          autoFocus
          value={code}
          onChange={(e) => setCode(e.target.value)}
          disabled={submitting}
          placeholder={mode === "totp" ? "123 456" : "ABCD-EFGH-IJKL"}
          aria-invalid={outcome.kind === "invalid_credentials"}
          aria-describedby={
            outcome.kind === "invalid_credentials"
              ? "login-error"
              : outcome.kind === "rate_limited"
                ? "login-error"
                : undefined
          }
          className="w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-base text-text-primary outline-none focus:border-accent disabled:opacity-50"
        />

        {outcome.kind === "invalid_credentials" ? (
          <p
            id="login-error"
            role="alert"
            className="rounded border border-red-500/40 bg-red-500/5 px-3 py-2 text-sm text-red-200"
          >
            Invalid credentials. Double-check the code on your authenticator
            app — they rotate every 30 seconds. If you keep getting this
            after a fresh code, your TOTP secret may not be enrolled on
            this install.
          </p>
        ) : null}

        {outcome.kind === "rate_limited" ? (
          <p
            id="login-error"
            role="alert"
            className="rounded border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm text-amber-100"
          >
            Too many login attempts from this address. Wait{" "}
            {outcome.retryAfterSeconds
              ? Math.ceil(outcome.retryAfterSeconds / 60)
              : 5}{" "}
            minutes before trying again. (This protects against brute-force
            guessing.)
          </p>
        ) : null}

        {pageError ? <ApiErrorPanel error={pageError} /> : null}

        <button
          type="submit"
          disabled={submitting || code.trim().length === 0}
          className="w-full rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "Verifying…" : "Sign in"}
        </button>
      </form>

      <p className="text-[11px] text-text-muted">
        Tokens are stored in this browser only and expire after 8 hours.
        Use the Logout button in the header to clear them sooner.
      </p>
    </div>
  );
}

function ModeTab({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`flex-1 rounded px-3 py-1.5 transition ${
        active
          ? "bg-accent/20 text-accent"
          : "text-text-muted hover:bg-surface hover:text-text-primary"
      }`}
    >
      {label}
    </button>
  );
}
