# Plan and feature list

Every feature asked for goes here, with where it stands. The planner runs locally (`server.py` on http://localhost:8765) and a scraper fills it once a day.

**Rule for the whole project:** everything is automatic, except what can't be found online: your salary, savings, what you put aside each month, and your own ratings and notes.

## Estate agents

Which agents the finder reads, and which are left. "Inner city" is the number of apartments for sale or coming soon inside the tolls, in the last run.

| Agent | Status | Inner city | How it's read |
|---|---|---|---|
| Bjurfors | Reading | 153 | Stockholm list, every numbered page |
| Alicia Edelman | Reading | 120 | For-sale list |
| Erik Olsson | Reading | 45 | One list per inner-city district; two runs on 2026-11-01 found the same 137 apartments |
| Bergets Ro | Reading | 37 | For-sale list, "show more" until it ends |
| Notar | Reading, not complete | 24 | 25 newest plus 12 newest per inner-city office |
| Tradition | Reading | 24 | For-sale list |
| BOSTHLM | Reading | 14 | For-sale list |
| Behrer & Partners | Reading | 9 | For-sale list |
| Historiska Hem | Reading | 6 | For-sale list |
| Stadshem | Reading | 5 | For-sale list |
| Södermäklarna | Reading | 2 | For-sale list |
| Svensk Fastighetsförmedling | Reading | 101 | Stockholm city list, ?p=2, ?p=3… |
| Länsförsäkringar | Reading | 67 | Sitemap (the list shows only 36 suburban apartments); many are "Kommande försäljning" |
| SkandiaMäklarna | Reading | 37 | City list and sitemap; only city apartments, as its terms allow |
| ESNY | Reading | 2 | Stockholm list; mostly above budget |
| Karlsson & Uddare | Reading | 1 | For-sale list; mostly Saltsjöbaden |
| Fastighetsbyrån | Reading, by permission | 187 | One page per area they name (Kungsholmen, Vasastan, Katarina…) plus the city's newest; allowed by e-mail for a hobby project (see below) |
| Wrede | Left out | – | Hides the address on most apartments, and nearly all are far above budget |
| Mäklarhuset | Reading, by permission | 22 | First two pages of the Kungsholmen, Vasastan, Södermalm and Östermalm searches |
| HusmanHagberg | Reading, by permission | 42 | Stockholm city list (18 newest) plus sitemap entries whose address names an inner-city area |
| Mäklarringen | Reading, by permission | 1 | Stockholm search; only its Östermalm office has inner-city apartments |
| Living | Left out | – | livingstockholm.se is now a city guide, and the agent's old address leads to Leander Pehrson |
| ERA | Left out | – | era.se and erasweden.com are parked domains; no Swedish ERA site found |

**Fastighetsbyrån's permission (2026-10-31).** Their robots.txt and terms forbid automatic reading of fastighetsbyran.com without special permission. Marcus asked them by e-mail, and they allowed it as long as this stays a hobby project. **If the project ever grows beyond that (shared with others, published or commercial), ask them again first.** The finder reads only their apartments in the inner city, once a day, with pauses; their entry in `scraper/agents.py` is marked `permission`.

**Mäklarhuset, HusmanHagberg and Mäklarringen (2026-11-01).** Marcus has written permission from them, so their entries are also marked `permission` and their robots.txt isn't read. Agents marked `permission` are the only ones allowed past their robots.txt.

## In progress

- **Find listings on the estate agents' own sites instead of Booli.** Read every agent's for-sale list, pressing "show more" where needed. Use recent sitemap entries where a list is incomplete. Keep the inner-city candidates, then read each apartment's page.
- **Remove everything about Booli from the planner and the scraper,** except Booli's count (next item).
- **Booli's count as the answer key.** Once a day, the scraper reads the count Booli shows for the same filter (for example "118 till salu och 160 snart till salu" in Stockholms innerstad). The planner compares it with what we found. It's one page a day; if Booli refuses it, the count is simply missing that day.
- **Our own neighbourhoods.** Look each address up on OpenStreetMap (Nominatim, at most one lookup a second) to get its neighbourhood and district, and to check that it's inside the inner city. Carry ratings over from Booli's areas by name, for example SoFo.
- **Our own search filter:** max price (4 500 000 kr), min rooms, min m², max avgift, not on the ground floor, coming soon included.

- **Collect price history for predictions.** Save as much as we can about every apartment the finder reads: its details, its agent and area, and each day's asking price, state, highest bid, number of bids and final price. The data goes into a SQLite database (`data/history.sqlite`), with CSV exports after every run (`data/apartments.csv`, `data/readings.csv`). Apartments that leave an agent's list are followed for up to 30 days, to catch the final price. This is the base for predicting final prices: how each agent prices compared with what apartments sell for, and how much prices rise in each area.



## Next

- **Erik Olsson's endless list:** one run found 111 apartments, the next 81, so scrolling doesn't always load the whole list. Scroll more patiently, or read each district's list in smaller parts.
- **Predict the final price.** Once there's enough history: for each agent and area, how far final prices land above asking and how many bids it usually takes. That gives a guessed final price on each showing and saved apartment, with a range and how many sales it's based on.

- **Follow bidding and final prices.** While an apartment is in bidding ("budgivning pågår"), read the highest bid from the agent's page every day. When it's sold, keep it instead of dropping it and record the final price where the agent publishes one. That gives our own price per m² for each neighbourhood, next to the asking price, and shows how far over asking apartments go in each area.
- **Sign up for viewings from the planner.** One button per showing fills in the agent's booking form with your name, e-mail and phone. It only runs when you press it, and it stops at a captcha or BankID.
- **More agents: the biggest gap.** Work through the "To do" rows of the agents table until the for-sale count nears Booli's.
- **Important addresses and travel times.** You add the places that matter, each with a name and how you'd get there: work, your girlfriend's place, the gym, your parents. Each apartment then shows the travel time to every one, and the ranking can weigh them.
  - Travel time by SL via Trafiklab ResRobot (a free key), at a time of day you choose for each address (work at 08:00 on a weekday). Without a key, estimate it from the nearest T-bana station. Walking and cycling times for nearby places.
  - Distance to the nearest grocery store and gym from OpenStreetMap, without having to add them.
- **A morning digest.** After the 06:30 run, a short summary: new apartments in your filter, saved apartments with new bids or a final price, price drops, and this week's showings. A notification on the Mac that opens the planner.
- **A showing-day route.** For each day with showings, put them in an order you can travel between by T-bana or on foot, with the time it takes, and flag showings that overlap.
- **Price drops and relistings.** Mark an apartment whose asking price fell, or that came back after leaving the list, since both often mean room to bid lower. The price history already records what's needed.
- **Monthly cost per apartment.** Each card shows what the apartment would cost you a month (mortgage interest and amortisation from your money plan, plus avgift and running costs) and whether it fits your budget.
- **Red flags from the description.** Point out an upcoming stambyte, a raised or planned higher avgift, a leased plot (tomträtt), and a small or heavily indebted housing association.
- **Ranking.** One score from budget fit, monthly cost, travel time to your important addresses, grocery store, gym, your area ratings, features and red flags, with sliders for how much each counts and a reason for every score.

## Done

- **Six more agents:** Svensk Fastighetsförmedling (its Stockholm city list, page by page), Länsförsäkringar (its sitemap, since its list shows only 36 suburban apartments), SkandiaMäklarna (only apartments in the city, as its terms allow a private person; its folded fact sections are opened), Fastighetsbyrån (by their permission), ESNY and Karlsson & Uddare. Cards are now cut out precisely on any site, cookie banners are answered with necessary cookies only, "PÅ GÅNG" means coming soon, and a "protected by reCAPTCHA" notice under a contact form no longer counts as a refusal. An apartment in bidding counts as for sale even when no price was read. Result: 62 for sale against Booli's 119, and 393 coming soon against 160. Coming soon is far above Booli because these agents announce many sales early ("Kommande försäljning", Fastighetsbyrån's "PÅ GÅNG") without a price, and Booli shows only some of them; with no price, some may turn out to be above budget.
- **A fair answer key.** Each apartment is counted once even when two agents list it. One without an asking price counts as coming soon, as on Booli, including Bjurfors' "på förhand" sales. One for sale counts only when its page states what the filter checks. Counts per district aren't possible: Booli answers one search a session and refuses the next, so the finder reads only the whole inner city. First fair result: 38 for sale against Booli's 119, and 167 coming soon against 160. Most for-sale apartments in your budget belong to agents not covered yet.
- **More and better neighbourhoods, rated at two levels.** 56 neighbourhoods across seven districts, including SoFo (centred on Nytorget), Mariatorget, Mariaberget, Högalid, Tanto, Katarina, Sofia and Slussen on Södermalm, and Fridhemsplan, Rålambshov, Thorildsplan and Kungsholms strand on Kungsholmen. Districts follow the shore, with 11 bays and channels cut out. Each apartment is named by the neighbourhood it lies in. You rate a district and any neighbourhood in it; an unrated neighbourhood takes its district's rating.
- **Erik Olsson:** read through its own area search, one page per inner-city district (`/homes?areaIds=467` Södermalm, `667` Kungsholmen, `387` Vasastan, `528` Östermalm, `898` Norrmalm/City, `1961` Gamla stan, `668` Gärdet), scrolling until each list ends. About 111 apartments.
- **More of Notar's list:** the finder reads Notar's 25 newest apartments plus the 12 newest from each of its six inner-city offices, about 48 inner-city apartments instead of 5. Notar has no complete list per office, so a few older ones may still be missing.
- **Real area boundaries on the map.** The 15 inner-city districts are drawn with Stockholm's official borders, from OpenStreetMap (`areas.geojson`, built by `scripts/build_areas.py`). Neighbourhoods have no official borders, so each covers the part of its district within about 500 m of its point and nearer to it than to any other; the rest keeps the district's name. Each area is coloured by your rating.
- **A faster planner.** Galleries and descriptions are built only when opened, Showings draws 30 apartments at a time with "Show more", typing redraws once you pause, nothing is redrawn when nothing changed, and a running finder reloads the list every 30 seconds instead of every 5.
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
