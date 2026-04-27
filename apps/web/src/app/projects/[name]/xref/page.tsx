import { ApiErrorPanel } from "@/components/review/ApiErrorPanel";
import { FirstUseCallout } from "@/components/review/FirstUseCallout";
import { ReviewLayout } from "@/components/review/ReviewLayout";
import { ToastHostProvider } from "@/components/review/ToastHost";
import { meridianApi, type ProjectCoverage } from "@/lib/api";
import {
  xrefApi,
  type XrefReportResponse,
} from "@/lib/apiClient/xref";

import { XrefPanel } from "./XrefPanel";

export const dynamic = "force-dynamic";

export default async function XrefPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = await params;

  let report: XrefReportResponse | null = null;
  let listError: unknown = null;
  let coverage: ProjectCoverage | null = null;

  try {
    [report, coverage] = await Promise.all([
      xrefApi.report(name),
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
        title="Cross-reference sweep"
        subtitle="Exhaustively scan every ingested document for textual references to other project sources."
        counts={counts}
      >
        <FirstUseCallout
          routeKey={`projects/${name}/xref`}
          title="What is a cross-reference sweep?"
        >
          <p>
            The sweep walks every authoritative source in the project and
            extracts <em>cross-reference anchors</em> — section numbers,
            standards, drawing IDs, equipment tags, owner IDs, spec
            references — then resolves each anchor to another project
            source where possible.
          </p>
          <p>
            Outcomes are three-way: <strong>confirmed</strong> (anchor
            resolved to a known project source),{" "}
            <strong>borderline</strong> (ambiguous — added to the SME
            review queue when persisted), and{" "}
            <strong>external_reference</strong> (legitimately points
            outside the project, e.g. to a published standard).{" "}
            <strong>Rejected</strong> findings are noisy false positives
            the deterministic pass discards.
          </p>
        </FirstUseCallout>

        {listError ? (
          <ApiErrorPanel error={listError} />
        ) : (
          <XrefPanel projectName={name} report={report} />
        )}
      </ReviewLayout>
    </ToastHostProvider>
  );
}
