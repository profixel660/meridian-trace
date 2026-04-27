# Meridian crash-intake Worker

A Cloudflare Worker that receives crash reports from Meridian desktop
installations and stores them in KV with a 90-day TTL. §3.6 implementation.

## What it does

- `GET /` — liveness probe. Returns `{"ok":true,"service":"meridian-crash-intake"}`.
- `POST /` — accepts a JSON crash payload (max 1 MB), stores it in KV under
  a sortable timestamped key, returns `{"ok":true,"key":"crash_..."}`.
- Optionally fires a Discord/Slack webhook on every crash, fire-and-forget.
- Optionally enforces a shared-secret header (`X-Meridian-Auth`) on POSTs.

No email send today — DNS for `t-bionic.<TLD>` is pinned. Once the domain
lands, MailChannels integration goes here.

## One-time deploy

You need a Cloudflare account (free tier is plenty for this volume) and
Node.js installed locally.

```powershell
# From the repo root, in PowerShell or bash:
cd infra/cloudflare/crash-worker
npm install                        # pulls wrangler + Workers types
npx wrangler login                 # opens browser, OAuth-style flow

# Create the KV namespace (one-time)
npx wrangler kv:namespace create MERIDIAN_CRASHES
# → prints something like:
#   { binding = "MERIDIAN_CRASHES", id = "abc123def456..." }

# Paste the returned `id` into wrangler.toml (replacing TODO_PASTE_KV_ID),
# then commit the change so future deploys use the same namespace.

# Optional: set a shared-secret token so only Meridian installations can
# POST. Pick a long random value; you'll bake it into the desktop client
# in a follow-up commit.
npx wrangler secret put MERIDIAN_INTAKE_TOKEN
# → wrangler prompts for the value, stores it encrypted

# Optional: set a Discord/Slack webhook for real-time notification.
npx wrangler secret put CRASH_NOTIFY_WEBHOOK
# → paste the full webhook URL when prompted

# Deploy
npx wrangler deploy
# → prints the public URL, e.g.
#   https://meridian-crash-intake.<your-cf-subdomain>.workers.dev
```

Copy the public URL — it goes into `src/meridian/crash/sender.py` to
replace the deferred `_PLACEHOLDER_URL` constant.

## Verifying it works

```powershell
# Liveness check
curl https://meridian-crash-intake.<your-subdomain>.workers.dev/

# Store a test payload (omit -H X-Meridian-Auth if you didn't set the
# secret token)
curl -X POST https://meridian-crash-intake.<your-subdomain>.workers.dev/ \
  -H "content-type: application/json" \
  -H "x-meridian-auth: <your-token>" \
  -d '{"version":"0.2.0-alpha","error":"test crash","stack":"..."}'

# List stored crashes
npx wrangler kv:key list --binding MERIDIAN_CRASHES

# Read a specific crash
npx wrangler kv:key get --binding MERIDIAN_CRASHES "crash_2026-..."
```

## Inspecting in the dashboard

Cloudflare dashboard → Workers & Pages → KV → `MERIDIAN_CRASHES` namespace.
Each crash report is one row. Click to view the JSON payload.

## Live-tail logs while debugging

```powershell
npx wrangler tail
```

Streams every Worker invocation to your terminal — useful for confirming
that crash POSTs are landing during the first few real installs.

## When DNS is ready

Once `t-bionic.<TLD>` and an inbox at `support@` are live, add a
MailChannels send to the Worker so each crash also forwards as an email.
MailChannels is free for Cloudflare Workers; the integration is ~30 LOC
of `fetch("https://api.mailchannels.net/tx/v1/send", ...)`. Tracked as a
follow-up; the KV write stays as the source of truth either way.
