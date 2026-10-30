# Stockholm Bostadsplan

A single-page planner for buying a bostadsrätt in Stockholm. Open `index.html` in a browser. There is no build step and nothing to install.

## Tabs

- **Overview:** the highest price you can likely bid, and which limit sets it (cash for the 10 % kontantinsats, the 5.5× income cap, or the bank's KALP stress test).
- **My finances:** salary, savings, debts and living costs, giving your max loan, cash breakdown and monthly cost after ränteavdrag.
- **Mortgage rates:** list rates and average rates (snittränta) from 15 Swedish lenders, with the monthly interest on your loan.
- **5-year budget:** editable rate scenarios (steady, rising, falling, 2022-style shock) in today's money, showing money left each month, loan, apartment value and equity.
- **Area prices:** yearly kr/kvm for 13 Stockholm districts, with a log-linear regression trend and a 95 % prediction range.
- **Apartments:** Booli search links built from your budget, plus tracked apartments with monthly cost and checks on the förening.
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

When the page runs as a claude.ai artifact, your plan saves to your account. When you open `index.html` directly, it saves in your browser's local storage.
