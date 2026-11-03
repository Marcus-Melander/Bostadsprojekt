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
| Innerstadsspecialisten | Reading | 26 | For-sale list; found missing by the Booli comparison (2026-11-02). Many apartments have no asking price ("Kontakta mäklaren"), and the floor is only in the address ("2tr") |
| Wallin & Co | Left out | – | wallinco.se; every apartment is on Essingeöarna or in Bromma, outside the tolls (Booli counts Stora Essingen as innerstad) |
| Bronze | Reading | 40 | bronzefast.se, its list of apartments; found missing by the Booli comparison. Most show "Utgångspris: på förfrågan", so they have no price |

**Fastighetsbyrån's permission (2026-10-31).** Their robots.txt and terms forbid automatic reading of fastighetsbyran.com without special permission. Marcus asked them by e-mail, and they allowed it as long as this stays a hobby project. **If the project ever grows beyond that (shared with others, published or commercial), ask them again first.** The finder reads only their apartments in the inner city, once a day, with pauses; their entry in `scraper/agents.py` is marked `permission`.

**Mäklarhuset, HusmanHagberg and Mäklarringen (2026-11-01).** Marcus has written permission from them, so their entries are also marked `permission` and their robots.txt isn't read. Agents marked `permission` are the only ones allowed past their robots.txt.

## Phases

Work goes in phases; each one builds on the one before. A phase is done when every item in it is. Every feature added or reworked goes into this plan straight away, and the phases are re-sorted whenever the order or scope changes.

### Phase 1: Finish what's built


- **Förening figures where they're still missing.** After 2026-11-02, 456 of 493 apartments in the filter have the förening's figures and 301 have a debt figure. The rest: 27 Fastighetsbyrån apartments whose agent wrote "Ingen information", Behrer's 2, and pages that only say "låg belåning" without a number; the Bolagsverket annual reports (next item) are the way to fill those.
- **The förening's annual report from Bolagsverket.** Since 2023 every förening's annual report must give the same key figures: årsavgift per m², debt per m², savings per m², interest-rate sensitivity and energy cost per m². Bolagsverket's free API ("värdefulla datamängder") serves digitally filed annual reports as iXBRL, where each figure is tagged, so they can be read for föreningar whose agent shows none, and more reliably than the agents' pages. Where it stands (2026-11-03): the reader is built (`scraper/bolagsverket.py`); Bolagsverket has sent the client secrets by text message (one for its test environment, one for the real API), and the client id is still to come by e-mail. Steps:
  1. **Credentials.** When the e-mail with the client id arrives, Marcus puts the id and the secret in `data/bolagsverket.json` (git ignores `data/`; the secrets never go in the code, the plan or a commit), and `python scraper/bolagsverket.py --ping` checks them, first with `"env": "test"`.
  2. **Try it on about 10 föreningar** (`--org`): are their reports there, digitally filed, and do the five key figures come out? The figures are found by words in their iXBRL names; adjust the words to the taxonomy's real names on the first reports. Reports filed on paper or as scans have no tags and are skipped.
  3. **The organisationsnummer for each förening.** The API looks föreningar up by number only, not by name. Checked on one page per agent (2026-11-03): 15 of 20 agents show it, 11 in the page's text and 4 (Alicia Edelman, Notar, Bronze, and Innerstadsspecialisten in some) only in its code, labelled as the apartment's own. The finder now keeps it as the apartment's `orgnr` (a förening's number starts 769, or 716/717 for older ones, so the agent's own 556… is never taken); every listing is re-read daily, so the next morning run fills it in. Not shown by Bjurfors, SkandiaMäklarna, Svensk Fastighetsförmedling, Mäklarhuset or HusmanHagberg: for those, try the documents they link (the annual report PDF has the number on its cover), then a free source that maps a förening's name to its number (Bolagsverket's own search has a captcha, so it's not used). Bolagsverket's answer gives the förening's name, so a number that isn't a bostadsrättsförening is caught (`isBrf`).
  4. **Fetch once and keep.** Each förening is fetched once and kept in `data/foreningar.json`, looked at again after 120 days for the new year's report; a refusal (401, 403, 429) stops the day's lookups, never retried. Runs after the morning finder (`--update`), so it never writes `listings.json` at the same time.
  5. **Use the figures.** The red flags and the ranking take Bolagsverket's debt per m² first and the agent's figure as the fallback, and the apartment card shows all five key figures with the report's year.
