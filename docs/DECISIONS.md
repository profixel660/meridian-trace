# Meridian — decision log

This file records the resolution of each deferred-decision item from `data/projects/OVERNIGHT_REPORT.md` §3, plus follow-on tasks created by those resolutions.

**Walkthrough date:** 2026-04-27
**Codebase state at decision time:** rounds 7–14 complete, schema v5, 39/39 e2e tests passing.

---

## §3.1 — TOTP secret-storage backend → **B (OS keychain)**

**Decision:** OS keychain via the `keyring` package. `KeyringStore` is now the v1 default, with file-store fallback if the keyring backend is genuinely unavailable.

**Why:** the projects directory lives under OneDrive. Plaintext JSON in OneDrive is a leak surface (synced devices, server-side revisions, anyone with folder access). OS keychain (Windows Credential Manager / macOS Keychain / Linux Secret Service) keeps the secret off OneDrive entirely.

**Implemented:** [src/meridian/auth/secrets.py](../src/meridian/auth/secrets.py) — `KeyringStore` class implemented (was a stub); `default_store()` flipped. `keyring>=25` added to `pyproject.toml` as a hard dependency. **Live verified:** Windows Credential Manager round-trip with `WinVaultKeyring` backend.

**Status:** shipped this session.

---

## §3.2 — API auth enforcement → **B (POST-only)**

**Decision:** enforce TOTP bearer auth on all POST routes; leave GET routes open. Built the matching Next.js login flow so the web shell stays usable.

**Why:** for a single-user desktop tool on localhost, global enforcement is mostly theatre, but POST-only prevents an unauthenticated browser tab from mutating state if the FastAPI is ever exposed beyond `127.0.0.1`. Belt-and-braces; doesn't break local dev / observability tools that read state.

**Implemented:**
- Backend: `dependencies=[Depends(require_session)]` added to 17 POST routes across `api/main.py`, `tender/api.py`, `evidence/api.py`, `cross_references_api.py`. Three POST routes left open with documented reasons: `/auth/login` (chicken-and-egg), `/auth/logout` (must accept stale tokens), `POST /projects` (first-run bootstrap).
- Frontend (Next.js): 6 new files — `lib/auth.ts`, `lib/fetcher.ts`, `app/login/page.tsx` + `LoginForm.tsx`, `components/AuthIndicator.tsx`, `components/AuthGate.tsx`. Token in localStorage; `apiFetch` wrapper injects Bearer; 401 → redirect to `/login?from=<current>`; AuthGate guards the dashboard.

**Status:** shipped this session. Web build verified clean (§3.9).

**Round-17 follow-up:** the new `/setup/*` wizard endpoints (round 17) are intentionally public — no `Depends(require_session)`. Chicken-and-egg: the wizard is what sets up auth, so it can't itself require auth. Marked in source with `# DEFERRED §3.2`. Revisit alongside the team-edition / multi-tenant decision (likely "first user enrols TOTP at the end of the wizard, subsequent admin actions require it"). See round-17 follow-up bullet at the bottom of this file.

---

## §3.3 — POST /auth/login endpoint → **already shipped (round 12)**

**Status:** no decision needed. Endpoint exists at `/auth/login` with rate limiting (10 attempts / 5 min per IP), constant-time error response, never logs the TOTP code. Used by §3.2 frontend.

---

## §3.4 — Code-signing certificates → **C-then-A**

**Decision:**
- **C:** ship unsigned for the next 1–2 months of internal/alpha testing. PMs trying to install will see a "Microsoft Defender SmartScreen prevented an unrecognised app from starting" warning and need to click through.
- **A (when commercial):** standard code-signing cert (~AUD $400–700/year from Sectigo, DigiCert, etc.). No hardware token required.
- **Upgrade to EV (B) only if** measured PM bounce rate from the SmartScreen warning is unacceptable.

**Why:** for the alpha-tester audience (you + a handful of friendly SMEs) the warning is acceptable. For commercial v1, a normal cert removes the friction at moderate annual cost. EV is overkill until volume justifies it.

**Action for you:** budget ~AUD $500/year for signing as a fixed cost when you start commercial distribution. No code change today.

---

## §3.5 — Auto-update endpoint URL → **A (GitHub Releases) — RESOLVED**

**Decision:** GitHub Releases on `profixel660/meridian-trace`.

**URL:** `https://github.com/profixel660/meridian-trace/releases/latest/download/manifest.json`

**Why:** zero infra, zero cost, durable, well-known. Until the first release is tagged, GitHub returns 404 and `check_for_updates` cleanly returns None.

