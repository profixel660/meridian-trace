# Per-purpose provider routing — v1 design

**Status:** design (draft).
**Authority:** `CONTEXT.md` §0 (Flexibility Principle), §6 (LiteLLM tech-stack lock), §12 (LLM providers — locked).
**Scope:** lift LLM provider+model from a single global default to a per-purpose configuration so the user can route some calls (e.g. triage) to local models while keeping the load-bearing calls (e.g. text-spec extraction, conflict pass) on cloud frontier models.
**Why now:** the cost+sovereignty story for AEC users is meaningfully better when we can route the cheap-and-volume-heavy steps locally. The LiteLLM seam already supports it; the only thing in the way is the hard-coded single-default in `call_llm`.

---

## 1. The six routable purposes

Per the existing `llm_call.purpose` enum:

| Purpose | Volume per project | Quality bar | Default route (v1) | Practical local target |
|---|---|---|---|---|
| `quality_scan` | 1 per source | medium | `anthropic / claude-sonnet-4-6` | Llama 3.3 70B Q4 — viable |
| `triage` | many (1 per chunk) | low | `anthropic / claude-haiku-4-5-20251001` | Qwen 2.5 14B / Llama 3.3 8B — strong fit |
| `extract_text_spec` | 1 per source | high | `anthropic / claude-sonnet-4-6` | 70B Q4 — A/B before trusting |
| `extract_bod` | 1 per source | high | `anthropic / claude-sonnet-4-6` | 70B Q4 — A/B; row-level structure helps |
| `conflict_pass` | 1 per project | very high (cross-row reasoning, most-onerous judgement) | `anthropic / claude-sonnet-4-6` | Keep cloud until proven |
| `error_explain` | rare (LLM-assisted error reports per CONTEXT.md §19) | low | `anthropic / claude-sonnet-4-6` | 70B Q4 — viable |

Note: `extract_text_spec` is the purpose used by both the text-spec extractor and the demarcation extractor (the demarcation extractor reuses the purpose to avoid a schema migration; disambiguation lives in `prompt_version_ref`). When we eventually add an `extract_demarcation` purpose via migration, it gets its own routing entry.

## 2. Configuration shape

### Default behaviour (no user config)

`Settings.purpose_routing` defaults to:

```python
PurposeRoute = tuple[str, str]  # (provider, model)

DEFAULT_PURPOSE_ROUTING: dict[LlmPurpose, PurposeRoute] = {
    "quality_scan":      ("anthropic", "claude-sonnet-4-6"),
    "triage":            ("anthropic", "claude-haiku-4-5-20251001"),
    "extract_text_spec": ("anthropic", "claude-sonnet-4-6"),
    "extract_bod":       ("anthropic", "claude-sonnet-4-6"),
    "conflict_pass":     ("anthropic", "claude-sonnet-4-6"),
    "error_explain":     ("anthropic", "claude-sonnet-4-6"),
}
```

Behaviour change for existing users on upgrade: **none**. The defaults preserve current routing.

### Override hierarchy (highest wins)

1. **Per-call argument** — `call_llm(..., provider=..., model=...)` overrides everything (existing API; preserved).
2. **Project-level setting** — stored as JSON in `app_setting` table under key `purpose_routing`. Lets a project be configured all-local while another stays all-cloud.
3. **Environment variables** — `MERIDIAN_ROUTE_<PURPOSE>=provider/model` (e.g. `MERIDIAN_ROUTE_TRIAGE=ollama/qwen2.5:14b`). Useful for dev-machine overrides without touching project state.
4. **Process-level Settings** — `Settings.purpose_routing` in `meridian.config`. Can be edited in code; pydantic-settings reads from `.env` too.
5. **`DEFAULT_PURPOSE_ROUTING`** above.

The resolved routing for every call is recorded on the `llm_call` row (already happens — `provider` + `model` columns). No reproducibility regression.

### Local-route presets (shipped as named recipes)

For onboarding / settings UI:

```python
LOCAL_PRESETS: dict[str, dict[LlmPurpose, PurposeRoute]] = {
    # 5090-class single-GPU workstation, Ollama running locally
    "ollama-5090-balanced": {
        "triage":            ("ollama", "qwen2.5:14b-instruct"),
        "quality_scan":      ("ollama", "llama3.3:70b-instruct-q4_K_M"),
        "extract_text_spec": ("anthropic", "claude-sonnet-4-6"),  # keep cloud
        "extract_bod":       ("ollama", "llama3.3:70b-instruct-q4_K_M"),
        "conflict_pass":     ("anthropic", "claude-sonnet-4-6"),  # keep cloud
        "error_explain":     ("ollama", "qwen2.5:14b-instruct"),
    },
    # Air-gapped: NO cloud calls at all. Quality risks the user accepts.
    "ollama-air-gapped": {
        "triage":            ("ollama", "qwen2.5:14b-instruct"),
        "quality_scan":      ("ollama", "llama3.3:70b-instruct-q4_K_M"),
        "extract_text_spec": ("ollama", "llama3.3:70b-instruct-q4_K_M"),
        "extract_bod":       ("ollama", "llama3.3:70b-instruct-q4_K_M"),
        "conflict_pass":     ("ollama", "llama3.3:70b-instruct-q4_K_M"),
        "error_explain":     ("ollama", "qwen2.5:14b-instruct"),
    },
    # Cost-only: triage local, everything else cloud.
    "triage-local-only": {
        "triage": ("ollama", "qwen2.5:14b-instruct"),
        # other purposes inherit defaults (cloud)
    },
}
```

