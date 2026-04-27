import Link from "next/link";

/**
 * Shared body content for the "How your data is handled" page.
 *
 * Rendered by both /onboarding/data-handling and /help/data-and-ai. Keep
 * disclaimer text here (not duplicated) — per CONTEXT.md §20 the language
 * is versioned in code so it can soften at v1.0.
 *
 * Extracted from app/onboarding/data-handling/page.tsx because Next.js 15
 * page modules may only export the route's allowed symbols (default,
 * metadata, generateMetadata, etc.) — additional named exports trip the
 * generated `.next/types/.../page.ts` checkFields constraint.
 */
export function DataHandlingBody() {
  return (
    <>
      <section className="space-y-3">
        <h2 className="text-base font-semibold text-text-primary">
          Where document content goes
        </h2>
        <p>
          When Meridian processes your project documents, the content of those
          documents is sent to the AI provider you have chosen. By default
          that provider is <strong>Anthropic</strong>. You can also configure{" "}
          <strong>OpenAI</strong>, or run entirely against a local model via{" "}
          <strong>Ollama</strong> for air-gapped projects.
        </p>
        <p>
          Nothing is sent to Undivided Systems beyond what is needed to
          activate your license. Your documents do not pass through our
          servers.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-base font-semibold text-text-primary">
          Provider policies apply
        </h2>
        <p>
          Once content is sent to a provider, that provider&apos;s privacy and
          data-retention terms govern it. If your organisation has data
          obligations, please review the relevant policy before you begin:
        </p>
        <ul className="list-disc space-y-1 pl-6 text-text-primary">
          <li>
            <a
              href="https://www.anthropic.com/legal/privacy"
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent hover:underline"
            >
              Anthropic privacy policy
            </a>
          </li>
          <li>
            <a
              href="https://openai.com/policies/privacy-policy"
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent hover:underline"
            >
              OpenAI privacy policy
            </a>
          </li>
        </ul>
      </section>

      <section className="space-y-3">
        <h2 className="text-base font-semibold text-text-primary">
          If your IT team needs to know more
        </h2>
        <p>
          We are preparing a one-page summary you can hand to IT or compliance
          covering data flow, providers, retention, and air-gap options.
        </p>
        <p>
          <a
            href="#"
            aria-disabled="true"
            className="inline-flex items-center gap-2 rounded-full border border-border px-4 py-2 text-sm text-text-muted"
          >
            IT &amp; compliance summary (PDF) — Coming soon
          </a>
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-base font-semibold text-text-primary">
          Air-gap and data-sovereign projects
        </h2>
        <p>
          For projects where document content cannot leave your network,
          Meridian supports local-only routing through{" "}
          <Link
            href="/onboarding/recommended-setup"
            className="text-accent hover:underline"
          >
            Ollama
          </Link>
          . In that mode, no document content is sent to any external
          provider. Capability and speed depend on the local model and the
          hardware it runs on.
        </p>
      </section>
    </>
  );
}
