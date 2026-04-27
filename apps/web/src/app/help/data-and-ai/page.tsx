import { DataHandlingBody } from "@/components/DataHandlingBody";

// Hardcoded for now; future versions will manage versioning per CONTEXT.md §20
// ("disclaimer text versioned in code").
const LAST_UPDATED = "2026-04-27";

export default function HelpDataAndAiPage() {
  return (
    <div className="space-y-8">
      <header className="max-w-[70ch] space-y-2">
        <p className="text-xs uppercase tracking-wide text-text-muted">
          Help
        </p>
        <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
          Data &amp; AI
        </h1>
        <p className="text-sm text-text-muted">
          A reference page covering where your project documents go, who
          processes them, and the options available for organisations with
          stricter data requirements.
        </p>
      </header>

      <section className="prose-onboarding max-w-[70ch] space-y-6 text-sm leading-relaxed text-text-primary">
        <DataHandlingBody />
      </section>

      <footer className="max-w-[70ch] border-t border-border pt-4 text-xs text-text-muted">
        Last updated: {LAST_UPDATED}
      </footer>
    </div>
  );
}
