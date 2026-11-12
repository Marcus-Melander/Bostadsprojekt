"""Sign up for a viewing: the agent's form filled in, in a Chrome window, for you to check and send.

The planner's "Anmäl till visning" (server.py's POST /api/book) starts this for one apartment and one showing. It opens
the apartment's page in a visible Chrome (finder.launch, Scrapling's session on your installed Chrome), answers the
cookie banner with necessary cookies only, finds the agent's viewing sign-up form and fills in your name, e-mail and
phone from the plan (plan.json's `contact`), and picks the showing when the form asks which one.

It never sends anything: the send button is left for you, the form is outlined and a note at the top of the window says
what's left to do. The window stays open until you close it. It never ticks a box either: consent is yours to give, and
newsletters and marketing stay unticked. It stops, and says so, at a captcha you'd have to click (reCAPTCHA's "Jag är
inte en robot", hCaptcha, a Cloudflare check page), BankID or a login, and when the site refuses (a 403 or the like is
never tried again) or its robots.txt asks visitors not to. Captchas that solve themselves while you fill in the form
(FriendlyCaptcha, Cloudflare Turnstile, invisible reCAPTCHA) are left alone and don't stop it.

How the form is found: every form on the page (and in its frames) is scored by what it asks for (a name, an e-mail or a
phone, a choice of showing) and what it says (visning, not värdering or nyhetsbrev); a form that's still hidden is opened
with the agent's own button ("Boka visning", "Anmäl dig" and the like), the one next to this showing's date when there are
several. Some agents send you to Vitec's booking page (bokavisning.maklare.vitec.net), which is then opened in the same
window. HINTS has each agent's own buttons where the generic search doesn't find them.

What happened goes to data/bookings.json by apartment, without your details: {url: {start, state, msg, at, agent, pid}}
with state opening, filled, partial, noform, captcha, bankid, login, refused, error. The planner shows it on the button.

    .venv/bin/python scraper/booking.py URL START            # START as in listings.json, e.g. 2026-11-15T13:10
    .venv/bin/python scraper/booking.py --test URL START     # Test Testsson, no window, a screenshot, nothing kept
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents  # noqa: E402
import finder  # noqa: E402

DATA = finder.DATA
KEEP_OPEN_S = 3 * 3600  # the window closes by itself after this, in case it's forgotten
TEST = {"name": "Test Testsson", "email": "test.testsson@example.com", "phone": "070-000 00 00"}

# The buttons that open a viewing sign-up, and those that open something else
OPEN_RX = re.compile(r"boka\s+(en\s+)?visning(stid)?|anmäl\s+(dig|er|mig)(\s+till\s+visning(en)?)?|anmäl(an)?\s+till\s+visning|"
                     r"jag\s+vill\s+(anmäla\s+mig|boka|gå\s+på\s+visning)|visningsanmälan|^\s*(boka|anmäl)\s*$|gå\s+på\s+visning", re.I)
NOT_OPEN_RX = re.compile(r"värdering|intresse|nyhetsbrev|budgiv|lån|försäkring|kalender|möte|annons|bevaka", re.I)

# Per agent, where the generic search needs help: `open`, more button texts that open the sign-up, tried first
HINTS = {
    # "Anmäl till visning" by the showing, then a choice of BankID or by hand: by hand
    "Fastighetsbyrån": {"open": [r"anmälan till visning", r"fyll i uppgifter(na)? manuellt"]},
    # A chat: "Boka visning", then the showing's time as a button, then Fortsätt to the details (before anything is filled)
    "Erik Olsson": {"open": [r"^\s*boka visning\s*$", r"^\s*fortsätt\s*$"]},
    "Länsförsäkringar": {"open": [r"^\s*boka visning\s*$", r"välj tid"]},
    # A side panel: the showing's time as a button under its day, then Gå vidare to the details
    "Bjurfors": {"open": [r"^\s*gå vidare\s*$", r"boka visningstid"]},
    "HusmanHagberg": {"open": [r"^\s*boka visning\s*$"]},
    "Notar": {"open": [r"anmäl dig"]},
}

STATES_SV = {
    "filled": "Ifyllt, skicka i fönstret",
    "partial": "Delvis ifyllt, titta i fönstret",
    "noform": "Hittade inget formulär",
    "captcha": "Stannade vid en captcha",
    "bankid": "Kräver BankID, fortsätt i fönstret",
    "login": "Kräver inloggning, fortsätt i fönstret",
    "refused": "Mäklarens sajt sa nej",
    "error": "Något gick fel",
}


# ---------- data/bookings.json ----------

def record(url, **fields):
    """Merge one apartment's entry into data/bookings.json; several bookings can run at once, so under a lock."""
    path = DATA / "bookings.json"
    DATA.mkdir(exist_ok=True)
    with open(DATA / ".bookings.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        allb = finder.read_json(path, {})
        allb[url] = {**allb.get(url, {}), **fields, "at": dt.datetime.now().isoformat(timespec="seconds")}
        finder.write_json(path, allb)


# ---------- Reading the page (in the page itself, so every kind of form reads the same) ----------

SCAN_JS = r"""([day, month, time, months]) => {
  const vis = (e) => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 2 && r.height > 2 && s.visibility !== "hidden" && s.display !== "none" && +s.opacity > 0.05 && r.right > 0 && r.left < innerWidth + 50; };
  const shown = (e) => { for (let x = e; x && x !== document.body; x = x.parentElement) if (getComputedStyle(x).display === "none" || x.getAttribute("aria-hidden") === "true") return false; return true; };
  const label = (e) => [e.labels && [...e.labels].map((l) => l.innerText).join(" "), e.getAttribute("aria-label"), e.placeholder, e.name, e.id,
    e.getAttribute("autocomplete"), e.closest("label") && e.closest("label").innerText].filter(Boolean).join(" ").toLowerCase().slice(0, 200);
  const trap = (e, l) => /honeypot|ohnohoney|_hf_h|hp_field|\bhp\b|\btrap\b/.test((e.name + " " + e.id + " " + e.className).toLowerCase())
    || /lämna (detta fält |fältet )?tomt|leave (this )?(field )?(blank|empty)/.test(l) || e.tabIndex === -1 || e.readOnly || e.disabled;
  // A showing's date and time, the way agents write them: "15 november", "15 nov.", "15/11", "2026-11-15"; "13:10", "13.10", "kl 13"
  const dayRx = new RegExp(`(^|\\D)0?${day}(\\s*(e|:e)?\\s*(${months.join("|")})|\\s*/\\s*0?${month}(\\D|$))|-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`, "i");
  const [hh, mm] = time.split(":").map(Number);
  const timeRx = new RegExp(`(^|\\D)0?${hh}[:.]${String(mm).padStart(2, "0")}(\\D|$)` + (mm === 0 ? `|kl\\.?\\s*0?${hh}(\\D|$)` : ""), "i");
  const anyDate = new RegExp(`\\d{1,2}\\s*(${months.join("|")})|\\d{1,2}/\\d{1,2}|\\d{4}-\\d{2}-\\d{2}|måndag|tisdag|onsdag|torsdag|fredag|lördag|söndag`, "i");
  const anyTime = /\d{1,2}[:.]\d{2}/;
  const kind = (l, type) => /för-?\s*och efternamn|fullständigt namn|full name|ditt namn|^namn\*?\s/.test(l) ? "full" : type === "email" || /e-?post|e-?mail|\bmail\b/.test(l) ? "email"
    : type === "tel" || /telefon|mobil|phone|\btel\b|cellphone|mobile/.test(l) ? "phone"
    : /förnamn|first|fname|given|fornamn/.test(l) ? "first"
    : /efternamn|last|surname|family|lname/.test(l) ? "last"
    : /(^|[^a-zåäö])(namn|name|your-name)([^a-zåäö]|$)/.test(l) && !/(förening|företag|gat|adress|bolag|company|street)/.test(l) ? "full" : "";
  // Text around a radio button, for timeslots labelled only "Boka"
  const around = (e) => { let x = e; for (let i = 0; i < 4 && x.parentElement; i++) { x = x.parentElement; if (x.querySelectorAll("input[type=radio]").length > 1) break; if (x.innerText.trim().length > 6) return x.innerText; } return e.labels && e.labels[0] ? e.labels[0].innerText : ""; };
  const score = (t) => ({ d: dayRx.test(t), t: timeRx.test(t), dated: anyDate.test(t) || anyTime.test(t) });
  const pick = (opts, chooserLabel) => {
    const real = opts.filter((o) => o.text.trim() && !/^\s*(välj|choose|select|--)/i.test(o.text));
    const both = real.filter((o) => o.s.d && o.s.t), dates = real.some((o) => /\d{1,2}\s*[a-zåäö]{3}|\/|-\d\d-|dag/i.test(o.text) && o.s.dated);
    if (both.length) return both[0];
    if (!real.some((o) => o.s.d) && real.some((o) => o.s.t) && !dates) return real.find((o) => o.s.t);
    if (!real.some((o) => o.s.t) && real.filter((o) => o.s.d).length === 1) return real.find((o) => o.s.d);
    if (real.length === 1 && /visning|tid|datum|showing|slot|time/i.test(chooserLabel)) return real[0];
    return null;
  };
  const roots = [...document.querySelectorAll("form")];
  // Also the smallest box around each group of contact fields: for forms built without a <form>, and for pages
  // wrapped whole in one <form>
  const texty = "input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button]), textarea";
  for (const e of document.querySelectorAll("input[type=email], input[name*=mail i], input[type=tel]")) {
    let box = e.parentElement;
    while (box && box !== document.body && box.querySelectorAll(texty).length < 3) box = box.parentElement;
    if (box && box !== document.body && !roots.includes(box)) roots.push(box);
  }
  const out = roots.map((f, i) => {
    const fields = [], choosers = [], boxes = [];
    for (const e of f.querySelectorAll("input, textarea, select")) {
      if (e.type === "hidden" || e.type === "submit" || e.type === "button") continue;
      const l = label(e), v = vis(e) || (e.type === "radio" || e.type === "checkbox") && e.closest("label") && vis(e.closest("label"));
      if (e.type === "checkbox") { boxes.push({ l: l.slice(0, 80), v }); continue; }
      if (e.tagName === "SELECT") {
        const opts = [...e.options].map((o, j) => ({ j, value: o.value, text: o.text, s: score(o.text) }));
        if (opts.some((o) => o.s.dated) || /visning|tid|datum|showing|slot|time/i.test(l)) choosers.push({ el: e, l, opts, v, sel: true });
        continue;
      }
      if (e.type === "radio") {
        const name = e.name || "r" + i;
        let c = choosers.find((x) => x.radio === name);
        if (!c) choosers.push(c = { radio: name, l: name, opts: [], v: false });
        const t = around(e);
        c.opts.push({ el: e, text: t, s: score(t) }); c.v = c.v || v;
        continue;
      }
      if (trap(e, l) || !(v || shown(e) && e.offsetParent === null && !vis(f))) continue;
      const k = kind(l, e.type);
      if (k && !fields.some((x) => x.k === k)) fields.push({ el: e, k, v });
    }
    const keep = choosers.filter((c) => c.sel ? c.opts.some((o) => o.s.dated) || /visning|tid|datum|showing|slot/i.test(c.l) : c.opts.some((o) => o.s.dated));
    const text = (f.innerText || "").slice(0, 3000), has = (k) => fields.some((x) => x.k === k);
    let sc = (has("email") ? 2 : 0) + (has("phone") ? 2 : 0) + (has("first") || has("full") ? 2 : 0) + (keep.length ? 3 : 0);
    // What it's for, from the form and what's written around it (the panel's heading, the showing it sits under)
    let ctx = text;
    for (let x = f.parentElement, k = 0; x && k < 3; x = x.parentElement, k++) ctx = (x.innerText || "").slice(0, 2000);
    const viewing = /visning/i.test(text) || /visning/i.test(ctx);
    if (viewing) sc += 3;
    if (keep.some((c) => c.opts.some((o) => o.s.d && o.s.t))) sc += 3;
    if (/värdering|värdera|nyhetsbrev|newsletter|sälja din|sälj din|lånelöfte|logga in/i.test(text) && !viewing) sc -= 8;
    if (/intresseanmälan|anmäl intresse/i.test(text) && !viewing) sc -= 3;
    if (f.querySelector("input[type=password]")) sc -= 20;
    if ((f.innerText || "").length > 2500) sc -= 5;  // most of a page, not a form
    if (/om din bostad|din nuvarande bostad/i.test(text) && !/visning/i.test(text)) sc -= 8;
    if (!fields.length || !viewing && !keep.length) sc = -99;  // nothing about a showing: a contact or valuation form
    return { i, sc, f, fields, keep, boxes, visible: vis(f) && fields.some((x) => x.v), text, own: /visning/i.test(text) || keep.length > 0 };
  }).filter((x) => x.sc >= 5).sort((a, b) => b.sc - a.sc || b.visible - a.visible);
  for (const e of document.querySelectorAll("[data-bp]")) e.removeAttribute("data-bp");
  if (!out.length) return null;
  const best = out[0];
  best.f.setAttribute("data-bp", "form");
  for (const x of best.fields) x.el.setAttribute("data-bp", x.k);
  const choices = best.keep.map((c, n) => {
    const o = pick(c.opts, c.l);
    if (!o) return { n, picked: false, label: c.l.slice(0, 40) };
    if (c.sel) { c.el.setAttribute("data-bp", "choose" + n); return { n, picked: true, sel: true, value: o.value, text: o.text.trim().slice(0, 60) }; }
    o.el.setAttribute("data-bp", "choose" + n); return { n, picked: true, sel: false, text: o.text.trim().replace(/\s+/g, " ").slice(0, 60) };
  });
  const r = best.f.getBoundingClientRect();
  return { sc: best.sc, visible: best.visible, own: best.own, fields: best.fields.map((x) => x.k), labels: best.fields.map((x) => label(x.el).slice(0, 30)), choices, boxes: best.boxes.filter((b) => b.v).map((b) => b.l),
    captcha: !!best.f.querySelector("iframe[src*='recaptcha/api2/anchor']:not([src*='size=invisible']), iframe[src*='hcaptcha'], iframe[src*='turnstile'], .g-recaptcha:not([data-size=invisible]), .h-captcha, .cf-turnstile"),
    bankid: /bankid/i.test(best.text) && !best.fields.length, text: best.text.slice(0, 200).replace(/\s+/g, " "), height: r.height };
}"""

# The buttons on the page that open a sign-up, nearest this showing first
# A button for this very showing ("sön 15 nov. 14:00"), in a booking widget that lists times as buttons
SLOT_JS = r"""([day, months, time, tried]) => {
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2 && getComputedStyle(e).visibility !== "hidden"; };
  const dayRx = new RegExp(`(^|\\D)0?${day}(\\s*(${months.join("|")})|/)`, "i"), timeRx = new RegExp(`(^|\\D)${time.replace(":", "[:.]")}`);
  for (const e of document.querySelectorAll("[data-bp-open]")) e.removeAttribute("data-bp-open");
  const b = [...document.querySelectorAll("button, [role=button], [role=option], label")].find((e) => {
    const t = (e.innerText || "").trim().replace(/\s+/g, " ");
    if (!vis(e) || t.length > 45 || !timeRx.test(t) || tried.includes(t + "|") || e.closest("a[href]") || e.type === "submit") return false;
    if (dayRx.test(t)) return true;
    // Only the time on the button ("12:45"): the day is written next to it
    for (let x = e.parentElement, k = 0; x && k < 3; x = x.parentElement, k++) if (dayRx.test((x.innerText || "").slice(0, 300))) return t.length < 14;
    return false;
  });
  if (!b) return null;
  b.setAttribute("data-bp-open", "1");
  return { t: (b.innerText || "").trim().replace(/\s+/g, " "), href: "" };
}"""

TRIGGERS_JS = r"""([rx, notRx, day, months, time, tried]) => {
  const open = new RegExp(rx, "i"), not = new RegExp(notRx, "i");
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2 && getComputedStyle(e).visibility !== "hidden"; };
  const dayRx = new RegExp(`(^|\\D)0?${day}\\s*(${months.join("|")})|(^|\\D)0?${time.replace(":", "[:.]")}`, "i");
  // Buttons, and the divs and spans some sites make clickable instead (cursor: pointer)
  const all = [...document.querySelectorAll("button, a, [role=button], input[type=button], div, span")].filter((e) => vis(e) &&
    (!/^(DIV|SPAN)$/.test(e.tagName) || getComputedStyle(e).cursor === "pointer" && (e.innerText || "").length < 60 && !e.closest("button, a")));
  for (const e of document.querySelectorAll("[data-bp-open]")) e.removeAttribute("data-bp-open");
  const hits = [];
  all.forEach((e, i) => {
    const t = (e.innerText || e.value || e.getAttribute("aria-label") || "").trim().replace(/\s+/g, " ");
    if (!t || t.length > 60 || !open.test(t) || not.test(t) || tried.includes(t + "|" + (e.href || ""))) return;
    let near = 0;
    for (let x = e, k = 0; x && k < 4; x = x.parentElement, k++) if (dayRx.test((x.innerText || "").slice(0, 400))) { near = 4 - k; break; }
    hits.push({ i, t, href: e.tagName === "A" && e.href && !/^(javascript:|#)/.test(e.getAttribute("href") || "#") ? e.href : "", near, target: e.target });
  });
  hits.sort((a, b) => b.near - a.near || a.t.length - b.t.length);
  if (hits[0]) all[hits[0].i].setAttribute("data-bp-open", "1");
  return hits[0] || null;
}"""

BLOCK_JS = r"""() => {
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 30 && r.height > 30; };
  const f = [...document.querySelectorAll("iframe")].filter(vis).map((x) => x.src || "");
  const title = document.title || "", body = (document.body && document.body.innerText || "").slice(0, 4000);
  if (/just a moment|ett ögonblick|attention required|checking your browser/i.test(title) || f.some((s) => /challenges\.cloudflare|hcaptcha\.com\/captcha/.test(s))) return "captcha";
  const dlg = [...document.querySelectorAll("[role=dialog], dialog[open], [class*=modal]")].filter(vis).map((d) => d.innerText).join(" ");
  if (/bankid/i.test(dlg) || /legitimera dig med bankid|logga in med bankid/i.test(body) && !document.querySelector("input[type=email]")) return "bankid";
  if ([...document.querySelectorAll("input[type=password]")].some(vis)) return "login";
  if (/(så fort|när|efter att) du (har )?loggat in|logga in för att (kunna )?(boka|anmäla)|du behöver logga in/i.test(dlg + " " + body)) return "login";
  return "";
}"""

MARK_JS = r"""(msg) => {
  const f = document.querySelector("[data-bp=form]");
  if (f) { f.style.outline = "4px solid #e8590c"; f.style.outlineOffset = "6px"; f.style.borderRadius = "6px"; f.scrollIntoView({ block: "center" }); }
  for (const e of document.querySelectorAll("[data-bp]:not([data-bp=form])")) e.style.boxShadow = "0 0 0 3px #ffd8a8";
  let n = document.getElementById("bostadsplan-note");
  if (!n) { n = document.createElement("div"); n.id = "bostadsplan-note"; document.body.appendChild(n); }
  n.setAttribute("style", "position:fixed;z-index:2147483647;left:12px;right:12px;top:12px;max-width:560px;margin:auto;padding:12px 40px 12px 14px;border-radius:10px;" +
    "background:#1f2933;color:#fff;font:15px/1.4 -apple-system,system-ui,sans-serif;box-shadow:0 6px 24px rgba(0,0,0,.3)");
  n.innerHTML = "<strong>Bostadsplan</strong><br>" + msg + "<button style='position:absolute;right:8px;top:8px;background:none;border:0;color:#fff;font-size:20px;cursor:pointer' aria-label='Stäng'>×</button>";
  n.querySelector("button").onclick = () => n.remove();
}"""

MONTHS = ["jan(uari)?", "feb(ruari)?", "mar(s)?", "apr(il)?", "maj", "jun(i)?", "jul(i)?", "aug(usti)?", "sep(tember)?", "okt(ober)?", "nov(ember)?", "dec(ember)?"]


def split_name(name):
    parts = (name or "").split()
    return (" ".join(parts[:-1]) or parts[0], parts[-1]) if len(parts) > 1 else (name or "", "")


def frames(page):
    """The page first, then its frames from the agent's own booking systems (not maps, ads or captchas)."""
    yield page.main_frame
    for f in page.frames:
        if f is not page.main_frame and f.url.startswith("http") and not re.search(r"google|recaptcha|hcaptcha|cloudflare|turnstile|friendlycaptcha|cookiebot|cookieinformation|adfenix|analytics|youtube|vimeo|facebook", f.url):
            yield f


def scan(page, show):
    day, month, time_ = show.day, show.month, f"{show:%H:%M}"
    for fr in frames(page):
        try:
            got = fr.evaluate(SCAN_JS, [day, month, time_, [MONTHS[month - 1]]])
        except Exception:
            continue
        if got:
            return fr, got
    return None, None


def blocker(page):
    for fr in frames(page):
        try:
            b = fr.evaluate(BLOCK_JS)
        except Exception:
            continue
        if b:
            return b
    return ""


def open_form(page, show, agent, tried, robots, log=print):
    """Press the agent's button that opens the sign-up; True when something was pressed or opened."""
    rxs = [None, *HINTS.get(agent, {}).get("open", []), OPEN_RX.pattern]  # None: this showing's own button
    for rx in rxs:
        for fr in frames(page):
            try:
                hit = fr.evaluate(SLOT_JS, [show.day, [MONTHS[show.month - 1]], f"{show:%H:%M}", tried]) if rx is None else \
                    fr.evaluate(TRIGGERS_JS, [rx, NOT_OPEN_RX.pattern, show.day, [MONTHS[show.month - 1]], f"{show:%H:%M}", tried])
            except Exception:
                continue
            if not hit:
                continue
            tried.append(hit["t"] + "|" + hit["href"])
            log(f"  pressed {hit['t']!r}" + (f" ({hit['href']})" if hit["href"] else ""))
            href = hit["href"]
            if href and urlparse(href).netloc != urlparse(fr.url).netloc and urlparse(href).path != urlparse(fr.url).path:
                if not robots.allowed(href):
                    continue
                resp = agents.goto(page, href)  # another site's booking page (Vitec's), in this window
                if resp and resp.status >= 400:
                    raise Refused(resp.status)
            else:
                pages = len(page.context.pages)
                btn = fr.locator("[data-bp-open]").first
                try:
                    btn.click(timeout=5000)
                except Exception:  # covered by a sticky bar or the like: the button's own click
                    btn.evaluate("e => e.click()")
                page.wait_for_timeout(600)
                if len(page.context.pages) > pages:  # it opened a tab: carry on there
                    return page.context.pages[-1]
            page.wait_for_timeout(1800)
            return page
    return None


class Refused(Exception):
    pass


def choose(fr, got):
    """Pick the showing where the form asks; some forms show the rest of their fields only then."""
    picked = 0
    for c in got["choices"]:
        if not c["picked"]:
            continue
        el = fr.locator(f'[data-bp="choose{c["n"]}"]').first
        try:
            if c["sel"]:
                el.select_option(value=c["value"], timeout=4000)
            else:
                try:
                    el.check(timeout=3000, force=True)
                except Exception:  # a radio drawn as a button: its own click
                    el.evaluate("e => { (e.labels && e.labels[0] || e).click(); e.checked = true; e.dispatchEvent(new Event('change', { bubbles: true })); }")
            picked += 1
        except Exception:
            pass
    return picked


def fill(fr, got, me):
    first, last = split_name(me["name"])
    has_last = "last" in got["fields"]
    values = {"first": first if has_last else me["name"], "last": last, "full": me["name"] if not has_last else first,
              "email": me["email"], "phone": me["phone"]}
    done = []
    for k in got["fields"]:
        v = values.get(k)
        if not v:
            continue
        try:
            el = fr.locator(f'[data-bp="{k}"]').first
            el.fill(v, timeout=4000)
            el.dispatch_event("blur")
            done.append(k)
        except Exception:
            pass
    return done


def book(url, start, me, show_window=True, shot=None, log=print):
    show = dt.datetime.fromisoformat(start)
    listing = finder.read_json(DATA / "listings.json", {}).get("items", {}).get(url, {})
    agent = listing.get("agent") or ""

    def rec(state, msg, **kw):
        log(f"{agent}: {state}: {msg}")
        if not shot:  # a test run leaves data/bookings.json alone
            record(url, start=start, state=state, msg=msg, agent=agent, **kw)

    robots = agents.Robots()
    if not robots.allowed(url):
        rec("refused", "Mäklarens robots.txt ber besökare att inte läsa sidan, så öppna den själv.")
        return "refused"
    browser = finder.launch(show_window)
    page = browser.new_page()
    state = "error"
    try:
        resp = agents.goto(page, url)
        if resp and resp.status >= 400:
            raise Refused(resp.status)
        agents.decline_cookies(page)
        page.wait_for_timeout(1500)
        tried, fr, got = [], None, None
        for _ in range(5):  # the form, or the buttons that open it, a few steps deep
            if b := blocker(page):
                state = b
                break
            fr, got = scan(page, show)
            if got and got["visible"] and got["own"]:  # a form that's itself about the showing; else look for its button
                break
            nxt = open_form(page, show, agent, tried, robots, log)
            if not nxt:
                break
            page = nxt
            agents.decline_cookies(page)
        else:
            if b := blocker(page):
                state = b
        if state in ("captcha", "bankid", "login"):
            msg = {"captcha": "Sidan visar en captcha. Bostadsplan rör den inte; fyll i formuläret själv i fönstret.",
                   "bankid": "Anmälan kräver BankID. Fortsätt själv i fönstret.",
                   "login": "Anmälan kräver inloggning. Logga in själv i fönstret."}[state]
        elif not got:
            state, msg = "noform", "Hittade inget formulär för visningsanmälan. Anmäl dig själv i fönstret."
        elif got["captcha"]:
            state, msg = "captcha", "Formuläret har en captcha. Bostadsplan rör den inte; fyll i själv i fönstret."
        else:
            picked = choose(fr, got)
            if picked:  # the fields the showing's choice brought out
                page.wait_for_timeout(1000)
                fr2, again = scan(page, show)
                if again and again["fields"]:
                    fr, got = fr2, {**again, "choices": got["choices"]}
            done = fill(fr, got, me)
            wanted = {"email", "phone"} & set(got["fields"]) | ({"first"} if "first" in got["fields"] else {"full"} if "full" in got["fields"] else set())
            whole = wanted <= set(done) and done and (picked or not got["choices"])
            state = "filled" if whole else "partial" if done else "noform"
            bits = []
            if got["choices"] and not picked:
                bits.append(f"välj visningen {show:%-d/%-m kl. %H:%M} själv")
            if missing := [{"first": "förnamn", "last": "efternamn", "full": "namn", "email": "e-post", "phone": "telefon"}[k] for k in got["fields"] if k not in done]:
                bits.append("fyll i " + ", ".join(missing))
            if got["boxes"]:
                bits.append("kryssa i samtycket om du vill")
            msg = ("Ifyllt med dina uppgifter" if state == "filled" else "Delvis ifyllt") + (". Kvar: " + "; ".join(bits) if bits else "") + \
                ". Kontrollera och tryck själv på skicka."
            if state == "noform":
                msg = "Hittade formuläret men kunde inte fylla i det. Anmäl dig själv i fönstret."
            log(f"  form: {got['text'][:90]!r} fields={list(zip(got['fields'], got['labels']))} choices={got['choices']} filled={done}")
        try:
            page.evaluate(MARK_JS, msg)
        except Exception:
            pass
        rec(state, msg, pid=os.getpid())
        if shot:
            page.screenshot(path=shot, full_page=False)
        elif show_window:  # the window is yours now; this waits until you close it
            until = time.time() + KEEP_OPEN_S
            while time.time() < until and not page.is_closed() and browser.session:
                try:
                    page.wait_for_timeout(2000)
                except Exception:
                    break
    except Refused as e:
        state = "refused"
        rec(state, f"Mäklarens sajt svarade {e} och försöks inte igen i dag. Anmäl dig själv på sidan.")
    except Exception as e:
        state = "error"
        rec(state, f"Något gick fel ({str(e).splitlines()[0][:120]}). Anmäl dig själv på mäklarens sida.")
    finally:
        try:
            browser.close()
        except Exception:
            pass
        if not shot:
            record(url, pid=None)
    return state


def main():
    ap = argparse.ArgumentParser(description="Fill in an agent's viewing sign-up form, for you to send")
    ap.add_argument("url")
    ap.add_argument("start")
    ap.add_argument("--test", metavar="SCREENSHOT", help="Test Testsson's details, no window, a screenshot here")
    a = ap.parse_args()
    if a.test:
        return book(a.url, a.start, TEST, show_window=False, shot=a.test)
    me = finder.read_json(DATA / "plan.json", {}).get("contact") or {}
    if not (me.get("name") and (me.get("email") or me.get("phone"))):
        record(a.url, start=a.start, state="error", msg="Fyll i ditt namn och din e-post eller telefon under Dina uppgifter först.")
        return
    book(a.url, a.start, {k: (me.get(k) or "").strip() for k in ("name", "email", "phone")})


if __name__ == "__main__":
    main()
