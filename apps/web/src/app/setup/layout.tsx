/**
 * Server-component segment layout for the `/setup/*` wizard.
 *
 * Intentionally minimal — the rich shell (header, step indicator, footer
 * with prev/next, keyboard nav) lives in the client-side `SetupShell`
 * component which each page renders. This layout exists to:
 *
 *  - Give the wizard a wider/centered prose column than the default
 *    review-app layout, while keeping the global header (Projects /
 *    Onboarding / Help / Glossary / Health / AuthIndicator) intact so
 *    the user never feels trapped on a wizard page with no escape.
 *  - Provide a hook for any future segment-level metadata.
 *
 * No `generateStaticParams` is needed — `/setup` and its four children
 * are concrete (no dynamic segments). Add it only if a future static-
 * export build complains.
 */

import type { ReactNode } from "react";

export default function SetupSegmentLayout({
  children,
}: {
  children: ReactNode;
}) {
  return <div className="mx-auto w-full max-w-3xl">{children}</div>;
}
