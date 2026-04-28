import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

import { AuthIndicator } from "@/components/AuthIndicator";

export const metadata: Metadata = {
  title: "Meridian - Trace",
  description: "Per-trade deliverables register — operator UI.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-background text-text-primary antialiased">
        <header className="border-b border-border bg-surface">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
            <Link
              href="/"
              className="text-lg font-semibold tracking-tight text-text-primary"
            >
              Meridian - Trace
            </Link>
            {/*
              Alpha-16: stripped header nav to focus on the core
              deliverables-extraction loop. Hidden but accessible by URL:
                /onboarding, /glossary, /help/data-and-ai, /health.
              The TOTP <AuthIndicator> stays — it gracefully renders
              "Sign in" when no token is stored AND alpha-15 disabled
              the redirect, so it's harmless. Restoring the nav links
              is reverting this single edit.
            */}
            <nav className="flex items-center gap-6 text-sm text-text-muted">
              <Link href="/" className="hover:text-text-primary">
                Projects
              </Link>
              <AuthIndicator />
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
