# Alpha-26 — Live process monitor + conflicts as a first-class platform feature

> Scope: two interconnected feature streams that share plumbing. (1) A live, beautiful process-monitoring panel docked at the bottom of every project page — answers "is it hung?" at a glance and doubles as a debug surface. (2) Elevation of the existing conflict-detection / resolution machinery from a buried URL-only queue to a first-class product surface — restored dashboard navigation, prominent dashboard tile, peer "Conflict register" page with Excel export, and an auto-inferred source-of-truth hierarchy visualisation.

## 1. Problem

**Live monitor.** The alpha-25.1 SME walkthrough surfaced a real complaint: extraction can take 5-15 minutes on a small library and the PM has no signal whether the system is working or hung. The alpha-25 `PipelineProgressTile` partly addresses this for the keystone flow but vanishes the moment `phase=done`, and operations *outside* extraction (review writes, exports, ingest re-runs) have no visibility at all. There is rich structured-event data being emitted (the alpha-25 `pipeline.*` family, the long-standing `extraction.source.*`, `triage.chunk.*`, `llm_call.*` events) — none of it reaches the browser.

**Conflicts hidden.** The alpha-16 dashboard prune (validating the deliverables-extraction loop) hid the Conflicts / Audit / Questions / Taxonomy queues from navigation, leaving them URL-only-reachable. Alpha-25.1's SME walkthrough showed that conflicts are the platform's deepest value-add — a list of requirements is a deliverable; a *list of resolved disagreements between sources* is the value PMs actually pay for — but the surface treats them as a sub-page of review. The conflict-pass machinery, the `most_onerous_reasoning` LLM output, and the resolution UX are all built; what's missing is surfacing.

A PM should land on the dashboard and immediately see: "what's running right now, what disagreed, who won, and how the project's sources rank against each other."

## 2. Goal

After alpha-26 ships:

- Every `/projects/<slug>/*` page has a sticky-bottom **live monitor** that pulses heartbeat green on real activity, surfaces current chunk progress with a bar + numeric, streams a tail of meaningful events, and turns amber → red on prolonged silence. Beautiful enough to demo; functional enough to debug.
- The dashboard renders **Conflicts as the most prominent review surface**, with the queue nav strip restored across all project pages.
- A new **Conflict register** page + `<slug>-conflicts.xlsx` export sits as a peer to the master register — every conflict (pending, resolved, superseded), every reasoning paragraph, every resolution stamp.
- A **Source-of-truth hierarchy** view on the dashboard auto-infers from resolved-conflict patterns and renders as a toggleable Sankey ⇄ Ranked-list visualisation. Persists user choice.

## 3. Scope

**In scope:**

1. **Backend SSE infrastructure** (§5): new `GET /api/projects/{name}/events` endpoint streaming structured-log events to subscribers; broadcaster taps the existing structlog processor chain; configurable subscriber cap defaulting to 5; per-project filtering.
2. **Live monitor frontend panel** (§6): sticky-bottom `LiveMonitor` component on every `/projects/<slug>/*` page; collapsed/active/expanded states; heartbeat dot with state-shifted glow; gradient bar; streaming tail; no-progress detection at 30 s / 90 s thresholds.
3. **Conflicts dashboard elevation** (§7): restore queue nav strip with all five queues + their pending counts; new prominent `ConflictsTile` component above the queue grid; counts-as-CTA self-prioritisation.
4. **Conflict register page + Excel export** (§8): new `/projects/<slug>/conflict-register` route with All / Pending / Resolved / Superseded filters; `GET /api/projects/{name}/conflict-register` JSON + `.xlsx` endpoints; deeplink-via-`?focus=` from register rows back into the resolution queue.
5. **Hierarchy backend** (§9): `GET /api/projects/{name}/hierarchy` aggregating resolved-conflict patterns into edge counts + ranked-list ordering; per-document-class grouping; cycle-tolerant; self-class conflicts surfaced separately.
6. **Hierarchy frontend** (§10): `HierarchyView` component on the dashboard between KPIs and the Conflicts tile; Sankey ⇄ Ranked toggle persisted to localStorage; hand-rolled SVG Sankey; row-click and ribbon-click deeplinks into the conflict register.
7. **Tests + gauntlet step 7k** (§11): SSE wire-format and subscriber-cap pytest coverage; conflict-register round-trip tests; hierarchy aggregation tests; gauntlet end-to-end.

**Out of scope:**

