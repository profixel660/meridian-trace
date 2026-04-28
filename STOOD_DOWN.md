# Stood-down inventory (alpha-15 → alpha-17 strip)

Canonical record of what's been hidden from the runtime / UI surface
during the v0.2 product-validation push, and exactly how to restore each
when the time comes. **Nothing in this file has been deleted from the
repo.** Every item is a single-edit revert.

The principle: ship the minimum surface that lets the user validate the
core deliverables-extraction loop (documents → register → Excel). Bloat
+ premature security gates were eating alpha cycles and not getting the
user closer to "is the product real?". Re-add when the core works AND
there's a concrete operator request for the surface.

---

## 1. Auth / TOTP enforcement (alpha-15 backend; alpha-17 frontend)

**State:** disabled wholesale. No route returns 401. No client-side
redirect to `/login`. No "Sign in" link in the header.

**Code preserved (still in repo, importable, tested):**
- `src/meridian/auth/fastapi_dep.py` — `require_session` dependency
- `src/meridian/auth/login_api.py` — `POST /auth/login`, `POST /auth/logout`
- `src/meridian/auth/totp.py` — TOTP secret + verification
- `src/meridian/auth/recovery.py` — recovery codes
- `src/meridian/auth/session.py` — session token issue/verify/revoke
- `src/meridian/auth/secrets.py` — keyring abstraction
- `src/meridian/auth/qr.py` — QR-code rendering for `meridian auth enroll`
- `apps/web/src/app/login/` — `/login` page + `LoginForm`
- `apps/web/src/components/AuthGate.tsx` — kept as no-op stub
- `apps/web/src/components/AuthIndicator.tsx` — kept; only the "Sign in"
  branch returns null instead of rendering

**To restore:**
1. `src/meridian/api/main.py` — re-add `dependencies=[Depends(require_session)]`
   to the 14 mutating endpoints (POST/PUT/DELETE on projects, ingest,
   extract, accept, export, etc). Restore the `from meridian.auth.fastapi_dep
   import require_session` import. Search for "alpha-15" comments to
   locate the strip points.
2. `apps/web/src/components/AuthGate.tsx` — revert to alpha-14 form
   (active `useEffect` + redirect to `loginUrlForCurrentPath()`).
3. `apps/web/src/components/AuthIndicator.tsx` — revert the `if (!hasToken)`
   branch from `return null` back to the `<a href="/login">Sign in</a>` JSX.

**Trigger to restore:** v0.3 readiness checkpoint — when there's a
product worth protecting AND the operator deploys outside localhost.

---

## 2. Header navigation (alpha-16)

**State:** stripped to two items — `Projects` link + `AuthIndicator` (which
itself renders nothing while auth is off).

**Hidden but still routable by direct URL:**
- `/onboarding` (and `/onboarding/why-frontier-ai`, `/onboarding/data-handling`,
  `/onboarding/recommended-setup`)
- `/glossary`
- `/help/data-and-ai`
- `/health`

**To restore:** `apps/web/src/app/layout.tsx`. Revert the alpha-16 edit
that removed the four `<Link>` elements from the `<nav>`. The pages
themselves are untouched.

---

## 3. Project dashboard — review queues + hand-off tools (alpha-16)

**State:** the project dashboard at `/projects/[name]` shows ONLY the
Quarantine queue card. The other four review queues and three hand-off
tools are hidden.

**Hidden but the routes + backend endpoints exist:**

| Route | Backend endpoint | Module |
|-------|-----------------|--------|
| `/projects/[name]/audit` | `GET /projects/{name}/audit` | `meridian.review.audit` |
| `/projects/[name]/questions` | `GET /projects/{name}/questions` | `meridian.review.questions` |
| `/projects/[name]/conflicts` | `GET /projects/{name}/conflicts` | `meridian.review.conflicts` + `meridian.analytics.conflict_register` |
| `/projects/[name]/taxonomy` | (review-route TBD) | `meridian.review.taxonomy` + `meridian.taxonomy.*` |
| `/projects/[name]/tender` | `meridian.tender.api` router | `meridian.tender.*` |
| `/projects/[name]/evidence` | `meridian.evidence.api` router | `meridian.evidence.*` |
| `/projects/[name]/xref` | `meridian.extract.cross_references_api` router | `meridian.extract.cross_references*` |