- **Final prices for sold apartments.** Bidding is followed bid by bid; make sure the final price is recorded every time a saved apartment sells, where the agent publishes it, so our own price per m² per neighbourhood builds up next to the asking price.
- **Close the gap to Booli.** Every morning the finder compares our count with Booli's for the same filter, and the planner shows the trend day by day. Coverage of Booli's for-sale count: 52 % on 31 October (17 agents), 60 % on 1 November (20 agents), 34 % on 2 November (20 for sale against Booli's 58). The drop is mostly prices that were never this apartment's: before the page was cut at its list of other apartments, an apartment with no price took the next apartment's (22 of Erik Olsson's alone), so the earlier figures were too high. Since 2 November the finder also matches the apartments on Booli's one search page (about 35 of its for-sale ones) against ours and logs which it misses and whose they are (see Done). The first comparison: 12 found, 9 here without an asking price, 13 missing. Of the missing, 5 were Bergets Ro's (its list stopped early; fixed, which took us to 25 for sale and 16 of Booli's 35 found), 5 are outside the tolls as we draw them (Stora Essingen, Norra Djurgården, Hjorthagen; Booli's "innerstad" includes them), and 3 belonged to agents we didn't read: Innerstadsspecialisten and Bronze are now read (18 of Booli's 35 found, 26 for sale), and Wallin & Co's one is on Stora Essingen, outside the tolls. Booli also counts some apartments without an asking price as for sale ("Pris ej angivet", 6 of 35), apparently when its own estimate falls inside the price range; we count every unpriced one as coming soon, so those 6 show up in our coming-soon number instead. Its rule isn't published, so ours is left as it is.
- **Close the agents' gaps:** Notar's list isn't complete (25 newest plus 12 per office), and Erik Olsson's list once stopped early.
- **Floor plans where they're missing.** After the backfill (2026-11-01), 291 of 487 apartments have one, but Bjurfors has 7 of 75, Svensk Fastighetsförmedling 11 of 79 and Länsförsäkringar 0 of 39. Some are early announcements without a plan yet; check which of the rest have one the finder misses.

### Phase 2: Decide faster each day

- **Our own page for each apartment.** Clicking an apartment (on a card, in Rangordning, in Sparade) or its dot on the map opens our own page for it, instead of the agent's site: photos, floor plan, description, the förening's facts and flags, price against your budget and monthly cost, score with its reasons, travel times and nearby grocery store, gym and bar, showings, and bidding for saved apartments, with the agent's listing as one link among the rest. The page has its own map showing where the apartment is, and its own address in the planner (so it can be bookmarked and shared later, once the planner is online). The idea of one page per apartment is like Booli's; the design is our own.

- **Price drops and relistings.** Mark an apartment whose asking price fell, or that came back after leaving the list, since both often mean room to bid lower. The price history already records what's needed.
- **Ranking, next steps.** Add price drops and relistings as a factor once they're marked, and the monthly cost against what you have left each month.
- **A morning digest.** After the morning run, a short summary: new apartments in your filter, saved apartments with new bids or a final price, price drops, and this week's showings. A notification on the Mac that opens the planner.

### Phase 3: Showing days

- **A showing-day route.** For each day with showings, put them in an order you can travel between by T-bana or on foot, with the time it takes, and flag showings that overlap.
- **Sign up for viewings from the planner.** One button per showing fills in the agent's booking form with your name, e-mail and phone. It only runs when you press it, and it stops at a captcha or BankID.

### Phase 4: Online

- **Host the planner on Vercel (free).** Put the website online so it opens from anywhere, including the phone. Vercel serves the page and small functions, but can't run the daily finder (Chrome and Playwright, about an hour a day); the finder stays on the Mac, or moves to a scheduled runner such as GitHub Actions, and uploads its results.
- **Keep the data in Supabase (free tier).** A hosted Postgres database, file storage and login, which both the finder and the website can reach:
  - **Database:** the price history (apartments, daily readings, bids, final prices), the listings, travel times, and your plan, important addresses and ratings, behind a login with row-level security so only you see your own data.
  - **Storage:** the floor plans, kept at full size (not shrunk). They're about 140 KB each and grow by roughly 1–2 GB a year, so they'll pass the free tier's file storage (about 1 GB) within a year; plan for a paid tier then, or keep older plans on the Mac.
  - **Limits to check first:** the free tier (about 500 MB of database and 1 GB of files, as last known) and that a free project pauses after a week without activity; the daily runs keep it active. Check Supabase's current pricing page before relying on these.
