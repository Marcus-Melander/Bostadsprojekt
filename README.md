# Stockholm Bostadsplan

A single-page planner for buying a bostadsrätt in Stockholm. It runs on your own Mac: a small Python server serves the planner and saves your plan, and a Playwright scraper reads Booli once a day.

## Running it

```bash
scripts/setup.sh                 # once: Python environment with Playwright
.venv/bin/python server.py       # then open http://localhost:8765
scripts/install-mac.sh           # optional: server at login, scraper daily at 06:30
```

- `scraper/booli_scraper.py` drives your installed Google Chrome. Booli refuses headless Chrome, so a Chrome window opens while it runs, which takes a few minutes.
- Booli search results give each apartment's next showing, price, size, avgift, features and estate agent. Booli refuses its own listing pages to scripts, so for apartments in your budget the scraper reads the **estate agent's** page instead (`scraper/agents.py`): every showing time, the build year and the description, which also gives features such as "stambyte done" or "renovated kitchen".
- Agents covered so far: Alicia Edelman, Notar, BOSTHLM, Historiska Hem, Tradition, Södermäklarna, Stadshem, Bergets Ro, Bjurfors and Behrer & Partners. Erik Olsson isn't covered yet.
- The scraper follows each site's robots.txt and waits 10–20 seconds between Booli pages. If a site blocks it, it stops reading that site and says so in the Showings tab; nothing is retried.
- Your plan, the scraped listings and the logs are kept in `data/`, which git ignores.
- `scripts/install-mac.sh --remove` removes the schedule again.

## Tabs

- **Overview:** the highest price you can likely bid, and which limit sets it (cash for the 10 % kontantinsats, the 5.5× income cap, or the bank's KALP stress test).
- **My finances:** salary, savings, debts and living costs, giving your max loan, cash breakdown and monthly cost after ränteavdrag.
- **Mortgage rates:** list rates and average rates (snittränta) from 15 Swedish lenders, with the monthly interest on your loan.
- **5-year budget:** editable rate scenarios (steady, rising, falling, 2022-style shock) in today's money, showing money left each month, loan, apartment value and equity.
- **Area prices:** yearly kr/kvm for 13 Stockholm districts, with a log-linear regression trend and a 95 % prediction range.
- **Apartments:** Booli search links built from your budget, plus tracked apartments with monthly cost and checks on the förening.
- **Showings:** apartments in innanför tullarna with a visning next week, read from Booli. Rate Booli's named areas (SoFo, Sofia, Birkastan…) from 1 to 5 or "Never here", add notes and log the showings you went to. You can paste a Booli search link to use its filters, or copy the current search back to Booli. The listings come from the daily scraper.
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
