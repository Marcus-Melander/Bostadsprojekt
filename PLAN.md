# Plan and feature list

Every feature asked for goes here, with where it stands. The planner runs locally (`server.py` on http://localhost:8765) and a scraper fills it once a day.

**Rule for the whole project:** everything is automatic, except what can't be found online: your salary, savings, what you put aside each month, and your own ratings and notes.

## In progress

- **Find listings on the estate agents' own sites instead of Booli.** Read every agent's for-sale list, pressing "show more" where needed. Use recent sitemap entries where a list is incomplete. Keep the inner-city candidates, then read each apartment's page.
- **Remove everything about Booli from the planner and the scraper,** except Booli's count (next item).
- **Booli's count as the answer key.** Once a day, the scraper reads the count Booli shows for the same filter (for example "118 till salu och 160 snart till salu" in Stockholms innerstad). The planner compares it with what we found. It's one page a day; if Booli refuses it, the count is simply missing that day.
- **Our own neighbourhoods.** Look each address up on OpenStreetMap (Nominatim, at most one lookup a second) to get its neighbourhood and district, and to check that it's inside the inner city. Carry ratings over from Booli's areas by name, for example SoFo.
- **Our own search filter:** max price (4 500 000 kr), min rooms, min m², max avgift, not on the ground floor, coming soon included.

- **Collect price history for predictions.** Save as much as we can about every apartment the finder reads: its details, its agent and area, and each day's asking price, state, highest bid, number of bids and final price. The data goes into a SQLite database (`data/history.sqlite`), with CSV exports after every run (`data/apartments.csv`, `data/readings.csv`). Apartments that leave an agent's list are followed for up to 30 days, to catch the final price. This is the base for predicting final prices: how each agent prices compared with what apartments sell for, and how much prices rise in each area.

## Next

- **Predict the final price.** Once there's enough history: for each agent and area, how far final prices land above asking and how many bids it usually takes. That gives a guessed final price on each showing and saved apartment, with a range and how many sales it's based on.

- **Follow bidding and final prices.** While an apartment is in bidding ("budgivning pågår"), read the highest bid from the agent's page every day. When it's sold, keep it instead of dropping it and record the final price where the agent publishes one. That gives our own price per m² for each neighbourhood, next to the asking price, and shows how far over asking apartments go in each area.
- **Sign up for viewings from the planner.** One button per showing fills in the agent's booking form with your name, e-mail and phone. It only runs when you press it, and it stops at a captcha or BankID.
- **Cover Erik Olsson.** Their list covers all of Sweden in one endlessly scrolling page and needs a way to filter to Stockholm.
- **More agents,** such as Fastighetsbyrån, SkandiaMäklarna, LF, Living and Svensk Fastighetsförmedling, until the answer key matches.
- **Commute, grocery store and gym.**
  - Distance to the nearest grocery store and gym from OpenStreetMap.
  - Travel time to work by SL, via Trafiklab ResRobot (a free key). Without a key, estimate it from the nearest T-bana station.
- **Ranking.** One score from budget fit, commute, grocery store, gym, your area ratings and features, with sliders for how much each counts and a reason for every score.

## Done

- **Redesign:** the menu has four sections (Showings, Saved, Money, Buying), each with sub-tabs for its pages, and Saved apartments is a section of its own.
- **Map on Showings:** apartments as dots, and each area drawn around its apartments in the colour of your rating (1–5 or Never here). Stadia Maps' quiet grey "Alidade Smooth" map, light and dark, which needs no key for a page on localhost.
- **Save apartments and follow their bidding.** "Save" on a showing (it replaces Track) adds the apartment to Saved. The finder then reads its page on every run, whatever the filter says. It records the highest bid each day and the final price once it's sold, and Saved shows the bid history.
- **Show whether the daily run is installed.** The finder panel shows "Runs every morning at 06:30, next run tomorrow" when the schedule is installed, and how to switch it on when it isn't.
- **The planner:** finances, mortgage rates, 5-year budget, area prices, bidding guide, furnishing budget and buying steps.
- **The Showings tab:**
  - next week's showings, checked against your budget;
  - a one-line description of each apartment;
  - photos and the agent's full description;
  - "I went", with a rating and notes for the apartment;
  - area ratings with notes, and "Never here" to hide an area.
- **The estate agent reader:** every showing time, the build year, running costs, the description, features such as stambyte or a renovated kitchen, and photos, for 10 agents:
  - Alicia Edelman, Notar, BOSTHLM, Historiska Hem and Tradition;
  - Södermäklarna, Stadshem, Bergets Ro, Bjurfors and Behrer & Partners.
- **Running locally:** a local server, the plan saved in `data/plan.json`, and a daily run at 06:30 via `scripts/install-mac.sh`.
- **Moving the plan off claude.ai:** done once with export and import; the export box was then removed.
- **The scraper doesn't stop on a refusal:** it keeps what it read, shows a plain message, and never retries.
