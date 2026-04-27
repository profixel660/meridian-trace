/**
 * Centralised PM-language prose for the setup wizard.
 *
 * Single source of truth — copyedits land here, layout stays in the
 * page components. Each constant is a React fragment factory so the
 * prose can include `<a>`, `<strong>`, etc. while remaining typed and
 * testable.
 *
 * Plain-English rules (locked memory: feedback_ux_discoverability.md):
 *  - No "API", "endpoint", "JSON", "venv", "config" in user-visible text.
 *  - Acceptable substitutes: "AI service", "your projects folder",
 *    "your first set of drawings or specs".
 *  - Every domain term ("deliverable", "slug", "project") gets a
 *    tooltip + glossary anchor on first use per page.
 */

import Link from "next/link";
import type { ReactNode } from "react";
import { Fragment, createElement as h } from "react";

/* ----------------------------- step 0 — welcome --------------------------- */

export const WELCOME_COPY = {
  title: "Welcome to Meridian",
  subtitle: "Let's get your first project up and running. About 10 minutes.",
  hero: () =>
    h(
      Fragment,
      null,
      h(
        "p",
        { className: "text-text-primary leading-relaxed" },
        "Meridian reads your project's drawings, specs, BODs, and scopes-of-works, and pulls out the ",
        h("strong", null, "deliverables"),
        " — the things someone has to do, supply, or hand over. You review what it found, fix the bits it got wrong, and out the other end you get a per-trade register you can hand to a tenderer.",
      ),
      h(
        "p",
        { className: "mt-3 text-text-muted" },
        "This setup runs once per machine. We'll connect Meridian to its AI service, name your first project, and import your first batch of documents.",
      ),
    ),
  steps: [
    {
      n: 1,
      title: "Connect your AI service",
      blurb: "Paste an Anthropic key so Meridian can read your drawings.",
      time: "≈ 2 min",
    },
    {
      n: 2,
      title: "Name your first project",
      blurb: "Give it a memorable name and pick where files live on disk.",
      time: "≈ 1 min",
    },
    {
      n: 3,
      title: "Import your first documents",
      blurb: "Pick the drawings or specs you want Meridian to learn from.",
      time: "≈ 5 min",
    },
    {
      n: 4,
      title: "Ready to review",
      blurb: "Open your project and start reviewing extracted deliverables.",
      time: "—",
    },
  ],
  alreadySetUp: {
    title: "You're already set up",
    body: "This machine has finished setup before. You can jump straight to your projects, or run setup again to add a new one.",
    primaryLabel: "Open my projects",
    secondaryLabel: "Run setup again",
  },
  readMoreHeader: "Read more before you begin",
  readMoreLinks: [
    {
      href: "/onboarding/why-frontier-ai",
      label: "Why Meridian needs a frontier AI service",
      blurb:
        "Why drawing-grade vision can't run on your laptop, in plain English.",
    },
    {
      href: "/onboarding/data-handling",
      label: "How your documents are handled",
      blurb:
        "What stays on this machine, what is sent to Anthropic, and what isn't.",
    },
    {
      href: "/onboarding/recommended-setup",
      label: "Recommended setup",
      blurb:
        "Defaults we suggest, plus alternatives if your organisation has policies.",
    },
  ],
};

/* ---------------------------- step 1 — api key --------------------------- */

