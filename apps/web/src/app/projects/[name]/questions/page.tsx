"use client";

import { use, useEffect, useState } from "react";

import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { EmptyState } from "@/components/review/EmptyState";
import { FirstUseCallout } from "@/components/review/FirstUseCallout";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import { ToastHostProvider } from "@/components/review/ToastHost";
import {
  type ProjectCoverage,
  type QuestionItem,
} from "@/lib/api";
import { apiFetch } from "@/lib/fetcher";

import { QuestionsQueue } from "./QuestionsQueue";

export default function QuestionsPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = use(params);

  const [items, setItems] = useState<QuestionItem[] | null>(null);
  const [listError, setListError] = useState<unknown>(null);
  const [coverage, setCoverage] = useState<ProjectCoverage | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setListError(null);
    (async () => {
      try {
        const [list, cov] = await Promise.all([
          apiFetch<QuestionItem[]>(
            `/projects/${encodeURIComponent(name)}/questions?status=pending`,
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
        ) : loading || items === null ? (
          <div className="text-text-muted text-sm">Loading…</div>
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
