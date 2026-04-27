"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import { Tooltip } from "@/components/review/Tooltip";
import { SetupShell } from "@/components/setup/SetupShell";
import { FIRST_PROJECT_COPY } from "@/components/setup/copy";
import {
  isValidSlug,
  setupApi,
  suggestSlug,
  type CreateProjectResponse,
} from "@/lib/setupClient";
import { isInTauri, pickFolder } from "@/lib/tauri";

type Outcome =
  | { kind: "idle" }
  | { kind: "creating" }
  | { kind: "created"; slug: string }
  | { kind: "conflict"; slug: string; message: string }
  | { kind: "invalid"; message: string; osError?: string | null }
  | { kind: "error"; message: string };

/**
 * Hardcoded fallback used only when `/setup/defaults` is unreachable
 * (the most common reason being that Stream A hasn't wired it yet).
 * Matches the backend's own _meridian_home() default for installed
 * Windows hosts; PMs without an unusual `MERIDIAN_HOME` override see
 * this same path resolved server-side.
 */
function fallbackProjectsDir(): string {
  if (typeof navigator === "undefined") return "~/Meridian/projects";
  const ua = `${navigator.platform || ""} ${navigator.userAgent || ""}`;
  if (/Win/i.test(ua)) return "C:\\Meridian\\projects";
  return "~/Meridian/projects";
}

interface AutoName {
  /** The suggested name as the backend returned it (already de-duplicated). */
  suggested: string;
  /** True if the bumped suffix made it different from the raw folder name. */
  wasBumped: boolean;
  /** The original (un-bumped) folder name, surfaced in the bumped headline. */
  originalFolderName: string;
}

/**
 * Placeholder shape shown only as the input's `placeholder` HTML
 * attribute (i.e. when the resolved default fails to load AND the user
 * has cleared the input). This is HELP TEXT not a default VALUE — the
 * `<you>` token must never reach the backend, which would 422 on the
 * angle brackets.
 */
const DIR_PLACEHOLDER_HINT = "e.g. C:\\Meridian\\projects";

/**
 * Step 3 (alpha-2 reframe) — Confirm your project name.
 *
 * If the user came through `/setup/first-documents` and a folder path is
 * stashed in sessionStorage, we ping `/setup/projects/suggest-name` and
 * pre-fill the name input with the response. If the suggested name was
 * bumped (`shell-c-d` → `shell-c-d-2` because a project already uses
 * `shell-c-d`), we surface that to the user in plain English.
 *
 * If the user navigated here directly (no folder context), we keep the
 * original alpha-1 behaviour — empty inputs, manual slug derivation, and
 * the projects-folder picker. The auto-name flow is purely additive.
 */
export default function SetupFirstProjectPage() {
  return (
    <Suspense
      fallback={
        <div className="py-10 text-sm text-text-muted">Loading…</div>
      }
    >
      <SetupFirstProjectPageInner />
    </Suspense>
  );
}

