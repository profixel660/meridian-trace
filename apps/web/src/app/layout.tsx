import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

import { AuthIndicator } from "@/components/AuthIndicator";

export const metadata: Metadata = {
  title: "Meridian",
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
              Meridian
            </Link>
            <nav className="flex items-center gap-6 text-sm text-text-muted">
              <Link href="/" className="hover:text-text-primary">
                Projects
              </Link>
              <Link href="/onboarding" className="hover:text-text-primary">
                Onboarding
              </Link>
              <Link
                href="/help/data-and-ai"
                className="hover:text-text-primary"
              >
                Help
              </Link>
              <Link href="/glossary" className="hover:text-text-primary">
                Glossary
              </Link>
              <Link href="/health" className="hover:text-text-primary">
                Health
              </Link>
              {/* Round-12 §3.2: TOTP bearer indicator + logout. Reads
                  state from localStorage on mount; renders a "Sign in"
                  link when no token is stored. */}
              <AuthIndicator />
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
