import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { FirstUseCallout } from "@/components/review/FirstUseCallout";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import { ToastHostProvider } from "@/components/review/ToastHost";
import { meridianApi, type ProjectCoverage } from "@/lib/api";
import {
  evidenceApi,
  type EvidencePackListItem,
} from "@/lib/apiClient/evidence";

import { EvidencePanel } from "./EvidencePanel";

export const dynamic = "force-dynamic";

export default async function EvidencePage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = await params;

  let packs: EvidencePackListItem[] = [];
  let listError: unknown = null;
  let coverage: ProjectCoverage | null = null;

  try {
    [packs, coverage] = await Promise.all([
      evidenceApi.list(name),
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
        title="Legal Evidence Pack"
        subtitle="Assemble a defensible audit-trail bundle suitable for hand-off to construction lawyers, claims teams, or opposing counsel."
        counts={counts}
      >
        <FirstUseCallout
          routeKey={`projects/${name}/evidence`}
          title="What is the Legal Evidence Pack?"
        >
          <p>
            The pack is a single zip containing the deliverables register,
            every LLM call (prompt + response + cost + model), every source
            document, full provenance links, schema notes, and a
            <code> cover.md</code> describing the contents. It&apos;s
            designed for hand-off in a dispute: a third-party reviewer can
            unzip it and reproduce every claim Meridian has made about the
            project.
          </p>
          <p>
            <strong>What it proves:</strong> that the deliverable register
            was assembled from these specific sources, with these specific
            LLM calls, on this date.{" "}
            <strong>What it does not prove:</strong> the underlying source
            documents are authoritative, or that the human reviewer&apos;s
            decisions were correct.{" "}
            <a
              href="/glossary#legal-evidence-pack"
              className="text-accent hover:underline"
            >
              Full definition →
            </a>
          </p>
        </FirstUseCallout>

        {listError ? (
          <ApiErrorPanel error={listError} />
        ) : (
          <EvidencePanel projectName={name} packs={packs} />
        )}
      </ReviewLayout>
    </ToastHostProvider>
  );
}
