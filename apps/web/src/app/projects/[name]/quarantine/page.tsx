import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { EmptyState } from "@/components/review/EmptyState";
import { FirstUseCallout } from "@/components/review/FirstUseCallout";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import { ToastHostProvider } from "@/components/review/ToastHost";
import {
  meridianApi,
  type ProjectCoverage,
  type QuarantinedItem,
} from "@/lib/api";

import { QuarantineQueue } from "./QuarantineQueue";

export const dynamic = "force-dynamic";

export default async function QuarantinePage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = await params;

  let items: QuarantinedItem[] = [];
  let listError: unknown = null;
  let coverage: ProjectCoverage | null = null;

  try {
    [items, coverage] = await Promise.all([
      meridianApi.projectQuarantine(name),
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
        title="Quarantine"
        subtitle="Deliverables held back from the master register pending your decision."
        counts={counts}
      >
        <FirstUseCallout
          routeKey={`projects/${name}/quarantine`}
          title="What is the Quarantine queue?"
        >
          <p>
            When the AI extracts a deliverable from a source document it runs
            it through a three-outcome gate. Anything that doesn&apos;t cleanly
            land INSIDE — BORDERLINE matches, low-confidence calls, or rows
            with raised flags — comes here for a human to decide.
          </p>
          <p>
            Use Accept to move the row onto the master register; Reject to
            remove it (kept in audit history); or Edit to refine wording or
            taxonomy before accepting. Source ref + flags help you check the
            AI&apos;s reasoning.
          </p>
        </FirstUseCallout>

        {listError ? (
          <ApiErrorPanel error={listError} />
        ) : items.length === 0 ? (
          <EmptyState
            title="No quarantined items"
            body={
              <>
                The quarantine queue is the AI&apos;s “I&apos;m not sure” pile.
                A deliverable lands here when the gate said BORDERLINE, the
                confidence was low, or a flag (e.g. <code>tbd_placeholder</code>,
                <code> definition_borderline</code>) was raised. Re-run
                extraction on a new source, or check{" "}
                <strong>Audit</strong> for OUTSIDE rows you might want to
                promote.
              </>
            }
            learnMoreHref="/glossary#quarantine"
          />
        ) : (
          <QuarantineQueue projectName={name} items={items} />
        )}
      </ReviewLayout>
    </ToastHostProvider>
  );
}
