"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

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

/** Reasonable Windows default until the API tells us otherwise. */
const DEFAULT_DIR_HINT = "C:\\Users\\<you>\\Meridian\\projects";

/**
 * Step 2 — Name your first project.
 *
 * Auto-derives a slug from the project name (editable). Path picker uses
 * the Tauri native folder dialog when available; in browser fallback the
 * picker can't return absolute paths, so we surface a non-blocking hint
 * and let the user accept the default.
 */
export default function SetupFirstProjectPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [projectsDir, setProjectsDir] = useState("");
  const [tauri, setTauri] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>({ kind: "idle" });

  useEffect(() => {
    setTauri(isInTauri());
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
            window.sessionStorage.setItem("meridian.setup.project_slug", res.slug ?? slug);
            window.sessionStorage.setItem("meridian.setup.project_name", name.trim());
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
            : "Could not reach the Meridian API.",
      });
    }
  }, [canSubmit, name, slug, projectsDir]);

  const handleContinue = () => router.push("/setup/first-documents");

  return (
    <SetupShell
      step="first-project"
      backHref="/setup/api-key"
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
              setName(e.target.value);
              if (outcome.kind !== "idle" && outcome.kind !== "creating")
                setOutcome({ kind: "idle" });
            }}
            placeholder={FIRST_PROJECT_COPY.fields.name.placeholder}
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-accent focus:outline-none"
          />
          <p className="text-xs text-text-muted">
            {FIRST_PROJECT_COPY.fields.name.helper}
          </p>
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
              placeholder={DEFAULT_DIR_HINT}
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
              Continue to import documents.
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
