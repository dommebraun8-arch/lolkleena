/**
 * Cloudflare Worker: Zugriffssperre + Tippspiel-Speicher für lolkleena.
 *
 * - Lässt nur "domi" und "lisa" (HTTP Basic Auth) durch die komplette Seite.
 * - Reicht alles außer /api/* unverändert an GitHub Pages weiter.
 * - /api/tipp (GET/PUT) ersetzt jsonblob.com als Speicher, per Cloudflare KV.
 * - /api/whoami liefert den eingeloggten Namen, damit das Frontend kein
 *   eigenes (unsicheres) Namensfeld mehr braucht.
 *
 * Setup: siehe README.md Abschnitt "Tippspiel absichern (Cloudflare Worker)".
 */

const ORIGIN = "https://dommebraun8-arch.github.io/lolkleena";

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
    const data = await env.TIPP_KV.get("store");
    return new Response(data || '{"picks":{}}', {
      headers: { "content-type": "application/json" },
    });
  }
  if (request.method === "PUT") {
    const body = await request.text();
    await env.TIPP_KV.put("store", body);
    return new Response("ok");
  }
  return new Response("Method not allowed", { status: 405 });
}
