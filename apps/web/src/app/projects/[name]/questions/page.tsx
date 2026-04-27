import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { EmptyState } from "@/components/review/EmptyState";
import { FirstUseCallout } from "@/components/review/FirstUseCallout";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import { ToastHostProvider } from "@/components/review/ToastHost";
import {
  meridianApi,
  type ProjectCoverage,
  type QuestionItem,
} from "@/lib/api";

import { QuestionsQueue } from "./QuestionsQueue";

export const dynamic = "force-dynamic";

export default async function QuestionsPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = await params;

  let items: QuestionItem[] = [];
  let listError: unknown = null;
  let coverage: ProjectCoverage | null = null;

  try {
    [items, coverage] = await Promise.all([
      meridianApi.projectQuestions(name, "pending"),
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
        title="Questions"
        subtitle="Open questions raised by the AI during extraction. Resolve to clear the qualifier from impacted deliverables."
        counts={counts}
      >
        <FirstUseCallout
          routeKey={`projects/${name}/questions`}
          title="What is the Questions queue?"
        >
          <p>
            When the AI hits something it can&apos;t resolve confidently
            (e.g. an inconsistent revision, a TBD value, a term that might mean
            two different things), it raises a question instead of guessing.
          </p>
          <p>
            Type a short answer and click <strong>Resolve</strong> — the AI
            will use it the next time the affected rows are re-evaluated. Use{" "}
            <strong>Dismiss</strong> if the question is no longer meaningful.
          </p>
        </FirstUseCallout>

        {listError ? (
          <ApiErrorPanel error={listError} />
        ) : items.length === 0 ? (
          <EmptyState
            title="No open questions"
            body={
              <>
                Nothing the AI flagged is waiting on you. Questions appear
                when extraction surfaces an ambiguity it can&apos;t safely
                resolve — for example, &quot;is this clause superseded by the
                later revision?&quot;.
              </>
            }
            learnMoreHref="/glossary#question"
          />
        ) : (
          <QuestionsQueue projectName={name} items={items} />
        )}
      </ReviewLayout>
    </ToastHostProvider>
  );
}
