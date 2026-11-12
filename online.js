/* The planner online: open to anyone with the address, and the data the Mac sends to Supabase (scraper/online.py).
   Visitors see the public part and keep their own plan in their browser; signing in (only users made in
   Supabase) keeps your plan in your account, and the owner also gets the private part.
   Only used when config.js (made by scripts/build-site.sh from Vercel's environment variables) names a project;
   on the Mac the page talks to the local server instead. */
(function () {
  const cfg = window.BOSTADSPLAN || {};
  const LOCAL = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
  if (LOCAL || !cfg.url || !cfg.anonKey) { window.ONLINE_API = null; return; }

  const SB_JS = "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/dist/umd/supabase.min.js";
  const BUCKET = "data", SIGN_FOR = 3600, CHUNK = 200;
  let sb = null, user = null;

  function loadScript(src) {
    return new Promise((ok, fail) => { const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = fail; document.head.appendChild(s); });
  }

  // The login, opened from "Logga in": e-mail and password, and an account for anyone who wants one
  function loginScreen(msg) {
    if (document.getElementById("login")) return;
    const d = document.createElement("div");
    d.id = "login";
    d.innerHTML = `<form>
        <h2>Bostadsplan</h2>
        <p>Logga in eller skapa ett konto, så sparas din plan och dina sparade lägenheter på kontot och följs av bostadsletaren.</p>
        <input type="email" name="email" required autocomplete="email" placeholder="namn@exempel.se" aria-label="E-postadress">
        <input type="password" name="password" required minlength="8" autocomplete="current-password" placeholder="Lösenord, minst 8 tecken" aria-label="Lösenord">
        <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn primary" type="submit">Logga in</button><button class="btn" type="button" data-signup>Skapa konto</button><button class="btn ghost" type="button" data-close>Avbryt</button></div>
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
    d.querySelector("[data-close]").addEventListener("click", () => d.remove());
    d.querySelector("[data-signup]").addEventListener("click", async () => {
      const f = d.querySelector("form"), m = d.querySelector(".msg");
      if (!f.reportValidity()) return;
      m.textContent = "Skapar kontot…";
      const { data, error } = await sb.auth.signUp({ email: f.email.value.trim(), password: f.password.value });
      if (error) { m.textContent = "Det gick inte: " + error.message; return; }
      if (data.session) return location.reload();
      m.textContent = "Klart. Bekräfta adressen med länken i mejlet och logga sedan in.";
    });
    d.querySelector("form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const email = e.target.email.value.trim(), password = e.target.password.value, m = d.querySelector(".msg");
      m.textContent = "Loggar in…";
      const { error } = await sb.auth.signInWithPassword({ email, password });
      if (!error) return location.reload();
      m.textContent = "Fel e-post eller lösenord.";
    });
  }

  const ready = (async () => {
    await loadScript(SB_JS);
    sb = window.supabase.createClient(cfg.url, cfg.anonKey, { auth: { persistSession: true, detectSessionInUrl: true } });
    const { data } = await sb.auth.getSession();
    user = data.session ? data.session.user.id : null;
    window.ONLINE_API.signedIn = !!user;
    return true;
  })().catch(() => false);

  async function file(name) {
    if (!(await ready)) return null;
    if (name.startsWith("private/") && !user) return null;
    const { data, error } = await sb.storage.from(BUCKET).download(name);
    if (error || !data) return null;
    return JSON.parse(await data.text());
  }

  // The local server's addresses, answered from Supabase
  // Travel times and the digest are the owner's (they show the owner's addresses and saved apartments): for anyone else
  // they're not there, and the page shows the apartments without them
  const FILES = { "/api/status": "public/status.json", "/api/predictions": "public/predictions.json",
    "/api/travel": "private/travel.json", "/api/digest": "private/digest.json", "/api/routes": "private/routes.json" };
  async function get(path) {
    if (path === "/api/floorplans") return floorplans();
    // The owner gets the full listings (with the agents' descriptions); anyone else the public ones
    const out = path === "/api/listings" ? (await file("private/listings.json")) || (await file("public/listings.json"))
      : FILES[path] ? await file(FILES[path]) : null;
    if (out && path === "/api/travel") { delete out.shops; out.running = false; }
    if (out && path === "/api/status") { out.running = false; out.scheduled = true; out.online = true; }
    return out;
  }

  // Floor plans: the names by apartment, and a signed link for each (they're in a private bucket)
  const URLS = {};
  async function floorplans() {
    const by = (await file("private/floorplans.json")) || {};
    const names = [...new Set(Object.values(by).flat())].filter((n) => !URLS[n]);
    for (let i = 0; i < names.length; i += CHUNK) {
      const { data } = await sb.storage.from(BUCKET).createSignedUrls(names.slice(i, i + CHUNK).map((n) => "private/floorplans/" + n), SIGN_FOR);
      for (const x of data || []) if (x.signedUrl) URLS[x.path.slice("private/floorplans/".length)] = x.signedUrl;
    }
    return by;
  }

  async function loadPlan() {
    if (!(await ready) || !user) return null; // a visitor's plan stays in their browser
    const { data, error } = await sb.from("plans").select("plan,updated_at").eq("user_id", user).maybeSingle();
    return error || !data ? null : data.plan;
  }
  async function savePlan(plan, scrape) {
    if (!(await ready) || !user) return false;
    const { error } = await sb.from("plans").upsert({ user_id: user, plan, scrape, updated_at: new Date().toISOString() });
    return !error;
  }
  async function signOut() { await sb.auth.signOut(); location.reload(); }

  window.ONLINE_API = { ready, get, loadPlan, savePlan, signOut, signIn: () => loginScreen(), signedIn: false, planUrl: (n) => URLS[n] || "" };
})();
