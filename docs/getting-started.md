# Getting started

A walk-through for the project manager who has just installed Meridian and wants to get to a first usable register inside an hour.

## What this tool does

Meridian reads a folder of mixed construction-project documents — Basis-of-Design narratives, technical specifications, drawings, demarcation schedules, owner-supplied-equipment specs, BOD response registers, emails — and produces a per-trade, per-service deliverables register as an Excel workbook. Each row links back to the document it came from and the exact location within it. Items the AI is unsure about land in a review queue rather than silently entering the master register, so you choose what makes the cut.

If the term *deliverable* is new to you, read [concepts.md](concepts.md) first.

## Before you start

You need:

- **Python 3.11 or newer** with the project's virtual environment installed (`.venv/` at the project root). Run `.venv/Scripts/python --version` to check.
- **An Anthropic API key.** Sign up at [console.anthropic.com](https://console.anthropic.com) and put a small credit balance on the account (USD 5 is enough to drive the test corpus). The key starts with `sk-ant-`.
- **Some sample documents.** Anything PDF, DOCX, XLSX, or plain text from a real project. Five to ten documents is enough for a first pass; the bootstrap sweep takes a sample, not the whole corpus.
- **About 200 MB of free disk for the project itself**, plus headroom for any logs and exported tender packages. Plan on 1 GB total per project as a safe ceiling.
- **A stable internet connection.** The heavy AI work runs in the cloud, not on your laptop.

Set the API key in your shell before running anything that calls the LLM:

```
# Windows PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# bash / zsh
export ANTHROPIC_API_KEY=sk-ant-...
```

The key is read from the environment on every invocation. Meridian does not write your key to disk.

## Your first project in five minutes

Meridian is driven via its CLI. You can invoke it three ways:

```
.venv/Scripts/python -m meridian.cli <command>     # always works
uv run meridian <command>                           # if you use uv
meridian <command>                                  # if the venv is on PATH
```

The examples below use the `meridian` shorthand. Substitute either of the longer forms if your shell does not find the command.

### 1. Create the project

```
meridian project-create my-first-project --notes "Pilot run on Sample-A documents"
```

Expected output:

```
Project created.
  id:   <a UUID>
  file: ...\data\projects\my-first-project.sqlite
```

A new SQLite file appears under `data/projects/`. This is the single source of truth for everything Meridian learns about this project.

### 2. Import a few documents

Point Meridian at one or more files. Wildcards work.

```
meridian import-doc my-first-project Samples/Sample-A/*.pdf
```

This step extracts text, hashes the file (so re-imports are skipped), and registers the source. **The first time you import documents into a fresh project**, Meridian offers to run a quick *bootstrap sweep* — a single LLM pass that looks at a sample of your corpus and proposes which document classes you have (BOD, drawing, demarcation schedule, etc.), which trades and services are likely in scope, and which document is the authority for each topic. Answer **Y** to take the suggestion; you'll review and confirm the proposals later. (The bootstrap sweep costs a few cents at most.) See [concepts.md](concepts.md) for what these proposals mean.

If you'd rather skip the prompt (for example in a script), pass `--no-auto-bootstrap`.

### 3. Preview cost, then extract

Before spending real money, see what extraction will cost:

```
meridian cost-preview my-first-project
```

The tool estimates the LLM spend based on document length and your current per-purpose routing. When you're happy, run extraction:

```
meridian extract my-first-project
```

This walks every imported source through quality scan, triage (which chunks contain candidate deliverables), extraction, and persistence. Each source is processed in its own subprocess so a crash on one document doesn't lose the others. Long-running extractions can be paused (`meridian pause my-first-project`) and resumed (`meridian resume my-first-project`) — Meridian remembers where it was on a chunk-by-chunk basis.

### 4. See what landed

```
meridian status my-first-project
meridian review-status my-first-project
```

`status` is a one-screen summary (sources, deliverables, queue counts). `review-status` is the full *baseline-trustworthiness dashboard* — it tells you what is in each review queue and whether the master register is safe to share yet.

### 5. Walk the review queues

The AI does not auto-approve everything. Things land in a review queue when:
- The AI rated its own confidence as low or medium, or
- One or more flags were raised on the deliverable, or
- The three-outcome gate said BORDERLINE rather than INSIDE.

Walk the quarantine one item at a time:

```
meridian review walk-quarantine my-first-project
```

For each item the CLI shows the deliverable, the source reference, and the flags. You **Accept** (it goes onto the master register), **Edit** (correct it then accept), or **Reject** (it stays in the audit trail but never reaches the master register). Similar walks exist for the audit queue, conflicts queue, questions queue, and taxonomy proposals. See [concepts.md](concepts.md) for what each queue means.

### 6. Export the register

```
meridian export my-first-project -o my-first-project.xlsx
```

You get an Excel workbook with the master sheet plus pivot views by trade, service, and category. The Excel is regenerated on demand; edits to the Excel do not survive a re-export. Make changes via the review queues instead.

## Where things live on disk

All project state lives under the projects directory. By default that is `data/projects/` at the repository root.

| Path | What's in it |
|---|---|
| `<projects-dir>/<slug>.sqlite` | The project's single source of truth — sources, deliverables, audit, queues, LLM call records, taxonomy. |
| `<projects-dir>/<slug>.logs/meridian-YYYYMMDD.log` | Structured JSONL logs for that project. Rotates at 10 MB; keeps the last 5 files. |
| `<projects-dir>/<slug>.tenders/` | Per-trade tender packages built by `meridian tender build`. |
| `<projects-dir>/<slug>.evidence/` | Legal Evidence Pack zips built by `meridian evidence build`. |
| `<projects-dir>/<slug>.reports/xref/` | Cross-reference sweep reports (CSV + Markdown). |
| `<projects-dir>/_auth/` | TOTP secret + recovery code records (single-user). See [security.md](security.md). |
| `<projects-dir>/_global.logs/` | Logs for CLI invocations not bound to a specific project. |
| `<projects-dir>/_meridian/crash_opt_in.json` | Your opt-in / opt-out flag for crash reporting. |

The `<slug>` is the project name with non-alphanumeric characters replaced — for example `My First Project` becomes `my-first-project`.

If you want to back a project up, **copy the matching `<slug>.sqlite` file**. Everything else is regenerable from it. (Logs and reports are convenience artefacts; the SQLite is canonical.)

## What to do next

- Read [concepts.md](concepts.md) to understand the deliverable definition, the three-outcome gate, and the review queues. Doing real work without this is possible but harder.
- When something goes wrong, [troubleshooting.md](troubleshooting.md) has the common failures, the log events to grep for, and the explainer command (`meridian explain-last-error`).
- For day-to-day reference, [cli-reference.md](cli-reference.md) groups every command by topic with an example.
- If you want to drive Meridian from the web UI rather than the terminal, install Node 20 or newer, then `cd apps/web && npm install && npm run dev` against `uvicorn meridian.api.main:app --reload --port 8000`. The web UI mirrors the CLI's review flows and adds richer visualisations.
- Ask your IT team to read [security.md](security.md) so they know what data leaves your machine (just document content to your chosen LLM provider) and what does not (no telemetry to Meridian itself).
