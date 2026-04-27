import Link from "next/link";

interface OnboardingCard {
  step: number;
  title: string;
  preview: string;
  href: string;
}

const CARDS: OnboardingCard[] = [
  {
    step: 1,
    title: "Why frontier AI is required",
    preview:
      "Drawings and cross-document reasoning need vision capability that cannot run on a PM laptop. Here is what that means in practice.",
    href: "/onboarding/why-frontier-ai",
  },
  {
    step: 2,
    title: "How your data is handled",
    preview:
      "An honest summary of where your documents go, what stays on your machine, and which provider policies apply.",
    href: "/onboarding/data-handling",
  },
  {
    step: 3,
    title: "Recommended setup",
    preview:
      "Anthropic is the default. Alternatives are supported if your organisation requires them, including air-gapped deployments.",
    href: "/onboarding/recommended-setup",
  },
];

export default function OnboardingLandingPage() {
  return (
    <div className="space-y-8">
      <header className="max-w-[70ch] space-y-2">
        <p className="text-xs uppercase tracking-wide text-text-muted">
          Welcome to Meridian - Trace
        </p>
        <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
          A 3-minute setup before you start
        </h1>
        <p className="text-sm text-text-muted">
          Three short screens covering what Meridian uses AI for, how your
          project documents are handled, and the setup we recommend.
        </p>
      </header>

      <ol className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {CARDS.map((card) => (
          <li key={card.step}>
            <Link
              href={card.href}
              className="group flex h-full flex-col rounded-lg border border-border bg-surface p-6 transition hover:border-accent"
            >
              <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-accent text-lg font-semibold text-[#0B0F14]">
                {card.step}
              </div>
              <h2 className="text-base font-semibold text-text-primary">
                {card.title}
              </h2>
              <p className="mt-2 flex-1 text-sm text-text-muted">
                {card.preview}
              </p>
              <span className="mt-4 text-sm font-medium text-accent group-hover:underline">
                Continue →
              </span>
            </Link>
          </li>
        ))}
      </ol>

      <div className="pt-4">
        <Link
          href="/"
          className="text-sm text-text-muted underline-offset-4 hover:text-text-primary hover:underline"
        >
          Skip onboarding
        </Link>
      </div>
    </div>
  );
}
