# Handover — alpha-25 (Meridian-Trace)

## TL;DR

Pick up Meridian-Trace where alpha-24 left off (shipped 2026-05-09 evening). The SME re-ran alpha-24 and confirmed she can land cleanly on the project dashboard for the first time, but immediately hits a button-less wall: 4 imported sources, 0 extracted, no GUI affordance to advance the pipeline. **That wall is the alpha-25 keystone (item #3 from the 02/05 punch list — auto-trigger bootstrap+extract from the wizard).** Today's job is to take it down, plus a small warm-up (the master-register `conflict_summary` column).

## Read these first

1. **Project memory** (canonical state, sequencing, open questions): `~/.claude/projects/c--Windows-System32/memory/project_meridian_sme_testing.md` — the user has multiple memory namespaces; Meridian's is in `c--Windows-System32`, not the harness default. Reference memory in this namespace points to it.
2. **Repo** at `C:\Users\PeterRoberts\OneDrive - Undivided Systems\Documents\Project_requirements_tester` (Windows path with spaces — quote in shell calls). Branch: `main`.
3. **Recent specs/plans** in `docs/superpowers/specs/` and `docs/superpowers/plans/` — the alpha-24 design + plan are useful examples of the project's preferred shape.
4. **Release notes** at `docs/release-notes.md` — alpha-24 entry is the latest.

## Where alpha-24 left things

- `v0.2.0-alpha.24` shipped to `origin/main` and tagged. Release: https://github.com/profixel660/meridian-trace/releases/tag/v0.2.0-alpha.24
- Closed punch-list item #4 (frontend double-submit) with three layers (phase guard + ConfirmDialog busy plumbing + server-side `Idempotency-Key` dedupe with atomic `_idempotency_claim`).
- Full e2e: 179 passed / 1 skipped. Gauntlet 16 steps green incl new step 7i.
- The SME tested alpha-24 the same evening — invisible to her by design (alpha-23 already neutered the user-visible damage), but she now reaches the dashboard cleanly and hits the keystone wall. Screenshots in `~\Pictures\Screenshots\20260509 M-TRACE GUI STILL NO BETTER*.png`.

## Alpha-25 scope (proposed — confirm with Peter at session start)

**Sequenced inside one alpha:**

1. **Keystone — auto-trigger bootstrap + extract from the wizard** (the heaviest item; unblocks ~6 downstream dashboard items)
2. **`conflict_summary` column** on the master-register Excel export (cheap warm-up; ~30 lines; pure wiring)
3. *Maybe:* `BaselineBanner` suppression on empty-of-deliverables projects (currently fires "NEEDS REVIEW: 0/0 blockers" — misleading)
4. *Maybe:* Top "Project" button restarts setup instead of opening the dashboard (02/05 carryover)

Items 3–4 may bundle naturally with the keystone since they touch the same dashboard component. Items in the punch list beyond these defer to alpha-26+.

## Keystone — open design questions to settle BEFORE coding

The brainstorming skill must lead. Two questions explicitly flagged in project memory plus a third Peter leans on:

1. **Block-vs-background:** does the wizard's import-folder step BLOCK until extraction completes, or does it kick off extraction + return + the dashboard polls? Tradeoff: blocking gives a clear "you're done" moment but a long progress window inside the wizard; background keeps the wizard tight but moves progress UX to the dashboard.
2. **Where to surface in-flight extraction progress:** dashboard tile? a new "Jobs" page? a toast? Memory leans dashboard tile — confirm.
3. **Strict vs permissive on Quarantine taxonomy add-new:** Peter leans permissive (let the user create + use a new value immediately, reviewer cleans up later). Lock when designing.

## Conventions (don't drift)

- **Commits:** `[scoped] alpha-NN <stream>: <subject>` with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.
- **Spec then plan then implement:** brainstorming → writing-plans → subagent-driven-development → finishing-a-development-branch. Don't skip skills. Don't dispatch parallel implementers (file conflicts even when files are disjoint — alpha-24 confirmed serial dispatch with cheap-model-per-mechanical-task is fast enough).
- **Surface LLM reasoning verbatim**, don't paraphrase. Per the feedback memory in this namespace — applies to `most_onerous_reasoning`, `audit.reasoning`, `taxonomy.llm_reasoning`, etc.
- **No new test infra** (vitest etc.) without an explicit grid pass — alpha-10 deferred frontend test infra and that posture stands. Backend e2e via `pytest` + FastAPI `TestClient` + the existing `mock_llm_client` fixture is the gate.

## Tooling

- **Tests:** `python -m pytest tests/e2e/ --ignore=tests/e2e/test_concurrency.py`
- **Frontend rebuild:** `cd apps/web && npm run build` (Node IS installed on this machine — alpha-24 confirmed)
- **Wheel build:** `uv build --wheel` (NOT default `uv build` — the sdist→wheel chain trips on gitignored `apps/web/out/`; known release-process gap, post-keystone cleanup)
- **Release gauntlet:** `python scripts/release_gauntlet.py` (use system python, not `uv run` — the latter fights the active venv on dist-info removal)
- **Project DBs to query:**
  - `C:\Meridian\projects\bod.sqlite` — SME's current project (4 sources, 0 extractions — the exact wall)
  - `<repo>/data/projects/syd2-shell-cd.sqlite` — older project with 323 deliverables + 11 conflicts; useful for the `conflict_summary` work

## Constraints

- SME availability is the gating resource; she's reviewing in chunks. Aim to ship alpha-25 today so she gets a meaningful unblock; defer the long tail to alpha-26+.
- Backend `/projects/{name}/extract` endpoint already exists (alpha-12). Same with `/projects/{name}/bootstrap`. Don't reimplement — wire the GUI to them.
- Schema: NONE expected for alpha-25 keystone or `conflict_summary`. If you find yourself needing schema migration, stop and reconsider.
- Locks: `acquire_project_lock` already wraps extraction; the GUI must surface 409 cleanly per the existing `ProjectBusy` pattern (alpha-13).

## Suggested opening move for the fresh session

```
"Read memory at ~/.claude/projects/c--Windows-System32/memory/project_meridian_sme_testing.md
and docs/superpowers/HANDOVER-2026-05-10-alpha25.md. Then invoke
superpowers:brainstorming on the alpha-25 keystone (item #3 — auto-trigger
bootstrap+extract from the wizard). Settle the three open design questions
before proposing scope."
```

That keeps the brainstorming skill in charge (per the bootstrap rule) and gives it a self-contained brief.

## What yesterday taught us (signal worth preserving)

- The `most_onerous_reasoning` field in the `conflict` table is high-value PM-grade output that's been sitting unread since extraction ran. Pure wiring win — Excel emitter just never reached for it. Same posture likely applies to other LLM-text fields. (Saved as feedback memory.)
- One-fix alpha releases (alpha-23, alpha-24) work — clean release boundaries, low ceremony, gauntlet stays honest. Don't be afraid to cut a small alpha-25 if scope creeps.
- The project's hatch config force-includes `apps/web/out/` so `uv build` (default) tries sdist→wheel and fails because `apps/web/out/` is gitignored. `uv build --wheel` works. Worth fixing properly post-keystone but not load-bearing.
