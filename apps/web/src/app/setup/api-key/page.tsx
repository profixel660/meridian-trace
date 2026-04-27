"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { Tooltip } from "@/components/review/Tooltip";
import { SetupShell } from "@/components/setup/SetupShell";
import { API_KEY_COPY } from "@/components/setup/copy";
import {
  type ApiKeyValidationResponse,
  setupApi,
} from "@/lib/setupClient";
import { openExternal } from "@/lib/tauri";

type FormOutcome =
  | { kind: "idle" }
  | { kind: "validating" }
  | { kind: "valid"; message: string }
  | { kind: "invalid"; message: string }
  | { kind: "unable"; message: string }
  | { kind: "error"; message: string };

/**
 * Step 1 — Connect your AI service.
 *
 * Three-outcome on validate:
 *  - valid: green confirmation, advance enabled
 *  - invalid: red panel with three numbered next-step bullets
 *  - unable_to_verify: amber, advance enabled (key saved, retried later)
 *
 * The "Where do I get this?" link uses `openExternal` so the link lands
 * in the user's default browser (not inside the Tauri webview where they
 * couldn't sign in to Anthropic anyway).
 */
export default function SetupApiKeyPage() {
  const router = useRouter();
  const [key, setKey] = useState("");
  const [outcome, setOutcome] = useState<FormOutcome>({ kind: "idle" });

  const validate = useCallback(async () => {
    if (!key.trim()) return;
    setOutcome({ kind: "validating" });
    try {
      const res: ApiKeyValidationResponse = await setupApi.validateApiKey(
        key.trim(),
      );
      switch (res.outcome) {
        case "valid":
          setOutcome({
            kind: "valid",
            message: res.message || API_KEY_COPY.outcomes.valid.body,
          });
          break;
        case "unable_to_verify":
          setOutcome({
            kind: "unable",
            message: res.message || API_KEY_COPY.outcomes.unable.body,
          });
          break;
        case "invalid":
        default:
          setOutcome({
            kind: "invalid",
            message: res.message || "",
          });
          break;
      }
    } catch (err) {
      setOutcome({
        kind: "error",
        message:
          err instanceof Error ? err.message : "Could not reach the Meridian API.",
      });
    }
  }, [key]);

  const canContinue = outcome.kind === "valid" || outcome.kind === "unable";
  // Alpha-2 reframe: documents come BEFORE project so the folder name can
  // pre-fill the project-name input on the next page.
  const handleContinue = () => router.push("/setup/first-documents");

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (canContinue) handleContinue();
      else if (outcome.kind !== "validating") void validate();
    }
    if (e.key === "Escape") {
      e.preventDefault();
      setKey("");
      setOutcome({ kind: "idle" });
    }
  };

  return (
    <SetupShell
      step="api-key"
      backHref="/setup"
      onContinue={handleContinue}
      continueDisabled={!canContinue}
      busy={outcome.kind === "validating"}
    >
      <div className="space-y-8">
        <header className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
            {API_KEY_COPY.title}
          </h1>
        </header>

        {/* WHY block — always above the form */}
        <section
          aria-label="Why this step"
          className="rounded-lg border border-accent/30 bg-accent/5 p-4 text-sm text-text-primary"
        >
          {API_KEY_COPY.why()}
        </section>

        {/* HOW — the form */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void validate();
          }}
          className="space-y-3"
        >
          <label
            htmlFor="anthropic-key"
            className="block text-sm font-medium text-text-primary"
          >
            {API_KEY_COPY.inputLabel}{" "}
            <Tooltip
              content={API_KEY_COPY.glossaryTooltip}
              widthClass="w-80"
            >
              <span className="cursor-help text-xs text-text-muted underline decoration-dotted">
                what&apos;s Claude?
              </span>
            </Tooltip>
            {"  "}
            <Tooltip
              content={API_KEY_COPY.prefixTooltip}
              widthClass="w-72"
            >
              <span className="cursor-help text-xs text-text-muted underline decoration-dotted">
                why &quot;sk-ant-&quot;?
              </span>
            </Tooltip>
            <span className="ml-2 text-[11px] font-normal text-text-muted">
              (technically: {API_KEY_COPY.inputLabelTechnical})
            </span>
          </label>
          <input
            id="anthropic-key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={key}
            onChange={(e) => {
              setKey(e.target.value);
              if (outcome.kind !== "idle" && outcome.kind !== "validating") {
                setOutcome({ kind: "idle" });
              }
            }}
            onKeyDown={onKeyDown}
            placeholder="sk-ant-..."
            className="w-full rounded-md border border-border bg-surface px-3 py-2 font-mono text-sm text-text-primary placeholder:text-text-muted focus:border-accent focus:outline-none"
          />
          <p className="text-xs text-text-muted">
            {API_KEY_COPY.inputHelper}{" "}
            <button
              type="button"
              onClick={() => void openExternal(API_KEY_COPY.whereToGetUrl)}
              className="text-accent hover:underline"
            >
              {API_KEY_COPY.whereToGet} →
            </button>
          </p>

          <div className="flex items-center gap-3 pt-2">
            <button
              type="submit"
              disabled={!key.trim() || outcome.kind === "validating"}
              className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
            >
              {outcome.kind === "validating"
                ? "Checking…"
                : API_KEY_COPY.validateLabel}
            </button>
            <p className="text-xs text-text-muted">
              Press <kbd className="rounded border border-border px-1">Enter</kbd>{" "}
              to validate, <kbd className="rounded border border-border px-1">Esc</kbd>{" "}
              to clear.
            </p>
          </div>
        </form>

        {/* Three-outcome surfaces */}
        {outcome.kind === "valid" ? (
          <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-4">
            <p className="text-sm font-medium text-emerald-300">
              ✓ {API_KEY_COPY.outcomes.valid.headline}
            </p>
            <p className="mt-1 text-sm text-text-muted">{outcome.message}</p>
          </div>
        ) : null}

        {outcome.kind === "unable" ? (
          <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
            <p className="text-sm font-medium text-amber-300">
              ⚠ {API_KEY_COPY.outcomes.unable.headline}
            </p>
            <p className="mt-1 text-sm text-text-muted">{outcome.message}</p>
          </div>
        ) : null}

        {outcome.kind === "invalid" ? (
          <div className="rounded-lg border border-red-500/50 bg-red-500/5 p-4">
            <p className="text-sm font-medium text-red-300">
              ✗ {API_KEY_COPY.outcomes.invalid.headline}
            </p>
            {outcome.message ? (
              <p className="mt-1 text-sm text-text-muted">{outcome.message}</p>
            ) : null}
            <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm text-text-primary">
              {API_KEY_COPY.outcomes.invalid.nextSteps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
          </div>
        ) : null}

        {outcome.kind === "error" ? (
          <div className="rounded-lg border border-red-500/50 bg-red-500/5 p-4">
            <p className="text-sm font-medium text-red-300">
              Couldn&apos;t reach the Meridian backend.
            </p>
            <p className="mt-1 text-sm text-text-muted">{outcome.message}</p>
            <p className="mt-2 text-xs text-text-muted">
              The desktop app starts the backend automatically; if you&apos;re
              running the browser preview, make sure{" "}
              <code className="rounded bg-surface px-1">uv run uvicorn meridian.api.main:app</code>{" "}
              is running on port 8000.
            </p>
          </div>
        ) : null}

        <footer className="border-t border-border pt-4 text-xs text-text-muted">
          {API_KEY_COPY.privacyFooter()}
        </footer>
      </div>
    </SetupShell>
  );
}
