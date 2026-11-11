/* The planner online: your login, and the data the Mac sends to Supabase (scraper/online.py).
   Only used when config.js (made by scripts/build-site.sh from Vercel's environment variables) names a project;
   on the Mac the page talks to the local server instead. */
(function () {
  const cfg = window.BOSTADSPLAN || {};
  const LOCAL = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
  if (LOCAL || !cfg.url || !cfg.anonKey) { window.ONLINE_API = null; return; }

  const SB_JS = "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/dist/umd/supabase.min.js";
  const BUCKET = "data", SIGN_FOR = 3600, CHUNK = 200;
  let sb = null;

  function loadScript(src) {
    return new Promise((ok, fail) => { const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = fail; document.head.appendChild(s); });
  }

  // The login: a link by e-mail to the address your Supabase user has (sign-up is off, so no one else gets in)
  function loginScreen(msg) {
    const d = document.createElement("div");
    d.id = "login";
    d.innerHTML = `<form>
        <h2>Bostadsplan</h2>
        <p>Logga in med din e-postadress, så skickar vi en inloggningslänk.</p>
        <input type="email" name="email" required autocomplete="email" placeholder="namn@exempel.se" aria-label="E-postadress">
        <button class="btn primary" type="submit">Skicka länken</button>
        <p class="msg" role="status">${msg || ""}</p>
      </form>`;
    const css = document.createElement("style");
    css.textContent = `#login{position:fixed;inset:0;z-index:9999;display:grid;place-items:center;padding:16px;background:var(--ground);color:var(--text)}
      #login form{width:min(380px,100%);display:grid;gap:12px;padding:var(--pad);border:1px solid var(--border);border-radius:var(--r);background:var(--surface)}
      #login h2{margin:0;font-size:var(--fs-title)}#login p{margin:0;color:var(--muted)}
      #login input{height:var(--control-height);padding:0 12px;border:1px solid var(--control);border-radius:var(--r-control);background:var(--surface);color:var(--text);font:inherit}
      #login .msg{min-height:20px;font-size:var(--fs-small)}`;
    document.head.appendChild(css);
    document.body.appendChild(d);
    d.querySelector("form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const email = e.target.email.value.trim(), m = d.querySelector(".msg");
      m.textContent = "Skickar…";
      const { error } = await sb.auth.signInWithOtp({ email, options: { shouldCreateUser: false, emailRedirectTo: location.origin + location.pathname } });
      m.textContent = error ? "Det gick inte: " + error.message : "Klart. Öppna länken i mejlet på den här enheten.";
    });
  }

  const ready = (async () => {
    await loadScript(SB_JS);
    sb = window.supabase.createClient(cfg.url, cfg.anonKey, { auth: { persistSession: true, detectSessionInUrl: true } });
    const { data } = await sb.auth.getSession();
    if (!data.session) { loginScreen(); return false; }
    return true;
  })().catch(() => { loginScreen("Kunde inte nå inloggningen. Försök igen om en stund."); return false; });

  async function file(name) {
    if (!(await ready)) return null;
    const { data, error } = await sb.storage.from(BUCKET).download(name);
    if (error || !data) return null;
    return JSON.parse(await data.text());
  }

  // The local server's addresses, answered from Supabase
  const FILES = { "/api/listings": "listings.json", "/api/travel": "travel.json", "/api/status": "status.json", "/api/digest": "digest.json" };
  async function get(path) {
    if (path === "/api/floorplans") return floorplans();
    const out = FILES[path] ? await file(FILES[path]) : null;
    if (out && path === "/api/travel") { delete out.shops; out.running = false; }
    if (out && path === "/api/status") { out.running = false; out.scheduled = true; out.online = true; }
    return out;
  }

  // Floor plans: the names by apartment, and a signed link for each (they're in a private bucket)
  const URLS = {};
  async function floorplans() {
    const by = (await file("floorplans.json")) || {};
    const names = [...new Set(Object.values(by).flat())].filter((n) => !URLS[n]);
    for (let i = 0; i < names.length; i += CHUNK) {
      const { data } = await sb.storage.from(BUCKET).createSignedUrls(names.slice(i, i + CHUNK).map((n) => "floorplans/" + n), SIGN_FOR);
      for (const x of data || []) if (x.signedUrl) URLS[x.path.slice("floorplans/".length)] = x.signedUrl;
    }
    return by;
  }

  async function loadPlan() {
    if (!(await ready)) return null;
    const { data, error } = await sb.from("plan").select("plan,updated_at").eq("id", 1).maybeSingle();
    return error || !data ? null : data.plan;
  }
  async function savePlan(plan, scrape) {
    if (!(await ready)) return false;
    const { error } = await sb.from("plan").upsert({ id: 1, plan, scrape, updated_at: new Date().toISOString() });
    return !error;
  }
  async function signOut() { await sb.auth.signOut(); location.reload(); }

  window.ONLINE_API = { ready, get, loadPlan, savePlan, signOut, planUrl: (n) => URLS[n] || "" };
})();