export const API_KEY_COPY = {
  title: "Connect your AI service",
  why: () =>
    h(
      Fragment,
      null,
      h(
        "p",
        { className: "leading-relaxed" },
        "Meridian uses ",
        h("strong", null, "Anthropic's Claude"),
        " as its reasoning engine — the same family of models that power Claude Code. Drawings, specs, and BODs need a vision-capable model that doesn't run on a laptop.",
      ),
      h(
        "p",
        { className: "mt-3 leading-relaxed" },
        h("strong", null, "Your documents stay on this machine."),
        " Only the text Meridian needs to reason about gets sent to Claude during extraction, and Anthropic's commercial terms forbid using that text to train future models. ",
        h(
          Link,
          {
            href: "/onboarding/data-handling",
            className: "text-accent hover:underline",
          },
          "Read the full data-handling explanation →",
        ),
      ),
    ),
  inputLabel: "Anthropic API key",
  inputHelper: "Starts with sk-ant- (about 100 characters long).",
  whereToGet: "Where do I get this?",
  whereToGetUrl: "https://console.anthropic.com/settings/keys",
  prefixTooltip:
    "Anthropic prefixes every key with sk-ant-. If yours starts with sk- but not sk-ant-, it's a different vendor's key and won't work here.",
  validateLabel: "Validate & save",
  outcomes: {
    valid: {
      headline: "Key looks good.",
      body: "Saved to this machine's secure credential store. You can press Continue or hit Enter.",
    },
    invalid: {
      headline: "That key didn't work.",
      nextSteps: [
        "Check it starts with sk-ant- (not just sk-).",
        "Confirm it hasn't been deleted or rotated at console.anthropic.com.",
        "Make sure the Anthropic account has billing set up — keys without billing return 401.",
      ],
    },
    unable: {
      headline: "We couldn't reach Anthropic right now.",
      body: "Your key was saved. We'll try again on the first extraction — most of the time this is a transient network hiccup. You can continue.",
    },
  },
  privacyFooter: () =>
    h(
      Fragment,
      null,
      "We store this key in your operating system's secure credential store — Windows Credential Manager. It's never written to disk in plain text. ",
      h(
        Link,
        {
          href: "/onboarding/data-handling",
          className: "text-accent hover:underline",
        },
        "More on data handling →",
      ),
    ),
};

/* ------------------------- step 2 — first project ------------------------ */

export const FIRST_PROJECT_COPY = {
  title: "Name your first project",
  why: () =>
    h(
      Fragment,
      null,
      h(
        "p",
        { className: "leading-relaxed" },
        "Each ",
        h("strong", null, "project"),
        " is a separate workspace. Documents you import, deliverables Meridian extracts, and reviews you do all live inside the project — they don't bleed into other projects.",
      ),
      h(
        "p",
        { className: "mt-3 leading-relaxed" },
        "Pick something memorable and short — your project's slug shows up in folder names and the title bar. You can always create more projects later from the Projects screen.",
      ),
    ),
  fields: {
    name: {
      label: "Project name",
      placeholder: "e.g. SYD2 Shell CD",
      helper:
        "What you'll see in the title bar. Letters, numbers, spaces, ampersands — anything's fine.",
    },
    slug: {
      label: "Slug",
      helper:
        "A URL-safe version of your project name. We use it for folder names so you can find files on disk easily. Lowercase letters, numbers, and dashes only.",
      tooltip:
        "Slug = the dashed-lowercase version of the name. Example: SYD2 Shell CD → syd2-shell-cd. We pick one for you; edit if you'd rather.",
      invalid:
        "Slug must be lowercase letters, numbers and dashes only — no spaces.",
    },
    projectsDir: {
      label: "Projects folder",
      helper:
        "Where Meridian will store this project's database, logs, and exported files.",
      defaultHint:
        "Default: C:\\Users\\<you>\\Meridian\\projects. We'll create the folder if it doesn't exist.",
      onedriveWarning:
        "Avoid OneDrive-, Dropbox-, or iCloud-synced folders. File syncing while Meridian is writing the database can cause corruption — pick a local folder.",
      pickButton: "Choose folder…",
      browserModeNote:
        "Folder picking only works in the desktop app. In the browser preview, leave the default and we'll create the folder when the desktop app first runs.",
    },
  },
  createLabel: "Create project",
  outcomes: {
    conflict: {
      headline: "A project with that slug already exists.",
      body: "You can open the existing one or pick a different slug below.",
      openLabel: "Open the existing project",
      changeLabel: "Pick a different slug",
    },
    invalid: {
      headline: "Couldn't create the project.",
      bodyPrefix: "We hit an error from your operating system: ",
      tryAnother: "Try a different folder",
    },
  },
};

/* ------------------------ step 3 — first documents ----------------------- */