- **Keep it private.** Showing the agents' photos, floor plans and descriptions on a public site is republishing. Keep the site behind a login, and check Fastighetsbyrån's hobby permission still covers it.

### Phase 5: Predictions

Needs months of final prices, which the daily runs collect meanwhile.

- **Predict the final price, two ways, side by side.**
  - **From price history:** for each neighbourhood and agent, how far final prices land above asking and how many bids it usually takes, from our own history (data/history.sqlite). A guessed final price on each showing and saved apartment, with a range and how many sales it's based on.
  - **From the apartment itself, without prices:** a model trained on features other than prices: the floor plan (data/floorplans/), rooms and size, floor, neighbourhood and district, your area ratings, the förening's economy (avgift per m², debt per m², stambyte, tomträtt), build year, features from the description, and travel times. It learns from final prices in the history, so it gives a second opinion that doesn't lean on the asking price. Show both, and where they disagree.

## Done

- **The morning run starts at the 06:25 wake.** On 3 November the Mac with its lid closed was back asleep 21 seconds after its 06:25 wake, so the 06:26 job waited for the next wake at 06:55. The job is now set for 06:24, a minute before the wake: that time passes while the Mac sleeps, and launchd starts a missed job the moment the Mac wakes, so the run begins at 06:25 and caffeinate keeps the Mac awake until it's done.

- **Innerstadsspecialisten and Bronze.** Both are now read from their for-sale lists (26 and 40 apartments in the inner city). Wallin & Co is left out, since all of its apartments are outside the tolls. An apartment whose page gives the floor only in its address ("Tulegatan 32A, 2tr") now gets that floor.

- **Booli compared apartment by apartment.** The one search page the finder reads from Booli each morning carries its first ~35 for-sale apartments as data (address, m², asking price, agent). The finder now keeps them and matches each against ours on address and m² (an address can hold several apartments), and the log says how many it found, has without a price, filters out, or misses, with the agents of the missing ones. Booli is still read only once a day, one page. Also fixed: an agent's "Visa fler" button that misses one click on a slow page is tried once more, rather than ending the list (Bergets Ro's stopped at 16 of 108 on 2 November).

- **Fixes after the 2 November morning run.** The run now starts at 06:26: with the lid closed the Mac stays awake only about two minutes after its 06:25 wake, and on 2 November it was asleep again by 06:30, so the run waited until 06:57. SkandiaMäklarna's prices written with commas (6,795,000 kr) are read, HusmanHagberg's "Kommande®" and SkandiaMäklarna's "REDO™" (sold before the open market, price from the agent) count as coming soon, and SVG icons (Södermäklarna's close button) are no longer tried as floor plans.

