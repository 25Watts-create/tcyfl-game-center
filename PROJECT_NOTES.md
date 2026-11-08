# TCYFL Game Center: Project Notes

Working notes for the tcyfl.net scraper and the Game Center page it builds.
Read this before changing the scraper or adding to the Playoffs section.

_Last reviewed: 2026-11-08, against the data snapshot generated `2026-11-08T13:48Z`._

---

## 1. What this project is

A single Python script (`tcyfl_scraper.py`) that:

1. Finds every division on tcyfl.net.
2. Scrapes each division's **Scores**, **Schedule**, **Standings** and **Playoff Schedule** pages.
3. Writes `tcyfl-data.json` (the raw scraped data).
4. Writes `tcyfl.html`, a self-contained page with the JSON embedded in it and all CSS/JS inline. No build step and no external requests.

A GitHub Action runs the script every 30 minutes on weekends and commits the output to `docs/`, which is served as a static site (GitHub Pages).

```
tcyfl.net ──(requests + BeautifulSoup)──▶ tcyfl_scraper.py
                                              │
                         ┌────────────────────┴───────────────────┐
                         ▼                                        ▼
               docs/tcyfl-data.json                docs/tcyfl.html  (TEMPLATE + __DATA__)
                                                          │  cp
                                                          ▼
                                                   docs/index.html  (what Pages serves)
```

### Files

| Path | What it is |
|---|---|
| `tcyfl_scraper.py` | Scraper, JSON writer and the full HTML/CSS/JS template (`TEMPLATE` at the bottom of the file). |
| `requirements.txt` | `requests`, `beautifulsoup4`. |
| `.github/workflows/update.yml` | Scheduled scrape and commit. |
| `docs/tcyfl-data.json` | Last scraped data. **Also the fallback**: if a division fails, its old entry from here is kept. |
| `docs/tcyfl.html` | Generated page. |
| `docs/index.html` | Copy of `tcyfl.html` made by the workflow. |

> **Never hand-edit anything in `docs/`.** It is overwritten on the next run. All front-end changes go in `TEMPLATE` inside `tcyfl_scraper.py`.

---

## 2. Running it

```bash
pip install -r requirements.txt
```

```bash
python3 tcyfl_scraper.py --out docs                       # all divisions
```

```bash
python3 tcyfl_scraper.py --out docs --only big10-varsity  # refresh one, keep the rest from JSON
```

```bash
python3 tcyfl_scraper.py --list                           # print discovered division ids
```

Then open `docs/tcyfl.html` directly in a browser (it works from `file://`).

The scraper sleeps 0.7 s after every request and retries 3 times with backoff. A full run is roughly 15 divisions × 4 to 5 pages.

**Iterating on the front end only:** you don't need to re-scrape. Load the JSON and re-render the template:

```bash
python3 -c "import json,tcyfl_scraper as s; d=json.load(open('docs/tcyfl-data.json')); open('docs/tcyfl.html','w').write(s.TEMPLATE.replace('__DATA__', json.dumps(d,separators=(',',':')).replace('</','<\\\\/')))"
```

---

## 3. Scraper internals (`tcyfl_scraper.py`)

| Function | Job | Things to know |
|---|---|---|
| `get(url)` | GET plus parse with retries and a polite delay. | One shared `requests.Session`. |
| `discover()` | Reads `index.php` nav links. | Matches `myschedules.php?co=<conf>&division=<div>` → id `"<co>-<div>"`, plus `myschedules7man.php` → `7man`. Display name is just `.capitalize()`, so you get "Big10 Feather" and "Pac10 Featherweight" (inconsistent, comes from the site). |
| `division_links(url)` | Finds the tab links on a division page by exact link text: `schedule`, `scores`, `standings`, `playoff schedule`. | If **none** match, it treats the page itself as a scores page. If the league renames a tab (e.g. "Playoffs"), that tab silently disappears. |
| `parse_games(soup)` | Parses any games table (schedule, results, playoffs). | Walks bold/heading tags and tables in document order. A heading matching `^(week\|round\|semi\|final\|champ\|wild)` becomes the current week label `w`. A table qualifies only if its header row has `date`, `home` and `away`. Scores come from columns literally named `score` (1st = away, 2nd = home). Team names are cut at `•`. OT is detected by any cell matching `OT\d*`. |
| `parse_standings(soup)` | Parses standings tables into tiers. | A "data row" is one whose 2nd cell is an integer. A table with no data rows is treated as a **title** for the next table (the first cell of its first row). 5-col tables with repeated headers are renamed `Div W, Div L, W, L, <last>`. |
| `merge(games)` | De-duplicates on `(date, away, home)`. | Prefers the copy that has a score; carries `f` (field) and `p` (playoff flag) across. |
| `scrape(div)` | Puts it together. | Results page is the source of truth. Schedule page only adds **weeks that results lacks** (schedule can be stale). Field names come from the schedule. Playoff games get `p: 1`. |
| `main()` | CLI, fallback and output. | If a division throws, or returns nothing when it had data before, the old JSON entry is kept and a `WARN` line is printed to stderr. |

