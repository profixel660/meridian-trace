import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { EmptyState } from "@/components/review/EmptyState";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import {
  meridianApi,
  type MasterRow,
  type ProjectCoverage,
} from "@/lib/api";

import { MasterTable } from "./MasterTable";

export const dynamic = "force-dynamic";

export default async function MasterPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = await params;

  let rows: MasterRow[] = [];
  let listError: unknown = null;
  let coverage: ProjectCoverage | null = null;

  try {
    [rows, coverage] = await Promise.all([
      meridianApi.projectMaster(name),
      meridianApi.projectCoverage(name).catch(() => null),
    ]);
  } catch (err) {
    listError = err;
  }

  const counts = coverage
    ? {
        quarantine: coverage.deliverable_status.quarantined,
        audit: coverage.audit.pending,
        questions: coverage.questions.pending,
        conflicts: coverage.conflicts.pending,
        taxonomy: coverage.taxonomy.pending_proposals,
      }
    : undefined;

  return (
    <ReviewLayout
      projectName={name}
      title="Master register"
      subtitle="Read-only view of every deliverable currently on the master register. Filter by trade / service / category."
      counts={counts}
      actions={
        <a
          href={`${
            process.env.NEXT_PUBLIC_MERIDIAN_API ?? "http://localhost:8000"
          }/projects/${encodeURIComponent(name)}/export.xlsx`}
          className="rounded-full border border-border px-4 py-2 text-sm text-text-primary hover:border-text-muted"
        >
          Download Excel
        </a>
      }
    >
      {listError ? (
        <ApiErrorPanel error={listError} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="Master register is empty"
          body={
            <>
              Nothing has been auto-approved or accepted yet. Import a source
              and run extraction, then review the Quarantine queue to populate
              the register.
            </>
          }
          learnMoreHref="/glossary#master-register"
        />
      ) : (
        <MasterTable rows={rows} />
      )}
    </ReviewLayout>
  );
}