- **Förening figures from five more agents.** The reader now opens each agent's own fact sections: Svensk Fastighetsförmedling's "Om bostaden" tab, Bjurfors' "Visa innehåll" sections, SkandiaMäklarna's "Bostadsfakta" and "Bostadsrättsföreningen", HusmanHagberg's "Fakta om bostaden", and Fastighetsbyrån's "Visa beskrivning"; each button is clicked once (a second click folded Svensk Fastighetsförmedling's away again), and an opened section is no longer cut away with the list of other apartments. The red flags also read Fastighetsbyrån's own "Skuldsättning/kvm". Apartments read before are read again when the reader learns a new layout (its version number, FACTS_VERSION). Coverage went from 307 to 456 of 493 apartments, debt figures from about 180 to 301; Svensk Fastighetsförmedling from 13 to 78 of 84, Bjurfors from 39 to all 76.

- **Prices and showings from the right apartment.** The page parser read prices and showing times from the whole page, so an apartment with no price could take one from the "Andra har också tittat på" or "Liknande bostäder" list below it (Sankt Göransgatan 102 at HusmanHagberg showed 3 650 000 kr). It now stops where the list of other apartments starts, and the backfill reads every apartment in the filter again with the fix.

- **A price sense check for apartments without a price.** An apartment with no asking price yet is left out when its estimated price (its district's latest price per m² from Svensk Mäklarstatistik, times its size) is more than 10 % above your max price, since the estimate comes from final prices and asking prices are often set a little lower; a 72 m² apartment in SoFo comes out at about 8,4 Mkr. On by default, with a checkbox under Sökfilter ("Dölj lägenheter utan pris som uppskattas mer än 10 % över maxpriset"). At 3,5–4 Mkr it took the list from 457 to 35 apartments, since even 35 m² in the inner city is estimated at 3,9–4,75 Mkr. The daily Booli comparison doesn't use it, since Booli counts every coming-soon apartment. One without a size is kept, as it can't be estimated.

- **Ranking (Rangordning, under Visningar).** Every apartment in your filter with an asking price gets a score from 0 to 100 from what we already know (those without a price wait, listed under the ranking, until the agent sets one): price against your max, your area rating, the förening's red and green flags, SL travel time to your important addresses (saved apartments), walking distance to grocery store, gym and bar, size, avgift per m² and features. Sliders from 0 to 3 set how much each counts; whatever isn't known for an apartment is left out rather than counted for or against it, and areas marked Aldrig här are left out. Each apartment shows the reasons that weigh most, for and against, and every card on Visningar shows its score.

- **The förening's figures from the agents' fact sections.** The finder opens each apartment's Förening, Föreningen, Om föreningen and Ekonomi tabs and folded sections (and BOSTHLM's separate fakta page), keeps the lines about the förening, and the red flags read them before the description: debt per m² from the indirekt nettoskuldsättning divided by the apartment's m², whether the förening owns the land or has tomträtt, äkta or oäkta, the number of apartments, the latest stambyte in the renovation list (flushing or inspecting the pipes doesn't count), and whether an avgift change is planned. A stambyte more than five years away is yellow ("Stambyte planerat 2035"). Works for Alicia Edelman, Notar, BOSTHLM, Historiska Hem, Tradition, Stadshem, Bergets Ro, Erik Olsson, Länsförsäkringar, SkandiaMäklarna, Karlsson & Uddare, Mäklarhuset and Mäklarringen; apartments found earlier got theirs with `scraper/plans.py --backfill`. Also fixed: a menu link to "Liknande bostäder" near the top of a page no longer cuts off the rest of it.

- **A morning run that doesn't need Amphetamine.** The Mac wakes itself at 06:25 every day (`sudo pmset repeat wakeorpoweron MTWRFSU 06:25:00`, set 2026-11-01; keep the charger connected). The job is set for 06:24, so it is always missed while the Mac sleeps, and launchd starts it the moment the Mac wakes; it's wrapped in `caffeinate -is`, so the Mac stays awake until the run is done and then sleeps again. With the lid closed the Mac stays awake only about 20 seconds after the wake, too short to wait for a later minute (06:30 and 06:26 both waited until the next wake at about 06:55). Check the wake with `pmset -g sched`.

- **A minimum price in the search filter** (Minpris, under Sökfilter). Apartments below it drop out of Visningar and out of the finder's reading, and Booli's count uses the same minimum (`minListPrice`), so the comparison stays like for like. Apartments without a price yet ("snart till salu") are always kept, as with the max price.

- **A daily comparison with Booli.** After each morning run, our count (for sale and coming soon) and Booli's for the same filter are kept for that day, with how many agents were read, in listings.json and data/compare.csv. The finder panel in the planner shows them day by day ("Jämfört med Booli, dag för dag") with our coverage of Booli's for-sale count. Earlier days were filled in from the finder's log.

- **The finder on the agents' own sites, with Booli only as the answer key.** Every agent's for-sale list is read (pressing "show more", following pages, or a recent sitemap where a list is incomplete), the inner-city candidates are opened, and Booli is used only for one count a day for the same filter. Addresses are placed on OpenStreetMap for their neighbourhood and district, with our own search filter (max price, rooms, m², avgift, not the ground floor, coming soon included).
- **Price history for predictions.** Every apartment the finder reads is saved with its details, agent and area, and each day's asking price, state, highest bid, number of bids and final price, in `data/history.sqlite` with CSV exports after every run. Apartments that leave a list are followed for up to 30 days, to catch the final price.
- **Monthly cost per apartment.** Each card shows what it would cost a month with your money plan, and whether it fits your budget.

