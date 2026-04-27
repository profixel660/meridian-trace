import { OnboardingShell } from "@/components/OnboardingShell";

export default function RecommendedSetupPage() {
  return (
    <OnboardingShell
      step={3}
      title="Recommended setup"
      subtitle="What we suggest by default, and the alternatives if your organisation needs them."
      backHref="/onboarding/data-handling"
      nextHref="/"
      nextLabel="Get started →"
    >
      <div className="rounded-lg border border-accent/40 bg-accent/10 p-4 text-text-primary">
        <p className="text-sm">
          <strong>The heavy AI work happens in the cloud, not on your laptop.</strong>{" "}
          That means modest hardware is fine; what you mostly need is a stable
          internet connection.
        </p>
      </div>

      <section className="space-y-3">
        <h2 className="text-base font-semibold text-text-primary">
          Default: Anthropic API key
        </h2>
        <p>
          We recommend running Meridian against Anthropic. As of this release
          it offers the best vision capability for the kinds of drawings and
          marked-up PDFs that show up in construction projects, a current
          service-level agreement, and it is the provider against which
          Meridian&apos;s default prompts have been tested.
        </p>
        <p>
          You will be asked for an Anthropic API key during setup. The key is
          stored locally on your machine.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-base font-semibold text-text-primary">
          If your organisation requires a different provider
        </h2>
        <p>
          OpenAI is supported as a bring-your-own-key alternative — paste the
          key in setup and Meridian will route through OpenAI instead.
        </p>
        <p className="text-text-muted">
          Gemini, Azure OpenAI, and AWS Bedrock are deferred to a later
          release. Let us know if your organisation needs one of these and it
          will help us prioritise.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-base font-semibold text-text-primary">
          If your organisation is air-gapped
        </h2>
        <p>
          Meridian supports local-only routing via{" "}
          <strong>Ollama</strong> using open models such as Llama 3.3 70B or
          Qwen 2.5 14B. In this mode no document content leaves your network.
          Quality and speed depend on the model and the machine it runs on.
        </p>
        <p>
          <a
            href="#"
            aria-disabled="true"
            className="inline-flex items-center gap-2 rounded-full border border-border px-4 py-2 text-sm text-text-muted"
          >
            Air-gap setup guide — Coming soon
          </a>
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-base font-semibold text-text-primary">
          Recommended hardware
        </h2>
        <ul className="list-disc space-y-1 pl-6">
          <li>Windows 10 / 11, or macOS 12 or later</li>
          <li>8 GB RAM (16 GB is comfortable)</li>
          <li>5–10 GB free disk space</li>
          <li>A stable internet connection</li>
        </ul>
        <p className="text-text-muted">
          Air-gap deployments using local models have higher hardware
          requirements; see the air-gap setup guide for details.
        </p>
      </section>
    </OnboardingShell>
  );
}
