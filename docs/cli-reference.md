# CLI reference

Every Meridian CLI command, grouped by topic, with one example per command. Every command supports `--help` for the full set of options. This page does not duplicate the in-app help — it tells you which command to reach for and shows a typical use.

You can invoke the CLI three ways. All examples use the shorthand `meridian`; substitute either of the longer forms if your shell does not find the command:

```
.venv/Scripts/python -m meridian.cli <command>
uv run meridian <command>
meridian <command>
```

A note on conventions:

- `<project>` is the project name (slugified for the SQLite filename).
- *Status: scaffolded — requires <X> decision* on a command means the local code path is fully implemented but a deployment decision is still pending; the command will tell you so when you run it.

## Project lifecycle

| Command | Intent | Example |
|---|---|---|
| `project-create` | Create a new project SQLite file under `data/projects/`. | `meridian project-create my-project --notes "Pilot run"` |
| `status` | One-screen summary of project contents (sources, deliverables, queues). | `meridian status my-project` |
| `review-status` | Full baseline-trustworthiness dashboard — every queue, master register status, provenance and cost completeness. | `meridian review-status my-project` |
| `db-migrate` | Apply pending schema migrations to an existing project DB. Idempotent — safe to re-run. | `meridian db-migrate my-project` |

There is no separate `init` command — `project-create` plus the auto-bootstrap on first `import-doc` are the equivalent flow.

## Import and extract

| Command | Intent | Example |
|---|---|---|
| `import-doc` | Hash, dedup, and extract text from one or more source documents. Offers to run the bootstrap sweep on the first import. | `meridian import-doc my-project Samples/Sample-A/*.pdf` |
| `bootstrap` | Run the per-project bootstrap LLM sweep — proposes document classes, taxonomies, and the authority chain for SME confirmation. | `meridian bootstrap my-project --sample-size 15` |
| `bootstrap-show` | Print the latest bootstrap proposal as a readable summary. | `meridian bootstrap-show my-project` |
| `extract` | Run quality scan + triage + extraction across the requested sources. Each source runs in its own subprocess. | `meridian extract my-project` |
| `pause` | Signal a running extraction job to pause after the current source. | `meridian pause my-project` |
| `resume` | Resume a paused extraction job. Picks up at the chunk boundary, not the source boundary. | `meridian resume my-project` |
| `conflicts` | Run the cross-source conflict-detection pass over the master register + audit. | `meridian conflicts my-project` |

The `extract` command is idempotent at the source level: sources with a completed prior extraction are skipped. Pass `--force` to re-extract; you will get a warning naming any reviewer-touched rows that would be orphaned.

## Cost controls

| Command | Intent | Example |
|---|---|---|
| `cost-preview` | Estimate LLM spend BEFORE running an extraction (CONTEXT.md §13). | `meridian cost-preview my-project` |
| `cost-summary` | Show realised LLM spend across this project. | `meridian cost-summary my-project` |

Run `cost-preview` whenever you change provider routing or import a noticeably bigger corpus.

## Review queues

All review walkers are interactive — they show one item at a time with full context (source ref, flags, reasoning) and prompt for an action.

