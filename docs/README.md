# Meridian documentation

Meridian extracts per-trade design deliverables from a pile of mixed project documents and assembles them into a structured master register. This folder is where you go to learn how to drive it.

If you are new, start at the top of the list.

## Documents in this folder

- [getting-started.md](getting-started.md) — install check, your first project, where files live on disk. Read this before you do anything else.
- [concepts.md](concepts.md) — what a "deliverable" is, the trade / service / category model, the three-outcome gate, the review queue, the authority chain. The mental model behind the tool.
- [cli-reference.md](cli-reference.md) — every command grouped by topic, with one example per command. Use this once you know the shape of the tool and need to look up specifics.
- [troubleshooting.md](troubleshooting.md) — the common failures (API errors, locked databases, empty tender packages, and so on) and how to fix them. Each entry names the log event to grep for.
- [security.md](security.md) — how Meridian handles your API keys, your TOTP secret, your license, your documents, and crash reports. Hand this to your IT department if they need to sign off the tool.
- [architecture.md](architecture.md) — for the technically curious PM or for an IT evaluator. Describes the per-project SQLite model, the ingest-to-export pipeline, the schema-version history, and the extension points.
- [release-notes.md](release-notes.md) — what shipped in each round (alpha-7 through alpha-12). Skim this when you upgrade to know what to look for.

## Reading order by goal

- **"I just installed Meridian, what now?"** — read [getting-started.md](getting-started.md), then skim [concepts.md](concepts.md).
- **"Something broke."** — go straight to [troubleshooting.md](troubleshooting.md). If the answer isn't there, run `meridian explain-last-error <project>`.
- **"My IT team has questions before I install this."** — give them [security.md](security.md) and [architecture.md](architecture.md).
- **"I want to know what this command does."** — [cli-reference.md](cli-reference.md).
- **"I'm trying to remember a term I saw in the UI."** — open the in-app glossary at `http://localhost:3000/glossary`, or read the glossary section in [concepts.md](concepts.md).

## A note on terminology

Meridian's vocabulary is shared between the documentation, the CLI, the web UI, and the extraction prompts. If a term appears here it is also defined in the in-app glossary at `apps/web/src/app/glossary/page.tsx` and (where applicable) is the exact word the LLM is told to use. If you see a term you don't recognise, the glossary is the place to look first.

## A note on what's shipped

Meridian is in pre-release. Some surfaces are scaffolded but waiting on a deployment decision (license signing keys, the auto-update endpoint, the crash-report endpoint, and the Windows installer). Where a command exists but the network endpoint isn't decided yet, the CLI will say so explicitly and refuse to send. Those features are flagged in [cli-reference.md](cli-reference.md) with a note like *"Status: scaffolded — requires <X> decision."*
