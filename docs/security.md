# Security and data handling

This page describes what Meridian sends, where, and what stays on your machine. It is intentionally short — the answers are simple. Hand it to your IT department if they need to sign off the tool before you install it.

## Headline summary

- Meridian sends document content to **the LLM provider you choose** (Anthropic by default). Nothing about your documents reaches Meridian's developer (Undivided Systems).
- The only network call to Undivided Systems is **license activation**, and even that uses a license file you have already downloaded — there is no install-time phone-home.
- Everything else — the project SQLite, logs, taxonomies, taxonomy proposals, queues, evidence packs — stays on your local disk.
- No telemetry runs without your explicit opt-in.

## The Anthropic API key (and other LLM providers)

The LLM provider key (e.g. `ANTHROPIC_API_KEY`) is read from your shell environment on every invocation. Meridian:

- **Does not write the key to disk by default.** No config file, no SQLite cell, no log line.
- **Does not transmit the key anywhere except to the provider it belongs to.**
- Logs the *fact* that an LLM call was made (provider, model, purpose, token counts, cost), never the key itself.

If you want a per-project key (e.g. one organisation per project), set the environment variable in the shell that runs Meridian for that project. Meridian does not have a built-in key store.

For local-only providers (Ollama, OpenAI-compatible servers), no key is required. See [the provider routing section in cli-reference.md](cli-reference.md#per-purpose-llm-provider-routing).

## TOTP secret (single-user authentication)

Meridian's auth model is single-user, self-enrolled at first launch via `meridian auth enroll`. The TOTP secret (a 160-bit random number) is the credential.

**Current backend (alpha-12 default):**
- Stored as a JSON file at `<projects-dir>/_auth/totp.json`.
- File permissions are restricted to your user account (`chmod 0600` on POSIX, ACL'd on Windows).
- **Risk:** anyone who can read your user account on this machine can read the secret. Acceptable for a developer machine where you are the only user; less acceptable on a shared OneDrive folder.

**Planned backend:**
- OS keychain (Windows Credential Manager / macOS Keychain / Linux Secret Service), via the `keyring` Python package. The `KeyringStore` stub exists in code; enabling it is a small change. See [release-notes.md](release-notes.md) under alpha-12 — this is decision §3.1.
- If your projects directory lives under OneDrive (or any cloud-synced folder), prefer the keychain backend when it ships, or move the projects directory to a local-only path.

**Recovery codes** are also generated at enrolment, hashed at rest, and one-time-use enforced. Save them in your password manager.

## License keys

Meridian licenses are issued by Undivided Systems support and verified locally with an Ed25519 signature. The model:

- The **public key** is embedded in the Meridian binary. Anyone can read it; it cannot mint licenses.
- The **private key** is held by support. Used once per license issued, then put away.
- License files contain: machine fingerprint binding, expiry timestamp, plan + features, and the Ed25519 signature.
- Validation runs locally on every Meridian launch — there is **no network call** to validate.
- Term is six months, then an eight-week grace with escalating reminders, then read-only lockout (open + re-export of prior Excels permitted; no new processing).
- **Tamper detection:** signature check, encrypted last-seen timestamp, file integrity hash, HMAC on SQLite state, fingerprint binding. Any of these failing triggers immediate read-only lockout. Recovery is via re-enrolment with a fresh license — Meridian does not permanently brick legitimate users (e.g. after a motherboard swap).
- There is no revocation endpoint. Expiry is the kill switch.

See `meridian license --help` and [cli-reference.md](cli-reference.md#licensing). The signing-key generation and storage policy is decision §3.8 (still open at the time of writing — see [release-notes.md](release-notes.md) under alpha-12).

## Crash reports

Crash reports are **opt-in** and require explicit preview before send. The flow:

1. When the CLI hits an unhandled error, it writes a local JSON dossier to `<projects-dir>/<slug>.logs/crash-<timestamp>.json`.
2. `meridian explain-last-error <project>` reads the dossier, runs **defensive secret redaction** (matches `sk-` prefixes, `Bearer ` tokens, `aws_access_key_id`-shaped values), asks your LLM for a plain-English diagnosis, and shows you the output.
3. **Send is opt-in**, off by default. To enable: `meridian crash opt-in --enable`. To send a specific dossier: `meridian crash preview <id>` (preview the redacted payload), then `meridian crash send <id>`.
4. The send path **refuses to POST to a placeholder endpoint**. Until the crash endpoint URL is configured (decision §3.6), `send` will tell you so and stop.

You can opt out at any time with `meridian crash opt-in --disable`. Local dossiers continue to be written either way (so `explain-last-error` always works); they're just never transmitted.

## Local data

| Thing | Where it lives | Sensitive? |
|---|---|---|
| Project SQLite | `<projects-dir>/<slug>.sqlite` | Yes — contains source-document text, deliverables, audit, LLM call records (prompt + response). Treat like the source documents themselves. |
| Logs | `<projects-dir>/<slug>.logs/meridian-YYYYMMDD.log` | Yes — JSONL events including LLM call metadata (token counts, cost, model). **Not** the API key, **not** the prompt or response bodies. |
| Tender packages | `<projects-dir>/<slug>.tenders/` | Yes — they contain accepted deliverables and source filenames. |
| Evidence packs | `<projects-dir>/<slug>.evidence/<pack-name>.zip` | Yes — contain everything the project SQLite contains, plus the prompts used and the embedded source documents. Treat as confidential project documentation. |
| Cross-reference reports | `<projects-dir>/<slug>.reports/xref/` | Yes — list deliverables and their cross-references. |
| TOTP secret | `<projects-dir>/_auth/totp.json` | Yes — see above. |
| Crash dossiers | `<projects-dir>/<slug>.logs/crash-*.json` | Yes — pre-redaction. The send path runs another redaction pass before transmission. |

There is **no telemetry without opt-in.** No anonymous usage stats, no error reports, no "phone home" calls. Meridian's only outbound connections are:

1. Your chosen LLM provider (e.g. `api.anthropic.com`).
2. The update manifest endpoint (when configured — decision §3.5), polled only when you run `meridian updates check` manually.
3. The crash endpoint (when configured and opted-in), called only when you run `meridian crash send`.

## Air-gapped mode

For projects where document content cannot leave the local network, use:

```
meridian routing air-gap-on <project>
```

This blocks any cloud-targeted LLM call at preflight — extraction will refuse to start until every purpose is routed to a local provider (Ollama or an OpenAI-compatible local server). Pair with the `air-gapped` routing preset:

```
meridian routing apply <project> --preset air-gapped
```

Verify the result with `meridian routing show <project>` before importing sensitive documents.

Air-gap mode is per-project. You can have one air-gapped project and one cloud-default project on the same machine.

## What to tell your IT department

The honest framing: Meridian is a desktop tool that reads your project documents, sends document content to an LLM provider you choose (with the LLM provider's standard policies applying — links provided in the in-app onboarding), assembles the AI's output into a local SQLite database, and exports Excel registers. Nothing about your documents reaches Meridian's developer.

A one-page PDF version of the data-handling story is generated by the in-app onboarding flow under Help → Data & AI. Hand that to compliance.

If your IT team has additional questions, the [architecture.md](architecture.md) doc covers the per-project SQLite model, the pipeline stages, and the extension points in more depth.
