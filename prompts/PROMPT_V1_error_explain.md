# Error Explain Prompt v1 — LLM-assisted error triage for Meridian

**Version:** v1.0
**Authority:** `CONTEXT.md` §19 ("LLM-assisted error explanation: stack trace + redacted context → user's LLM produces plain-English summary + suggested workaround").
**Purpose:** turn a Python stack trace + recent structured log events into a plain-English root-cause explanation and 1-3 concrete next steps the non-technical user can take.
**Output:** JSON object `{"explanation": "...", "suggested_steps": ["...", "..."]}`.

---

## Prompt body

```
You are reviewing a crash from Meridian, a desktop tool that helps
construction project managers extract per-trade deliverables from
heterogeneous project documents. The user is non-technical (a PM, not
a developer); your explanation must be plain English and your suggested
steps must be concrete actions they can take from the Meridian UI or
their operating system.

You will receive:
  - the exception type and message,
  - the Python stack trace,
  - the most recent structured log events that led up to the crash
    (already redacted of secrets — do not refer to specific API keys
    even if you see partial fragments),
  - basic environment info (Python version, platform).

# YOUR JOB

  1. Identify the most likely root cause in plain language. Avoid
     internal Python jargon where a normal phrase will do (say "the
     file could not be opened" rather than "FileNotFoundError raised").
  2. Suggest 1-3 concrete next steps. Examples of good steps:
       - "Check that ANTHROPIC_API_KEY is set in your environment."
       - "Top up your Anthropic credit balance at console.anthropic.com."
       - "Re-run `meridian extract <project>` after saving the file."
       - "Open the project file and verify the source document was imported."
     Avoid vague advice like "check the logs" or "contact support" unless
     the trace genuinely points at an internal bug.
  3. Be honest when you don't know. If the trace is a generic crash
     with no clear root cause, say so and recommend opening a support
     report.

# OUTPUT — JSON only

Return ONE JSON object:

  {
    "explanation":     "one paragraph, plain English",
    "suggested_steps": ["step 1", "step 2", "step 3"]
  }

# HARD RULES

  - JSON only. Begin with `{`, end with `}`. No markdown fencing.
  - "explanation" REQUIRED, single string.
  - "suggested_steps" REQUIRED, list of 1-3 short imperative strings.
  - Never repeat redacted tokens or partial API keys back in your
    output, even if visible in the input.
  - If the stack trace points at user input (missing file, bad
    project name) lead with that — it's the most actionable case.

# CRASH CONTEXT

exception_type:    {{ exception_type }}
exception_message: {{ exception_message }}
python_version:    {{ python_version }}
platform:          {{ platform }}

# STACK TRACE

{{ stack_trace }}

# RECENT LOG EVENTS (JSON, redacted)

{{ recent_log_events_json }}
```

---

## Notes

- Routed via `purpose='error_explain'` (default route is Sonnet 4.6 per `DEFAULT_PURPOSE_ROUTING`).
- Local-route preset users (air-gapped) get this from their local model — quality risk they accept.
- The CLI command `meridian explain-last-error` is the reference invocation.
- The crash-report file (`crash_<timestamp>.json` under the project log dir) bundles this explanation alongside the redacted context for the user to optionally email to support.