- **The planner in Swedish.** Every page, label, chart, map, date and status message is in Swedish (Visningar, Sparade, Ekonomi, Köpa), including what the finder writes for the planner: its run status and the red flag labels. Plans saved earlier keep working: an apartment's status and the default rate scenarios are stored as before and shown in Swedish, and the features the finder records in English for the price history are shown in Swedish. Our conversation stays in English.

- **Red flags from the description.** Each apartment shows what its description says about the förening: in red, an upcoming stambyte, the avgift going up, tomträtt (leased land), an oäkta förening, or debt over 15 000 kr/m²; in yellow, debt of 5 000–15 000 kr/m², a possible avgift raise, planned building works, or a förening under 12 apartments; in green, a stambyte done, land owned, äkta förening, low or no debt, no avgift raise planned. Hover a flag to read the sentence it comes from. Reassurances like "inga planerade avgiftshöjningar", "ej tomträtt" and "stambyte kommer inte att ske" are read the right way round. Saved apartments also show what to ask the agent (when the stambyte was, for a house built before 1980 whose description doesn't say). The rules read only the agent's description; a förening's figures listed elsewhere on the page aren't read yet. Also fixed: the "apartments without a showing yet" list hadn't been filling.

- **Bidding, bid by bid, on saved apartments.** Each saved apartment's card shows how its bidding has gone: every bid as a step, coloured by bidder, against the asking price and your max, with the number of bids and bidders, how far over asking it is, the latest bid and who placed it, and a table of every bid with its raise. Where an agent shows its bid list (SkandiaMäklarna, Notar, HusmanHagberg, Mäklarhuset's own bidding page, and others with a "Visa budgivning" or "Visa budhistorik" button), the finder opens it and keeps every bid it has seen across days; otherwise the chart uses the highest bid read each day. Fixed along the way: an apartment no longer counts as in bidding because another apartment listed lower on its page is, which had mislabelled pages at Fastighetsbyrån, Svensk Fastighetsförmedling and Bjurfors; and the cookie banner's "Neka alla" is now answered, which had blocked HusmanHagberg's buttons.

- **Floor plans (planlösning).** Each apartment card has a Floor plan section that opens the agent's own plan in place. The finder looks for it the way the page offers it: an image named as a floor plan (alt text, caption or lightbox), the images beside a "Planritning" label, the section a "Planritning" link points to, or the page's floor plan button; for agents that don't name them, the last images of the full gallery. Each candidate is checked to be a drawing, not a photo (at least 65 % one flat background colour, with lines), which tells all the floor plans tested from all the photos, including beige and black backgrounds. Every plan is saved in data/floorplans/ with the apartment's address, area, rooms, size, avgift, floor and agent in index.json, as training data (kept on this computer only). Found for 18 of the 20 agents; Länsförsäkringar and Notar show no floor plan on their pages. Apartments found earlier got theirs with `scraper/plans.py --backfill`.

- **Important addresses and travel times.** Add the places that matter in the planner (name, address, and the time you want to arrive on a weekday). Each apartment you save shows the trip there by SL, door to door, from SL's own journey planner (Journey-planner v2 on Trafiklab, open without a key), with the lines and changes on hover. Work and your girlfriend's place are added (the addresses stay in data/, which isn't in git). To keep requests few, `scraper/travel.py` looks up only saved apartments, once per address, a lookup a second, after every finder run and whenever you change an address; answers are kept in data/travel.json and looked up again after 90 days.
- **A heart on every apartment** saves it (and looks up its trips) in one tap; tap again to unsave.
- **Nearest grocery store, gym and bar on every apartment,** with walking minutes and the name, from OpenStreetMap (fetched once a week). Grocery stores are only ICA, Hemköp, Coop, Willys and Lidl; bars include pubs; yoga and pilates studios aren't counted as gyms.
- **A smaller "Your areas" panel:** one line per area with its rating and a short note field, visits folded away, and areas marked Never here left out (they're still in Areas).

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
- **Show whether the daily run is installed.** The finder panel shows "Körs varje morgon 06:25; nästa körning i morgon" when the schedule is installed, and how to switch it on when it isn't.
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
- **Running locally:** a local server, the plan saved in `data/plan.json`, and a daily run at 06:25 via `scripts/install-mac.sh`.
- **Moving the plan off claude.ai:** done once with export and import; the export box was then removed.
- **The scraper doesn't stop on a refusal:** it keeps what it read, shows a plain message, and never retries.