---

## 4. Data model (`tcyfl-data.json`)

```jsonc
{
  "generated": "2026-11-08T13:48+00:00",
  "divisions": [
    {
      "id": "big10-varsity",          // "<co>-<division>" or "7man"
      "name": "Big10 Varsity",
      "games": [
        {
          "w":  "Week 1",              // label from the nearest heading
          "d":  "8/22",                // M/D, no year
          "t":  "6:30 PM",
          "a":  "Grayslake",  "h": "Warren",
          "as": 6,            "hs": 34, // null when not played or not posted
          "ot": false,
          "f":  "Warren High School",  // field
          "p":  1                      // only present on playoff games
        }
      ],
      "tiers": [
        { "name": "Premier",
          "cols": ["Div W","Div L","W","L","Pts"],
          "rows": [ { "t": "North Shore", "v": ["6","0","9","0","12"] } ] }   // v is strings
      ]
    }
  ]
}
```

### Current divisions (snapshot 2026-11-08)

| id | games | playoff games | tiers | notes |
|---|---|---|---|---|
| `7man` | 0 | 0 | 0 | Page parses to nothing. |
| `big10-varsity` / `jv` / `lightweight` / `middleweight` / `feather` / `bantam` | 89–276 | 8–24 | 2–6 (Premier, Elite, Regal, Royal, Ultimate, Ultra) | Regular season Weeks 1–10 (no Week 5). Playoff R1 = 10/31–11/1, R2 = 11/7–11/8. |
| `pac10-middleweight` / `featherweight` | 129–156 | 12 | 4 (National, National, American, American) | Team names carry a **Baden/Wilson** suffix in games, not in standings. 8 R1 + 4 R2 games = four 4-team brackets. |
| `pac10-bantam` | 60 | 6 | 1 | 4 R1 + 2 R2 games (top-8 bracket). |
| `pac10-flyweight` | 53 | 0 | 0 | Fly Bowl, no bracket. No standings page parsed. |
| `mac10-lightweight` / `middleweight` | 107–121 | 14 | 4 | Playoffs are a **one-day event** (10/24 or 10/25) where some teams play 2 to 3 games. Not a bracket. |
| `mac10-featherweight` / `bantam` | 6–51 | 0 | 1–2 | |

---

## 5. Front end (`TEMPLATE`)

Plain JS, no framework. Data is in `const DATA=...`.

**Routing** is hash-based: `#d.<divisionId>.<view>[.<teamSlug>]`
- views: `scores`, `standings`, `teams`, `playoffs`, `team`
- e.g. `#d.big10-varsity.team.north_shore`

**Derived per division on load:**
- `d.S[name]`: per-team totals (`w,l,t,pf,pa`, form string `f`, plus `tier`, `st` and `cols` copied from standings). **Built from every game, playoff games included.**
- `d.B[slug]` → name, for team URLs.
- `d.weeks`: unique `w` labels in first-seen order. `d.cw` is the week of the last scored game (the default chip).
- If a division has no standings tiers, one is computed from scores.

**Views:** `scores()` shows week chips and cards. `standings()` shows the league tables plus PF/PA from `d.S`. `teams()` shows cards grouped by tier. `team()` shows a team's record, PF/PA, form and schedule.

### Playoffs tab (what exists today)

It is a **projection** built from regular-season results, plus a flat list of the scraped playoff games at the bottom.

- `PO` config (edit to change qualifier rules):
  ```js
  big10:               {}                          // full ranking, no cut line
  pac10-middleweight:  {per:4}                     // top 4 per conference
  pac10-featherweight: {per:4}
  pac10-bantam:        {top:8, pairs:[[1,5],[2,6],[3,7],[4,8]]}
  pac10-flyweight:     {none:"...Fly Bowl..."}
  mac10-*:             tab hidden (poCfg returns null)
  ```
- Ranking (`order()`): win % → head-to-head (only for an exact 2-team tie) → opponent wins → playoff points (the standings column starting `Pts`). Uses regular-season games only (`!g.p`).
- `poGames()` lists every `p` game as cards.

