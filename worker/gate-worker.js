/**
 * Cloudflare Worker: Zugriffssperre + Tippspiel-Speicher für lolkleena.
 *
 * - Lässt nur "domi" und "lisa" (HTTP Basic Auth) durch die komplette Seite.
 * - Reicht alles außer /api/* unverändert an GitHub Pages weiter.
 * - /api/tipp (GET/PUT) ersetzt jsonblob.com als Speicher, per Cloudflare KV.
 * - /api/tipp/pick (POST) schreibt einen einzelnen Tipp und mergt ihn
 *   serverseitig in den Speicher - und zwar ausschließlich unter dem Namen des
 *   eingeloggten Nutzers. Ohne das würden zwei gleichzeitig geöffnete Browser
 *   den jeweils zuletzt geschriebenen Gesamtstand hochladen und sich damit die
 *   Tipps des anderen überschreiben.
 * - /api/whoami liefert den eingeloggten Namen, damit das Frontend kein
 *   eigenes (unsicheres) Namensfeld mehr braucht.
 *
 * Setup: siehe README.md Abschnitt "Tippspiel absichern (Cloudflare Worker)".
 */

const ORIGIN = "https://dommebraun8-arch.github.io/lolkleena";
const KV_KEY = "store";
const MAX_BODY = 256 * 1024;

export default {
  async fetch(request, env) {
    const auth = checkAuth(request, env);
    if (!auth) {
      return new Response("Zugriff verweigert", {
        status: 401,
        headers: { "WWW-Authenticate": 'Basic realm="lolkleena"' },
      });
    }

    const url = new URL(request.url);

    if (url.pathname === "/api/whoami") {
      return Response.json({ name: auth.name });
    }

    if (url.pathname === "/api/tipp/pick") {
      return handlePick(request, env, auth.name);
    }

    if (url.pathname === "/api/tipp") {
      return handleTipp(request, env);
    }

    const originResp = await fetch(ORIGIN + url.pathname + url.search, {
      headers: { "user-agent": request.headers.get("user-agent") || "" },
    });
    const resp = new Response(originResp.body, originResp);
    resp.headers.delete("content-security-policy");
    return resp;
  },
};

function checkAuth(request, env) {
  const header = request.headers.get("Authorization");
  if (!header || !header.startsWith("Basic ")) return null;
  let decoded;
  try {
    decoded = atob(header.slice(6));
  } catch (e) {
    return null;
  }
  const sep = decoded.indexOf(":");
  if (sep === -1) return null;
  const user = decoded.slice(0, sep);
  const pass = decoded.slice(sep + 1);

  if (user === "domi" && pass === env.DOMI_PASSWORD) return { name: "Domi" };
  if (user === "lisa" && pass === env.LISA_PASSWORD) return { name: "Lisa" };
  return null;
}

async function handleTipp(request, env) {
  if (request.method === "GET") {
    const data = await env.TIPP_KV.get(KV_KEY);
    return new Response(data || '{"picks":{},"worlds":{}}', {
      headers: { "content-type": "application/json" },
    });
  }
  if (request.method === "PUT") {
    const body = await request.text();
    if (body.length > MAX_BODY) return new Response("Zu groß", { status: 413 });
    try {
      JSON.parse(body);
    } catch (e) {
      return new Response("Kein gültiges JSON", { status: 400 });
    }
    await env.TIPP_KV.put(KV_KEY, body);
    return new Response("ok");
  }
  return new Response("Method not allowed", { status: 405 });
}

async function readStore(env) {
  const raw = await env.TIPP_KV.get(KV_KEY);
  let store = null;
  if (raw) {
    try {
      store = JSON.parse(raw);
    } catch (e) {
      store = null;
    }
  }
  if (!store || typeof store !== "object" || Array.isArray(store)) store = {};
  if (!store.picks || typeof store.picks !== "object") store.picks = {};
  if (!store.worlds || typeof store.worlds !== "object") store.worlds = {};
  return store;
}

function worldsSection(store, season, name) {
  if (!store.worlds[season] || typeof store.worlds[season] !== "object") {
    store.worlds[season] = {};
  }
  const s = store.worlds[season];
  [name].forEach((k) => {
    if (!s[k] || typeof s[k] !== "object") s[k] = {};
  });
  return s[name];
}

function isId(v, maxLen) {
  return typeof v === "string" && v.length > 0 && v.length <= (maxLen || 64);
}
function isScore(v) {
  return Number.isInteger(v) && v >= 0 && v <= 5;
}
function cleanIdList(v, limit) {
  if (!Array.isArray(v)) return [];
  const out = [];
  for (const item of v) {
    if (isId(item) && !out.includes(item)) out.push(item);
    if (out.length >= limit) break;
  }
  return out;
}

async function handlePick(request, env, player) {
  if (request.method !== "POST") {
    return new Response("Method not allowed", { status: 405 });
  }
  const body = await request.text();
  if (body.length > MAX_BODY) return new Response("Zu groß", { status: 413 });

  let patch;
  try {
    patch = JSON.parse(body);
  } catch (e) {
    return new Response("Kein gültiges JSON", { status: 400 });
  }
  if (!patch || typeof patch !== "object") {
    return new Response("Ungültiger Tipp", { status: 400 });
  }

  const store = await readStore(env);
  const ts = Date.now();

  if (patch.kind === "match") {
    if (!isId(patch.matchId) || !isScore(patch.s1) || !isScore(patch.s2)) {
      return new Response("Ungültiger Match-Tipp", { status: 400 });
    }
    if (!store.picks[patch.matchId] || typeof store.picks[patch.matchId] !== "object") {
      store.picks[patch.matchId] = {};
    }
    store.picks[patch.matchId][player] = { s1: patch.s1, s2: patch.s2, ts };
  } else if (patch.kind === "champion") {
    if (!isId(patch.season, 8) || !isId(patch.teamKey)) {
      return new Response("Ungültiger Champion-Tipp", { status: 400 });
    }
    worldsSection(store, patch.season, "champion")[player] = {
      teamKey: patch.teamKey,
      teamName: isId(patch.teamName, 128) ? patch.teamName : patch.teamKey,
      ts,
    };
  } else if (patch.kind === "swiss") {
    if (!isId(patch.season, 8)) {
      return new Response("Ungültiger Swiss-Tipp", { status: 400 });
    }
    worldsSection(store, patch.season, "swiss")[player] = {
      perfect: cleanIdList(patch.perfect, 2),
      winless: cleanIdList(patch.winless, 2),
      advance: cleanIdList(patch.advance, 8),
      ts,
    };
  } else if (patch.kind === "bracket") {
    if (!isId(patch.season, 8) || !patch.picks || typeof patch.picks !== "object") {
      return new Response("Ungültiger Bracket-Tipp", { status: 400 });
    }
    const picks = {};
    for (const [slot, team] of Object.entries(patch.picks)) {
      if (isId(slot, 16) && isId(team)) picks[slot] = team;
      if (Object.keys(picks).length >= 32) break;
    }
    worldsSection(store, patch.season, "bracket")[player] = { picks, ts };
  } else {
    return new Response("Unbekannte Tipp-Art", { status: 400 });
  }

  await env.TIPP_KV.put(KV_KEY, JSON.stringify(store));
  return Response.json(store);
}