- Manual hierarchy overrides (admin UI, stored override edges).
- Per-document hierarchy (filename × filename precedence).
- SSE replay-on-reconnect / event-history endpoint.
- WebSocket bidirectional channel.
- Live monitor on non-`/projects/*` pages.
- Cross-tab broadcast deduplication.
- Server-side conflict register pagination beyond client virtualisation.
- Pipeline cancel button (alpha-24 punch-list residual).
- PM "what to do now" as a separate component (folded into the Conflicts tile's counts-as-CTA hierarchy).
- Quarantine taxonomy combobox + add-new (deferred to alpha-27+; benefits from its own brainstorm).
- Schema migration. None expected. If we find ourselves needing one, stop and reconsider.

## 4. Architecture overview

```
                                 Browser
                  ┌────────────────────────────────────┐
                  │  Dashboard                         │
                  │  ├── KPI tiles                     │
                  │  ├── HierarchyView (Sankey/List)   │
                  │  ├── ConflictsTile                 │
                  │  └── Queue card grid               │
                  │                                    │
                  │  Conflict register page            │
                  │  Conflicts queue (existing)        │
                  │                                    │
                  │  ┌──────────────────────────────┐  │
                  │  │ LiveMonitor (sticky bottom)  │  │
                  │  └──────────────────────────────┘  │
                  └────────────────────────────────────┘
                              │  HTTP + SSE
                              ▼
                  ┌────────────────────────────────────┐
                  │  FastAPI                           │
                  │  ├── /api/projects/{n}/events      │  ← SSE
                  │  ├── /api/projects/{n}/conflict-   │
                  │  │     register{,.xlsx}            │
                  │  ├── /api/projects/{n}/hierarchy   │
                  │  └── existing endpoints unchanged  │
                  │                                    │
                  │  meridian.events.broadcaster       │
                  │  ↑ taps structlog processor chain  │
                  └────────────────────────────────────┘
```

No schema changes. No new long-running workers. No new background threads beyond the SSE subscribers (one `asyncio.Task` per active subscriber, capped).

## 5. Live monitor — backend (SSE)

### 5.1 Endpoint

```
GET /api/projects/{name}/events     → text/event-stream
                                       (200 + streaming, 503 on subscriber-cap, 404 on unknown project)
```

The endpoint validates the project exists (`_ensure_project` raises 404), claims a subscriber slot via the broadcaster (raises 503 if at cap), and returns a `StreamingResponse(event_generator(), media_type="text/event-stream")`.

### 5.2 Wire format

```
event: log
data: {"ts":"2026-05-10T11:42:00.781Z","level":"info","event":"triage.chunk.completed","ctx":{"chunk_id":"c062","keep":true,"tokens":1840},"project_slug":"bod"}

event: heartbeat
data: {"ts":"2026-05-10T11:42:01.500Z"}
```

- One JSON object per `data:` line. Each event is a complete SSE frame (`event:` + `data:` + blank line).
- `ts` is ISO-8601 UTC with millisecond precision.
- `level` ∈ {`info`, `warning`, `error`, `debug`}.
- `event` is the structlog event name (e.g. `triage.chunk.completed`).
- `ctx` is the dict of structured kwargs passed to the structlog call, with non-JSON-serialisable values stringified.
- `project_slug` is the structlog-bound context (set by the alpha-13 middleware on every project-scoped request).

Heartbeat fires every 5 s when no real events have flowed since the last frame. Keeps proxies/load-balancers from idle-killing the connection and feeds the frontend's "last-event-at" timer.

### 5.3 Allow-list

Only events matching the allow-list reach subscribers. Rejected events still write to the structured-log JSON file unchanged.

```python
_BROADCAST_ALLOW_LIST: frozenset[str] = frozenset({
    # Extraction lifecycle
    "extraction.job.start",
    "extraction.job.finish",
    "extraction.source.start",
    "extraction.source.committed",
    "extraction.source.finish",
    "extraction.source.skip",
    "extraction.source.fail",
    # Per-chunk progress (the load-bearing "is it working?" signal)
    "triage.chunk.completed",
    "triage.chunk.orphan_in_progress",
    # LLM call ledger
    "llm_call.completed",
    # Pipeline (alpha-25 family)
    "pipeline.bootstrap_soft_failed",
    "pipeline.conflict_pass_skipped_empty_corpus",
    "pipeline.conflict_pass_soft_failed",
    "pipeline.busy",
    "pipeline.failed",
})
```

`api.request` / `api.response` are intentionally excluded — noise on this surface. Promotion is a one-line allow-list addition when a future event earns its keep.

### 5.4 Broadcaster

New module `src/meridian/events/broadcaster.py`:

```python
"""In-process structured-event broadcaster for the alpha-26 SSE surface.

A single broadcaster instance lives at module scope. Subscribers register
via `subscribe(slug)` which returns an asyncio.Queue + a token; the SSE
endpoint reads from the queue and unsubscribes on disconnect. The structlog
processor chain calls `emit(event_dict)` on every event; the broadcaster
filters by allow-list + slug and pushes copies to each matching queue.

Bounded by `settings.events_max_subscribers` (default 5).
"""

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any

from meridian.config import settings
from meridian.logging import get_logger

_log = get_logger("meridian.events.broadcaster")

_BROADCAST_ALLOW_LIST: frozenset[str] = frozenset({...})  # see §5.3


@dataclass
class _Subscriber:
    slug: str
    queue: asyncio.Queue[dict[str, Any]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=200),
    )


_subscribers: dict[int, _Subscriber] = {}
_subscribers_lock = threading.Lock()
_next_token: int = 0


class SubscriberLimitExceeded(Exception):
    """Raised when subscribe() is called past settings.events_max_subscribers."""


def active_count() -> int:
    with _subscribers_lock:
        return len(_subscribers)


def subscribe(slug: str) -> tuple[int, asyncio.Queue[dict[str, Any]]]:
    """Register a subscriber. Raises SubscriberLimitExceeded if full."""
    global _next_token
    with _subscribers_lock:
        if len(_subscribers) >= settings.events_max_subscribers:
            raise SubscriberLimitExceeded(
                f"Subscriber cap reached ({settings.events_max_subscribers})"
            )
        token = _next_token
        _next_token += 1
        sub = _Subscriber(slug=slug)
        _subscribers[token] = sub
    _log.info("events.subscriber.registered", token=token, slug=slug,
              active=active_count())
    return token, sub.queue


def unsubscribe(token: int) -> None:
    with _subscribers_lock:
        _subscribers.pop(token, None)
    _log.info("events.subscriber.unregistered", token=token,
              active=active_count())


def emit(event_dict: dict[str, Any]) -> None:
    """Called by the structlog processor on every event. Filters + fans out."""
    event_name = event_dict.get("event")
    if event_name not in _BROADCAST_ALLOW_LIST:
        return
    target_slug = event_dict.get("project_slug")
    # Build the wire payload once.
    payload = {
        "ts": event_dict.get("timestamp"),
        "level": event_dict.get("level", "info"),
        "event": event_name,
        "ctx": {k: v for k, v in event_dict.items()
                if k not in {"event", "level", "timestamp", "project_slug"}},
        "project_slug": target_slug,
    }
    with _subscribers_lock:
        targets = [
            s.queue for s in _subscribers.values()
            if target_slug is None or s.slug == target_slug or s.slug == "*"
        ]
    for q in targets:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            # Subscriber is too slow — drop oldest, push newest.
            try:
                q.get_nowait()
                q.put_nowait(payload)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
```

The broadcaster uses a `threading.Lock` (not `asyncio.Lock`) because structlog runs in the calling thread — could be the FastAPI worker thread, the pipeline worker thread, or a sync test thread. The asyncio queues themselves are thread-safe for `put_nowait` from any thread.

### 5.5 Structlog processor integration

In `src/meridian/logging/setup.py`'s `configure_logging`, add to the processor chain (after the existing JSON-rendering processor, BEFORE the file handler):

```python
def _broadcast_processor(_logger, _method_name, event_dict):
    try:
        from meridian.events.broadcaster import emit
        emit(event_dict)
    except Exception:  # noqa: BLE001 — broadcaster MUST NEVER break logging
        pass
    return event_dict
```

Lazy-imports the broadcaster so logging-without-broadcaster (CLI, tests) doesn't blow up if the events module is unavailable. Returns the event_dict unchanged so downstream processors continue.

### 5.6 Subscriber cap configuration

`Settings` (`src/meridian/config.py`) gains:

```python
events_max_subscribers: int = Field(
    default=5,
    description=(
        "Maximum simultaneous SSE subscribers across the whole backend. "
        "Bounds the in-memory subscriber queues. Override via "
        "MERIDIAN_EVENTS_MAX_SUBSCRIBERS=<n>."
    ),
)
```

Reads from env / `.env` via the existing pydantic-settings chain. The 503 response shape:

```json
{
  "detail": {
    "error": "subscriber_limit",
    "limit": 5,
    "active": 5,
    "message": "Server has 5 active monitor connections. Close another tab or wait."
  }
}
```

### 5.7 `/setup/runtime` integration

The existing `/setup/runtime` endpoint adds an `events` section:

```json
{
  "events": {
    "active_subscribers": 2,
    "max_subscribers": 5,
    "broadcaster_enabled": true
  }
}
```

Diagnostic surface; lets an operator check from the browser (or a Status-Meridian.bat probe) whether the backend has stuck subscribers without restarting.

## 6. Live monitor — frontend panel

### 6.1 Component shape

`apps/web/src/components/dashboard/LiveMonitor.tsx`. Mounted inside `<ReviewLayout>`'s root container, position: `sticky; bottom: 0; z-index: 30`. Inherits the layout's max-width container.

```tsx
interface Props {
  projectSlug: string;
}

export function LiveMonitor({ projectSlug }: Props) { ... }
```

### 6.2 Three render states

**Collapsed (idle).** 32 px tall. Single line:

```
●  Idle · last event 4m ago                                                  ↑
```

**Active — collapsed sub-state.** Same height. Bar fills the middle:

```
●  Extracting · chunk · BoD.pdf  [▓▓▓▓▓▓▓░░░░░]  62 / 100                    ↑
```

**Expanded.** ~180 px tall. Top row = the active bar. Below: streaming tail (last 5 events), oldest fading to 30 % opacity.

```
●  Extracting · chunk · BoD.pdf  [▓▓▓▓▓▓▓░░░░░]  62 / 100                    ↓
─────────────────────────────────────────────────────────────────────────────
▸ 11:42:03  extraction.source.committed   filename=spec.pdf deliverables=18
●  11:42:00  triage.chunk.completed         chunk=62 keep=true tokens=1840
●  11:41:57  triage.chunk.completed         chunk=61 keep=false reason=boilerplate
●  11:41:54  llm_call.completed             purpose=triage cost_cents=0.3
●  11:41:50  extraction.source.start        filename=spec.pdf chunks=100
```

### 6.3 Visual treatment ("showpiece")

- **Heartbeat dot.** `width: 6px; height: 6px; border-radius: 50%; box-shadow: 0 0 6px <color>`. Color shifts:
  - **Cyan** (`#06b6d4`): active + events flowing within the last 2 s.
  - **Green** (`#34d399`): active + steady (events within the last 30 s).
  - **Amber** (`#fbbf24`): >30 s since last event.
  - **Red** (`#ef4444`): >90 s since last event.
  - **Muted gray** (`#6b7280`): collapsed-idle (no events ever / last event >5 min ago).
  - Pulse animation: 1.4 s ease-in-out, opacity 0.4 → 1 → 0.4. Disabled in idle/red states (still + dim).
- **Bar fill.** `linear-gradient(90deg, #3b82f6, #06b6d4)` with `box-shadow: inset 0 0 4px rgba(6,182,212,0.5)`. Width transitions over 200 ms.
- **Tabular-numeric font** (`font-feature-settings: "tnum"`) on the `62 / 100` text — digits don't jiggle.
- **Last-event-age timer** updates at 1 Hz via a single `<span>` mutated by `requestAnimationFrame`; no React re-render of surrounding elements.
- **New-row entrance** in the tail: 200 ms slide-up + 600 ms one-shot cyan glow that fades. Pure CSS (`@keyframes`).

### 6.4 SSE consumption

A custom hook `useEventStream(projectSlug)`:

```ts
function useEventStream(projectSlug: string): {
  events: MonitorEvent[];
  status: "connecting" | "live" | "subscriber_limit" | "error";
  lastEventAt: number | null;
} {
  // EventSource(`/api/projects/${slug}/events`)
  // - onopen: status = "live"
  // - onmessage with event "log": prepend to events buffer (max 200 retained)
  // - onmessage with event "heartbeat": update lastEventAt only
  // - onerror with res.status === 503: status = "subscriber_limit"
  // - onerror otherwise: auto-reconnects via EventSource native behaviour
  // Cleanup: close EventSource on unmount.
}
```

The hook batches React state updates with a 50 ms debounce so flurries of events (5-10/sec during active extraction) don't cause re-render storms. Buffer is a circular array; oldest evicted past 200.

### 6.5 No-progress detection

Independent of the heartbeat dot color: a thin amber stripe across the bar (`background: linear-gradient(90deg, transparent, rgba(251,191,36,0.3), transparent)`) renders when `now - lastEventAt > 30s`. Stripe goes red at 90 s. Tooltip on hover: "No events received in the last 32 seconds. The pipeline may be wedged on a single source — check `backend.log`."

### 6.6 Persistence

`localStorage["meridian.live_monitor.collapsed"]` ∈ `"0" | "1"` (default `"0"` = expanded on first visit). The first activity-detected event auto-sets to `"0"` (one-shot per session) so a previously-collapsed user sees the panel announce itself when something starts.

### 6.7 Subscriber-limit state

When `useEventStream` reports `status === "subscriber_limit"`, the panel collapses to a single muted line:

```
○  Monitor unavailable — server has 5 active connections                     ↻
```

The ↻ is a manual retry button (re-creates the EventSource). No automatic retry storm.

### 6.8 Accessibility

- Tail container: `role="log" aria-live="polite"`.
- Heartbeat dot: `aria-label="Pipeline status: <state>"` with `<state>` ∈ {idle, active, slow, stalled, unavailable}.
- Bar progress: `role="progressbar" aria-valuemin=0 aria-valuemax=100 aria-valuenow=<pct>`.
- Toggle: `<button aria-expanded={!collapsed}>`.

## 7. Conflicts dashboard elevation

### 7.1 Restored queue nav strip

In `apps/web/src/components/review/ReviewLayout.tsx`, the `QUEUES` array (currently `[{key: "quarantine", ...}]`) extends to:

```tsx
const QUEUES: Array<{ key: keyof QueueCounts; label: string; href: string }> = [
  { key: "quarantine", label: "Quarantine",  href: "quarantine" },
  { key: "conflicts",  label: "Conflicts",   href: "conflicts" },
  { key: "audit",      label: "Audit",       href: "audit" },
  { key: "questions",  label: "Questions",   href: "questions" },
  { key: "taxonomy",   label: "Taxonomy",    href: "taxonomy" },
];
```

The right cluster (Sources · Master register) extends to add **Conflict register**:

```tsx
<Link href={`${base}/sources`}>Sources</Link>
<Link href={`${base}/master`}>Master register</Link>
<Link href={`${base}/conflict-register`}>Conflict register</Link>
```

The visual divider between left and right clusters remains.

### 7.2 ConflictsTile component (new)

`apps/web/src/components/dashboard/ConflictsTile.tsx`. Mounted in `DashboardBody` between the KPI grid and the queue card cluster. Renders one of three shapes:

**Pending > 0 (amber, prominent):**

```
┌──────────────────────────────────────────────────────────────┐
│ ⚠ 12 pending conflicts need a call from you                  │
│ Two sources disagree about the same item. Resolve in queue   │
│ order or jump straight from the conflict register.           │
│                                                               │
│ [Start here →]  [View hierarchy →]  [Open register →]        │
└──────────────────────────────────────────────────────────────┘
```

**Pending == 0 AND resolved > 0 (green-checkmark, calm):**

```
┌──────────────────────────────────────────────────────────────┐
│ ✓ All 47 cross-source conflicts have been resolved           │
│                                                               │
│ [View hierarchy →]  [Open register →]                        │
└──────────────────────────────────────────────────────────────┘
```

**Pending == 0 AND resolved == 0 (muted, informational):**

```
┌──────────────────────────────────────────────────────────────┐
│ No cross-source conflicts detected yet                       │
│ Conflicts surface when two sources disagree about the same   │
│ deliverable. They'll appear here as your sources build up.   │
└──────────────────────────────────────────────────────────────┘
```

The tile reads `coverage.conflicts.pending` (already in the `/coverage` payload) and a new `coverage.conflicts.resolved_count` (added in §9.4).

### 7.3 Counts-as-CTA hierarchy

The dashboard self-prioritises which surface gets the "Start here →" highlight on any given page load:

1. `coverage.conflicts.pending > 0` → ConflictsTile lights up.
2. Else `coverage.deliverable_status.quarantined > 0` → Quarantine card.
3. Else `coverage.audit.pending > 0` → Audit card.
4. Else `coverage.questions.pending > 0` → Questions card.
5. Else `coverage.taxonomy.pending_proposals > 0` → Taxonomy card.
6. Else: green "trustworthy" state, no CTA.

This replaces the deferred "PM what-to-do-now" guidance work — the dashboard becomes self-prioritising via existing data, no separate widget.

### 7.4 Queue card grid (restored)

Below ConflictsTile, a 4-up grid (2-up on tablet, 1-up on mobile) of:

- Quarantine
- Audit
- Questions
- Taxonomy

Each card: title · pending count badge · one-line description · "Open queue →". Same `QueueCard` component shape as the existing Quarantine card. Conflicts is NOT in this grid (it's the tile above).

## 8. Conflict register peer surface

### 8.1 Routes

- Frontend: `/projects/<slug>/conflict-register`. New route file: `apps/web/src/app/projects/[name]/conflict-register/page.tsx` + `ConflictRegisterTable.tsx`.
- Backend JSON: `GET /api/projects/{name}/conflict-register?status=all|pending|resolved|superseded` (default `all`).
- Backend Excel: `GET /api/projects/{name}/conflict-register.xlsx`.

### 8.2 JSON response shape

```json
{
  "items": [
    {
      "id": "uuid",
      "kind": "cross_source_content",
      "status": "resolved_accept_a",
      "most_onerous_party_id": "uuid",
      "most_onerous_reasoning": "The chiller-spec ≤22°C ceiling is not enforced...",
      "created_at": "2026-05-10T11:42:00Z",
      "resolved_at": "2026-05-10T13:15:00Z",
      "parties": [
        {
          "party_kind": "deliverable",
          "party_id": "uuid",
          "party_position": "≤22°C cooling ceiling",
          "summary_or_text": "Chiller plant designed to service 1-2 hot aisles..."
        },
        ...
      ]
    },
    ...
  ],
  "counts": {
    "all": 47,
    "pending": 12,
    "resolved": 34,
    "superseded": 1
  }
}
```

The `items` array shape is the existing `ConflictItem` model from `meridian.api.main`, extended to include `resolved_at`. The `counts` object is new.

### 8.3 Page layout

```
┌─ ReviewLayout chrome (queues nav + artifacts cluster) ───────────────┐
│                                                                       │
│ Conflict register                                                     │
│ Every cross-source disagreement detected and the call you made.       │
│                                                                       │
│ [All 47]  [Pending 12]  [Resolved 34]  [Superseded 1]   [Download xlsx]│
│                                                                       │
│ ┌──┬──────────┬───────────┬──────────┬──────────┬──────┬────────┐   │
│ │ #│ Source A │ Value     │ Source B │ Value    │ Kind │ Status │ … │
│ ├──┼──────────┼───────────┼──────────┼──────────┼──────┼────────┤   │
│ │ 1│ BoD.pdf  │ ≤22°C     │ OSE.pdf  │ ≤24°C    │ ...  │ ✓ Acc.A│ … │
│ │ 2│ Vendor   │ 800 V01   │ Drawing  │ 800 V02  │ ...  │ ⚠ Pend │ … │
│ │ …│                                                                 │
│ └──┴──────────┴───────────┴──────────┴──────────┴──────┴────────┘   │
└───────────────────────────────────────────────────────────────────────┘
```

### 8.4 Table columns

| Column | Source | Notes |
|---|---|---|
| `#` | row index | sticky left edge |
| `source A` | first party's source filename (or "Audit row" for audit-vs-deliverable) | |
| `value A` | `parties[0].party_position` | wrap, ~30ch |
| `source B` | second party's source filename | |
| `value B` | `parties[1].party_position` | wrap, ~30ch |
| `kind` | `conflict.kind` rendered as a chip | colour by kind family |
| `most-onerous reasoning` | `conflict.most_onerous_reasoning` verbatim | wrap, ~50ch |
| `status` | colour-coded badge: amber=pending, green=resolved, gray=superseded | |
| `resolution` | `"Accept A" | "Accept B" | "Reject both" | "Hybrid" | "—"` | derived from `status` for resolved variants |
| `resolved at` | `conflict.resolved_at` formatted, or `created_at` for pending | localised |

Pending rows render an inline `Resolve →` CTA in the resolution column that navigates to `/projects/<slug>/conflicts?focus=<conflict_id>` (§8.6).

The table is virtualised when item count > 100 (uses the existing `MasterTable` virtualisation pattern from `apps/web/src/app/projects/[name]/master/`).

### 8.5 Excel export

`<slug>-conflicts.xlsx` produced via openpyxl. Single sheet `"Conflicts"`. Columns mirror the page table verbatim. Reuses the alpha-25 `_format_conflict_summary` helper for the reasoning rendering posture (verbatim, no paraphrase). `wrap_text=True` on the reasoning + value columns; column widths 60/30/30/60/30/14/60/14/14/22.

The `xlsx` export reuses the alpha-24 `BackgroundTask` cleanup pattern from `projects_export_stream` so the temp file is removed after the response is sent.

### 8.6 Deeplink-via-`?focus=`

The existing `/projects/<slug>/conflicts` page (the resolution queue) accepts a `?focus=<conflict_id>` query string. On mount:

- Find the conflict matching the id in `items`.
- Set `selectedIdx` to its position.
- Scroll the queue UI to it.
- If not found (already resolved, or superseded since the register loaded), render a one-time toast: "This conflict has been resolved or superseded since you opened the register."

~15 lines in `ConflictsQueue.tsx` + a `useSearchParams()` read.

### 8.7 Pending / resolved semantics divergence

| Surface | Pending | Resolved | Superseded |
|---|---|---|---|
| Master register `conflict_summary` (alpha-25) | ✓ shown | hidden | hidden |
| **Conflict register** (alpha-26) | ✓ shown | ✓ shown | ✓ shown |

The master register surfaces "what to act on". The conflict register surfaces "the audit trail of what disagreed and how it got resolved" — every row matters there, including resolved ones, because **the resolution IS the value-add**.

## 9. Hierarchy backend (auto-inference)

### 9.1 Endpoint

```
GET /api/projects/{name}/hierarchy   → 200 + JSON, 404 on unknown project
```

### 9.2 Response shape

```json
{
  "edges": [
    {
      "winner_class": "BoD",
      "loser_class": "OSE",
      "wins": 23,
      "losses": 1,
      "win_rate": 0.96,
      "sample_conflict_ids": ["uuid", "uuid", "uuid"]
    },
    ...
  ],
  "ranked": [
    { "class": "BoD",         "wins": 23, "losses": 1,  "win_rate": 0.96, "rank": 1 },
    { "class": "OSE",         "wins": 9,  "losses": 23, "win_rate": 0.28, "rank": 2 },
    { "class": "Vendor",      "wins": 3,  "losses": 17, "win_rate": 0.15, "rank": 3 },
    { "class": "Drawing",     "wins": 1,  "losses": 12, "win_rate": 0.08, "rank": 4 },
    { "class": "RFI",         "wins": 0,  "losses": 3,  "win_rate": 0.0,  "rank": 5 }
  ],
  "resolved_count": 47,
  "same_class_conflicts": [
    { "class": "OSE", "count": 4 }
  ],
  "computed_at": "2026-05-10T11:42:00.000Z"
}
```

`sample_conflict_ids` is the 3 most-recent conflict IDs feeding this edge — used by the Sankey tooltip to deeplink into the register.

### 9.3 Inference rules

For each `conflict` row with `status LIKE 'resolved_%'`:

| status | Action |
|---|---|
| `resolved_accept_a` | party A wins, party B loses → edge `(A.document_class → B.document_class)` |
| `resolved_accept_b` | party B wins, party A loses → edge `(B.document_class → A.document_class)` |
| `resolved_reject_both` | both lose; counted in `resolved_count` only, no edge contribution |
| `resolved_hybrid` | no clean winner; counted in `resolved_count` only, no edge contribution |
| anything else | skipped (pending / superseded don't shape hierarchy) |

If `winner.document_class == loser.document_class` (self-class): NOT added to `edges`; counted in `same_class_conflicts[<class>].count`.

NULL `document_class` values are coerced to the literal string `"Unclassified"` — the inference is honest about what's being counted.

`win_rate` for an edge is `wins / (wins + losses)`. For the ranked list: `wins / (wins + losses)` summed across ALL edges where the class appears.

### 9.4 SQL aggregation

Single GROUP BY query joining `conflict` × `conflict_party` × `source_document` (via `deliverable.source_id` for `party_kind='deliverable'`, or `audit_record.source_id` for `party_kind='audit'`). Pseudocode:

```sql
SELECT
    winner_class, loser_class,
    COUNT(*) AS wins
FROM (
    SELECT
        c.id,
        CASE
            WHEN c.status = 'resolved_accept_a'
                THEN sd_a.document_class
            WHEN c.status = 'resolved_accept_b'
                THEN sd_b.document_class
        END AS winner_class,
        CASE
            WHEN c.status = 'resolved_accept_a'
                THEN sd_b.document_class
            WHEN c.status = 'resolved_accept_b'
                THEN sd_a.document_class
        END AS loser_class
    FROM conflict c
    JOIN conflict_party cp_a ON cp_a.conflict_id = c.id AND cp_a.party_kind = 'deliverable' AND ... /* index 0 */
    JOIN conflict_party cp_b ON cp_b.conflict_id = c.id AND cp_b.party_kind = 'deliverable' AND ... /* index 1 */
    LEFT JOIN deliverable d_a ON d_a.id = cp_a.party_id
    LEFT JOIN deliverable d_b ON d_b.id = cp_b.party_id
    LEFT JOIN source_document sd_a ON sd_a.id = d_a.source_id
    LEFT JOIN source_document sd_b ON sd_b.id = d_b.source_id
    WHERE c.status IN ('resolved_accept_a', 'resolved_accept_b')
)
WHERE winner_class IS NOT NULL AND loser_class IS NOT NULL
GROUP BY winner_class, loser_class
```

Audit-vs-deliverable conflicts (`party_kind='audit'`) join via `audit_record.source_id` instead of `deliverable.source_id`. The implementation will handle both party kinds.

### 9.5 Caching

None. v1 corpora produce <500 conflicts/project; the GROUP BY is sub-millisecond. Compute on every GET.

### 9.6 Coverage payload extension

`src/meridian/coverage/dashboard.py` — `coverage.conflicts` extends to include:

```python
class _ConflictsCoverage(BaseModel):
    pending: int                    # existing
    resolved_count: int             # NEW
    superseded_count: int           # NEW
```

Used by the ConflictsTile (§7.2) to render the green-checkmark state when `resolved_count > 0 and pending == 0`.

## 10. Hierarchy frontend (toggle Sankey ⇄ Ranked)

### 10.1 Component

`apps/web/src/components/dashboard/HierarchyView.tsx`. Mounted in `DashboardBody` between the KPI tiles and the ConflictsTile (§7.2). Empty-state suppressed (no render) when `resolved_count == 0`.

### 10.2 Section header

```
Source-of-truth hierarchy · 47 resolved conflicts                 [● Flow ◯ List]
```

The toggle is a two-position segmented control. Persists choice to `localStorage["meridian.hierarchy.view"]` ∈ `"flow" | "list"`. Default `"flow"`.

### 10.3 Flow (Sankey) renderer

Hand-rolled SVG. ~80 lines of layout math:

1. Group `edges` by `winner_class` and `loser_class` separately.
2. Order each side by total win/loss count (descending).
3. Assign vertical lane heights proportional to total touch count.
4. For each edge, draw a Bezier ribbon `M (x_loser, y_loser) C (mid_x, y_loser), (mid_x, y_winner), (x_winner, y_winner)` with `stroke-width = wins * scale`.

Color palette: cyan-blue gradient for high-rate edges, fading to muted purple for low-rate. Hover on a ribbon → tooltip:

```
BoD beats OSE 23 times (96 % win rate)
View 3 sample conflicts →
```

The "View 3 sample conflicts →" link navigates to `/projects/<slug>/conflict-register?focus=<sample_conflict_ids[0]>` (uses the `?focus=` deeplink from §8.6, but on the register page rather than the queue).

Lane labels truncate with ellipsis below 480 px viewport. Self-class conflicts are NOT shown as ribbons (they'd be loops); surfaced in the footer (§10.5).

### 10.4 List (Ranked) renderer

Vertical ordered list. Each row:

```
1   BoD                                  23 wins · 1 loss · 96 %
2   OSE-spec                              9 wins · 23 losses · 28 %
3   Vendor-spec                           3 wins · 17 losses · 15 %
4   Drawing                               1 win · 12 losses · 8 %
5   RFI                                   0 wins · 3 losses · 0 %
```

Background gradient from class colour to transparent. Click on a row → filters the conflict register to that source class (`/projects/<slug>/conflict-register?source_class=BoD`).

### 10.5 Footer (both views)

```
Plus 4 conflicts within the same source class →
Hierarchy is auto-inferred from your resolved conflicts. How does this work?
```

The first line renders only when `same_class_conflicts` is non-empty; clicking navigates to `/projects/<slug>/conflict-register?same_class=true`. The "How does this work?" link goes to a glossary entry (added in §10.7).

### 10.6 Refresh trigger

`HierarchyView` re-fetches `/api/projects/{name}/hierarchy` when:

1. Component mount.
2. The live monitor emits an `extraction.job.finish` or `pipeline.conflict_pass_succeeded` event (consumed via the same event-stream hook the LiveMonitor uses — both can subscribe).
3. The user resolves a conflict in `/conflicts` and returns to the dashboard (visibilitychange event detection or a `lastConflictResolvedAt` localStorage signal set by `ConflictsQueue`).

A 600 ms ease transition between Sankey states makes recomputes feel intentional rather than glitchy.

### 10.7 Glossary entry

`apps/web/src/app/glossary/page.tsx` gains a new section:

```
## Source-of-truth hierarchy

Every time you resolve a cross-source conflict via Accept A or Accept B,
Meridian records which source's value won. Aggregated across many
conflicts, this produces a directed graph: which kind of document
*tends to trump* which other kind. The Source-of-truth Hierarchy
visualisation on the dashboard shows that graph as either a flow
diagram (Sankey ribbons, thickness = win count) or a ranked list
(precedence order with win-rate per class).

The hierarchy is auto-inferred. If your project has unusual precedence
rules (e.g., "in this project Drawings always trump RFIs regardless of
who appears first"), you can override individual edges manually — that
feature ships in alpha-27.
```

## 11. Tests

### 11.1 Backend e2e (`tests/e2e/test_alpha26_*.py`)

**`test_alpha26_events_sse.py`:**
- `test_sse_stream_basic` — open EventSource against TestClient, fire a synthetic `extraction.source.start` event via `_log.info(...)`, subscriber receives a frame within 200 ms.
- `test_sse_subscriber_cap` — set `MERIDIAN_EVENTS_MAX_SUBSCRIBERS=2`, open three connections, third returns 503 with `subscriber_limit` body.
- `test_sse_filtered_by_project_slug` — two projects, two subscribers, events from project A do not reach project B's subscriber.
- `test_sse_heartbeat_fires_on_idle` — no real events for 6 s, subscriber observes a heartbeat frame.
- `test_sse_allow_list` — fires `api.request` (not in allow-list), confirms it does NOT reach subscribers; fires `triage.chunk.completed`, confirms it DOES.
- `test_sse_unsubscribe_on_disconnect` — subscriber count returns to 0 after EventSource closes.

**`test_alpha26_conflict_register.py`:**
- `test_register_endpoint_default_returns_all` — populates resolved + pending + superseded fixtures; asserts default response includes all three.
- `test_register_endpoint_filters_by_status` — `?status=pending` returns only pending; `?status=resolved` returns the four `resolved_*` variants collapsed.
- `test_register_xlsx_round_trip` — generates fixture with one of each status, GETs the xlsx, openpyxl-loads, asserts column headers + that resolved-row's reasoning lands verbatim and resolution column reads "Accept A".
- `test_register_includes_resolved_at_for_resolved_rows` — pending rows have `resolved_at: null`; resolved rows have a valid timestamp.

**`test_alpha26_hierarchy.py`:**
- `test_hierarchy_basic_aggregation` — 3 resolved BoD-vs-OSE conflicts (BoD wins twice, OSE wins once); asserts `edges` has BoD→OSE with `wins=2, losses=1` and OSE→BoD with `wins=1, losses=2`; `ranked` has BoD ranked above OSE.
- `test_hierarchy_skips_hybrid_and_reject_both` — hybrid + reject_both fixtures contribute to `resolved_count` but produce zero edges.
- `test_hierarchy_self_class_separated` — two OSE-vs-OSE resolved conflicts surface in `same_class_conflicts` (NOT in `edges`).
- `test_hierarchy_empty_project` — no resolved conflicts → `{"edges": [], "ranked": [], "resolved_count": 0, "same_class_conflicts": []}`.
- `test_hierarchy_unclassified_source_class` — source with `document_class IS NULL` shows up as `"Unclassified"` in edges/ranked.

### 11.2 Frontend

No test infra (alpha-10 deferral stands). Manual gauntlet step + SME walkthrough.

### 11.3 Gauntlet — step 7k

Append to `scripts/release_gauntlet.py`:

1. Spawn backend on :8004 with `MERIDIAN_EVENTS_MAX_SUBSCRIBERS=5`.
2. Generate 3 synthetic `.docx` files; ingest via `/setup/import-folder`.
3. Open an EventSource against `/api/projects/<slug>/events` BEFORE step 4 starts.
4. POST `/api/projects/<slug>/pipeline`, poll until `phase=done`.
5. Assert at least 1 `extraction.source.start` and 1 `extraction.source.committed` frame arrived during step 4.
6. GET `/api/projects/<slug>/conflict-register?status=all` — assert non-empty `items` array (3-source corpus typically produces some conflicts even synthetic).
7. GET `/api/projects/<slug>/conflict-register.xlsx` — openpyxl-load, assert `resolution` column header.
8. POST `/api/projects/<slug>/conflicts/<id>/resolve` with `{"action": "accept_a", "accept_party_id": "<id>"}` for one pending conflict.
9. GET `/api/projects/<slug>/hierarchy` — assert `resolved_count == 1` and `len(ranked) >= 1`.
10. Open 6 EventSources in parallel — assert subscribers 6+ return 503.
11. Tear down.

## 12. Carry-overs to alpha-27+

Unchanged from alpha-25.1 plus alpha-26-specific deferrals:

- Manual hierarchy override (admin UI + stored override edges + override-vs-inferred badge).
- Quarantine taxonomy combobox + add-new affordance (open since alpha-24 punch list).
- Pipeline cancel / Ctrl+C support.
- `--isolated` extract child-process IPC.
- CLI / wizard data-dir consolidation.
- Onboarding three small fixes (Tour copy, Step 2 Ollama hyperlink, Step 3 missing Projects link).
- `?` keyboard-shortcut binding fix.
- SSE replay-on-reconnect (event-history endpoint).
- Server-side conflict register pagination (kicks in past 500 conflicts/project).
- Per-document hierarchy (filename × filename precedence).

## 13. Convention compliance

- Commits: `[scoped] alpha-26 <stream>: <subject>` with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer (HEREDOC for multi-line).
- Skill order: brainstorming → writing-plans → subagent-driven-development → finishing-a-development-branch.
- Serial dispatch only — no parallel implementers.
- LLM-text fields render verbatim wherever they reach a user surface (`most_onerous_reasoning`, `current_source_filename`, `error_message`, `party_position`).
- No new test infra (vitest etc.) without an explicit grid pass.
- System Python for tests: `python -m pytest tests/e2e/ --ignore=tests/e2e/test_concurrency.py`.
- Wheel build: `uv build --wheel` (default `uv build` trips on gitignored `apps/web/out/`).
- Backend conventions per alpha-25 spec §13.