Presets are seed configurations the user can apply with one click. Once applied they become the project's `purpose_routing` — fully editable per purpose afterwards.

## 3. Code seams (concrete changes)

### 3.1 `meridian.config.Settings`

Add:

```python
class Settings(BaseSettings):
    ...
    # Per-purpose routing. None = inherit from DEFAULT_PURPOSE_ROUTING.
    purpose_routing: dict[str, tuple[str, str]] | None = None
```

Resolution helper:

```python
def resolve_route(self, purpose: str) -> tuple[str, str]:
    # 1. Env var: MERIDIAN_ROUTE_<PURPOSE>=provider/model
    env_key = f"MERIDIAN_ROUTE_{purpose.upper()}"
    raw = os.environ.get(env_key)
    if raw and "/" in raw:
        provider, _, model = raw.partition("/")
        return provider, model
    # 2. settings.purpose_routing
    if self.purpose_routing and purpose in self.purpose_routing:
        return self.purpose_routing[purpose]
    # 3. defaults
    return DEFAULT_PURPOSE_ROUTING[purpose]
```

### 3.2 Project-level routing (read at job start)

`meridian.projects` gains:

```python
def get_project_routing(conn: sqlite3.Connection) -> dict[str, tuple[str, str]] | None:
    row = conn.execute(
        "SELECT value FROM app_setting WHERE key = 'purpose_routing'"
    ).fetchone()
    return json.loads(row["value"]) if row else None

def set_project_routing(conn: sqlite3.Connection, routing: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO app_setting (key, value, updated_at) VALUES ('purpose_routing', ?, ?)",
        (json.dumps(routing), _now()),
    )
```

The orchestrator reads project routing at job start, falls back to settings/env/defaults per purpose.

### 3.3 `meridian.llm.client.call_llm`

Today: requires explicit `provider` + `model` arguments.
After: `provider` and `model` become optional. When omitted, resolved via `purpose` against the routing chain.

```python
def call_llm(
    conn,
    *,
    purpose: LlmPurpose,
    provider: str | None = None,
    model: str | None = None,
    ...,
) -> LlmCall:
    if not provider or not model:
        provider, model = settings.resolve_route(purpose)
    ...
```

Existing call sites that pass explicit provider/model continue to work (per-call override, top of the hierarchy).

### 3.4 Conditional auth precheck

Today: `call_llm` raises if ANTHROPIC_API_KEY missing AND provider="anthropic".
After: same, but air-gap-friendly. The check fires **per call**, after routing resolution. If the resolved route is `ollama/...`, no Anthropic key is needed.

Also add an explicit air-gap mode:

```python
class Settings(BaseSettings):
    ...
    air_gapped: bool = False  # if True, any non-local route raises before the call
```

When `air_gapped=True`, every call_llm checks the resolved provider; non-local providers (`anthropic`, `openai`, `azure`, `bedrock`, `gemini`) raise `RuntimeError("air-gapped mode: cloud route resolved for purpose X")`. Fail fast, before the network call.

### 3.5 Existing call sites cleanup

- `meridian.extract.quality_scan` — `model` arg becomes optional.
- `meridian.extract.text_spec` — same.
- `meridian.extract.bod_import` — same.
- `meridian.extract.demarcation` — same.
- `meridian.extract.conflict_pass` — same.
- `meridian.extract.triage` — currently hardcodes `claude-haiku-4-5-20251001`. Move to routing chain so users can override.
- `meridian.extract.orchestrator.create_job` — provider/model snapshot on the job becomes "the resolved default at job start" (just for reproducibility headers; per-call routing still applies).

## 4. CLI surface

```bash
# View resolved routing for a project
meridian routing show <project>

# Apply a preset
meridian routing apply <project> ollama-5090-balanced

# Set one purpose
meridian routing set <project> triage ollama qwen2.5:14b

# Reset a purpose to default
meridian routing unset <project> triage

# Air-gap toggle (project-level)
meridian routing air-gap-on  <project>
meridian routing air-gap-off <project>
```

## 5. UI surface (Next.js shell, future session)