**To restore:**
1. `apps/web/src/app/projects/[name]/page.tsx` — re-add the four
   `QueueCard` blocks (audit / questions / conflicts / taxonomy) and
   the three `ToolCard` blocks (tender / evidence / xref) under the
   "Hand-off & integrity" header. Search for the "Alpha-16: stripped"
   comment to find the insertion point.
2. `apps/web/src/components/review/ReviewLayout.tsx` — restore the four
   removed entries to the `QUEUES` array (`audit`, `questions`, `conflicts`,
   `taxonomy`).

**Trigger to restore:** when extraction has been validated end-to-end
on a real corpus AND there's a specific need — e.g. "I have rows in the
audit queue and need to walk them" or "I need to package a tender."

---

## 4. Backend modules — loaded but currently unreachable from the GUI

These have backend code + (in some cases) routers + tests, but no
operator-facing UI surface in alpha-15+. They stay imported because
removing them would force a tests-cascading-rewrite for no gain.

| Module | Purpose | Why deferred |
|--------|---------|--------------|
| `meridian/auth/*` | TOTP / session / login | Section 1 |
| `meridian/cost/*` (preview, summary, rates) | Cost estimation | No UI; downstream of register |
| `meridian/coverage/*` | Coverage dashboard | UI hidden; backend used internally for `/projects/{name}/coverage` (which IS still called by the dashboard) |
| `meridian/backup/*` | Project backup | CLI-only feature; not on critical path |
| `meridian/crash/*` | Cloudflare worker integration | Telemetry; pending Worker deployment |
| `meridian/licensing/*` | License key verification | Round-17 prep; not relevant pre-product |
| `meridian/updates/*` | Auto-update client | Premature for alpha line |
| `meridian/analytics/*` | 7 sub-modules: cli_a, cli_b, conflict_register, nrc_summary, ose_procurement, risk_hotspots, trade_overlap | Reporting layer; downstream of register |
| `meridian/bootstrap/*` | Proposal scaffolding | Unclear current value |
| `meridian/tender/*` | Tender packaging | Section 3 |
| `meridian/evidence/*` | Evidence packs | Section 3 |
| `meridian/extract/cross_references*` | Cross-ref generation | Section 3 |

**Note on `meridian/coverage/*`:** the project dashboard's API call to
`/projects/{name}/coverage` IS still wired and returns data; only the
specific cards in the dashboard UI that consume the audit/questions/etc.
pending counts are hidden. The endpoint still works.

---

## 5. Wizard "ready" page copy (cosmetic — alpha-18 finding, not yet fixed)

The static text on `/setup/ready` still says:

> Flagged rows show up in your project's review queues (Quarantine,
> Audit, Questions, Conflicts, Taxonomy). Work through them at your own
> pace.
>
> When you're ready to send a per-trade register out, the dashboard's
> Hand-off & integrity section (Tender, Evidence, Cross-references) is
> where you'll go.

These reference the queues + tools hidden in section 3. Cosmetic only —
operator sees the words on the ready page but the dashboard won't show
those cards. Not blocking forward motion. Fix in a future scoped alpha
if it confuses an SME.

**File:** `apps/web/src/components/setup/copy.ts` — `READY_COPY.whatsNext`.

---

## How to use this file

- On any future "what was disabled?" question, this is the answer.
- When restoring a surface, prefer reverting the specific edits this
  file calls out over re-implementing from scratch — the existing
  scaffolding around each strip is intact.
- When stripping NEW surfaces, add a section here in the same shape.
  Future-you will thank current-you.
