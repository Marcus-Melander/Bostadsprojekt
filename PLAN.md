# Plan: showings, areas and location scores

The goal is a new **Showings** view in the planner. It lists apartments in innanför tullarna that have a visning next week. Each one gets a one-sentence description and a score for how well it fits you. You can rate an area after a showing, and apartments near places you disliked are hidden.

The work is split into seven steps of about a day each. Each step ends in something you can use.

## Constraints that shape the design

- **The published planner can't fetch other websites.** A claude.ai artifact runs in a sandbox, so listing data has to arrive some other way: a local script that writes a data file, the desktop app's browser, or something you paste in.
- **Booli blocks plain scripted requests.** A direct request from a script gets HTTP 403. We will not disguise a script as a browser to get around that. Step 1 picks a source that works within what Booli allows.
- **Location data must be free and have no key where possible.** Grocery stores and gyms come from OpenStreetMap through the Overpass API, which is free and needs no key. Commute times need SL's journey planner through Trafiklab's ResRobot API, which needs a free personal key.

## Steps

### 1. Listing source (needs your decision)

| Option | How it works | Trade-off |
|---|---|---|
| A. Booli in the app's browser | When you open the planner in the Claude desktop app, it reads Booli's search results and listing pages through the built-in browser, which you approve. It opens only the pages a person would, at a slow pace. | Most automatic. It only works in the desktop app, and Booli's terms of use should be checked first. |
| B. Paste a listing | You paste a Booli link or the listing text, and the planner extracts the fields. | Always works and is fully within the rules, but it is manual: about 10 seconds per apartment. |
| C. Booli e-mail alerts | Booli e-mails new listings for a saved search, and the planner reads them through your Gmail connector. | Automatic and legitimate, but the e-mails may not include showing times. |
| D. Ask Booli for API access | Booli has offered an API to partners. | Cleanest data, but it is uncertain and slow to get. |

Whichever source we pick fills the same record for each apartment: id, link, address, latitude and longitude, price, kvm, rooms, avgift, floor, showing times and the description text.

### 2. Features and a one-line description

- Rules in Swedish pick out features from the description: balkong, hiss, eldstad, renoverat kök or badrum, stambyte gjort, takhöjd, våning, läge (gård or gata, söderläge), förråd, uteplats and tvättstuga.
- The planner's built-in Claude access then writes one sentence per apartment from those features. For example: "Bright 2:a on 4th floor with balcony to the courtyard, kitchen renovated 2021, stambyte done."
- Each sentence is generated once and saved.

### 3. Showings next week

- The view shows a day-by-day list of showings from Monday to Sunday.
- Each row shows the time, the address, the one-line description, the monthly cost from your finances, and whether it fits your max bid.
- One click adds an apartment to your tracked apartments or to the bidding plan.

### 4. Area ratings and "never near here again"

- After a showing you rate the place from 1 to 5, or choose **never near here**. The rating is saved with the apartment's coordinates.
- Apartments within a set distance of a disliked place are hidden or scored lower. The distance is 600 m by default and you can change it. Apartments near places you liked get a boost.
- You can also rate whole neighbourhoods, such as Hornstull, SoFo, Birkastan or Gärdet, before you've been to any showing there.

### 5. Location scores

- **Grocery stores and gyms:** a local script downloads supermarkets and gyms in innanför tullarna from OpenStreetMap once, and the planner works out the walking distance to the nearest of each.
- **Commute:** you enter your work address. With a ResRobot key, the script works out the door-to-door SL travel time from a grid of points over the inner city. Without a key, the planner estimates it from the nearest T-bana station and the line to your work.
- These are saved as a data file, so the planner needs no network at runtime.

### 6. Ranking

- One score per apartment combines:
  - fit with your budget;
  - commute time;
  - distance to a grocery store and to a gym;
  - your area ratings;
  - features you care about.
- You set how much each part counts with sliders.
- Each apartment shows why it scored as it did. For example: "12 min to work, ICA 150 m, no gym within 1 km, near a place you rated 2."

### 7. Map, tests and release

- Add a map if the artifact sandbox allows map tiles. Otherwise, draw a simple outline of innanför tullarna.
- Extend the browser test, update the README and push.

## Order and dependencies

- Step 1 must come first, because every later step uses the listing record.
- Steps 4 and 5 don't depend on each other.
- Step 6 needs steps 2 to 5.
