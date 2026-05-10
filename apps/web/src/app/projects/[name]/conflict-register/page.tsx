"use client";

import { useEffect, useState } from "react";

import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import {
  conflictRegisterXlsxUrl,
  fetchConflictRegister,
  type ConflictRegisterResponse,
  type ConflictRegisterStatusFilter,
} from "@/lib/apiClient/conflicts";
import { useRuntimeProjectSlug } from "@/lib/useRuntimeProjectSlug";

import { ConflictRegisterTable } from "./ConflictRegisterTable";

export default function ConflictRegisterPage() {
  const name = useRuntimeProjectSlug();
  const [status, setStatus] = useState<ConflictRegisterStatusFilter>("all");
  const [data, setData] = useState<ConflictRegisterResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!name) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const result = await fetchConflictRegister(name, status);
        if (!cancelled) setData(result);
      } catch (err) {
        if (!cancelled) setError(err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [name, status]);

  if (!name) {
    return (
      <ReviewLayout projectName="" title="Conflict register" subtitle="">
        <div className="text-text-muted text-sm">Loading…</div>
      </ReviewLayout>
    );
  }

  return (
    <ReviewLayout
      projectName={name}
      title="Conflict register"
      subtitle="Every cross-source disagreement detected and the call you made on each."
      actions={
        <a
          href={conflictRegisterXlsxUrl(name)}
          download
          className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          Download xlsx
        </a>
      }
    >
      <div className="mb-4 flex flex-wrap gap-2 text-xs">
        {(["all", "pending", "resolved", "superseded"] as const).map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setStatus(s)}
            className={`rounded-full px-3 py-1 ${
              status === s
                ? "bg-accent text-white"
                : "bg-surface text-text-muted hover:text-text-primary"
            }`}
          >
            {s.charAt(0).toUpperCase() + s.slice(1)}
            {data && (
              <span className="ml-1 opacity-70">{data.counts[s]}</span>
            )}
          </button>
        ))}
      </div>
      {error ? (
        <ApiErrorPanel error={error} />
      ) : loading || data === null ? (
        <div className="text-text-muted text-sm">Loading…</div>
      ) : (
        <ConflictRegisterTable projectSlug={name} items={data.items} />
      )}
    </ReviewLayout>
  );
}
