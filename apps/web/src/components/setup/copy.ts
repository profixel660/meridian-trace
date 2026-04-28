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
 *  - Acceptable substitutes: "AI service", "your project folder",
 *    "your project's PDFs and drawings".
 *  - Every domain term ("deliverable", "slug", "project") gets a
 *    tooltip + glossary anchor on first use per page.
 *  - Alpha-2 vernacular pass: avoid "source document" (use "document" or
 *    "file"); avoid "ingest" (use "import" or "add"); avoid "source
 *    corpus" (use "your project folder"); avoid "extraction" in user
 *    prose (use "find deliverables in your documents"); avoid
 *    "bootstrap" (use "quick setup" or "first scan"); for "Anthropic API
 *    key", lead with "Claude AI key (from Anthropic)".
 */

import Link from "next/link";
import type { ReactNode } from "react";
import { Fragment, createElement as h } from "react";

/* ----------------------------- step 0 — welcome --------------------------- */

export const WELCOME_COPY = {
  title: "Welcome to Meridian - Trace",
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
        "This setup runs once per machine. We'll connect Meridian to its Claude AI service, point it at your project folder, and confirm the project name.",
      ),
    ),
  steps: [
    {
      n: 1,
      title: "Connect your Claude AI key",
      blurb:
        "Paste a Claude AI key (from Anthropic) so Meridian can read your drawings.",
      time: "≈ 2 min",
    },
    {
      n: 2,
      title: "Pick your project folder",
      blurb:
        "Choose the folder that holds your project's PDFs, drawings, specs, and emails — we'll find the files in it.",
      time: "≈ 2 min",
    },
    {
      n: 3,
      title: "Confirm your project name",
      blurb:
        "We'll suggest a name from your folder; tweak it if you'd like.",
      time: "≈ 1 min",
    },
    {
      n: 4,
      title: "Ready to review",
      blurb:
        "Open your project and start reviewing the deliverables Meridian found.",
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
  title: "Connect your Claude AI key",
  why: () =>
    h(
      Fragment,
      null,
      h(
        "p",
        { className: "leading-relaxed" },
        "Meridian uses ",
        h("strong", null, "Claude (from Anthropic)"),
        " as its reasoning engine — the same family of models that power Claude Code. Drawings, specs, and BODs need a vision-capable model that doesn't run on a laptop.",
      ),
      h(
        "p",
        { className: "mt-3 leading-relaxed" },
        h("strong", null, "Your documents stay on this machine."),
        " Only the text Meridian needs to reason about gets sent to Claude while it's reading your documents, and Anthropic's commercial terms forbid using that text to train future models. ",
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
  inputLabel: "Claude AI key (from Anthropic)",
  inputLabelTechnical: "Anthropic API key",
  glossaryTooltip:
    "Claude is the AI engine Meridian uses to read your documents. You sign up at anthropic.com — we'll show you how if you don't have one yet.",
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
      body: "Your key was saved. We'll try again the first time Meridian reads a document — most of the time this is a transient network hiccup. You can continue.",
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
        " is a separate workspace. Documents you import, deliverables Meridian finds, and reviews you do all live inside the project — they don't bleed into other projects.",
      ),
      h(
        "p",
        { className: "mt-3 leading-relaxed" },
        "Pick something memorable and short — your project's slug shows up in folder names and the title bar. You can always create more projects later from the Projects screen.",
      ),
    ),
  /** Helper line shown under the project-name input when an auto-suggestion was applied. */
  autoNameHelper:
    "We suggested this from your folder name — change if you'd like.",
  /** Inline hint shown near the name input when the suggested name was bumped because the slug already exists. */
  autoNameBumpedHint: (original: string, suggested: string) =>
    `A project with the name “${original}” already exists. We suggested “${suggested}” — feel free to change it.`,
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
        "We've pre-filled this with Meridian's recommended location for your machine. Edit it if you'd like the project to live somewhere else; we'll create the folder if it doesn't exist.",
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
  title: "Where are your project documents?",
  subtitle:
    "Pick the folder that has your project's PDFs, drawings, specs, and emails — we'll find them all.",
  why: () =>
    h(
      Fragment,
      null,
      h(
        "p",
        { className: "leading-relaxed" },
        "Meridian finds ",
        h(
          Link,
          {
            href: "/glossary#deliverable",
            className: "text-accent hover:underline",
          },
          "deliverables",
        ),
        " — the things someone must do or hand over — by reading your project's drawings, specs, BODs, scopes-of-works, and similar files.",
      ),
      h(
        "p",
        { className: "mt-3 leading-relaxed" },
        "The first import seeds the project's vocabulary (what trades, services, and equipment matter for THIS project). You can add more later. ",
        h("strong", null, "Mixed formats are fine"),
        " — PDFs, Word, Excel, AutoCAD drawings, and emails are all handled.",
      ),
    ),
  /** Big primary CTA — folder picker. */
  pickFolderButton: "📁 Choose project folder",
  pickFolderTooltip:
    "Pick the folder that has your project's PDFs, drawings, specs, and emails. We'll find them and import them all.",
  /** First-use callout body. */
  firstUseTitle: "What goes in here?",
  firstUseBody:
    "Your construction project folder — anything you'd normally read to understand what gets built. PDFs of specs, drawings, BOD spreadsheets, emails, etc. We'll scan the folder, show you what we found, and let you confirm before we import anything.",
  /** Browser fallback prompt. */
  browserPathPromptLabel: "Now paste the full path to that folder",
  browserPathPromptPlaceholder: "e.g. C:\\Projects\\MyProject",
  browserPathPromptHelper:
    "Browsers don't tell us the full path of folders you pick, for security reasons — only the folder's name. So we need you to paste the full Windows path here so Meridian can scan it. (Tip: in Windows Explorer, click the address bar to copy the path.) The desktop app picks folders directly without this step.",
  /** Empty manifest state. */
  emptyManifestTitle: "We didn't find any importable files in that folder",
  emptyManifestBody:
    "Supported types are PDF, Word (.docx), Excel (.xlsx), AutoCAD drawings (.dwg), and emails (.eml / .msg). Pick a different folder, or check the folder you picked is the right one.",
  emptyManifestRetryLabel: "Pick a different folder",
  /** Manifest preview — summary line builder. */
  manifestSummary: (parts: string[], folderName: string) => {
    const list =
      parts.length === 0
        ? "no importable files"
        : parts.length === 1
          ? parts[0]
          : parts.length === 2
            ? `${parts[0]} and ${parts[1]}`
            : `${parts.slice(0, -1).join(", ")}, and ${parts[parts.length - 1]}`;
    return `Found ${list} in '${folderName}' — import them?`;
  },
  manifestShowFilesLabel: "Show files",
  manifestSkippedHeader: "Files we'll skip",
  manifestSkippedHelper:
    "These files were found but won't be imported — usually because the type isn't supported or the file is locked.",
  /** Confirm-import button + dialog. */
  confirmImportLabel: "Import these documents",
  confirmImportDialog: {
    title: "Import these documents?",
    body: "We'll add these to a new Meridian project. You can review and remove items later from the Sources screen — nothing is permanent.",
    confirm: "Yes, import them",
    cancel: "Not yet",
  },
  /** Progress card during import. */
  progressHeader: (imported: number, total: number) =>
    `Imported ${imported} of ${total}`,
  progressCurrentFile: (filename: string) =>
    `Currently importing ${filename}`,
  progressBackgroundNote:
    "You can leave this screen — the import keeps running in the background. Status will be on your project dashboard.",
  /** Skip button + dialog. */
  skipLabel: "Skip for now, I'll add documents later",
  skipConfirm: {
    title: "Skip the document scan?",
    body: "Without documents, Meridian can't find any deliverables yet. You can always come back from the Sources screen on your project dashboard.",
    confirm: "Yes, skip for now",
    cancel: "Keep picking a folder",
  },
  /** Three-outcome surfaces. */
  outcomes: {
    cancelled: "Folder pick cancelled — try again when you're ready.",
    invalidPath: {
      headline: "That path doesn't look like a folder.",
      body:
        "Did you mean to type the parent directory? Folder paths usually look like C:\\Projects\\MyProject — no file name on the end. Try again, or pick a different folder.",
    },
    networkDown: {
      headline: "We couldn't reach Meridian to scan that folder.",
      body:
        "Most often this is a transient network hiccup. Try again in a moment, or skip for now and add documents later from the Sources screen.",
      skipNote:
        "Skipping is safe — you can always import documents later. Just confirm you'd like to move on without them.",
    },
    partial: {
      headline: "Some files imported, some didn't.",
      body:
        "Failed files are grouped by reason below. Each group has a one-line fix you can take to recover those files; the rest of your import is already done.",
      /**
       * Alpha-13: dedup-aware summary line. Replaces alpha-11's
       * "{imported} of {total} files imported. {failed_count} failed."
       * which hid the dedup count entirely — an SME re-importing the
       * same folder saw "0 of 347 imported. 18 failed." and had no
       * idea their 329 files were already safe in the project.
       *
       * Three rendering branches based on which counts are non-zero:
       *   imported>0 deduped>0 → "X added, Y already in the project, Z failed."
       *   imported=0 deduped>0 → "All N documents were already in the project. Z failed."
       *   imported>0 deduped=0 → "X of total imported. Z failed." (alpha-11 form)
       */
      summary: (imported: number, deduped: number, failed: number, total: number) => {
        const fileWord = (n: number) => (n === 1 ? "file" : "files");
        const docWord = (n: number) => (n === 1 ? "document" : "documents");
        const verbWas = (n: number) => (n === 1 ? "was" : "were");
        if (imported === 0 && deduped > 0) {
          return (
            `All ${deduped} ${docWord(deduped)} ${verbWas(deduped)} already in the project (deduplicated by content). ` +
            `${failed} ${fileWord(failed)} failed.`
          );
        }
        if (imported > 0 && deduped > 0) {
          return (
            `${imported} ${docWord(imported)} added; ` +
            `${deduped} ${verbWas(deduped)} already in the project; ` +
            `${failed} ${fileWord(failed)} failed.`
          );
        }
        // imported>0 deduped=0 — alpha-11 form, kept for the "fresh import" case.
        return `${imported} of ${total} ${fileWord(total)} imported. ${failed} failed.`;
      },
    },
    failed: {
      headline: "No files were imported.",
      body:
        "Check the folder path is correct and try again. Most failures are unreadable PDFs or files locked by another program.",
    },
    succeeded: {
      headline: "Import complete.",
      body: (imported: number, deduped: number) => {
        const dedupeNote =
          deduped > 0
            ? ` (${deduped} ${deduped === 1 ? "was" : "were"} already in the project — skipped).`
            : ".";
        return `${imported} ${imported === 1 ? "document" : "documents"} added${dedupeNote} Press Continue to confirm your project name.`;
      },
    },
  },
  /**
   * Pre-import callout when the scan finds DWG files but ODA File
   * Converter isn't installed on this machine. Surfaced BEFORE the
   * user presses Import so the partial-success path is informed.
   * Alpha-11 finding: a real-world SME corpus had 18 of 347 files
   * silently skipped because ODA isn't bundled with the installer.
   */
  odaMissingCallout: {
    headline: (dwgCount: number) =>
      `${dwgCount} AutoCAD ${dwgCount === 1 ? "drawing" : "drawings"} won't import — ODA File Converter is not installed.`,
    body:
      "Meridian reads .dwg files by routing them through ODA File Converter, a free third-party tool that turns drawings into PDFs. You can still import the rest of your folder — drawings will be skipped and you can add them later after installing the converter.",
    installLinkLabel: "Install ODA File Converter",
    skipNote:
      "Or import without the drawings — your specs, BODs, and emails will still be processed.",
  },
  odaMissingPostImport: {
    headline: (dwgCount: number) =>
      `${dwgCount} AutoCAD ${dwgCount === 1 ? "drawing" : "drawings"} ${dwgCount === 1 ? "was" : "were"} skipped — install ODA File Converter to add ${dwgCount === 1 ? "it" : "them"}.`,
    body:
      "Once ODA is installed, return to this folder from your project's Sources screen and re-scan. Already-imported files won't duplicate — Meridian de-duplicates by content hash.",
  },
  /**
   * Per-error-code remediation copy. Backend tags every per-file
   * failure with one of these codes; the partial-success panel renders
   * one row per group with the matching remediation. Adding a new
   * code is scoped: it must also be added to
   * `lib/setupClient.ts::ImportErrorCode` and
   * `meridian.wizard.models.ImportErrorCode` (the backend source of truth).
   */
  errorGroupRemediation: {
    oda_missing:
      "Install ODA File Converter from opendesign.com and re-scan this folder.",
    pdf_unreadable:
      "Open the PDF to confirm it isn't corrupt. If it's a scan with no text, run OCR before re-importing.",
    file_locked:
      "Close the file in Word / Excel / AutoCAD / your file manager's preview pane and re-scan.",
    permission_denied:
      "Grant read access to this file (right-click → Properties → Security) or move it out of a system-protected folder.",
    file_missing:
      "The file moved or was deleted between scan and import. Re-scan and try again.",
    unsupported_mime:
      "Meridian doesn't yet handle this file type. Convert to PDF, .docx, or .xlsx and re-scan.",
    worker_aborted:
      "The import worker terminated unexpectedly. Capture backend.log and report the issue. Re-running is safe — already-imported files de-duplicate by content hash.",
    unknown: "",
  } as const,
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
    "Meridian finds deliverables in your documents in the background — for a small batch of documents this usually finishes within a few minutes.",
    "Flagged rows show up in your project's review queues (Quarantine, Audit, Questions, Conflicts, Taxonomy). Work through them at your own pace.",
    "When you're ready to send a per-trade register out, the dashboard's Hand-off & integrity section (Tender, Evidence, Cross-references) is where you'll go.",
  ],
};

/* ---------------------------- shared chrome ----------------------------- */

export const SHELL_COPY = {
  brand: "Meridian - Trace setup",
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
