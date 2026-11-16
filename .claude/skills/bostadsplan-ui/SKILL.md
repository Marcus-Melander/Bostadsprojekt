---
name: bostadsplan-ui
description: How the Bostadsplan planner (index.html) looks and is built. Use before adding or changing anything in the planner's UI, a view, a panel, a chart, a chip or a button, so new work matches the design ("Achromatic ledger") and the page's own building blocks.
---

# The planner's UI

The planner is one page, `index.html`: plain HTML, CSS and JavaScript with no build step, served on the Mac by
`server.py` and online by Vercel (`scripts/build-site.sh`). Keep it that way: no framework, no bundler, no new
stylesheet files. Read the parts of `index.html` you touch before writing; reuse what is there.

## The design: Achromatic ledger

Chosen on 30 October 2026 with the taste skill (app-screen mode). The idea, in one line: **the chrome holds no
colour; money and counts are set in ink, one data hue marks the screen's main data, and the only other colour is a
small signal where something changed or needs attention.**

- **Colour budget: under about 1 % of the screen coloured.** Panels, tabs, buttons, headings and borders are ink
  and greys. The one data hue is verdigris, `--data` (Gamla stan's copper roofs), for the main series or mark of a
  chart, and `--data-soft` behind it. Red, green and amber appear only as small signals: chips (`.chip.good`,
  `.warn`, `.bad`), a figure over budget, a lowered price. Never a coloured panel, a tinted card, a brand-coloured
  button or a coloured heading.
- **The primary action is near-black** (`.btn.primary`); everything else is a plain or ghost button.
- **Bold (700) is for money only.** Other numbers are 400–500, always tabular (`font-variant-numeric: tabular-nums`,
  already on `body`).
- **Dark mode** comes from the same tokens: every colour is a token, redefined under
  `@media (prefers-color-scheme: dark)` and `:root[data-theme="dark"]`. Never write a hex colour outside `:root`;
  if a new colour is truly needed, add a token for both themes.

## Tokens (in `:root` at the top of `index.html`)

- **Colour:** `--ground` (the page), `--surface` (panels), `--raised` (notes, hover), `--border`, `--rule`,
  `--control`, `--text`/`--ink`, `--muted`, `--primary`, `--data`, `--data-soft`, `--chart-2` (the grey second
  series), `--grid`, `--positive`/`--good`, `--negative`/`--bad`, `--warning`/`--warn`, the `--pill-*` pairs for
  chips, and `--bid-1` … `--bid-8` for the bidders in a bidding chart (the one place with several hues, each also
  labelled).
- **Type scale: every font size is one of these, never a bare px value.**
  `--fs-axis` 11 (chart labels) · `--fs-small` 12 (chips, captions) · `--fs-meta` 13 (notes, eyebrows, secondary
  lines) · `--fs-body` 14 (body, controls) · `--fs-lead` 15 (a view's lead text, reading text, small headings) ·
  `--fs-section` 16 (panel headings) · `--fs-figure` 22 (secondary figures) · `--fs-title` 24 (a view's title) ·
  `--fs-kpi` 34 (the one big figure). In the `font` shorthand: `font: 500 var(--fs-body) var(--f-body)`; in SVG:
  `style="font-size:var(--fs-axis)"`. The font is Geist (`--f-body`), weights 400, 500 and 700.
- **Space and shape:** one gap everywhere, `--gap` (12px); panel padding 20px, `--pad` 24px for larger areas;
  radii `--r` 8px (panels) and `--r-control` 6px (buttons, inputs); controls `--control-height` 36px; chips 20px
  high and fully round.

## Building blocks to reuse

- **Layout:** a 48px top bar with underline tabs over a centred 1152px column. A view starts with `.lead` (an `h1`
  and a `p` of muted lead text), then `.bench` (a 380px side column and the main column) or a single `.stack`.
  `.row` lines things up with the gap and wraps.
- **Panels:** `<section class="panel"><h3>Title <span class="dim">count</span></h3>…</section>`.
- **Chips:** `<span class="chip good|warn|bad|info">…</span>`, short, in Swedish; a chip says one thing.
- **Facts and figures:** `.facts` (a row of short facts, separated by the page's own style), `.kv` (a definition
  list of label and value), `.eyebrow` over `.bigfig` for the one big figure, `.note` for a muted explanation,
  `.dim` for muted text.
- **Lists of apartments:** `.show-card` (Visningar's cards), `.dg-list` with `dgRow()` (Morgonkoll), `.rt-*`
  (Visningsrunda). An apartment always links to its own page with `aptHref(p)`.
- **Buttons:** `.btn` (plain), `.btn.primary` (the screen's one main action), `.btn.ghost`; actions go through
  `data-act="…"` and the page's click handler, not inline `onclick`.
- **Charts:** inline SVG drawn in the page, as the price history, bidding and five-year charts are: one data hue
  on the main series, `--chart-2` for comparisons, thin lines, a y-scale, a hover tooltip, and a table or text
  fallback. Follow the dataviz skill for anything new.

## Text and behaviour

- **All text on the page is Swedish** (Visningar, Sparade, Ekonomi, Köpa); code and comments are English.
  Numbers in Swedish style: `kr()`, `mkr()`, `pct()`, `fmtShort()`, `fmtDay()` already do it.
- **Online and on the Mac:** `LOCAL` and `ONLINE` say where the page runs; read data through `getJSON(path)`, which
  goes to the local server or to Supabase (`online.js`). Anything that only works on the Mac (starting the finder,
  viewing sign-up) is shown disabled online with a short note. Anything personal (your plan, addresses, travel
  times, the digest, saved floor plans) stays behind the owner's login online.
- **Escape everything that comes from data** with `esc()` before it goes into HTML.

## Before you call it done

1. Check it in a browser at desktop width and at phone width (375px): nothing runs off the side, and the page has
   no sideways scroll.
2. Check dark mode looks right (the tokens should carry it).
3. No hex colours outside `:root`, no bare px font sizes, no new hues; the colour budget still holds.
4. Text is Swedish, short, and says what the thing is or does.
