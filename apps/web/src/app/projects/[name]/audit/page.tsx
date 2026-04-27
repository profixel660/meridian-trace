import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { EmptyState } from "@/components/review/EmptyState";
import { FirstUseCallout } from "@/components/review/FirstUseCallout";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import { ToastHostProvider } from "@/components/review/ToastHost";
import {
  meridianApi,
  type AuditItem,
  type ProjectCoverage,
} from "@/lib/api";

import { AuditQueue } from "./AuditQueue";

export const dynamic = "force-dynamic";

export default async function AuditPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = await params;

  let items: AuditItem[] = [];
  let listError: unknown = null;
  let coverage: ProjectCoverage | null = null;

  try {
    [items, coverage] = await Promise.all([
      meridianApi.projectAudit(name),
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
    <ToastHostProvider>
      <ReviewLayout
        projectName={name}
        title="Audit (OUTSIDE candidates)"
        subtitle="Rows the gate ruled OUTSIDE the deliverable definition. Promote any that should actually be on the master register."
        counts={counts}
      >
        <FirstUseCallout
          routeKey={`projects/${name}/audit`}
          title="What is the Audit queue?"
        >
          <p>
            Every chunk of source text the AI considers ends in one of three
            outcomes — INSIDE (becomes a deliverable), BORDERLINE (lands in
            Quarantine), or OUTSIDE (logged here). The Audit queue exists so
            nothing the AI looked at is invisible to you.
          </p>
          <p>
            If you spot something the AI dismissed that you actually want on
            the master register, click <strong>Promote</strong> and tag it with
            trade / service / category.
          </p>
        </FirstUseCallout>

        {listError ? (
          <ApiErrorPanel error={listError} />
        ) : items.length === 0 ? (
          <EmptyState
            title="No audit rows"
            body={
              <>
                Nothing in the OUTSIDE log yet. Items land here when the gate
                rules a candidate OUTSIDE the deliverable definition (for
                example, a generic clause about programme timing). Re-run
                extraction to populate.
              </>
            }
            learnMoreHref="/glossary#audit"
          />
        ) : (
          <AuditQueue projectName={name} items={items} />
        )}
      </ReviewLayout>
    </ToastHostProvider>
  );
}
