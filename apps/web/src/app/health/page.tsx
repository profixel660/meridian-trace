import { meridianApi, MeridianApiError } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function HealthPage() {
  let content: React.ReactNode;
  try {
    const { status, version } = await meridianApi.health();
    content = (
      <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
        <dt className="text-text-muted">status</dt>
        <dd className="font-mono text-text-primary">{status}</dd>
        <dt className="text-text-muted">version</dt>
        <dd className="font-mono text-text-primary">{version}</dd>
      </dl>
    );
  } catch (err) {
    const isApi = err instanceof MeridianApiError;
    const detail = isApi
      ? `${err.status}: ${err.body || err.message}`
      : err instanceof Error
        ? err.message
        : String(err);
    content = (
      <div className="text-sm text-text-muted">
        <p>
          Meridian API at{" "}
          <code className="font-mono">{meridianApi.apiBase}</code> is
          unreachable.
        </p>
        <pre className="mt-2 whitespace-pre-wrap text-xs">{detail}</pre>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
        Health
      </h1>
      <div className="rounded-lg border border-border bg-surface-elevated p-6">
        {content}
      </div>
    </div>
  );
}