function SetupFirstProjectPageInner() {
  const router = useRouter();
  const search = useSearchParams();
  const skippedDocs = search?.get("skipped") === "1";

  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [nameTouched, setNameTouched] = useState(false);
  const [projectsDir, setProjectsDir] = useState("");
  const [tauri, setTauri] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>({ kind: "idle" });
  const [autoName, setAutoName] = useState<AutoName | null>(null);
  const [autoNameLoading, setAutoNameLoading] = useState(false);

  useEffect(() => {
    setTauri(isInTauri());
  }, []);

  /* ----------- pre-fill projects_dir from the backend default ------------ */
  // The backend resolves a real, valid path (no `<you>` token); falling
  // back to the OS-shaped guess only when the endpoint is unavailable
  // ensures the user always sees a submittable path on first paint.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const defaults = await setupApi.defaults();
        if (cancelled) return;
        const resolved = defaults?.projects_dir || fallbackProjectsDir();
        // Only pre-fill if the user hasn't typed anything yet — never
        // clobber user input on a slow-arriving network response.
        setProjectsDir((prev) => (prev ? prev : resolved));
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn(
          "[setup/first-project] could not resolve default projects_dir, using local fallback",
          err,
        );
        if (cancelled) return;
        setProjectsDir((prev) => (prev ? prev : fallbackProjectsDir()));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  /* ------------------------ auto-name from folder ------------------------- */

  useEffect(() => {
    let cancelled = false;
    let folderPath: string | null = null;
    let folderName: string | null = null;
    try {
      folderPath = window.sessionStorage.getItem("meridian.setup.folder_path");
      folderName = window.sessionStorage.getItem("meridian.setup.folder_name");
    } catch {
      folderPath = null;
    }
    if (!folderPath) return;
    setAutoNameLoading(true);
    (async () => {
      try {
        const res = await setupApi.suggestProjectName(folderPath);
        if (cancelled) return;
        setAutoName({
          suggested: res.suggested_name,
          wasBumped: !res.is_available,
          originalFolderName: folderName ?? res.suggested_name,
        });
        // Pre-fill the name + slug unless the user has already typed.
        setName((prev) => (prev || nameTouched ? prev : res.suggested_name));
        setSlug((prev) =>
          prev || slugTouched ? prev : suggestSlug(res.suggested_name),
        );
      } catch {
        // Suggest endpoint is best-effort — silently fall back to the
        // empty manual flow. The folder-name we have in sessionStorage
        // is still a reasonable seed, so use it.
        if (cancelled) return;
        if (folderName) {
          setAutoName({
            suggested: folderName,
            wasBumped: false,
            originalFolderName: folderName,
          });
          setName((prev) => (prev || nameTouched ? prev : folderName!));
          setSlug((prev) =>
            prev || slugTouched ? prev : suggestSlug(folderName!),
          );
        }
      } finally {
        if (!cancelled) setAutoNameLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // We intentionally don't depend on nameTouched/slugTouched — the auto
    // run is one-shot on mount; subsequent edits are user-driven.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-suggest the slug from the name unless the user has touched it.
  useEffect(() => {
    if (!slugTouched) setSlug(suggestSlug(name));
  }, [name, slugTouched]);

  const slugError =
    slug && !isValidSlug(slug) ? FIRST_PROJECT_COPY.fields.slug.invalid : null;

  const canSubmit =
    name.trim().length > 0 &&
    isValidSlug(slug) &&
    outcome.kind !== "creating";

  const pickFolderHandler = async () => {
    const picked = await pickFolder({
      defaultPath: projectsDir || undefined,
      title: "Choose where to store this project",
    });
    if (picked) setProjectsDir(picked);
  };

  const handleCreate = useCallback(async () => {
    if (!canSubmit) return;
    setOutcome({ kind: "creating" });
    try {
      const res: CreateProjectResponse = await setupApi.createProject(
        name.trim(),
        slug,
        projectsDir || "",
      );
      switch (res.outcome) {
        case "created":
          setOutcome({ kind: "created", slug: res.slug ?? slug });
          // Persist the chosen slug so the next step can reference it.
          try {
            window.sessionStorage.setItem(
              "meridian.setup.project_slug",
              res.slug ?? slug,
            );
            window.sessionStorage.setItem(
              "meridian.setup.project_name",
              name.trim(),
            );
          } catch {
            // ignore — sessionStorage may be blocked
          }
          break;
        case "conflict":
          setOutcome({
            kind: "conflict",
            slug: res.slug ?? slug,
            message: res.message,
          });
          break;
        case "invalid":
        default:
          setOutcome({
            kind: "invalid",
            message: res.message || "Could not create the project.",
            osError: res.os_error ?? null,
          });
          break;
      }
    } catch (err) {
      setOutcome({
        kind: "error",
        message:
          err instanceof Error
            ? err.message
            : "Could not reach the Meridian backend.",
      });
    }
  }, [canSubmit, name, slug, projectsDir]);

  const handleContinue = () => router.push("/setup/ready");

  return (
    <SetupShell
      step="first-project"
      backHref="/setup/first-documents"
      onContinue={handleContinue}
      continueDisabled={outcome.kind !== "created"}
      busy={outcome.kind === "creating"}
    >
      <div className="space-y-8">
        <header>
          <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
            {FIRST_PROJECT_COPY.title}
          </h1>
        </header>

        <section
          aria-label="Why this step"
          className="rounded-lg border border-accent/30 bg-accent/5 p-4 text-sm text-text-primary"
        >
          {FIRST_PROJECT_COPY.why()}
        </section>

        {/* Bumped-name advisory — only shown when /suggest-name returned a
            different name than the raw folder name. Helps PMs understand
            why the input doesn't quite match the folder they picked. */}
        {autoName?.wasBumped ? (
          <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm text-amber-200">
            {FIRST_PROJECT_COPY.autoNameBumpedHeadline(
              autoName.originalFolderName,
              autoName.suggested,
            )}
          </div>
        ) : null}

        {skippedDocs ? (
          <div className="rounded-lg border border-border bg-surface-elevated p-3 text-sm text-text-muted">
            You skipped the document scan — no problem. You can add documents
            later from the Sources screen on your project dashboard.
          </div>
        ) : null}

        {/* Name */}
        <div className="space-y-2">
          <label
            htmlFor="proj-name"
            className="block text-sm font-medium text-text-primary"
          >
            {FIRST_PROJECT_COPY.fields.name.label}
          </label>
          <input
            id="proj-name"
            type="text"
            value={name}
            onChange={(e) => {
              setNameTouched(true);
              setName(e.target.value);
              if (outcome.kind !== "idle" && outcome.kind !== "creating")
                setOutcome({ kind: "idle" });
            }}
            placeholder={FIRST_PROJECT_COPY.fields.name.placeholder}
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-accent focus:outline-none"
          />
          {autoName ? (
            <p className="text-xs text-text-muted">
              {FIRST_PROJECT_COPY.autoNameHelper}
            </p>
          ) : (
            <p className="text-xs text-text-muted">
              {FIRST_PROJECT_COPY.fields.name.helper}
            </p>
          )}
          {autoNameLoading ? (
            <p className="text-[11px] text-text-muted">
              Loading suggested name from folder…
            </p>
          ) : null}
        </div>

        {/* Slug */}
        <div className="space-y-2">
          <label
            htmlFor="proj-slug"
            className="flex items-center gap-2 text-sm font-medium text-text-primary"
          >
            {FIRST_PROJECT_COPY.fields.slug.label}
            <Tooltip
              content={FIRST_PROJECT_COPY.fields.slug.tooltip}
              widthClass="w-80"
            >
              <span className="cursor-help text-xs text-text-muted underline decoration-dotted">
                what&apos;s a slug?
              </span>
            </Tooltip>
          </label>
          <input
            id="proj-slug"
            type="text"
            value={slug}
            onChange={(e) => {
              setSlugTouched(true);
              setSlug(e.target.value);
              if (outcome.kind !== "idle" && outcome.kind !== "creating")
                setOutcome({ kind: "idle" });
            }}
            className="w-full rounded-md border border-border bg-surface px-3 py-2 font-mono text-sm text-text-primary focus:border-accent focus:outline-none"
            aria-invalid={Boolean(slugError)}
          />
          <p className="text-xs text-text-muted">
            {FIRST_PROJECT_COPY.fields.slug.helper}
          </p>
          {slugError ? (
            <p className="text-xs text-red-300">{slugError}</p>
          ) : null}
        </div>

        {/* Projects folder */}
        <div className="space-y-2">
          <label
            htmlFor="proj-dir"
            className="block text-sm font-medium text-text-primary"
          >
            {FIRST_PROJECT_COPY.fields.projectsDir.label}
          </label>
          <div className="flex gap-2">
            <input
              id="proj-dir"
              type="text"
              value={projectsDir}
              onChange={(e) => setProjectsDir(e.target.value)}
              placeholder={DIR_PLACEHOLDER_HINT}
              className="flex-1 rounded-md border border-border bg-surface px-3 py-2 font-mono text-xs text-text-primary placeholder:text-text-muted focus:border-accent focus:outline-none"
            />
            <button
              type="button"
              onClick={() => void pickFolderHandler()}
              disabled={!tauri}
              className="rounded-md border border-border px-3 py-2 text-xs text-text-primary hover:border-accent disabled:opacity-40"
            >
              {FIRST_PROJECT_COPY.fields.projectsDir.pickButton}
            </button>
          </div>
          <p className="text-xs text-text-muted">
            {FIRST_PROJECT_COPY.fields.projectsDir.helper}
          </p>
          <p className="text-xs text-text-muted">
            {FIRST_PROJECT_COPY.fields.projectsDir.defaultHint}
          </p>
          <p className="rounded border border-amber-500/40 bg-amber-500/5 p-2 text-xs text-amber-200">
            ⚠ {FIRST_PROJECT_COPY.fields.projectsDir.onedriveWarning}
          </p>
          {!tauri ? (
            <p className="rounded border border-border bg-surface p-2 text-xs text-text-muted">
              {FIRST_PROJECT_COPY.fields.projectsDir.browserModeNote}
            </p>
          ) : null}
        </div>

        <div className="flex items-center gap-3 pt-2">
          <button
            type="button"
            onClick={() => void handleCreate()}
            disabled={!canSubmit}
            className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
          >
            {outcome.kind === "creating"
              ? "Creating…"
              : FIRST_PROJECT_COPY.createLabel}
          </button>
        </div>

        {outcome.kind === "created" ? (
          <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-4">
            <p className="text-sm font-medium text-emerald-300">
              ✓ Project created.
            </p>
            <p className="mt-1 text-sm text-text-muted">
              Slug: <code className="font-mono">{outcome.slug}</code>. Press
              Continue to finish setup.
            </p>
          </div>
        ) : null}

        {outcome.kind === "conflict" ? (
          <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
            <p className="text-sm font-medium text-amber-300">
              ⚠ {FIRST_PROJECT_COPY.outcomes.conflict.headline}
            </p>
            <p className="mt-1 text-sm text-text-muted">
              {FIRST_PROJECT_COPY.outcomes.conflict.body}
            </p>
            <div className="mt-3 flex gap-2">
              <Link
                href={`/projects/${encodeURIComponent(outcome.slug)}`}
                className="rounded-full border border-accent/50 px-3 py-1.5 text-xs text-accent hover:bg-accent/10"
              >
                {FIRST_PROJECT_COPY.outcomes.conflict.openLabel} →
              </Link>
              <button
                type="button"
                onClick={() => {
                  setSlugTouched(true);
                  setOutcome({ kind: "idle" });
                  document.getElementById("proj-slug")?.focus();
                }}
                className="rounded-full border border-border px-3 py-1.5 text-xs text-text-muted hover:border-text-muted hover:text-text-primary"
              >
                {FIRST_PROJECT_COPY.outcomes.conflict.changeLabel}
              </button>
            </div>
          </div>
        ) : null}

        {outcome.kind === "invalid" ? (
          <div className="rounded-lg border border-red-500/50 bg-red-500/5 p-4">
            <p className="text-sm font-medium text-red-300">
              ✗ {FIRST_PROJECT_COPY.outcomes.invalid.headline}
            </p>
            <p className="mt-1 text-sm text-text-muted">
              {FIRST_PROJECT_COPY.outcomes.invalid.bodyPrefix}
              <code className="rounded bg-surface px-1 text-xs">
                {outcome.osError || outcome.message}
              </code>
            </p>
            <button
              type="button"
              onClick={() => void pickFolderHandler()}
              className="mt-3 rounded-full border border-border px-3 py-1.5 text-xs text-text-muted hover:border-text-muted hover:text-text-primary"
            >
              {FIRST_PROJECT_COPY.outcomes.invalid.tryAnother}
            </button>
          </div>
        ) : null}

        {outcome.kind === "error" ? (
          <div className="rounded-lg border border-red-500/50 bg-red-500/5 p-4">
            <p className="text-sm font-medium text-red-300">
              Couldn&apos;t reach the Meridian backend.
            </p>
            <p className="mt-1 text-sm text-text-muted">{outcome.message}</p>
          </div>
        ) : null}
      </div>

    </SetupShell>
  );
}