| Command | Intent | Example |
|---|---|---|
| `review walk-quarantine` | Walk through quarantined deliverables one at a time. | `meridian review walk-quarantine my-project` |
| `review accept` | Accept one quarantined deliverable into the master register. | `meridian review accept my-project <deliverable-id>` |
| `review reject` | Reject a deliverable (kept in audit; excluded from master). Optional `--reason "..."`. | `meridian review reject my-project <deliverable-id> --reason "duplicate"` |
| `review edit` | Create an edited child row (parent kept for audit trail). Use `--summary`, `--trade`, `--service`, `--category` to override fields. | `meridian review edit my-project <deliverable-id> --service HVAC` |
| `review walk-audit` | Walk audit (OUTSIDE) rows; promote any the LLM was wrong to reject. | `meridian review walk-audit my-project` |
| `review promote-audit` | Promote a single audit row into a deliverable. | `meridian review promote-audit my-project <audit-id>` |
| `review walk-questions` | Walk pending HITL questions; resolve or dismiss each. | `meridian review walk-questions my-project` |
| `review walk-conflicts` | Walk pending conflicts; resolve via accept-A / accept-B / reject-both / hybrid. | `meridian review walk-conflicts my-project` |
| `review walk-taxonomy` | Walk unconfirmed taxonomy proposals; confirm, merge, or reject each. | `meridian review walk-taxonomy my-project` |
| `review confirm-taxonomy` | Confirm one taxonomy proposal as canonical. | `meridian review confirm-taxonomy my-project --table service --value "DCS / Controls"` |
| `review merge-taxonomy` | Merge one taxonomy value into another (cascades all deliverable rows — destructive, asks for confirmation). | `meridian review merge-taxonomy my-project --table service --source "Chiller System" --target "HVAC"` |

The merge action repoints potentially many rows at once. The CLI shows the row count before committing.

The list-only equivalent of the question walker is also available:

| Command | Intent | Example |
|---|---|---|
| `list-questions` | Dump pending HITL questions as JSON to stdout. | `meridian list-questions my-project` |

## Export

| Command | Intent | Example |
|---|---|---|
| `export` | Export the master register + pivots (by trade, by service, by category) to an Excel workbook. Regenerated from SQLite on every call. | `meridian export my-project -o my-project.xlsx` |

Excel is a render target, not the working data. Edits to the Excel **do not survive** a re-export. Make changes via the review queues.

## Tender packages

| Command | Intent | Example |
|---|---|---|
| `tender list` | List trades with at least one accepted deliverable in this project. | `meridian tender list my-project` |
| `tender build` | Build a per-trade tender package and write it to `<projects-dir>/<slug>.tenders/`. Read-only — no DB writes, no LLM calls. | `meridian tender build my-project --trade Mechanical --format xlsx` |

`--format` accepts `xlsx` (Excel workbook with cover sheet, deliverables grouped by service then category) or `md` (single Markdown document). Conflict-family flag pills resolve to filenames, not opaque UUIDs.

## Legal Evidence Pack

| Command | Intent | Example |
|---|---|---|
| `evidence build` | Assemble a defensible audit-trail bundle as a single timestamped zip. Includes deliverables, every LLM call (with secret redaction), full provenance, embedded prompts, sources, and a cover.md. | `meridian evidence build my-project` |
| `evidence verify` | Re-compute SHA-256 hashes inside a pack and compare to MANIFEST.json. | `meridian evidence verify path\to\my-project-evidence-2026-04-27.zip` |

Evidence packs can run to several gigabytes for a real project. The CLI is the only supported way to verify; the web UI deliberately does not stream the upload.

## Cross-reference sweep

| Command | Intent | Example |
|---|---|---|
| `xref sweep` | Run the deterministic cross-reference sweep over every ingested doc. Four-outcome classification: confirmed / borderline / external_reference / rejected. | `meridian xref sweep my-project --dry-run` |
| `xref report` | Re-render the most recent sweep results from the DB as CSV + Markdown under `<projects-dir>/<slug>.reports/xref/`. | `meridian xref report my-project --format md` |

**The default for `xref sweep` is `--commit`** — it persists findings and adds borderlines to the questions queue. Pass `--dry-run` to preview without DB writes (recommended the first time you sweep on a corpus). The `--llm-assist` flag opts in to a per-anchor LLM second-pass on ambiguous findings (off by default — costs token budget).

## Per-purpose LLM provider routing

Each LLM purpose (`quality_scan`, `triage`, `extract_text_spec`, `extract_bod`, `extract_demarcation`, `conflict_pass`, `error_explain`) is independently routable. Presets ship under two names: an **operator-facing alias** that describes deployment intent (this is the vocabulary used in CONTEXT.md §12) and a **technical name** that describes the underlying provider/model recipe. `routing apply` accepts either form.

