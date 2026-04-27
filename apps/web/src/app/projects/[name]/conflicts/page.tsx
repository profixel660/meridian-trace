import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { EmptyState } from "@/components/review/EmptyState";
import { FirstUseCallout } from "@/components/review/FirstUseCallout";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import { ToastHostProvider } from "@/components/review/ToastHost";
import {
  meridianApi,
  type ConflictItem,
  type ProjectCoverage,
} from "@/lib/api";

import { ConflictsQueue } from "./ConflictsQueue";

export const dynamic = "force-dynamic";

export default async function ConflictsPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = await params;

  let items: ConflictItem[] = [];
  let listError: unknown = null;
  let coverage: ProjectCoverage | null = null;

  try {
    [items, coverage] = await Promise.all([
      meridianApi.projectConflicts(name, "pending"),
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
        title="Conflicts"
        subtitle="Two or more sources disagree about the same item. Pick the winning side, accept a hybrid, or reject both."
        counts={counts}
      >
        <FirstUseCallout
          routeKey={`projects/${name}/conflicts`}
          title="What is the Conflicts queue?"
        >
          <p>
            A conflict is recorded whenever two sources cover the same
            deliverable but disagree — different responsibilities, different
            revisions, different document classes, or competing scope
            demarcations. The AI tags the most onerous side with reasoning, but
            the final call is yours.
          </p>
          <p>
            Use <strong>Accept A</strong> / <strong>Accept B</strong> to keep
            one party (the other is rejected); <strong>Reject both</strong> if
            neither is right; or open a row to record a hybrid resolution.
          </p>
        </FirstUseCallout>

        {listError ? (
          <ApiErrorPanel error={listError} />
        ) : items.length === 0 ? (
          <EmptyState
            title="No open conflicts"
            body={
              <>
                Nothing crossed between sources is in dispute. Conflicts are
                detected during extraction when the same deliverable surfaces
                in two sources with diverging values — for example, a
                Demarcation Schedule allocates a duct run to MEP but the BOD
                puts it on the architect.
              </>
            }
            learnMoreHref="/glossary#conflict"
          />
        ) : (
          <ConflictsQueue projectName={name} items={items} />
        )}
      </ReviewLayout>
    </ToastHostProvider>
  );
}