**Implemented:** [src/meridian/updates/client.py](../src/meridian/updates/client.py) — `_PLACEHOLDER_MANIFEST_URL` constant replaced with `_DEFAULT_MANIFEST_URL` pointing at the real repo. Old name aliased for backwards compatibility. Tag marker swapped from `# DEFERRED §3.5` to `# §3.5 RESOLVED 2026-04-27`.

**Status:** shipped this session. Migration to a custom-domain CDN later is one constant + a redirect.

---

## §3.6 — Crash report endpoint URL → **C (email-via-serverless), placeholder for now**

**Decision:** direct email to support@undivided.systems via a tiny serverless SMTP function for v1. **Move to (B) Sentry** once volume justifies it (~5+ reports/week — when manual triage stops scaling).

**Why:** crash reports will be rare given the LLM-assisted-explanation gate already in place. Email keeps everything in one inbox you already check.

**Action for you:** swap the placeholder constant in [src/meridian/crash/sender.py](../src/meridian/crash/sender.py) (tagged `# DEFERRED §3.6`) when you set up the endpoint. The `send_crash_report` function refuses to POST to the placeholder URL today.

**Status:** no code change today; placeholder remains active and refuses to send.

---

## §3.7 — Installer technology → **D-static via Tauri**

**Decision:** Tauri (Rust-based) wrapping a statically-exported Next.js shell.

**Why:**
- **Tauri over Electron:** ~10 MB bundles vs ~150 MB; faster startup; lower memory footprint. PMs run Revit / Bluebeam / Outlook in parallel — battery and RAM matter.
- **D-static over D-sidecar:** smallest possible install; no Node runtime in the bundle. Requires refactoring the round-11 Next.js pages from server components (`await params`, server-side data fetching) to client-only rendering. ~200 LOC refactor. Worth doing while the round-11 pages are still fresh.

**Action for you:** install the Rust toolchain on your dev machine when you're ready to start building installers. Then we scaffold the Tauri config + the static refactor in one round.

**Status:** scaffold landed in round 16 (file generation only — no Rust/MSVC install yet). `src-tauri/` directory ready; Next.js shell now configured for static export (`output: "export"`); `tauri:dev` / `tauri:build` scripts wired into `apps/web/package.json`. Round 17 will land the `/setup` wizard pages; round 18 installs Rust + MSVC and produces the first `.msi`. See `data/projects/OVERNIGHT_REPORT.md` round-16 section for detail.

---

## §3.8 — License signing key generation → **A (password manager + offline USB backup)**

**Decision:** generate the Ed25519 keypair once; store private key in your password manager (1Password / Bitwarden); back up to an encrypted USB stored offline. **Migrate to (B) YubiKey once you have ≥10 paying customers.**

**Why:** Ed25519 is a single 32-byte key — fits trivially in a password manager. The threat model ("if someone steals the private key they can mint pirate licences") is real but bounded. Hardware-token (~AUD $80) and Cloud KMS (~AUD $5/month) are both overkill for one-person licence issuance at v1 scale.

**Action for you:** generate the keypair; embed the public key in [src/meridian/licensing/verify.py](../src/meridian/licensing/verify.py) (tagged `# DEFERRED §3.8`). The verify path is already built and tested.

**Status:** no code change today.

---

## §3.9 — Web shell first build → **clean (verified)**

**Decision:** install Node + run the first-ever `npm run build`.

**Why:** round-11 added 3 new pages (tender / evidence / xref) and §3.2 added 6 more files (login flow, auth lib, fetcher, indicator, gate). All TypeScript that had been written but never compiled.

**Implemented:**
- Installed Node 24.15 LTS via `winget install OpenJS.NodeJS.LTS` (required UAC).
- `npm install` in `apps/web/` — 342 packages, 21s, no peer-dep conflicts.
- `npm run build` — clean after fixing 2 trivial errors:
  - Tailwind oxide version mismatch (aligned `@tailwindcss/postcss` and `tailwindcss` to `4.2.4`)
  - Next.js 15 page-export constraint (`app/onboarding/data-handling/page.tsx` had a non-default named export; extracted to `components/DataHandlingBody.tsx`)
- **Round-11 + §3.2 TypeScript code type-checked cleanly on first try.** No null-check, async-params, server/client-boundary, or apiClient field issues.
- Final: 21 routes built (7 static, 14 dynamic), shared first-load 105 kB. All round-11 + login routes ~115 kB each.

**Action for you:** `cd apps/web && /c/Program\ Files/nodejs/npm.cmd run dev` (or use `npm run dev` once your shell PATH refreshes) to drive the UI in a browser at `http://localhost:3000`. Backend at `uvicorn meridian.api.main:app --reload --port 8000` in another terminal.