export const FIRST_DOCS_COPY = {
  title: "Import your first documents",
  why: () =>
    h(
      Fragment,
      null,
      h(
        "p",
        { className: "leading-relaxed" },
        "Meridian extracts ",
        h(
          Link,
          {
            href: "/glossary#deliverable",
            className: "text-accent hover:underline",
          },
          "deliverables",
        ),
        " — the things someone must do or hand over — from your project's drawings, specs, BODs, scopes-of-works, and similar documents.",
      ),
      h(
        "p",
        { className: "mt-3 leading-relaxed" },
        "The first import seeds the project's vocabulary (what trades, services, and equipment matter for THIS project). You can import more later. ",
        h("strong", null, "Mixed-format is fine"),
        " — PDFs, Word documents, even mixed scans-and-native — all are handled.",
      ),
    ),
  pickButton: "Choose files…",
  pickHelper:
    "Hold Ctrl (or Cmd on Mac) to pick multiple. PDF, DOC, and DOCX are supported.",
  emptyTitle: "No files yet",
  emptyBody:
    "Pick the drawings, specs, or scopes-of-works you want Meridian to learn from. You can add more later from the Sources screen.",
  browserModeBlock: {
    title: "Document import requires the desktop app",
    body: "Browsers can't pass real local file paths to Meridian — only the bundled desktop app can. You can skip this step now and import documents from the Sources screen once you open Meridian on the desktop.",
  },
  removeLabel: "Remove",
  importLabel: (n: number) =>
    n === 1 ? "Import 1 document" : `Import ${n} documents`,
  skipLabel: "Skip for now",
  skipConfirm: {
    title: "Skip document import?",
    body: "Without documents, Meridian can't extract any deliverables yet. You can always come back from the Sources screen on your project dashboard.",
    confirm: "Yes, skip for now",
    cancel: "Keep picking files",
  },
  progress: {
    queued: "Queued",
    running: "Importing…",
    ok: "Done",
    error: "Failed",
  },
  outcomes: {
    partial: {
      headline: "Some files imported, some didn't.",
      body: "Failed files are listed below. You can retry each one or skip ahead and deal with the rest from the Sources screen.",
      retryLabel: "Retry",
    },
    failed: {
      headline: "No files were imported.",
      body: "Check the log path below and try again. Most failures are unreadable PDFs or files locked by another program.",
    },
  },
};

/* ----------------------------- step 4 — ready ---------------------------- */

export const READY_COPY = {
  title: "You're ready",
  hero: (opts: { docCount: number; skipped: boolean }): ReactNode =>
    h(
      "p",
      { className: "text-text-primary leading-relaxed" },
      "Setup complete. Your first project is created",
      opts.skipped
        ? " — you skipped document import for now. Come back from the Sources screen when you're ready."
        : opts.docCount === 0
          ? " and is ready for documents."
          : opts.docCount === 1
            ? " and your first document is being processed in the background."
            : ` and your first ${opts.docCount} documents are being processed in the background.`,
    ),
  summaryLabels: {
    apiKey: "AI service",
    project: "Project",
    docs: "Documents",
    folder: "On disk",
  },
  ctas: {
    open: "Open my project",
    tour: "Take a tour",
    glossary: "Read the glossary",
  },
  whatsNextTitle: "What happens next",
  whatsNext: [
    "Meridian extracts deliverables in the background — for a small batch of documents this usually finishes within a few minutes.",
    "Flagged rows show up in your project's review queues (Quarantine, Audit, Questions, Conflicts, Taxonomy). Work through them at your own pace.",
    "When you're ready to send a per-trade register out, the dashboard's Hand-off & integrity section (Tender, Evidence, Cross-references) is where you'll go.",
  ],
};

/* ---------------------------- shared chrome ----------------------------- */

export const SHELL_COPY = {
  brand: "Meridian setup",
  closeLabel: "Close setup",
  closeConfirm: {
    title: "Quit setup?",
    body: "We'll save what you've done so far. Next time you open Meridian, you'll resume from this step.",
    confirm: "Quit setup",
    cancel: "Keep going",
  },
  back: "← Back",
  continue: "Continue →",
  shortcuts: [
    { keys: "Enter", description: "Activate the primary button" },
    { keys: "Esc", description: "Close setup (with confirmation)" },
    { keys: "?", description: "Show / hide this shortcut sheet" },
    { keys: "Tab", description: "Move between fields" },
  ],
};
