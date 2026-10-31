# Stockholm Bostadsplan

A single-page planner for buying a bostadsrätt in Stockholm. It runs on your own Mac: a small Python server serves the planner and saves your plan, and an apartment finder reads the estate agents' own sites once a day. Everything is automatic except what can't be found online: your finances, ratings and notes. `PLAN.md` lists every feature and where it stands.

## Running it

```bash
scripts/setup.sh                 # once: Python environment with Playwright
.venv/bin/python server.py       # then open http://localhost:8765
scripts/install-mac.sh           # optional: server at login, finder daily at 06:30
```

- `scraper/finder.py` drives your installed Google Chrome. It reads each estate agent's for-sale list (`scraper/agents.py`), opens the inner-city apartments that may fit your filter, and saves every showing time, price, size, avgift, floor, build year, description, features and photos.
- Each address is looked up on OpenStreetMap's Nominatim (`scraper/geo.py`, at most one lookup a second, cached in `data/geocode.json`). That gives the apartment's neighbourhood and district, and drops anything outside innanför tullarna.
- Agents covered: Alicia Edelman, Notar, BOSTHLM, Historiska Hem, Tradition, Södermäklarna, Stadshem, Bergets Ro, Bjurfors and Behrer & Partners.
- **Booli is used only as an answer key.** Once a run, the finder opens one Booli search page, in a Chrome window of its own, to read how many apartments Booli shows for the same filter in Stockholms innerstad. The Showings tab compares that with what the finder found. If Booli refuses the page, the count is missing that day.
- The finder follows each site's robots.txt, waits a few seconds between pages, and stops reading a site that blocks it. Nothing is retried.
- Your plan, the scraped listings and the logs are kept in `data/`, which git ignores.
- `scripts/install-mac.sh --remove` removes the schedule again.

## Tabs

- **Overview:** the highest price you can likely bid, and which limit sets it (cash for the 10 % kontantinsats, the 5.5× income cap, or the bank's KALP stress test).
- **My finances:** salary, savings, debts and living costs, giving your max loan, cash breakdown and monthly cost after ränteavdrag.
- **Mortgage rates:** list rates and average rates (snittränta) from 15 Swedish lenders, with the monthly interest on your loan.
- **5-year budget:** editable rate scenarios (steady, rising, falling, 2022-style shock) in today's money, showing money left each month, loan, apartment value and equity.
- **Area prices:** yearly kr/kvm for 13 Stockholm districts, with a log-linear regression trend and a 95 % prediction range.
- **Apartments:** how high to search given room for bidding, plus tracked apartments with monthly cost and checks on the förening.
- **Showings:** apartments in innanför tullarna with a visning next week, found on the estate agents' sites, with photos, the agent's description and the monthly cost. Set your filter (max price, rooms, size, avgift, ground floor, coming soon). Rate neighbourhoods from 1 to 5 or "Never here", add notes and log the showings you went to.
- **Bidding:** a bidding plan between asking price and your walk-away price, what each extra bid costs per month, and what Swedish and Norwegian research says about jump bidding.
- **Furnishing:** a room-by-room furniture budget at three price levels.
- **Buying steps:** a checklist from saving the kontantinsats to getting the keys.

## Data and assumptions

All reference data is built into `index.html` and has to be updated by hand.

| Data | Source | As of |
|---|---|---|
| Mortgage rates (`BANKS`) | [lanupplysningen.se](https://www.lanupplysningen.se/bolan/bolanerantor/) | 29 Oct 2026 |
| Area prices (`AREAS`) | [Svensk Mäklarstatistik](https://www.maklarstatistik.se/omrade/riket/stockholms-lan/stockholm/), årshistorik | yearly averages to 2025 |
| Prisbasbelopp (`PBB`) | 59 200 kr | 2026 |

The rules used are the ones in force since 1 April 2026: a 90 % bolånetak, and amortering of 2 % above 70 % loan-to-value and 1 % between 50 and 70 %. Ränteavdrag is 30 % of interest up to 100 000 kr a year and 21 % above that.

These are planning estimates. Your bank decides what it will lend in the lånelöfte.

## Saving

From the local server, your plan saves to `data/plan.json`. When you open `index.html` straight from disk instead, it saves in that browser's local storage and shows no listings.