**Status:** shipped this session.

**Follow-up (small):** ESLint config wasn't scaffolded — first `npm run lint` is interactive (prompts for Strict/Base/Cancel). Worth scaffolding non-interactively for CI in a future round.

---

## §3.10 — Service/'Chiller System' taxonomy → **confirmed canonical for syd2-shell-cd; future projects get auto-assessment**

**Decision:**
- **For syd2-shell-cd**: `Chiller System` confirmed as canonical. 143 deliverables stay as-is. Confirmation persisted at `2026-04-27T00:02:47Z`.
- **For future projects**: build auto-assessment into the bootstrap LLM sweep — for each proposed taxonomy value, the LLM evaluates whether the project's corpus justifies a separate entry, or recommends merging into a parent (e.g. `Chiller System` → `HVAC` if no significant chiller-specific content).

**Why:** data centre / mission-critical projects with substantial chiller infrastructure earn `Chiller System` as a service. General construction projects with passing chiller mentions don't — they should inherit `HVAC`. The classification call is corpus-shaped; the LLM is best positioned to make it once it has scanned the corpus.

**Implemented (now):** `meridian review confirm-taxonomy syd2-shell-cd --table service --value "Chiller System"` — done.

**Follow-up (delivered as round 15):** smart taxonomy auto-assessment shipped end-to-end. Schema v5 → v6, four new columns per taxonomy table (`llm_recommended_action`, `llm_merge_target`, `llm_confidence`, `llm_reasoning`), bootstrap prompt v1.0 → v1.1, persist policy with 0.85 confidence threshold, CLI walk-taxonomy + API GET /taxonomy/pending + Next.js TaxonomyQueue all updated to surface the recommendation. 7 new e2e tests. See round-15 section of `data/projects/OVERNIGHT_REPORT.md` for full detail.

**Status:** this-project decision shipped (Chiller System confirmed canonical); future-project feature shipped this session as round 15.

---

## Decision-walk-through follow-ups (not on the original §3 list)

These surfaced during the walkthrough and deserve their own backlog entries:

- **`apps/web/` ESLint config scaffolding** — first `npm run lint` is interactive; needs non-interactive setup for CI and for round-trip lint passes. Small.
- **§3.10 auto-taxonomy-assessment** — scoped above; recommend round 15.
- **Tauri scaffold + Next.js static-export refactor** — large. Becomes the path to a real installer once you're ready (see §3.7).
- **POST /projects auth tightening for v2** — left open in §3.2 for first-run bootstrap; revisit when there's a "team edition" or hosted multi-tenant model.
- **YubiKey migration plan** — stub it when you cross 10 paying customers.
- **Round-16 Tauri scaffold** — `src-tauri/` directory exists; `cargo build` will fail today because Rust isn't installed and the icon set is intentionally absent (see `src-tauri/icons/README.md`). Round 18 installs the Rust toolchain + MSVC Build Tools 2022 + WiX Toolset and produces the first signed-or-unsigned `.msi`. Sidecar spawn of the bundled FastAPI backend lands round 17 alongside the wizard pages.
- **v0.1.3 — installer auth for private repos.** v0.1.2's installer hits HTTP 404 when calling `https://api.github.com/repos/profixel660/meridian-trace/releases/latest` because the repo is private and the installer is anonymous (carries no GitHub credentials, even though the SME-as-a-human IS a collaborator). Confirmed via `curl` against both `/releases/latest` and the repo root — both return 404 to anonymous callers. **Recommended fix (Path A, ~30 min subagent work):** installer prompts the SME for a fine-scoped PAT (Contents: Read + Metadata: Read on `meridian-trace` only, 90-day expiry), validates it via a test API call, stores it in Windows Credential Manager (same backend as the §3.1 TOTP secret), uses it as `Authorization: Bearer <PAT>` on every GitHub API call. README needs a screenshotted walkthrough of how to generate the PAT. **Interim workarounds for SME testing this week:** (a) flip repo to Public temporarily for the test window (10s setting change; `releases/latest` becomes anonymously fetchable); (b) Peter generates a PAT on his own account and shares it with the SME alongside the Anthropic key (less hygienic — ties her install to Peter's credentials). Looping back to v0.1.3 once the SME's first-pass test is unblocked.

---

## Round 17 follow-ups

These surfaced during round 17 (`/setup` wizard + Tauri sidecar wiring + backend wizard API). Each is a deliberate deferral, not an oversight — capturing here so the next sweep doesn't have to re-derive them.

