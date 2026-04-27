# Troubleshooting

The common ways Meridian breaks, organised so you can find the right page in under a minute. Each entry names the structured-log event you can grep for in `<projects-dir>/<slug>.logs/meridian-YYYYMMDD.log`, plus the explainer command.

When in doubt, run:

```
meridian explain-last-error <project>
```

That command reads the most recent error from the structured log, redacts secrets, asks the LLM for a plain-English diagnosis + suggested next steps, and writes a local crash dossier you can later send to support (see [security.md](security.md)).

## Anthropic API errors

### Symptom: `Anthropic credit balance too low` or `insufficient_quota`

Your account has run out of credit. Top up at [console.anthropic.com](https://console.anthropic.com) under Plans & Billing. Keys remain valid; only the balance changed.

- **Log event to grep:** `llm.call.error` with `provider: anthropic` and a `status_code: 400` containing `credit_balance`.
- **Explainer:** `meridian explain-last-error <project>`.

### Symptom: `429 Too Many Requests` / rate-limit error

Anthropic enforces per-organisation rate limits. Wait a minute and re-run `meridian extract`. Extraction is resumable at the chunk boundary — you do not lose work.

If the limit is consistently a problem on your plan, route the cheap-and-volume-heavy `triage` purpose to a local Ollama model:

```
meridian routing apply <project> --preset hybrid
```

- **Log event to grep:** `llm.call.error` with `status_code: 429`.

### Symptom: `401 Unauthorized` / `invalid_api_key`

The `ANTHROPIC_API_KEY` environment variable is missing, malformed, or revoked. The key starts with `sk-ant-` and is roughly 100 characters long. Confirm it's set in the shell that's running Meridian:

```
# PowerShell
$env:ANTHROPIC_API_KEY

# bash / zsh
echo $ANTHROPIC_API_KEY
```

If it's blank, re-export it. If you copied it from email or a password manager and it includes a leading or trailing space, trim it.

- **Log event to grep:** `llm.call.error` with `status_code: 401`.

## Project DB locked

### Symptom: `database is locked` / `OperationalError`

Two Meridian processes are writing to the same SQLite file at once. Common cause: an extraction job is still running while you try to also drive a review walk in another terminal. SQLite write-locks are transient and resolve themselves once the writer commits, but a hung process can hold the lock indefinitely.

As of round-15, every connection now sets a `PRAGMA busy_timeout` so the engine waits before raising — short reviewer/CLI writes wait up to **5 seconds** for a competing writer to commit, and long-running writers (extraction worker, ingest, reviewer mutations served via the API) wait up to **30 seconds**. You should now only see `database is locked` if the contention truly outlasts those windows (i.e. another process is hung or the disk is pathologically slow), not on every momentary collision.

Resolution:

1. Run `meridian status <project>` — if it returns instantly, the lock is gone and the failing command is safe to retry.
2. Check whether there's an extraction job stuck in `extracting` state. Pause it (`meridian pause <project>`) and resume; the chunk-level resume will recover any partial work.
3. As a last resort, close every Meridian process and re-run the command. The transactional finalisation pattern (introduced in alpha-11) means partial extraction state rolls back rather than leaving torn writes.

- **Log event to grep:** `db.error` with `error_code: SQLITE_BUSY` or `SQLITE_LOCKED`.
- **Explainer:** `meridian explain-last-error <project>`.

## Schema migration warnings

### Symptom: `schema version mismatch — project at v3, code expects v5`

Your project SQLite was created on an older Meridian version. Schema upgrades are opt-in — they don't run automatically in case you want to keep the project at its existing version for reproducibility. Run:

```
meridian db-migrate <project>
```

The migration is idempotent (safe to re-run). Backfill is applied automatically — see [release-notes.md](release-notes.md) under alpha-10 and alpha-11 for what each migration does.

If the migration itself errors, **stop and back up the SQLite file before retrying** — copy `<projects-dir>/<slug>.sqlite` somewhere safe. Then run with `--verbose` (if available) and send the dossier via `meridian explain-last-error`.

- **Log event to grep:** `db.migrate.start` and `db.migrate.error`.

## Bootstrap sweep finds nothing

### Symptom: Bootstrap proposal returns zero document classes, taxonomies, or authority entries

Two likely causes:

1. **Corpus is too small.** The default sample size is 15 documents; if you have fewer, the LLM may not see enough variety to propose anything. Re-run with the entire corpus included:
   ```
   meridian bootstrap <project> --sample-size 50
   ```
   Cap is the corpus size, so a too-large value is safe.

2. **Wrong document classes for the heuristics.** If the corpus is methodology PDFs, marketing decks, or unfilled templates, the bootstrap won't propose construction-document classes — there aren't any. Add a real spec or BOD and re-run.

If you still get nothing, dump the latest bootstrap proposal and inspect what the LLM saw:

```
meridian bootstrap-show <project>
```

- **Log event to grep:** `bootstrap.run.complete` with `proposals_count: 0`.

## Tender package has empty rows or missing trades

### Symptom: `meridian tender list` shows `0 deliverables` for a trade you expected to see

Tender packages are built from the **accepted-deliverables register only**. Quarantined and rejected rows are excluded by design. If a trade is missing or under-populated:

1. **Check the queues.** `meridian review-status <project>` shows how many quarantined items are pending. Walk them (`meridian review walk-quarantine <project>`) so accepted ones land on the master.
2. **Check for taxonomy quarantine.** When the LLM proposed a taxonomy value (a new trade or service) that hasn't been confirmed yet, all deliverables tagged with that value sit outside the master register until you confirm via `meridian review walk-taxonomy <project>`. This is a common source of "where did my Mechanical rows go?" — they may all be tagged with an unconfirmed proposal.
3. **Check for case-sensitivity drift.** Any LLM-proposed taxonomy value that doesn't case-match the canonical vocabulary auto-quarantines. The fix is the same: walk taxonomy and confirm or merge.

- **Log event to grep:** `tender.build.start` followed by `tender.build.complete` — the latter records the deliverable count.

## Review queue noise

### Symptom: Cross-reference sweep questions queue is full of garbage equipment-tag matches

The cross-reference sweep was overhauled in alpha-11 to reduce noise by 86%. If you are seeing a noisy queue, you may be on an older sweep result. Re-run:

```
meridian xref sweep <project> --dry-run
```

Inspect the four-outcome breakdown (confirmed / borderline / external_reference / rejected). Only borderlines enter the questions queue when the sweep is persisted.

If the borderline count is still high (more than ~20% of total findings), file a follow-up — the regex tightening is corpus-tuned and may need another pattern added for your document set. (`xref sweep` defaults to `--commit`; pass `--dry-run` for the preview-only mode shown above.)

- **Log event to grep:** `xref.sweep.complete` with the four-outcome counts.

## Web shell won't start

### Symptom: `npm: command not found` when trying to run the web UI

Node.js is not installed on this machine. The Meridian Python backend works without it; only the Next.js review UI requires Node. Install Node 20 or newer (Windows: `winget install OpenJS.NodeJS.LTS`), then:

```
cd apps/web
npm install
npm run dev
```

In a separate terminal, start the API: `uvicorn meridian.api.main:app --reload --port 8000`. Then browse to `http://localhost:3000`.

If you'd rather drive everything from the CLI, the web UI is optional. All review queues have a `meridian review walk-*` equivalent.

### Symptom: `EADDRINUSE: port 3000 is already in use`

Another process is on port 3000 (often a stale `npm run dev`). Kill the stray process or run on a different port:

```
PORT=3010 npm run dev
```

Likewise, if `port 8000 is already in use`, pick a different uvicorn port (`--port 8010`) and update the web shell's API base URL.

## Restore backup fails

### Symptom: `schema version mismatch` after copying a backed-up `.sqlite` file in

The backup was created against an older schema version. Migrate it to current:

```
meridian db-migrate <project>
```

Schema migrations are forward-only (no rollback). If you need to view the old data structure, keep the original backup elsewhere and migrate a copy.

If the backup zip is from a Legal Evidence Pack (`<slug>.evidence/`), use `meridian evidence verify <pack.zip>` first to confirm the SHA-256 hashes match the manifest. A failed verify means the zip is corrupt or has been tampered with — not a schema problem.

- **Log event to grep:** `db.migrate.error` for migration; `evidence.verify.fail` for pack integrity.

## License or update commands say "not configured"

These features are scaffolded but waiting on a deployment decision (the signing key for licenses, the manifest URL for updates, the endpoint URL for crash reports). The local code paths are implemented and tested; the network surface is deliberately disabled until configuration lands.

- See [cli-reference.md](cli-reference.md) for which commands are flagged "Status: scaffolded".
- See [release-notes.md](release-notes.md) under alpha-12 for the decision points still open.

There's no workaround you can do locally for these — wait for the deployment decision.

## Generic recovery commands

| Command | When to use it |
|---|---|
| `meridian explain-last-error <project>` | First thing after any error. Plain-English diagnosis + next steps. |
| `meridian status <project>` | Quick sanity check — sources, deliverables, queue counts. |
| `meridian review-status <project>` | Full baseline-trustworthiness dashboard. |
| `meridian db-migrate <project>` | After schema mismatch warnings. Idempotent. |
| `meridian crash list` | List local crash dossiers (these accumulate; safe to leave). |
| `meridian crash preview <id>` | See what a dossier would send before you send it. |

## When all else fails

1. Run `meridian explain-last-error <project>` and read the LLM's suggested next step.
2. If the suggested step doesn't apply, run `meridian crash preview <id>` on the most recent dossier — secrets are redacted and you can read what the tool saw.
3. The structured logs at `<projects-dir>/<slug>.logs/` are JSONL — open one in any editor that handles long lines and grep for the timestamp around the error.
4. The pytest suite at `tests/e2e/` exercises every major code path — if your bug reproduces against the test fixtures, that's a high-signal report.

The audit trail in SQLite plus the JSONL logs together capture every reviewer action and every LLM call. You can always reconstruct what happened.