---

## 6. Automation (`.github/workflows/update.yml`)

- Cron `*/30 * * * 6,0`: every 30 min on **Saturday and Sunday, UTC**.
- Runs the scraper, copies `tcyfl.html` → `index.html`, commits as `github-actions[bot]` if `docs/` changed.
- `concurrency: tcyfl-update` stops overlapping runs.

---

## 7. Review findings: known issues

Ordered by impact. All of these were checked against the committed data and by rendering `docs/tcyfl.html` in a headless browser.

### 🔴 1. PAC10/MAC10 team names don't match between games and standings
Games say `Cary Navy Baden` / `Prairie Ridge Wilson`. Standings say `Cary Navy` / `Prairie Ridge`. The suffix is the bracket **class** (Baden or Wilson). Effects:
- **Standings PF/PA show 0 for every team** in `pac10-middleweight`, `pac10-featherweight` and the MAC10 divisions.
- **The Playoffs tab ranks every PAC10 team 0-0, .000** and falls back to sorting by Pts, so the projection is meaningless there.
- Teams tab shows each team twice (once with the suffix, once without), and standings links open an empty team page.
- MAC10 standings are worse: just the club name (`Huntley` appears in several tiers), so names are ambiguous without the class.

**Fix direction:** in the scraper, split a trailing ` Baden` / ` Wilson` off game team names into a class field and keep the base name. Then map each standings tier to its class. The tier titles are currently duplicated (`National, National, American, American`), which suggests the class heading sits in a row or element `parse_standings` isn't capturing. **This needs a look at the live standings HTML.**

### 🔴 2. Playoff games collide with regular-season week labels
Playoff pages use headings `Week 1`, `Week 2`, so playoff games get `w: "Week 1"`. In the Scores tab, the **Week 1 chip shows 22 cards for big10-varsity: 16 regular-season plus 6 playoff games** (tagged "· Playoffs" but mixed in). Fix: give playoff games their own label in `scrape()`, e.g. `g["w"] = "Playoffs " + g["w"]` or `"Round N"`.

### 🔴 3. No playoff scores are captured
Every one of the 132 playoff games has `as/hs = null`, including Round 1 games played 10/31 to 11/1. Either the playoff page has no score columns, or they're named something other than `score`, or results are posted somewhere else. **Unverified: check the live Playoff Schedule HTML.** Until then, winners can be inferred (see §8): for every completed round in every bracketed division, exactly one team from each game appears in the next round.

### 🟠 4. Playoff games count toward team records
`d.S` totals include `p` games, so once playoff scores arrive the Standings PF/PA, team record and form will include playoff games, while the league's own W/L columns won't. Decide which you want; probably regular season only, with a separate playoff line on the team page.

### 🟠 5. Big10 Playoffs tab has no cut line
`PO.big10 = {}`, so it's a full ranking with no qualifiers or matchups, even though the actual bracket is already posted (R1 and R2 games exist). For divisions with a posted bracket, the real bracket is more useful than a projection.

### 🟠 6. MAC10 playoffs are hidden but exist
MAC10 lightweight and middleweight each have 14 playoff games (single-day event, teams playing multiple games). The tab is hidden, so these only show up mixed into Scores → Week 1.

### 🟡 7. `BYE` is treated as a team
`BYE` appears as an opponent in 6 divisions (time `12:00 AM`). It shows up in the Teams tab (only `TBD` is filtered). Filter `BYE` the same way as `TBD`.

### 🟡 8. Every scheduled run commits, even with no data change
The last 4 commits checked were **timestamp-only** (`generated` changes each run, so `git diff` is never empty). That is about 48 commits per weekend of noise. Fix: compare the new JSON without `generated` to the old one and skip writing if equal (or keep the old timestamp).

### 🟡 9. Schedule window misses some updates
The cron is UTC Sat/Sun only. Sunday games in Central time that finish after ~7 PM CT (= Monday UTC) and weekday makeups (e.g. big10-bantam Week 8 on **Wed 11/11**) won't show until the next Saturday. Consider adding a Monday early-UTC run and one daily run, or triggering by hand with `workflow_dispatch`.

### 🟡 Smaller notes
- Dates have no year. Fine within one season; matters if you ever keep history.
- `merge()` key is `(date, away, home)`, so the same matchup listed with a different date format won't merge.
- Tier names aren't unique, so anything keyed by tier name (Teams tab grouping, `s.tier`) merges two conferences into one.
- `7man` scrapes to nothing. The page layout probably differs; worth a look or remove it from the dropdown when empty.
- `pac10-flyweight` has no standings parsed (falls back to calculated standings).

