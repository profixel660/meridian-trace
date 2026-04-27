"use client";

import { useEffect, useState } from "react";

import { meridianApi, MeridianApiError, type HealthResponse } from "@/lib/api";
import { apiFetch } from "@/lib/fetcher";

export default function HealthPage() {
  const [data, setData] = useState<HealthResponse | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await apiFetch<HealthResponse>("/health");
        if (!cancelled) setData(result);
      } catch (e) {
        if (!cancelled) setErr(e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  let content: React.ReactNode;
  if (loading) {
    content = <div className="text-text-muted text-sm">Loading…</div>;
  } else if (err) {
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
  } else if (data) {
    content = (
      <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
        <dt className="text-text-muted">status</dt>
        <dd className="font-mono text-text-primary">{data.status}</dd>
        <dt className="text-text-muted">version</dt>
        <dd className="font-mono text-text-primary">{data.version}</dd>
      </dl>
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