- **Round-17 wizard pre-auth posture** — the `/setup/*` endpoints (six new in `src/meridian/wizard/api.py`) are intentionally public. No `Depends(require_session)`. Chicken-and-egg: the wizard sets up auth, so the wizard itself can't require auth. Marked in source with `# DEFERRED §3.2`. When team-edition / multi-tenant landing is on the table, revisit per §3.2 and tighten — likely an "admin TOTP" model where the first user enrols TOTP at the end of the wizard and subsequent admin actions require it (the wizard's `/setup/complete` step becomes the natural enrolment hook). Until then, the public posture is correct. Tracked.

- **Round-17 sidecar dev-mode fallback** — Tauri currently falls back to `python -m uvicorn meridian.api.main:app` when no bundled PyInstaller binary is present at `src-tauri/binaries/meridian-server-x86_64-pc-windows-msvc.exe`. Round 18's first task post-Rust-install is replacing this fallback path with the real `meridian-server.exe` — or, more accurately, dropping the binary at the expected location so the existing `tauri_plugin_shell::ShellExt::shell().sidecar(...)` lookup picks it up automatically (no Rust code change required, just file placement). The fallback **can** remain as a dev-mode escape hatch — useful for "I want the desktop UI without rebuilding the sidecar after every Python change". Decide round 18 whether to keep it (gated on `#[cfg(debug_assertions)]`) or rip out entirely. Lean is keep-as-debug-only.

- **Round-17 wizard state-file compat** — the GUI wizard reads/writes the same `_meridian/onboarding_state.json` the existing CLI wizard (`src/meridian/onboarding/wizard.py`) uses. Round 17 added GUI-only fields (`documents_skipped`, `wizard_completed_at_iso`); older CLI-only state files are forward-compatible (missing fields default sensibly), and new state files written by GUI are consumable by CLI. **Do not introduce divergent state file formats.** The user has explicitly committed to the cross-surface compat invariant — a CLI-started → GUI-finished flow (or vice-versa) must work. If a future round needs a structurally-incompatible field, bump a `schema_version` discriminator in the state file and migrate both readers in lockstep.

- **Round-17 wizard skip semantics** — only the `first-documents` step is skippable (via `POST /setup/import/skip`). API key and first-project are hard-gated — the wizard cannot complete without them. Rationale: a Meridian install with no API key and no project is a non-functional install; better to fail the wizard loudly than ship a half-set-up app that breaks on first real use. If round-19's SME re-test surfaces friction here (e.g. SME wants to "look around" before committing an API key), revisit — likely shape is a "demo-mode" flag that lets the wizard skip the api-key step but parks the install in a read-only state until configured.

- **Round-17 e2e test count — soft.** The round-17 final-state line claims ~62/62 e2e tests passing (52 round-16 baseline + ~10 new). Stream C's actual delivered count may differ; the integration step should reconcile against `pytest -q tests/e2e/ | tail -1` and update the round-17 section if the count diverges. Not a defect — just a soft number until verified.

- **TODO:** placeholder for any contract surprise from Stream C that this docs stream couldn't anticipate. Fill in during the round-17 integration step.

---

## Summary

| § | Decision | Implemented today | Awaits external action |
|---|---|---|---|
| 3.1 | OS keychain | Yes | — |
| 3.2 | POST-only | Yes (backend + frontend) | — |
| 3.3 | (already shipped) | — | — |
| 3.4 | Unsigned then standard cert | — | Buy cert when commercial |
| 3.5 | GitHub Releases (profixel660/meridian-trace) | Yes | — |
| 3.6 | Email-via-serverless | — | Set up endpoint + swap constant |
| 3.7 | Tauri + D-static | Scaffold (round 16) | Round 18 — Rust + MSVC install |
| 3.8 | Password manager + USB | — | Generate keypair + embed pubkey |
| 3.9 | Web build | Yes | — |
| 3.10 | Confirm canonical (this project); auto-assess (future) | Both shipped | — |

Net of session: **6 of 10 decisions implemented end-to-end** (§3.1 keychain, §3.2 POST-only auth + login UI, §3.5 update URL swapped, §3.9 web build verified, §3.10-both-halves: confirm + round-15 auto-assessment); 3 deferred awaiting external action (§3.4 cert purchase, §3.6 endpoint setup, §3.8 keypair generation); 1 already shipped before walkthrough (§3.3); 1 partially complete (§3.7 scaffold landed round 16; Rust install + .msi build awaits round 18).

**Round 15 also delivered** (driven by §3.10): 7 new e2e tests, schema v6, bootstrap prompt v1.1, full surface update across CLI / API / Next.js TaxonomyQueue.
