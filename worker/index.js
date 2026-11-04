// HackerTV Worker. Static files in ./public are served by Workers Assets without running
// this code; only /api/* reaches it (see run_worker_first in wrangler.jsonc).
//
// POST /api/presence {id, ch}         -> heartbeat; returns {total, channel}
// POST /api/presence {id, leave:true} -> viewer turned the TV off / closed the tab
//
// Viewers are anonymous random session ids kept in memory only (no IPs, nothing stored).

const WINDOW_MS = 150_000;   // a viewer counts if seen in the last 2.5 min (heartbeat: 60 s)
const MAX_SESSIONS = 100_000;
const ID_RE = /^[A-Za-z0-9-]{8,64}$/;
const CH_RE = /^[a-z0-9-]{1,40}$/;
const MAX_BODY = 512;        // a heartbeat is ~80 bytes

export class Presence {
  constructor(ctx) {
    this.ctx = ctx;
    this.sessions = new Map(); // id -> { t, ch }
    this.lastPrune = 0;
  }

  prune(now) {
    if (now - this.lastPrune < 5_000) return;
    this.lastPrune = now;
    for (const [id, s] of this.sessions) if (now - s.t > WINDOW_MS) this.sessions.delete(id);
  }

  async fetch(request) {
    let body;
    try {
      const text = await readCapped(request);
      if (text === null) return json({ error: "too large" }, 413);
      body = JSON.parse(text);
    } catch { return json({ error: "bad json" }, 400); }
    const { id, ch, leave } = body || {};
    if (typeof id !== "string" || !ID_RE.test(id)) return json({ error: "bad id" }, 400);
    const now = Date.now();
    this.prune(now);

    if (leave) {
      this.sessions.delete(id);
      return json({ ok: true });
    }
    if (typeof ch !== "string" || !CH_RE.test(ch)) return json({ error: "bad channel" }, 400);
    if (this.sessions.has(id) || this.sessions.size < MAX_SESSIONS) this.sessions.set(id, { t: now, ch });

    let channel = 0;
    for (const s of this.sessions.values()) if (s.ch === ch) channel++;
    return json({ total: this.sessions.size, channel });
  }
}

// Read at most MAX_BODY bytes. content-length can be absent (chunked), so cap the stream itself.
async function readCapped(request) {
  if (!request.body) return "";
  const reader = request.body.getReader();
  const chunks = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_BODY) { await reader.cancel(); return null; }
    chunks.push(value);
  }
  const buf = new Uint8Array(size);
  let o = 0;
  for (const c of chunks) { buf.set(c, o); o += c.byteLength; }
  return new TextDecoder().decode(buf);
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
    },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/presence") {
      if (request.method !== "POST") return json({ error: "POST only" }, 405);
      // Reject oversized bodies before they reach the Durable Object.
      if (Number(request.headers.get("content-length") || 0) > MAX_BODY) return json({ error: "too large" }, 413);
      const stub = env.PRESENCE.get(env.PRESENCE.idFromName("global"));
      return stub.fetch(request);
    }
    if (url.pathname.startsWith("/api/")) return json({ error: "not found" }, 404);
    return env.ASSETS.fetch(request);
  },
};