---

## 8. Playoffs section: plan

### What the data supports today

| Division group | Shape | What to show |
|---|---|---|
| Big10 (6 divisions) | Week 1 → Week 2 ... Top seeds get a bye into R2 (R2 = R1 winners + equal number of bye teams). | Real bracket by round, with inferred winners and bye teams labelled. |
| PAC10 middle/feather | 16 teams, 8 R1 → 4 R2: four 4-team brackets (class × conference). | Four small brackets, grouped by Baden/Wilson and conference. |
| PAC10 bantam | 8 teams: 4 R1 → 2 R2 → final. | One 8-team bracket. |
| PAC10 flyweight | Fly Bowl, no bracket. | Keep the message. |
| MAC10 light/middle | One-day event, multiple games per team. | Day schedule grouped by class or field, plus results. Not a bracket. |

### Inferring winners without scores
For round *k*, a team in a round *k* game that also appears in round *k+1* is the winner. Checked against current data: **100% of completed-round games** resolve this way in Big10 and PAC10 (e.g. big10-varsity: 6/6, big10-middleweight: 12/12). Teams that appear in round *k+1* but not round *k* had a **bye**. Always prefer a real score when one exists, and mark inferred results as such in the UI ("advanced").

### Proposed changes, in order

1. **Scraper: clean playoff game data** (`scrape()`)
   - Relabel playoff weeks so they don't collide: keep the round number as `r` (int) and set `w` to `"Playoffs R<n>"`.
   - Split class suffix: `"Cary Navy Baden"` → name `"Cary Navy"` + `cls: "Baden"`. Do this for **all** games in divisions where it applies, which also fixes issue #1. Keep it configurable (regex `\s(Baden|Wilson)$`).
2. **Scraper: build a `playoffs` block per division** so the JSON is useful on its own:
   ```jsonc
   "playoffs": {
     "format": "bracket",            // "bracket" | "event" | "none"
     "rounds": [
       { "r": 1, "name": "Round 1", "games": [ /* game objects + "win": "a"|"h"|null, "how": "score"|"advanced" */ ] },
       { "r": 2, "name": "Round 2", "byes": ["North Shore", "..."], "games": [ ... ] }
     ]
   }
   ```
3. **Front end: new Playoffs view**
   - Top: the actual bracket (rounds as columns on desktop, stacked on mobile). Winner bold, "advanced" badge when inferred, "BYE" for bye teams, upcoming games show date, time and field.
   - Below, collapsible: the existing **projected seeding** table (keep `PO` and `order()`, just fix the name matching).
   - Show MAC10's tab with the event-day layout instead of hiding it.
4. **Scores tab**: playoff chips (`Playoffs R1`, `Playoffs R2`, ...) after the regular-season weeks.
5. **Team page**: separate "Playoffs" block; keep record/PF/PA regular-season only.

### Verify on the live site first (couldn't reach tcyfl.net from the review sandbox)
- [ ] Playoff Schedule page: what are the table headers? Is there any score column? Are round headings `Week N` or something like `Quarterfinal`?
- [ ] Where do playoff results get posted: playoff page, Scores page, or only the Downloads PDFs?
- [ ] Standings page for PAC10/MAC10: where is the Baden/Wilson label for each table?
- [ ] Big10: are the brackets per tier (Premier/Elite/...)? The bye pattern suggests separate brackets. If so, how can a game be tied to its bracket (heading, table, or only by team)?
- [ ] 7-Man page layout.

Quick way to save a page for offline parsing work:

```bash
mkdir -p samples && curl -sL 'https://www.tcyfl.net/<playoff page url>' -o samples/playoffs-big10-varsity.html
```

Then iterate with `parse_games(BeautifulSoup(open(path).read(), "html.parser"))` without hitting the server.

---

## 9. Conventions and gotchas

- Keep the output **one self-contained HTML file**. No CDN scripts, no fetch of the JSON at runtime. That's what makes `file://` and Pages both work.
- Escape all scraped text with `esc()` in the template. Team and field names come from a third-party site.
- `</` is escaped in the embedded JSON (`<\/`) so a team name can't close the `<script>` tag. Keep that.
- Standings values (`v`) are **strings**. Game scores (`as`, `hs`) are **ints or null**.
- When adding a field to game objects, remember `merge()` only carries `f` and `p` across duplicates. Add new fields there too.
- Be polite to tcyfl.net: keep the delay, and don't increase the cron frequency beyond what's useful.