- **Onboarding screen 3 ("Recommended setup")** gains a third option: "I have a local GPU + Ollama" — applies a preset.
- **Project Settings → Advanced → Routing** panel: per-purpose dropdowns of `(provider, model)`, with current resolved route shown alongside the inherited default.
- **Per-job indicator**: the run UI shows a small pill per purpose ("☁ Anthropic Sonnet" vs "🖥 Ollama Llama 3.3 70B") so the user knows what each call hit. Reads from `llm_call.provider` + `model`.
- **Air-gap badge**: when air-gap is on, a permanent header badge confirms it. Cloud routes shown in red strikethrough.

## 6. What stays in scope for the routing v1 ship

- Per-purpose routing config (Settings + project-level + env-var overrides)
- Preset application
- Conditional auth precheck (only require key if cloud route resolved)
- Air-gap mode (config + fail-fast)
- CLI commands above
- `llm_call.provider` + `model` already capture the per-call route — no schema change needed

## 7. Deferred to v1.x (named, not built)

- **Cost preview** that splits cloud spend from local-electrons. Local cost = $0 in the preview but flagged as "GPU-bound: <N> calls × <model>".
- **Local-vs-cloud A/B harness**. Re-run the same chunk through both routes, persist both into `llm_call`, surface a side-by-side diff for the SME to score. Build credibility before flipping production routes to local.
- **Local-route health checks**. On startup, if any project routing references `ollama/<model>`, ping the local endpoint to confirm reachability + model availability. Surface clear error if Ollama is down or the model isn't pulled.
- **Local embedding routes**. When v1.x adds semantic dedup or chunk grouping, default to local sentence-transformers / Ollama embeddings.
- **Vision routes**. When drawing extraction lands, route to Qwen 2-VL via Ollama as a credible local option.
- **Provider routing on a per-source basis** (e.g. "this sensitive doc must go local"). Project-level granularity is enough for v1.

## 8. What does NOT change

- `llm_call` schema (already records provider/model — full reproducibility preserved).
- `extraction_job.provider` / `extraction_job.model` columns: keep for the snapshot of the *default* at job start. Per-call routes can differ.
- Prompt versioning (`prompt_version_ref` column) — unchanged.
- The §3 three-outcome gate, taxonomy, flag vocabulary — unchanged. Local model just consumes the same prompts.
- Cost recording (`llm_call.cost_cents`) — for local routes this resolves to 0 (LiteLLM returns no cost for Ollama). The cost-preview UI handles the UX of "local = electrons not dollars" separately.

## 9. Risk + decision items to surface

| Risk | Mitigation |
|---|---|
| Local model produces lower-quality JSON, silently corrupts master register | A/B harness (deferred). Until built, *user assumes the risk* by configuring local for high-stakes purposes. UI warns when high-stakes purposes (extract_*, conflict_pass) are configured to non-default routes. |
| User configures local route but Ollama isn't running | Fail-fast on first call with a clear error message naming the route + the endpoint. Health check (deferred) catches this earlier at job start. |
| Air-gap mode + a purpose accidentally routes to cloud | Fail-fast preflight; orchestrator validates ALL purpose routes against air-gap before starting any call. |
| Cost confusion: "I switched triage to local but my bill didn't drop" | Cost preview UI shows pre-route + post-route comparison. Until UI lands, `llm_call.cost_cents` aggregation by provider provides the truth. |
| Reproducibility across mixed local/cloud runs | Already preserved — every call records its actual provider+model+input_hash. Re-runs match by input_hash; provider differences are visible. |

## 10. Open questions to surface to SME

1. **Should the air-gap badge be permanent or dismissible?** — engineering preference is permanent (PMs forget settings).
2. **Should the project default to inherit-from-process or take a snapshot at create-time?** — proposal: inherit-from-process (newer process defaults flow through). User can lock by explicit `purpose_routing` set if they want stability.
3. **Should "preset applied" be auditable?** — proposal: yes; record in `app_setting` change log table (deferred — no log table yet, would require schema add).
4. **For high-stakes purposes (extract_text_spec / conflict_pass), should the user have to *acknowledge a warning* before configuring a non-default route?** — proposal: yes in UI; CLI just prints a one-line warning.

---

## 11. Implementation sequence (when picked up)

1. Settings additions + `resolve_route()` (~30 min).
2. `call_llm` made provider/model-optional + conditional auth precheck (~20 min).
3. Air-gap preflight (~20 min).
4. Project-level routing read/write helpers (~20 min).
5. Existing extractors loosen `model` arg (~10 min total).
6. CLI `routing` subcommand (~30 min).
7. Smoke test: switch triage to a fake "ollama" provider, confirm preflight + per-call resolution + reproducibility record (~20 min).

Estimated work: half a session. Lands cleanly alongside the OCR / DWG / cost-preview items already queued for the next pipeline session.

---

*Designed in the same spirit as CONTEXT.md §0: anything that bakes today's assumption (single global provider) into the codebase rather than into configuration is a regression. This routing seam is the smallest change that opens the local-tools door without compromising the headline-quality cloud path.*
