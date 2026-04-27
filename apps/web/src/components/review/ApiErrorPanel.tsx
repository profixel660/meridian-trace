import { MeridianApiError, meridianApi } from "@/lib/api";

interface ApiErrorPanelProps {
  error: unknown;
  /** Custom hint above the uvicorn command. */
  hint?: string;
}

/**
 * Friendly error card for read-side fetch failures. Mirrors the pattern
 * used on the existing `/` page so the UI feels consistent.
 *
 * Discoverability rule #7: "tell the user what to do next" — the body
 * always includes the uvicorn command for the most common failure mode
 * (backend not running).
 */
export function ApiErrorPanel({ error, hint }: ApiErrorPanelProps) {
  const isApi = error instanceof MeridianApiError;
  const title = isApi
    ? `Meridian API returned ${error.status}`
    : "Could not reach the Meridian API";
  const detail = isApi
    ? error.body || error.message
    : error instanceof Error
      ? error.message
      : String(error);

  return (
    <div className="rounded-lg border border-red-500/40 bg-red-500/5 p-6">
      <h2 className="text-base font-semibold text-text-primary">{title}</h2>
      <p className="mt-2 text-sm text-text-muted">
        {hint ??
          `Confirm the FastAPI backend is running at ${meridianApi.apiBase}. From the project root:`}
      </p>
      <pre className="mt-3 overflow-x-auto rounded border border-border bg-background p-3 text-xs text-text-primary">
        uv run uvicorn meridian.api.main:app --reload --port 8000
      </pre>
      <p className="mt-3 text-xs text-text-muted">
        Override the API base via <code>NEXT_PUBLIC_MERIDIAN_API</code>.
      </p>
      <details className="mt-3">
        <summary className="cursor-pointer text-xs text-text-muted">
          Error detail
        </summary>
        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs text-text-muted">
          {detail}
        </pre>
      </details>
    </div>
  );
}
