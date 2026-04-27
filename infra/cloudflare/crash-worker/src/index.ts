/**
 * Meridian crash-report intake Worker (§3.6).
 *
 * Accepts POST requests from Meridian desktop installations and stores
 * the crash payload in Cloudflare KV with a 90-day TTL. Receive-and-store;
 * no email send today (DNS pinned — once `t-bionic.<TLD>` is live we add
 * MailChannels here so support@ gets the digest).
 *
 * Optional features (set via `wrangler secret put`):
 *   MERIDIAN_INTAKE_TOKEN  → require X-Meridian-Auth: <token> on every POST
 *   CRASH_NOTIFY_WEBHOOK   → fire a Discord/Slack webhook on each crash
 *
 * Inspect crashes:
 *   wrangler kv:key list --binding MERIDIAN_CRASHES
 *   wrangler kv:key get  --binding MERIDIAN_CRASHES "<key>"
 * or via the Cloudflare dashboard's KV namespace browser.
 */

interface Env {
  MERIDIAN_CRASHES: KVNamespace;
  CRASH_NOTIFY_WEBHOOK?: string;
  MERIDIAN_INTAKE_TOKEN?: string;
}

const MAX_BODY_SIZE = 1_000_000; // 1 MB hard cap
const TTL_SECONDS = 90 * 24 * 60 * 60; // 90 days

export default {
  async fetch(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    if (req.method === "GET") {
      // Liveness probe — useful for the desktop client to verify reachability
      // before attempting a crash POST.
      return jsonResponse({ ok: true, service: "meridian-crash-intake" }, 200);
    }
    if (req.method !== "POST") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    if (env.MERIDIAN_INTAKE_TOKEN) {
      const provided = req.headers.get("x-meridian-auth");
      if (provided !== env.MERIDIAN_INTAKE_TOKEN) {
        return new Response("Unauthorized", { status: 401 });
      }
    }

    const cl = parseInt(req.headers.get("content-length") ?? "0", 10);
    if (cl > MAX_BODY_SIZE) {
      return new Response("Payload Too Large", { status: 413 });
    }

    const body = await req.text();
    if (body.length > MAX_BODY_SIZE) {
      return new Response("Payload Too Large", { status: 413 });
    }

    let payload: unknown;
    try {
      payload = JSON.parse(body);
    } catch {
      return new Response("Invalid JSON", { status: 400 });
    }

    // Mint a key: ISO timestamp + random suffix so concurrent crashes don't
    // collide. Sortable lexicographically.
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    const suffix = crypto.randomUUID().slice(0, 8);
    const key = `crash_${ts}_${suffix}`;

    await env.MERIDIAN_CRASHES.put(key, JSON.stringify(payload), {
      expirationTtl: TTL_SECONDS,
    });

    if (env.CRASH_NOTIFY_WEBHOOK) {
      // Fire-and-forget via ctx.waitUntil so the response isn't delayed by
      // the webhook latency. Discord / Slack expect `content` field; both
      // accept a plain text message in that key.
      ctx.waitUntil(
        fetch(env.CRASH_NOTIFY_WEBHOOK, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            content: `🛑 Meridian crash report received — key \`${key}\``,
          }),
        }).catch(() => {
          /* swallow notification failures; KV write is the source of truth */
        }),
      );
    }

    return jsonResponse({ ok: true, key }, 200);
  },
};

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}