| Operator alias | Technical name | What it does |
|---|---|---|
| `cloud-default` | `cloud-sonnet-default` | All purposes on Anthropic Sonnet 4.6 (Haiku 4.5 for triage). No local dependency. |
| `hybrid` | `ollama-5090-balanced` | Triage and lower-stakes purposes on local Ollama (5090-class GPU); `extract_text_spec` and `conflict_pass` (the load-bearing calls) stay on cloud Sonnet. |
| `air-gapped` | `ollama-air-gapped` | Every purpose on local Ollama. No cloud calls; cloud routes blocked at preflight. |
| _(no alias)_ | `triage-local-only` | Cost-control recipe — only triage routed to local Ollama; everything else inherits the cloud defaults. |

Run `meridian routing list-presets` to print this table from the live config (helpful for confirming that the version you have shipped includes a particular preset).

| Command | Intent | Example |
|---|---|---|
| `routing show` | Print the resolved (provider, model) for every purpose. | `meridian routing show my-project` |
| `routing list-presets` | List every preset with operator alias, technical name, and description. | `meridian routing list-presets` |
| `routing apply` | Apply a named routing preset to the project. Accepts alias OR technical name. | `meridian routing apply my-project hybrid` _or_ `meridian routing apply my-project ollama-5090-balanced` |
| `routing set` | Set the route for a single purpose at the project level. Positional: NAME PURPOSE PROVIDER MODEL. | `meridian routing set my-project triage ollama llama3` |
| `routing unset` | Remove a per-project route override for one purpose. | `meridian routing unset my-project triage` |
| `routing air-gap-on` | Enable air-gap mode for this project (block any cloud route at preflight). | `meridian routing air-gap-on my-project` |
| `routing air-gap-off` | Disable air-gap mode for this project. | `meridian routing air-gap-off my-project` |

Air-gap mode fails preflight if any configured route would call out to a cloud provider. Use it for projects where document content cannot leave the local network.

`routing apply` returns one of three outcomes — **success** (preset persisted), **preset-not-found** (exit 1; the requested name resolves to neither alias nor technical name), or **preset-found-but-validation-failed** (the preset is persisted but a warning prints because, e.g., an Ollama-based preset was applied without `OLLAMA_BASE_URL` set in the environment). The third outcome is intentionally a warning, not an error: staging routing config before bringing the local Ollama endpoint up is a legitimate workflow.

## Analytics

Standalone analytics over the deliverables, audit, and conflict data. Each emits a CSV + Markdown report.

