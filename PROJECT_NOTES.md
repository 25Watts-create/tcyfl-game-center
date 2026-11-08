# TCYFL Game Center: Project Notes

Working notes for the tcyfl.net scraper and the Game Center page it builds.
Read this before changing the scraper or the Playoffs tab.

_Last updated: 2026-11-08 (Playoffs tab rebuilt from the league's Seeds & Scores bracket; MAC10 excluded)._

---

## 1. What this project is

A single Python script (`tcyfl_scraper.py`) that:

1. Finds every **Big10, PAC10 and 7-Man** division on tcyfl.net. **MAC10 is excluded** (`EXCLUDE = {"mac10"}`).
2. For each division, scrapes **Scores**, **Schedule**, **Standings**, **Playoff Schedule** and **Seeds & Scores** (the bracket).
3. Writes `tcyfl-data.json` (the raw scraped data).
4. Writes `tcyfl.html`, a self-contained page with the JSON embedded and all CSS/JS inline. No build step and no external requests.

A GitHub Action runs it every 30 minutes on weekends and commits the output to `docs/`, which is served as a static site (GitHub Pages).

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

| Path | What it is |
|---|---|
| `tcyfl_scraper.py` | Scraper, JSON writer and the full HTML/CSS/JS template (`TEMPLATE` at the bottom). |
| `requirements.txt` | `requests`, `beautifulsoup4`. |
| `.github/workflows/update.yml` | Scheduled scrape and commit. |
| `docs/tcyfl-data.json` | Last scraped data. **Also the fallback**: if a division fails, its old entry is kept. |
| `docs/tcyfl.html`, `docs/index.html` | Generated page (index is a copy). |

> **Never hand-edit `docs/`.** It is overwritten on every run. Front-end changes go in `TEMPLATE` inside `tcyfl_scraper.py`.

---

## 2. Running it

```bash
pip install -r requirements.txt
```

```bash
python3 tcyfl_scraper.py --out docs && cp docs/tcyfl.html docs/index.html
```

```bash
python3 tcyfl_scraper.py --out docs --only big10-varsity pac10-middleweight
```

```bash
python3 tcyfl_scraper.py --list
```

Open `docs/tcyfl.html` directly in a browser (works from `file://`).

**Front-end-only changes:** re-render from the saved JSON without hitting the site:

```bash
python3 -c "import json,tcyfl_scraper as s; d=json.load(open('docs/tcyfl-data.json')); open('docs/tcyfl.html','w').write(s.TEMPLATE.replace('__DATA__', json.dumps(d,separators=(',',':')).replace('</','<\\\\/')))"
```

**Cloud sessions:** tcyfl.net is only reachable from the `tcyfl-game-center` cloud environment (it allows `tcyfl.net` and `www.tcyfl.net`). The Default environment blocks it.

---

## 3. The league's pages

Each division page (`myschedules.php?co=<conf>&division=<div>`) has these tabs (link text → URL):

| Tab | URL | Notes |
|---|---|---|
| Schedule | `TabbedGameSchedulesNEW.php?league=..&division=..` | May be stale; only used for weeks Scores lacks, and for field names. |
| Scores | `tabbedResultsNEW.php?...` | Source of truth for regular-season results. |
| Standings | `tabbedBig10standingsNEW.php` / `tabbedPac10standingsNEW.php` | PAC10 tables put **Baden/Wilson** in the first header cell. |
| Playoff Schedule | `TabbedPlayoffSchedulesNEW.php?...` | Date/time/away/home/field per `WEEK N`. **No score column.** Flyweight says "no bowl games posted". |
| Seeds & Scores | `bracket.php?league=..&division=..` | **302 redirect** to the real page: `playoffpairings6.php` (Big10 6-team, PAC10 Flyweight), `pac10playoffpairings.php` (PAC10 Middle/Feather), `playoffpairings8b_NS.php` (PAC10 Bantam). **The only place playoff seeds and scores appear.** |

### Bracket formats (2026 season)

| Divisions | Format |
|---|---|
| Big10 (all 6) | One 6-team bracket per class (`1A`…`6A`). Seeds 1–2 get byes. R1: 6v3, 5v4. R2: 1 v lowest remaining seed, 2 v the other. Then the class championship. |
| PAC10 Middleweight, Featherweight | Per conference (`National`, `American`): a 4-team **Baden** bracket and a 4-team **Wilson** bracket (1v4, 2v3). Their winners meet in the conference championship. |
| PAC10 Bantam | One 8-team bracket: 1v5, 2v6, 3v7, 4v8. |
| PAC10 Flyweight | No drawn bracket. The page is only a text description of the format. |
| 7-Man | Not parsed (its page lists sub-divisions like `K-1st`, `2-3rd`; scrapes to nothing today). |

**Baden and Wilson** are PAC10 divisions that group teams. They are not Big10-style classes. Game and playoff-schedule pages append the division to the team name (`Cary Navy Baden`); standings and brackets don't. The scraper strips the suffix (`GROUP_SUFFIX`) and records it in `division.groups`. No PAC10 team's base name exists in both Baden and Wilson, so stripping is safe. Re-check this if a collision appears.

---

## 4. Scraper internals (`tcyfl_scraper.py`)

| Function | Job |
|---|---|
| `fetch(url)` / `get(url)` | GET with retries and a 0.7 s polite delay; `get` returns BeautifulSoup. Redirects are followed (needed for `bracket.php`). |
| `discover()` | Reads `index.php` nav links; skips conferences in `EXCLUDE`. |
| `division_links(url)` | Finds tab links by exact text: `schedule`, `scores`, `standings`, `playoff schedule`, `seeds & scores`. If the league renames a tab, that data silently disappears. |
| `parse_games(soup)` | Any games table (header row must contain `date`, `home`, `away`). Week label from the nearest bold/heading text. Scores come from columns literally named `score`. |
| `parse_standings(soup)` | Tiers. Tier name = title table text plus the first header cell when it is non-empty (gives `National · Baden`). |
| `merge(games)` | De-dupes on `(date, away, home)`, preferring the copy with a score. |
| `parse_bracket(html)` | Parses Seeds & Scores (see below). |
| `_fill_bracket(rounds, champ)` | Fills seeds and full names for later-round slots from earlier rounds, and sets each game's winner. |
| `link_playoffs(po, sched)` | Joins bracket ↔ Playoff Schedule: schedule `Week N` = bracket round N, matched on the team pair. Copies date/time/field onto bracket games, and seeds/scores/round/bracket onto schedule games. Also infers a winner when a team appears in next week's schedule but the bracket hasn't been updated yet. |
| `strip_groups(games)` | Removes the Baden/Wilson suffix and returns `{team: group}`. |
| `scrape(div)` | Puts it together. Playoff games are kept **out of** `games` (regular season only) and go in `playoffs`. |

### How `parse_bracket` reads the grid
The bracket is drawn as an HTML table per bracket (`class="seedtable"`):
- **Column = round.** Column 0 is round 1, column 1 is round 2 (Big10 byes are placed straight into it), and so on. A final column with a single cell holds the champion.
- **Team cells** have class `t` or `tright` and read `"<seed>. <Name> (<score>) [OTn]"`. Empty `t`/`tright` cells are slots not filled in yet. Within a column, consecutive slots (top to bottom) are a game.
- Later-round names sometimes lack a seed or are shortened (`Huntley` for `Huntley Black`). `same_team()` matches on equality or a word-prefix, and `_fill_bracket` restores the longer name and seed.
- **Rowspan label cells** (`BADEN`, `WILSON`, Big10's `AAA`) shift the following row's cells right, so `_grid()` does a real row/col layout. When a table has 2+ labels, each game at or left of the label column takes the nearest label as `grp`.
- The markup has unclosed `<td>`s, so `_grid()` tokenizes with regex instead of relying on a parser.
- **Winner:** higher score if both are posted; otherwise the team that appears in the next round (or as champion), shown in the UI as "advanced (score not posted)".

---

## 5. Data model (`tcyfl-data.json`)

```jsonc
{
  "generated": "2026-11-08T14:12+00:00",
  "divisions": [{
    "id": "pac10-middleweight", "name": "Pac10 Middleweight",
    "games": [   // REGULAR SEASON ONLY
      {"w":"Week 1","d":"8/22","t":"6:30 PM","a":"Cary Navy","h":"Glenview","as":6,"hs":34,"ot":false,"f":"Cary Junior High"}
    ],
    "tiers": [{"name":"National · Baden","cols":["Won","Lost","Pts"],"rows":[{"t":"Mount Prospect Blue","v":["9","0","13"]}]}],
    "groups": {"Cary Navy":"Baden", "...": "Wilson"},          // PAC10 only
    "playoffs": {                                                // absent when nothing is posted
      "note": "",                                                // format text when no bracket is drawn (Flyweight)
      "brackets": [{
        "name": "National",                                      // Big10: "1A".."6A"; PAC10 Bantam: ""
        "note": "",                                              // Big10's "Second round: ..." rule text
        "champ": null,                                           // {s,n,sc,ot} once filled
        "rounds": [{"name":"Round 1","games":[{
            "t1":{"s":4,"n":"North Shore Royal","sc":0,"ot":""},
            "t2":{"s":1,"n":"Mount Prospect Blue","sc":20,"ot":""},
            "win":2,                                             // 0 unknown, 1 = t1, 2 = t2
            "grp":"Baden",                                       // PAC10 only
            "d":"10/31","t":"10:00 AM","f":"Melas Park","home":2 // from the playoff schedule, when matched
        }]}, {"name":"Round 2"}, {"name":"Championship"}]
      }],
      "games": [ // the Playoff Schedule, enriched
        {"w":"Week 1","d":"10/31","t":"10:00 AM","a":"North Shore Royal","h":"Mount Prospect Blue",
         "as":0,"hs":20,"ot":false,"f":"Melas Park","r":"Round 1","br":"National","grp":"Baden","sa":4,"sh":1}
      ]
    }
  }]
}
```

Round names: the last round is `Championship`; the others are `Round N`.

---

## 6. Front end (`TEMPLATE`)

Plain JS, no framework. Hash routing: `#d.<divisionId>.<view>[.<teamSlug>]`, where view is `scores | standings | teams | playoffs | team`.

- `d.S` holds per-team totals built from **regular-season games only**. `BYE` and `TBD` are not teams (`isTeam`).
- **Scores**: week chips (regular season only).
- **Standings**: league tables plus PF/PA. PAC10 tiers are now `National · Baden`, etc., and names match the game data.
- **Teams**: cards grouped by tier.
- **Team page**: record, PF/PA, form, then a **Playoffs** block (if any) and the **Regular season** schedule.
- **Playoffs** (`playoffs()`): replaces the old projected-ranking tab.
  1. The format note, if the league only posts text (PAC10 Flyweight).
  2. One card per bracket: rounds as columns (`.bk`, scrolls sideways on phones). Each game shows seeds, scores (OT), the winner in bold, the Baden/Wilson tag, and date · time · field. Unplayed slots show TBD with a dashed border. A Champion column appears once the league fills it in.
  3. **Playoff schedule**: every scheduled playoff game grouped by week, with seeds and scores pulled from the bracket.
  The tab is hidden when a division has no `playoffs` data.

---

## 7. Automation (`.github/workflows/update.yml`)

- Cron `*/30 * * * 6,0`: every 30 min on Saturday and Sunday, **UTC**.
- Runs the scraper, copies `tcyfl.html` → `index.html`, commits if `docs/` changed. `concurrency` stops overlapping runs.
- The Action runs on GitHub's servers, so it reaches tcyfl.net regardless of cloud-session network settings.

---

## 8. Known issues and ideas

Fixed in the 2026-11-08 update:
- ✅ PAC10 names mismatched between games and standings (PF/PA showed 0, projection ranked everyone 0-0, duplicate teams).
- ✅ Playoff games mixed into the regular-season Week 1/2 chips.
- ✅ No playoff scores (they live only on Seeds & Scores, which wasn't scraped).
- ✅ Playoff games counted in regular-season records.
- ✅ `BYE` shown as a team.
- ✅ Duplicate tier names (`National, National, ...`).
- ✅ MAC10 removed.

Still open:
- 🟡 **Every scheduled run commits even when nothing changed**: `generated` changes each run. About 48 noise commits per weekend. Fix: skip writing when the JSON minus `generated` is unchanged.
- 🟡 **Schedule window**: UTC Sat/Sun only. Late Sunday games (after ~7 PM CT) and weekday makeups wait until the next Saturday. Consider a Monday early-UTC run, or use `workflow_dispatch`.
- 🟡 **7-Man** scrapes to nothing (its page links per-grade sub-divisions). Either parse those or drop it from the dropdown.
- 🟡 **PAC10 Flyweight** shows only format text: no bracket is drawn and no bowl games are posted.
- 🟡 Bracket ↔ schedule matching assumes `Week N` == round N. If the league ever adds a playoff week with no bracket round, matching shifts. `link_playoffs` is the place to fix that.
- 🟡 Dates have no year; `merge()` keys on `(date, away, home)`.

---

## 9. Conventions and gotchas

- Keep the output **one self-contained HTML file** (no CDN, no runtime fetch).
- Escape all scraped text with `esc()` in the template.
- `</` is escaped in the embedded JSON (`<\/`) so team names can't close the `<script>` tag.
- Standings values (`v`) are **strings**; game scores are **ints or null**.
- When adding a field to regular-season game objects, `merge()` only carries `f` and `p` across duplicates.
- To debug a parser offline, save the page and run the parser on the file:
  ```bash
  mkdir -p samples && curl -sL 'https://www.tcyfl.net/bracket.php?league=big10&division=varsity' -o samples/br-big10-varsity.html
  ```
  ```bash
  python3 -c "import json,tcyfl_scraper as s; print(json.dumps(s.parse_bracket(open('samples/br-big10-varsity.html',encoding='latin-1').read()),indent=1))"
  ```
- Be polite to tcyfl.net: keep the delay; don't raise the cron frequency beyond what's useful.
