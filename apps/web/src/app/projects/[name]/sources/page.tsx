"use client";

import { useEffect, useState } from "react";

import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { EmptyState } from "@/components/review/EmptyState";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import { StatusBadge } from "@/components/review/StatusBadge";
import {
  type ProjectCoverage,
  type SourceItem,
} from "@/lib/api";
import { apiFetch } from "@/lib/fetcher";
import { useRuntimeProjectSlug } from "@/lib/useRuntimeProjectSlug";

function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export default function SourcesPage() {
  const name = useRuntimeProjectSlug();

  const [items, setItems] = useState<SourceItem[] | null>(null);
  const [listError, setListError] = useState<unknown>(null);
  const [coverage, setCoverage] = useState<ProjectCoverage | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!name) return;
    let cancelled = false;
    setLoading(true);
    setListError(null);
    (async () => {
      try {
        const [list, cov] = await Promise.all([
          apiFetch<SourceItem[]>(
            `/projects/${encodeURIComponent(name)}/sources`,
          ),
          apiFetch<ProjectCoverage>(
            `/projects/${encodeURIComponent(name)}/coverage`,
          ).catch(() => null),
        ]);
        if (!cancelled) {
          setItems(list);
          setCoverage(cov);
        }
      } catch (err) {
        if (!cancelled) setListError(err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [name]);

  const counts = coverage
    ? {
        quarantine: coverage.deliverable_status.quarantined,
        audit: coverage.audit.pending,
        questions: coverage.questions.pending,
        conflicts: coverage.conflicts.pending,
        taxonomy: coverage.taxonomy.pending_proposals,
      }
    : undefined;

  if (!name) {
    return (
      <ReviewLayout
        projectName=""
        title="Sources"
        subtitle="Source documents imported into this project, with extraction status."
      >
        <div className="text-text-muted text-sm">Loading…</div>
      </ReviewLayout>
    );
  }

  return (
    <ReviewLayout
      projectName={name}
      title="Sources"
      subtitle="Source documents imported into this project, with extraction status."
      counts={counts}
    >
      {listError ? (
        <ApiErrorPanel error={listError} />
      ) : loading || items === null ? (
        <div className="text-text-muted text-sm">Loading…</div>
      ) : items.length === 0 ? (
        <EmptyState
          title="No sources imported"
          body={
            <>
              Source documents are PDFs, drawings, and Excel files that the
              project pulls deliverables from. Use the setup wizard to import
              a folder of documents, or add files individually.
            </>
          }
          ctaHref="/setup/first-documents"
          ctaLabel="Add documents"
          learnMoreHref="/glossary#provenance"
          learnMoreLabel="What is a source document?"
        />
      ) : (
        <ul className="space-y-3">
          {items.map((s) => (
            <li
              key={s.id}
              className="rounded-md border border-border bg-surface-elevated p-4"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="text-base font-semibold text-text-primary">
                  {s.filename}
                </h3>
                <span className="text-[11px] text-text-muted">
                  imported {new Date(s.imported_at).toLocaleDateString()} •{" "}
                  {bytes(s.size_bytes)}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                {s.is_template ? (
                  <StatusBadge
                    label="template"
                    variant="warn"
                    explanation={{
                      title: "Template detected",
                      body: "Source appears to be an unfilled template; auto-excluded from extraction.",
                    }}
                  />
                ) : null}
                {s.is_demarcation_schedule ? (
                  <StatusBadge
                    label="demarcation schedule"
                    variant="neutral"
                    explanation={{
                      title: "Demarcation Schedule",
                      body: "Authoritative source for trade / responsibility allocation. Cross-checked against other sources.",
                    }}
                    glossaryAnchor="/glossary#demarcation-schedule"
                  />
                ) : null}
                {s.document_class ? (
                  <span className="rounded-full border border-border px-2 py-0.5 text-text-muted">
                    {s.document_class}
                  </span>
                ) : null}
                {s.document_state ? (
                  <span className="rounded-full border border-border px-2 py-0.5 text-text-muted">
                    {s.document_state}
                  </span>
                ) : null}
                {s.revision ? (
                  <span className="rounded-full border border-border px-2 py-0.5 text-text-muted">
                    Rev {s.revision}
                  </span>
                ) : null}
              </div>
              <dl className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                <Stat label="Deliverables" value={s.deliverables_count} />
                <Stat label="Candidates reviewed" value={s.audit_count} />
                <Stat label="MIME" value={s.mime_type} />
                <Stat
                  label="Extraction method"
                  value={EXTRACTION_PATH_LABEL[s.extraction_path] ?? s.extraction_path}
                />
              </dl>
              {s.quality_scan_summary ? (
                <p className="mt-3 rounded border border-border bg-background p-3 text-xs text-text-muted">
                  Quality scan: {s.quality_scan_summary}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </ReviewLayout>
  );
}

const EXTRACTION_PATH_LABEL: Record<string, string> = {
  text_spec: "Text specification",
  bod_import: "Basis of Design import",
  drawing: "Drawing",
  demarcation: "Demarcation schedule",
  excluded: "Excluded from extraction",
  pending: "Pending extraction",
};

function Stat({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string | number;
  mono?: boolean;
}) {
  return (
    <div className="border-b border-border/60 pb-1">
      <dt className="text-[10px] uppercase tracking-wide text-text-muted">
        {label}
      </dt>
      <dd
        className={`text-text-primary ${
          mono ? "break-all font-mono text-[11px]" : "text-sm"
        }`}
      >
        {value}
      </dd>
    </div>
  );
}