| Command | Intent | Example |
|---|---|---|
| `analytics risk-hotspots` | Compute and export the top-N risk hotspots — deliverables with the highest combined flag, confidence, and conflict scores. | `meridian analytics risk-hotspots my-project` |
| `analytics nrc-summary` | Export every deliverable carrying `scope_shifted_to_nrc` (items shifted to the customer's fit-out). | `meridian analytics nrc-summary my-project` |
| `analytics conflict-register` | Export the conflict register (Summary / Conflicts / Parties sheets). | `meridian analytics conflict-register my-project` |
| `analytics compliance` | Compliance traceability — pivot of deliverables × applicable_standards. | `meridian analytics compliance my-project` |
| `analytics trade-overlap` | Trade overlap — co-occurrence pairs across extraction groups. | `meridian analytics trade-overlap my-project` |
| `analytics ose-procurement` | OSE procurement completeness — per-vendor scope checklist. | `meridian analytics ose-procurement my-project` |

The OSE completeness number assumes the vendor's OSE spec is in the corpus. Vendors mentioned only in BOD rows will appear under-covered — that is a corpus gap, not a data quality issue.

## Authentication (single-user TOTP)

Meridian's auth surface is single-user, self-enrolled at first launch. Driven by the CLI; the FastAPI app accepts the resulting bearer token via `POST /auth/login`.

| Command | Intent | Example |
|---|---|---|
| `auth enroll` | Self-enrol a TOTP secret + recovery codes. Shows a QR code for your authenticator app. | `meridian auth enroll` |
| `auth status` | Show whether TOTP is enrolled (no secrets exposed). | `meridian auth status` |
| `auth verify` | Verify a TOTP code against the stored secret (testing helper). | `meridian auth verify 123456` |
| `auth logout` | Revoke every active session token. | `meridian auth logout` |
| `auth reset` | Wipe the TOTP secret + recovery codes. Requires you to type `RESET` as the literal confirmation token. | `meridian auth reset --confirm RESET` |

See [security.md](security.md) for where the TOTP secret lives on disk and what backend swaps are planned.

## Licensing

Meridian licenses are issued by support and verified locally with an Ed25519 signature. The public key is embedded in the app; only support holds the private key.

| Command | Intent | Example |
|---|---|---|
| `license install` | Copy a license file into place and verify it immediately. | `meridian license install path\to\meridian.license` |
| `license status` | Show the currently installed license, expiry date, plan, and features. | `meridian license status` |
| `license verify` | Verify a license string without installing it. | `meridian license verify "<license-string>"` |

*Status: scaffolded — requires §3.8 decision (key generation policy).* The local verification path is implemented and tested end-to-end. Issuing real licenses awaits the keypair generation + storage decision (see [release-notes.md](release-notes.md) under alpha-12).

## Updates

Meridian's auto-update mechanism reads a small JSON manifest from a CDN endpoint, compares semver, and prompts the user. The local checking, version comparison, and skip-list are implemented.

| Command | Intent | Example |
|---|---|---|
| `updates check` | Check the update manifest. Prints "up to date" or "version X.Y available". | `meridian updates check` |
| `updates skip` | Record "skip this version" so future `updates check` calls ignore it. | `meridian updates skip 0.2.0` |
| `updates show-skipped` | List the versions you have previously chosen to skip. | `meridian updates show-skipped` |

*Status: scaffolded — requires §3.5 decision (manifest endpoint URL).* Until the URL lands, `check` will tell you the endpoint is unconfigured.

## Crash reporting

Local crash dossiers are always written to disk by the error handler. The send path is opt-in and runs an additional defensive secret-redaction pass before transmission.

| Command | Intent | Example |
|---|---|---|
| `crash list` | List all local crash dossiers, newest first. | `meridian crash list` |
| `crash preview` | Show the redacted payload that `send` would transmit. | `meridian crash preview <dossier-id>` |
| `crash send` | Send a crash report to the configured endpoint. | `meridian crash send <dossier-id>` |
| `crash opt-in` | Show or change the crash-reporting opt-in flag. | `meridian crash opt-in --enable` |

*Status: scaffolded — requires §3.6 decision (crash endpoint URL).* The send command refuses to POST to the placeholder URL — explicit configuration is required before any real send.

## Diagnostics

| Command | Intent | Example |
|---|---|---|
| `explain-last-error` | Use the LLM to explain the most recent error in the structured log. Reads the JSONL log, redacts secrets, returns a plain-English diagnosis + suggested next steps. | `meridian explain-last-error my-project` |

`explain-last-error` is the first thing to reach for after a crash. It also writes a local crash dossier ready for `crash preview` and `crash send` (when the endpoint URL is configured).

## What's not in this reference

- **`init`, `backup`** — not shipped. Use `project-create` plus the auto-bootstrap on first `import-doc` for setup; copy the `<slug>.sqlite` file out for backup. See [getting-started.md](getting-started.md) for the disk layout.
- **`auth login` / `auth logout` HTTP endpoints** — exposed by the FastAPI app at `POST /auth/login` and `POST /auth/logout`. Not driven from the CLI; they're how the future Next.js shell authenticates.
- **Browser preview / web shell commands** — the web UI is launched separately via `cd apps/web && npm install && npm run dev`. See [getting-started.md](getting-started.md).
