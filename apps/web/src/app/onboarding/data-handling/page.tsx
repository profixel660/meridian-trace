import { OnboardingShell } from "@/components/OnboardingShell";
import { DataHandlingBody } from "@/components/DataHandlingBody";

export default function DataHandlingPage() {
  return (
    <OnboardingShell
      step={2}
      title="How your data is handled"
      subtitle="Where your project documents go, what stays put, and whose policies apply."
      backHref="/onboarding/why-frontier-ai"
      nextHref="/onboarding/recommended-setup"
    >
      <DataHandlingBody />
    </OnboardingShell>
  );
}
