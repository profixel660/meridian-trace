"use client";

import { Tooltip } from "@/components/review/Tooltip";
import {
  FILE_KIND_LABELS,
  FILE_KIND_LABELS_PLURAL,
  type FolderScanResponse,
} from "@/lib/setupClient";

import { FIRST_DOCS_COPY } from "./copy";

interface FolderManifestPreviewProps {
  manifest: FolderScanResponse;
}

/**
 * Renders the manifest returned by `POST /setup/import-folder/scan` as a
 * PM-friendly summary line plus a collapsible list of every file.
 *
 * Discoverability rules applied:
 *  - Tooltip on each file-kind chip explains what that bucket is.
 *  - `<details>` element gives the "Show files" affordance per the brief.
 *  - Skipped files surface separately with a helper line so users know
 *    why something didn't make the cut.
 *  - Empty case is handled by the parent (it renders an `EmptyState`
 *    instead of this component).
 */
export function FolderManifestPreview({ manifest }: FolderManifestPreviewProps) {
  const buckets = (
    Object.entries(manifest.files_by_kind) as Array<
      [keyof typeof FILE_KIND_LABELS, string[]]
    >
  ).filter(([, paths]) => paths.length > 0);

  const summaryParts = buckets.map(([kind, paths]) => {
    const label =
      paths.length === 1 ? FILE_KIND_LABELS[kind] : FILE_KIND_LABELS_PLURAL[kind];
    return `${paths.length} ${label}`;
  });

  return (
    <section
      aria-label="Folder manifest preview"
      className="space-y-3 rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-4"
    >
      <header className="space-y-1">
        <p className="text-sm font-medium text-emerald-200">
          ✓ {FIRST_DOCS_COPY.manifestSummary(summaryParts, manifest.folder_name)}
        </p>
        <p className="font-mono text-[11px] text-text-muted" title={manifest.folder_path}>
          {manifest.folder_path}
        </p>
      </header>

      <div className="flex flex-wrap gap-2 pt-1">
        {buckets.map(([kind, paths]) => (
          <Tooltip
            key={kind}
            content={`${FILE_KIND_LABELS[kind]} files — ${paths.length} found in this folder.`}
            widthClass="w-56"
          >
            <span className="cursor-help rounded-full border border-border bg-surface-elevated px-2.5 py-0.5 text-xs text-text-primary">
              {paths.length} {paths.length === 1 ? FILE_KIND_LABELS[kind] : FILE_KIND_LABELS_PLURAL[kind]}
            </span>
          </Tooltip>
        ))}
      </div>

      <details className="rounded border border-border bg-surface p-3 text-xs">
        <summary className="cursor-pointer select-none text-text-primary">
          {FIRST_DOCS_COPY.manifestShowFilesLabel}
        </summary>
        <div className="mt-3 space-y-3">
          {buckets.map(([kind, paths]) => (
            <div key={kind}>
              <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">
                {FILE_KIND_LABELS_PLURAL[kind]} ({paths.length})
              </p>
              <ul className="mt-1 space-y-0.5 pl-3">
                {paths.map((p) => (
                  <li
                    key={p}
                    className="truncate font-mono text-[11px] text-text-primary"
                    title={p}
                  >
                    {p}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </details>

      {manifest.skipped.length > 0 ? (
        <details className="rounded border border-amber-500/40 bg-amber-500/5 p-3 text-xs">
          <summary className="cursor-pointer select-none text-amber-200">
            {FIRST_DOCS_COPY.manifestSkippedHeader} ({manifest.skipped.length})
          </summary>
          <p className="mt-2 text-text-muted">
            {FIRST_DOCS_COPY.manifestSkippedHelper}
          </p>
          <ul className="mt-2 space-y-1">
            {manifest.skipped.slice(0, 50).map((s) => (
              <li
                key={s.path}
                className="flex flex-col gap-0.5 border-t border-border/50 pt-1"
              >
                <span
                  className="truncate font-mono text-[11px] text-text-primary"
                  title={s.path}
                >
                  {s.path}
                </span>
                <span className="text-[11px] text-text-muted">
                  Reason: {s.reason}
                </span>
              </li>
            ))}
            {manifest.skipped.length > 50 ? (
              <li className="italic text-text-muted">
                …and {manifest.skipped.length - 50} more.
              </li>
            ) : null}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
