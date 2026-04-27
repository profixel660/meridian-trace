import { OnboardingShell } from "@/components/OnboardingShell";

export default function WhyFrontierAiPage() {
  return (
    <OnboardingShell
      step={1}
      title="Why frontier AI is required"
      subtitle="A short note on why Meridian leans on cloud-hosted AI models rather than something you can run locally."
      backHref="/onboarding"
      nextHref="/onboarding/data-handling"
    >
      <p>
        Meridian works across the kinds of documents a project manager actually
        receives: drawings (often scanned PDFs), specifications hundreds of
        pages long, scope-of-engagement schedules, and the cross-references
        between them. Reading a drawing properly means seeing it — recognising
        a title block, understanding what a callout points to, identifying
        where one document refers to another.
      </p>

      <p>
        That is a workload that smaller AI models simply do not handle well
        today. The models that do handle it well are large enough that they
        cannot run on a normal laptop or desktop. They live on hosted cloud
        services and are accessed over the internet.
      </p>

      <p>
        The good news: you do not need to understand any of the model
        selection. Meridian ships with sensible defaults baked in, chosen and
        tested for the kinds of documents construction PMs work with day to
        day. Most users never need to change them.
      </p>

      <h2 className="text-base font-semibold text-text-primary">
        Advanced users: per-purpose routing
      </h2>
      <p>
        If your organisation has a preferred AI provider, or if you want to
        route different parts of the pipeline (extraction, classification,
        question-answering) to different providers, that configuration lives
        in an Advanced panel and does not intrude on the default flow.
      </p>
    </OnboardingShell>
  );
}
