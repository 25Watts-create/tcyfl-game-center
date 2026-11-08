#!/usr/bin/env python3
"""TCYFL Game Center builder.

Scrapes every Big10, PAC10 and 7-Man division on tcyfl.net (MAC10 is excluded):
schedules, scores, standings, the playoff schedule and the Seeds & Scores bracket.
Regenerates two files in the output folder:
  tcyfl-data.json   the scraped data
  tcyfl.html        a self-contained, modern front end with the data embedded

Usage:
  python3 tcyfl_scraper.py                    # all divisions -> ./tcyfl.html
  python3 tcyfl_scraper.py --out ~/public_html/tcyfl
  python3 tcyfl_scraper.py --only big10-varsity pac10-bantam
  python3 tcyfl_scraper.py --list             # show discovered divisions

If a division fails to scrape, its previous data from tcyfl-data.json is kept.
Needs: python3, requests, beautifulsoup4.
"""
import argparse, datetime, html, json, re, sys, time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.tcyfl.net/"
EXCLUDE = {"mac10"}  # conferences (the "co" in the URL) this project ignores
# PAC10 teams are grouped into the Baden and Wilson divisions. Game pages append the
# division to the team name ("Cary Navy Baden"); standings and brackets do not.
GROUP_SUFFIX = re.compile(r"\s+(Baden|Wilson)$")
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "Mozilla/5.0 (compatible; tcyfl-game-center/1.0)"


def fetch(url, tries=3):
    """GET a page and return its HTML text (follows redirects)."""
    for i in range(tries):
        try:
            r = SESSION.get(url, timeout=30)
            r.raise_for_status()
            time.sleep(0.7)  # be polite to the league server
            return r.text
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def get(url):
    return BeautifulSoup(fetch(url), "html.parser")


def cell(c):
    return re.sub(r"\s+", " ", c.get_text(" ", strip=True))


def num(s):
    s = (s or "").strip()
    return int(s) if s.isdigit() else None


def discover():
    """Find every division from the site navigation."""
    soup = get(BASE + "index.php")
    found = {}
    for a in soup.find_all("a", href=True):
        h = a["href"]
        m = re.search(r"myschedules\.php\?co=(\w+)&division=(\w+)", h)
        if m:
            co, dv = m.groups()
            if co.lower() in EXCLUDE:
                continue
            found[f"{co}-{dv}"] = {"id": f"{co}-{dv}", "name": f"{co.capitalize()} {dv.capitalize()}", "url": urljoin(BASE, h)}
        elif "myschedules7man.php" in h:
            found["7man"] = {"id": "7man", "name": "7-Man", "url": urljoin(BASE, h)}
    return list(found.values())


def division_links(url):
    """Read the tab links (Schedule / Scores / Standings / Playoff Schedule / Seeds & Scores)."""
    soup = get(url)
    out = {}
    pats = (("schedule", r"^schedule$"), ("scores", r"^scores$"), ("standings", r"^standings$"),
            ("playoffs", r"^playoff schedule$"), ("bracket", r"^seeds\s*(&|and)\s*scores$"))
    for a in soup.find_all("a", href=True):
        t = cell(a).lower()
        for key, pat in pats:
            if key not in out and re.search(pat, t):
                out[key] = urljoin(url, a["href"])
    if not out:  # unfamiliar page layout: treat the page itself as a results page
        out["scores"] = url
    return out


def parse_games(soup):
    """Parse schedule OR results tables. Week labels come from bold headings."""
    games, week = [], ""
    for el in soup.find_all(["strong", "b", "h1", "h2", "h3", "h4", "h5", "h6", "table"]):
        if el.name != "table":
            t = cell(el)
            if re.match(r"^(week|round|semi|final|champ|wild)", t, re.I):
                week = t.title()
            continue
        if el.find("table"):
            continue
        rows = el.find_all("tr")
        if not rows:
            continue
        hdr = [cell(c).lower() for c in rows[0].find_all(["th", "td"])]
        if "date" not in hdr or "home" not in hdr or "away" not in hdr:
            continue
        ia, ih = hdr.index("away"), hdr.index("home")
        sc = [i for i, x in enumerate(hdr) if x == "score"]
        fi = hdr.index("field") if "field" in hdr else None
        for r in rows[1:]:
            c = [cell(x) for x in r.find_all("td")]
            if len(c) <= max(ia, ih):
                continue
            g = {
                "w": week or "Games",
                "d": re.sub(r"\s*-\s*", "/", c[0]),
                "t": c[1].replace(".", ":") if len(c) > 1 else "",
                "a": c[ia].split("\u2022")[0].strip(),
                "h": c[ih].split("\u2022")[0].strip(),
                "as": num(c[sc[0]]) if sc and len(c) > sc[0] else None,
                "hs": num(c[sc[1]]) if len(sc) > 1 and len(c) > sc[1] else None,
                "ot": any(re.fullmatch(r"OT\d*", x, re.I) for x in c),
                "f": c[fi] if fi is not None and len(c) > fi else "",
            }
            if g["a"] and g["h"]:
                games.append(g)
    return games


def parse_standings(soup):
    tiers, title = [], None
    for t in soup.find_all("table"):
        if t.find("table"):
            continue
        rows = [[cell(c) for c in r.find_all(["th", "td"])] for r in t.find_all("tr")]
        rows = [r for r in rows if any(r)]
        if not rows:
            continue
        data = [r for r in rows if len(r) >= 3 and re.fullmatch(r"-?\d+", r[1] or "")]
        if not data:
            title = rows[0][0].strip() or title
            continue
        head = [r for r in rows if r not in data]
        width = len(data[0]) - 1
        cols = head[-1][1:] if head else []
        # PAC10 tables name the Baden/Wilson division in the first header cell.
        sub = head[-1][0].strip() if head and head[-1] else ""
        if sub:
            title = f"{title} · {sub}" if title else sub
        if len(cols) != width:
            cols = [f"Col {i + 1}" for i in range(width)]
        if len(cols) == 5 and cols[:2] == cols[2:4]:
            cols = ["Div W", "Div L", "W", "L", cols[4]]
        tiers.append({"name": title or "Standings", "cols": cols, "rows": [{"t": r[0], "v": r[1:]} for r in data]})
        title = None
    return tiers


def merge(games):
    out = {}
    for g in games:
        k = (g["d"], g["a"], g["h"])
        if k not in out:
            out[k] = g
            continue
        old = out[k]
        if old["as"] is None and g["as"] is not None:
            g["f"] = old.get("f") or g.get("f")
            g["p"] = old.get("p") or g.get("p")
            out[k] = g
        else:
            old["f"] = old.get("f") or g.get("f")
            old["p"] = old.get("p") or g.get("p")
    return list(out.values())


# ---------------------------------------------------------------------------
# Seeds & Scores (bracket.php). The league draws each bracket as a grid table:
# column 0 = round 1, column 1 = round 2, ... and the last single cell = champion.
# Team cells have class "t" or "tright" and read "<seed>. <name> (<score>) [OTn]";
# empty cells of those classes are slots not filled in yet. Big10 byes are placed
# straight into column 1. Labels such as BADEN / WILSON sit in rowspan cells, which
# shift later cells to the right, so the grid has to be laid out properly.
# ---------------------------------------------------------------------------
_SLOT = re.compile(r"^(?:(\d+)\s*\.\s*)?(.*?)\s*(?:\((\d+)\))?\s*(OT\d*)?\s*$", re.I)


def _text(fragment):
    t = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(t)).replace("\xa0", " ").strip()


def _attr(attrs, name):
    m = re.search(name + r"""\s*=\s*["']?([^"'\s>]+)""", attrs, re.I)
    return m.group(1) if m else ""


def _grid(chunk):
    """Lay out one bracket table as {(row, col): (cls, text, rowspan)}; tolerates unclosed <td>."""
    cells, taken = {}, set()
    for r, row in enumerate(re.split(r"<tr\b[^>]*>", chunk, flags=re.I)[1:]):
        c = 0
        for m in re.finditer(r"<t[dh]\b([^>]*)>(.*?)(?=<t[dh]\b|</t[dh]>|</tr|$)", row, re.I | re.S):
            while (r, c) in taken:
                c += 1
            attrs = m.group(1)
            cs = int(_attr(attrs, "colspan") or 1)
            rs = int(_attr(attrs, "rowspan") or 1)
            cells[(r, c)] = (_attr(attrs, "class").lower(), _text(m.group(2)), rs, cs)
            for dr in range(rs):
                for dc in range(cs):
                    taken.add((r + dr, c + dc))
            c += cs
    return cells


def _slot(text):
    m = _SLOT.match(text)
    if not m or not m.group(2):
        return None
    seed, name, score, ot = m.groups()
    return {"s": int(seed) if seed else None, "n": GROUP_SUFFIX.sub("", name).strip(),
            "sc": int(score) if score else None, "ot": (ot or "").upper()}


def same_team(a, b):
    """Bracket names are sometimes shortened ("Huntley" for "Huntley Black")."""
    a, b = (a or "").lower().strip(), (b or "").lower().strip()
    return bool(a and b) and (a == b or a.startswith(b + " ") or b.startswith(a + " "))


def parse_bracket(text):
    """Return {"brackets": [...], "note": str}. Each bracket: name, note, rounds, champ."""
    chunks = re.split(r"<table\b[^>]*seedtable[^>]*>", text, flags=re.I)
    if len(chunks) == 1:  # no drawn bracket, only a format description (e.g. PAC10 Flyweight)
        body = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
        body = re.sub(r"(?i)<br\s*/?>|</p>|<hr[^>]*>", "\n", body)
        lines = [_text(x) for x in body.split("\n")]
        return {"brackets": [], "note": "\n".join(x for x in lines if x)}
    out = []
    for chunk in chunks[1:]:
        cells = _grid(chunk)
        name, note, labels, cols = "", "", [], {}
        for (r, c), (cls, txt, rs, cs) in sorted(cells.items()):
            if cls in ("t", "tright"):
                cols.setdefault(c, []).append((r, _slot(txt)))
            elif cs >= 4 and txt and not name and not cols:
                name = txt
            elif re.search(r"round|seed", txt, re.I) and len(txt) > 30:
                note = txt
            elif re.fullmatch(r"[A-Z]{3,}", txt):
                labels.append((r + (rs - 1) / 2, c, txt.title()))
        if not cols:
            continue
        order = sorted(cols)
        champ = None
        if len(cols[order[-1]]) == 1 and len(order) > 1:
            champ = cols[order.pop()][0][1]
        rounds = []
        for c in order:
            sl = cols[c]
            games = []
            for i in range(0, len(sl) - 1, 2):
                (r1, a), (r2, b) = sl[i], sl[i + 1]
                games.append({"t1": a, "t2": b, "mid": (r1 + r2) / 2, "col": c})
            rounds.append(games)
        # Sub-bracket labels (BADEN / WILSON) only matter when a table holds more than one.
        # Games in or left of the label column belong to the nearest label.
        if len(labels) > 1:
            last = max(l[1] for l in labels)
            for games in rounds:
                for g in games:
                    if g["col"] <= last:
                        g["grp"] = min(labels, key=lambda l: abs(l[0] - g["mid"]))[2]
        _fill_bracket(rounds, champ)
        n = len(rounds)
        out.append({"name": name, "note": note, "champ": champ, "rounds": [
            {"name": "Championship" if i == n - 1 and n > 1 else f"Round {i + 1}",
             "games": [{k: v for k, v in g.items() if k not in ("mid", "col")} for g in games]}
            for i, games in enumerate(rounds)]})
    return {"brackets": out, "note": ""}


def _fill_bracket(rounds, champ):
    """Fill missing seeds/full names from earlier rounds and work out each winner."""
    known = [t for games in rounds for g in games for t in (g["t1"], g["t2"]) if t and t["s"]]
    for games in rounds:
        for g in games:
            for t in (g["t1"], g["t2"]):
                if t and t["s"] is None:
                    m = next((k for k in known if same_team(k["n"], t["n"])), None)
                    if m:
                        t["s"], t["n"] = m["s"], max(m["n"], t["n"], key=len)
    if champ and champ["s"] is None:
        m = next((k for k in known if same_team(k["n"], champ["n"])), None)
        if m:
            champ["s"], champ["n"] = m["s"], max(m["n"], champ["n"], key=len)
    for i, games in enumerate(rounds):
        nxt = [t for g in rounds[i + 1] for t in (g["t1"], g["t2"]) if t] if i + 1 < len(rounds) else ([champ] if champ else [])
        for g in games:
            a, b, w = g["t1"], g["t2"], 0
            if a and b and a["sc"] is not None and b["sc"] is not None and a["sc"] != b["sc"]:
                w = 1 if a["sc"] > b["sc"] else 2
            elif a and b:
                ina = any(same_team(a["n"], t["n"]) for t in nxt)
                inb = any(same_team(b["n"], t["n"]) for t in nxt)
                w = 1 if ina and not inb else 2 if inb and not ina else 0
            g["win"] = w


def link_playoffs(po, sched):
    """Attach date/time/field from the Playoff Schedule to bracket games, and scores
    from the bracket to schedule games. Schedule "Week N" = bracket round N."""
    for g in sched:
        g.pop("p", None)
    for b in po["brackets"]:
        for i, rnd in enumerate(b["rounds"]):
            for bg in rnd["games"]:
                a, h = bg["t1"], bg["t2"]
                if not a or not h:
                    continue
                for g in sched:
                    if re.sub(r"\D", "", g["w"]) not in ("", str(i + 1)):
                        continue
                    if same_team(g["a"], a["n"]) and same_team(g["h"], h["n"]):
                        aw, hm = a, h
                    elif same_team(g["a"], h["n"]) and same_team(g["h"], a["n"]):
                        aw, hm = h, a
                    else:
                        continue
                    bg.update(d=g["d"], t=g["t"], f=g["f"], home=2 if hm is h else 1)
                    g.update(r=rnd["name"], br=b["name"], grp=bg.get("grp", ""), sa=aw["s"], sh=hm["s"])
                    if aw["sc"] is not None and hm["sc"] is not None:
                        g["as"], g["hs"] = aw["sc"], hm["sc"]
                        g["ot"] = g["ot"] or bool(aw["ot"] or hm["ot"])
                    break
    # The bracket page can lag the schedule: a team listed in next week's playoff games won.
    for b in po["brackets"]:
        for i, rnd in enumerate(b["rounds"]):
            nxt = [n for g in sched if re.sub(r"\D", "", g["w"]) == str(i + 2) for n in (g["a"], g["h"])]
            for bg in rnd["games"]:
                a, h = bg["t1"], bg["t2"]
                if bg["win"] or not a or not h or not nxt:
                    continue
                ina, inh = (any(same_team(t["n"], n) for n in nxt) for t in (a, h))
                bg["win"] = 1 if ina and not inh else 2 if inh and not ina else 0
    po["games"] = sched
    return po


def strip_groups(games):
    """Split the PAC10 Baden/Wilson suffix off team names; return {team: group}."""
    groups = {}
    for g in games:
        for k in ("a", "h"):
            m = GROUP_SUFFIX.search(g[k])
            if m:
                g[k] = g[k][: m.start()].strip()
                groups[g[k]] = m.group(1)
    return groups


def scrape(div):
    L = division_links(div["url"])
    results = parse_games(get(L["scores"])) if "scores" in L else []
    sched = parse_games(get(L["schedule"])) if "schedule" in L else []
    fields = {(g["d"], g["a"], g["h"]): g["f"] for g in sched}
    for g in results:
        g["f"] = fields.get((g["d"], g["a"], g["h"]), g["f"])
    # The schedule page can be stale, so only use it for weeks the results page lacks.
    have = {g["w"] for g in results}
    games = merge(results + [g for g in sched if g["w"] not in have])
    po_games = parse_games(get(L["playoffs"])) if "playoffs" in L else []
    groups = strip_groups(games)
    groups.update(strip_groups(po_games))
    po = parse_bracket(fetch(L["bracket"])) if "bracket" in L else {"brackets": [], "note": ""}
    po = link_playoffs(po, po_games)
    tiers = parse_standings(get(L["standings"])) if "standings" in L else []
    out = {"id": div["id"], "name": div["name"], "games": games, "tiers": tiers}
    if groups:
        out["groups"] = groups
    if po["brackets"] or po["games"] or po["note"]:
        out["playoffs"] = po
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=".", help="output folder (default: current folder)")
    ap.add_argument("--only", nargs="*", help="division ids to refresh, e.g. big10-varsity")
    ap.add_argument("--list", action="store_true", help="list divisions and exit")
    args = ap.parse_args()

    divs = discover()
    if args.list:
        for d in divs:
            print(d["id"], "-", d["name"])
        return
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    jp = out / "tcyfl-data.json"
    old = {}
    if jp.exists():
        try:
            old = {d["id"]: d for d in json.loads(jp.read_text())["divisions"]}
        except Exception:
            pass
    result = {}
    for d in divs:
        if args.only and d["id"] not in args.only:
            if d["id"] in old:
                result[d["id"]] = old[d["id"]]
            continue
        try:
            r = scrape(d)
            if not r["games"] and not r["tiers"] and d["id"] in old:
                raise ValueError("page returned no data")
            result[d["id"]] = r
            print(f"ok    {d['id']}: {len(r['games'])} games, {sum(len(t['rows']) for t in r['tiers'])} standings rows")
        except Exception as e:
            print(f"WARN  {d['id']}: {e}" + (" (kept previous data)" if d["id"] in old else ""), file=sys.stderr)
            if d["id"] in old:
                result[d["id"]] = old[d["id"]]
    data = {"generated": datetime.datetime.now().astimezone().isoformat(timespec="minutes"), "divisions": list(result.values())}
    jp.write_text(json.dumps(data, separators=(",", ":")))
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    (out / "tcyfl.html").write_text(TEMPLATE.replace("__DATA__", blob), encoding="utf-8")
    print(f"wrote {out / 'tcyfl.html'}")


TEMPLATE = r'''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><title>TCYFL Game Center</title>
<style>
:root{--bg:#f4f6f9;--card:#fff;--tx:#141a23;--mu:#667085;--ln:#e3e7ee;--ac:#0b5fff;--w:#10795a;--l:#c0392b;--hd:#0b1f4d;color-scheme:light}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0e1116;--card:#171c24;--tx:#eef1f5;--mu:#9aa4b2;--ln:#273040;--ac:#7aa7ff;--w:#3ccf91;--l:#ff7b6b;--hd:#050a18;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0e1116;--card:#171c24;--tx:#eef1f5;--mu:#9aa4b2;--ln:#273040;--ac:#7aa7ff;--w:#3ccf91;--l:#ff7b6b;--hd:#050a18;color-scheme:dark}
*{box-sizing:border-box}html,body{margin:0}body{background:var(--bg);color:var(--tx);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding-bottom:env(safe-area-inset-bottom,0px)}
a{color:inherit;text-decoration:none}a:hover{color:var(--ac)}
header{background:var(--hd);color:#fff;padding:calc(14px + env(safe-area-inset-top,0px)) 16px 0}
.in,main,footer{max-width:960px;margin:0 auto}
h1{font-size:19px;margin:0 0 8px}h1 span{color:#ff9b4a}
select{font:inherit;padding:6px 8px;border-radius:8px;border:1px solid #3a4a75;background:#16275a;color:#fff;max-width:100%}
nav{display:flex;gap:4px;margin-top:10px}nav a{padding:9px 14px;border-radius:8px 8px 0 0;color:#c8d3f0;font-weight:600}nav a.on{background:var(--bg);color:var(--tx)}
main{padding:16px}
.chips{display:flex;gap:6px;overflow-x:auto;padding-bottom:8px;margin-bottom:8px}
.chips button{flex:none;border:1px solid var(--ln);background:var(--card);color:var(--tx);border-radius:999px;padding:6px 13px;font:inherit;cursor:pointer}
.chips button.on{background:var(--ac);border-color:var(--ac);color:#fff}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
.g,.tc,.box{background:var(--card);border:1px solid var(--ln);border-radius:12px;padding:11px 13px}
.d,.mu{color:var(--mu);font-size:12.5px}
.r{display:flex;justify-content:space-between;padding:3px 0}.r.win{font-weight:700}.r:not(.win){color:var(--mu)}
.box{padding:0;margin-bottom:14px;overflow-x:auto}
table{width:100%;border-collapse:collapse;min-width:420px}th,td{padding:8px 10px;text-align:right;border-top:1px solid var(--ln)}
th:first-child,td:first-child{text-align:left}th{font-size:12px;color:var(--mu);border:0;font-weight:600}
h2{margin:0 0 2px;font-size:24px}h3{margin:18px 0 8px;font-size:15px}.tier{padding:10px 12px 4px;font-weight:700}
.kp{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0}.kp div{background:var(--card);border:1px solid var(--ln);border-radius:12px;padding:10px;text-align:center;font-size:12px;color:var(--mu)}.kp b{display:block;font-size:20px;color:var(--tx)}
.form i{display:inline-block;width:24px;height:24px;line-height:24px;text-align:center;border-radius:6px;font-style:normal;font-size:12px;font-weight:700;margin-right:4px;color:#fff;background:var(--mu)}
.form i.W{background:var(--w)}.form i.L{background:var(--l)}
.tg{display:grid;grid-template-columns:120px 1fr auto;gap:8px;padding:9px 12px;background:var(--card);border:1px solid var(--ln);border-radius:10px;margin-bottom:6px}
.tg .W{color:var(--w);font-weight:600}.tg .L{color:var(--l);font-weight:600}
.tc{display:flex;justify-content:space-between}.back{color:var(--ac);font-weight:600}
footer{padding:8px 16px 28px;color:var(--mu);font-size:12px}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:700;background:var(--w);color:#fff}
.note{margin:0 0 12px;color:var(--mu);font-size:13px}.pre{white-space:pre-line}
.sd{display:inline-block;min-width:20px;margin-right:6px;padding:0 4px;border-radius:5px;background:var(--ln);color:var(--tx);font-style:normal;font-size:11px;font-weight:700;text-align:center;line-height:18px}
.r span{display:flex;align-items:center;min-width:0}
.bk{display:flex;gap:12px;overflow-x:auto;padding-bottom:6px;margin-bottom:6px}
.bk .col{flex:1 0 220px;display:flex;flex-direction:column;gap:8px}.bk .gs{flex:1;display:flex;flex-direction:column;justify-content:space-around;gap:10px}
.bk .rh{font-size:12px;font-weight:700;color:var(--mu);text-transform:uppercase;letter-spacing:.04em}
.bk .g.pend{border-style:dashed}.tag{color:var(--ac);font-weight:700}
.champ{border:2px solid var(--w);border-radius:12px;padding:10px 13px;background:var(--card);font-weight:700}
.champ small{display:block;color:var(--w);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
.brk{background:var(--card);border:1px solid var(--ln);border-radius:12px;padding:12px;margin-bottom:16px}.brk h3{margin:0 0 4px}
@media(max-width:520px){.kp{grid-template-columns:repeat(2,1fr)}.tg{grid-template-columns:1fr auto}.tg .mu:first-child{grid-column:1/-1}.bk .col{flex-basis:200px}}
</style></head><body>
<header><div class="in"><h1>TCYFL <span>Game Center</span></h1><select id="dv" aria-label="Division"></select>
<nav id="nv"><a data-v="scores">Scores</a><a data-v="standings">Standings</a><a data-v="teams">Teams</a><a data-v="playoffs">Playoffs</a></nav></div></header>
<main id="m"></main>
<footer>Unofficial. Data pulled from tcyfl.net on <span id="gen"></span>. Scores are as posted by the league.</footer>
<script>
const DATA=__DATA__;
const D=DATA.divisions,$m=document.getElementById("m"),$dv=document.getElementById("dv");
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const slug=s=>s.toLowerCase().replace(/[^a-z0-9]+/g,"_").replace(/^_|_$/g,"");
const isTeam=n=>n&&n!="TBD"&&n.toUpperCase()!="BYE";
D.forEach(d=>{d.S={};d.B={};
 const g=n=>d.S[n]||(d.B[slug(n)]=n,d.S[n]={n,w:0,l:0,t:0,pf:0,pa:0,f:[]});
 d.games.forEach(x=>{[x.a,x.h].filter(isTeam).forEach(g);if(x.as==null||x.hs==null||!isTeam(x.a)||!isTeam(x.h))return;
  [[x.a,x.as,x.hs],[x.h,x.hs,x.as]].forEach(([n,f,o])=>{const s=d.S[n],r=f>o?"W":f<o?"L":"T";s.pf+=f;s.pa+=o;s[r.toLowerCase()]++;s.f.push(r)})});
 if(!d.tiers.length)d.tiers=[{name:"Standings (calculated from scores)",cols:["W","L"],rows:Object.values(d.S).sort((a,b)=>b.w-a.w||a.l-b.l||(b.pf-b.pa)-(a.pf-a.pa)).map(s=>({t:s.n,v:[s.w,s.l]}))}];
 d.tiers.forEach(t=>t.rows.forEach(r=>{const s=g(r.t);s.tier=t.name;s.st=r.v;s.cols=t.cols}));
 d.weeks=[...new Set(d.games.map(x=>x.w))];
 d.cw=([...d.games].reverse().find(x=>x.as!=null)||{}).w||d.weeks[0];});
$dv.innerHTML=D.map(d=>`<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("");
$dv.onchange=()=>{location.hash=`d.${$dv.value}.scores`};
document.getElementById("gen").textContent=DATA.generated.replace("T"," ").slice(0,16);
const wk={};let cur=null;
const rec=s=>s.w+"-"+s.l+(s.t?"-"+s.t:"");
const tl=(d,n)=>!isTeam(n)||!d.S[n]?esc(n):`<a href="#d.${d.id}.team.${slug(n)}">${esc(n)}</a>`;
const none=m=>`<p class="mu">${m}</p>`;
const sd=s=>s?`<i class="sd">${s}</i>`:"";
function card(d,g){const aw=g.as!=null&&g.as>g.hs,hw=g.hs!=null&&g.hs>g.as,top=[g.br,g.grp,g.r].filter(Boolean).join(" · ");
 return `<div class="g">${top?`<div class="d tag">${esc(top)}</div>`:""}<div class="d">${esc(g.d)}${g.t?" · "+esc(g.t):""}${g.ot?" · OT":""}</div><div class="r${aw?" win":""}"><span>${sd(g.sa)}${tl(d,g.a)}</span><b>${g.as??"–"}</b></div><div class="r${hw?" win":""}"><span>${sd(g.sh)}${tl(d,g.h)}</span><b>${g.hs??"–"}</b></div>${g.f?`<div class="d">${esc(g.f)}</div>`:""}</div>`}
function scores(d){if(!d.games.length)return none("No games posted yet.");const w=wk[d.id]||d.cw;
 return `<div class="chips">${d.weeks.map(x=>`<button class="${x==w?"on":""}" data-w="${esc(x)}">${esc(x)}</button>`).join("")}</div><div class="grid">${d.games.filter(g=>g.w==w).map(g=>card(d,g)).join("")}</div>`}
function standings(d){return d.tiers.map(t=>`<div class="box"><div class="tier">${esc(t.name)}</div><table><tr><th>Team</th>${t.cols.map(c=>`<th>${esc(c)}</th>`).join("")}<th>PF</th><th>PA</th></tr>${t.rows.map(r=>{const s=d.S[r.t];return `<tr><td>${tl(d,r.t)}</td>${r.v.map(v=>`<td>${esc(v)}</td>`).join("")}<td>${s.pf}</td><td>${s.pa}</td></tr>`}).join("")}</table></div>`).join("")}
function teams(d){const grp={};Object.values(d.S).forEach(s=>(grp[s.tier||"Teams"]=grp[s.tier||"Teams"]||[]).push(s));
 return Object.entries(grp).map(([k,a])=>`<h3>${esc(k)}</h3><div class="grid">${a.map(s=>`<a class="tc" href="#d.${d.id}.team.${slug(s.n)}"><b>${esc(s.n)}</b><span class="mu">${rec(s)}</span></a>`).join("")}</div>`).join("")}
const poOf=d=>d.playoffs&&(d.playoffs.brackets.length||d.playoffs.games.length||d.playoffs.note)?d.playoffs:null;
function tgRow(d,g,n){const hm=g.h==n,o=hm?g.a:g.h,f=hm?g.hs:g.as,x=hm?g.as:g.hs,ok=f!=null&&x!=null,lab=g.r||g.w;
 return `<div class="tg"><span class="mu">${esc(lab)} · ${esc(g.d)}${g.t?" · "+esc(g.t):""}</span><span>${hm?"vs":"at"} ${tl(d,o)}${g.f?` <span class="mu">· ${esc(g.f)}</span>`:""}</span><span class="${ok?(f>x?"W":f<x?"L":""):"mu"}">${ok?(f>x?"W ":f<x?"L ":"T ")+f+"-"+x+(g.ot?" OT":""):"Pending"}</span></div>`}
function team(d,n){const s=d.S[n],df=s.pf-s.pa,po=poOf(d),pg=po?po.games.filter(g=>g.a==n||g.h==n):[];
 return `<a class="back" href="#d.${d.id}.standings">← ${esc(d.name)} standings</a><h2>${esc(n)}</h2><div class="mu">${esc(s.tier||d.name)}${s.st?" · "+s.cols.map((c,i)=>esc(c)+" "+esc(s.st[i])).join(" · "):""}</div>
<div class="kp"><div><b>${rec(s)}</b>Record</div><div><b>${s.pf}</b>Points for</div><div><b>${s.pa}</b>Points against</div><div><b>${df>0?"+":""}${df}</b>Differential</div></div>
<div class="form">${s.f.map(x=>`<i class="${x}">${x}</i>`).join("")}</div>`+
 (pg.length?`<h3>Playoffs</h3>`+pg.map(g=>tgRow(d,g,n)).join(""):"")+
 `<h3>Regular season</h3>`+d.games.filter(g=>g.a==n||g.h==n).map(g=>tgRow(d,g,n)).join("")}
/* ---- Playoffs: the league's Seeds & Scores bracket joined with its Playoff Schedule (see link_playoffs in the scraper). ---- */
function slot(d,t,won,lost){if(!t)return `<div class="r"><span class="mu">TBD</span><b></b></div>`;
 return `<div class="r${won?" win":""}"><span>${sd(t.s)}${tl(d,t.n)}</span><b>${t.sc??(won?"✓":"")}${t.ot&&t.sc!=null?` <small class="mu">${esc(t.ot)}</small>`:""}</b></div>`}
function bgame(d,g){const when=g.d?`${esc(g.d)}${g.t?" · "+esc(g.t):""}${g.f?" · "+esc(g.f):""}`:"Date TBD",adv=g.win&&(!g.t1||g.t1.sc==null);
 return `<div class="g${g.win?"":" pend"}">${g.grp?`<div class="d tag">${esc(g.grp)}</div>`:""}${slot(d,g.t1,g.win==1)}${slot(d,g.t2,g.win==2)}<div class="d">${when}${adv?" · advanced (score not posted)":""}</div></div>`}
function bracket(d,b){
 return `<div class="brk"><h3>${esc(b.name||"Bracket")}</h3>${b.note?`<p class="note">${esc(b.note)}</p>`:""}<div class="bk">`+
  b.rounds.map(r=>`<div class="col"><div class="rh">${esc(r.name)}</div><div class="gs">${r.games.map(g=>bgame(d,g)).join("")}</div></div>`).join("")+
  (b.champ?`<div class="col"><div class="rh">Champion</div><div class="gs"><div class="champ"><small>Champion</small>${sd(b.champ.s)}${tl(d,b.champ.n)}</div></div></div>`:"")+`</div></div>`}
function playoffs(d){const po=poOf(d);if(!po)return none("No playoff information posted for this division.");
 const wks=[...new Set(po.games.map(g=>g.w))];
 return (po.note?`<div class="brk"><p class="note pre">${esc(po.note)}</p></div>`:"")+
  (po.brackets.length?`<p class="note">Seeds and scores from the league's bracket; dates, times and fields from its playoff schedule.</p>`+po.brackets.map(b=>bracket(d,b)).join(""):"")+
  (po.games.length?`<h3>Playoff schedule</h3>`+wks.map(w=>{const gs=po.games.filter(g=>g.w==w),r=gs.find(g=>g.r);
   return `<h3 class="mu">${esc(w)}${r?" · "+esc(r.r):""}</h3><div class="grid">${gs.map(g=>card(d,g)).join("")}</div>`}).join(""):"")}
function go(){const p=(location.hash.slice(1)||"").split(".");let d=p[0]=="d"&&D.find(x=>x.id==p[1]);
 if(!d)d=D[0];if(!d){$m.textContent="No data yet. Run tcyfl_scraper.py.";return}
 cur=d;let v=p[2]||"scores";if(v=="playoffs"&&!poOf(d))v="scores";$dv.value=d.id;
 document.querySelectorAll("#nv a").forEach(a=>{a.href=`#d.${d.id}.${a.dataset.v}`;a.style.display=a.dataset.v=="playoffs"&&!poOf(d)?"none":"";a.classList.toggle("on",a.dataset.v==(v=="team"?"standings":v))});
 const n=v=="team"&&d.B[p[3]];
 $m.innerHTML=n?team(d,n):v=="standings"?standings(d):v=="teams"?teams(d):v=="playoffs"?playoffs(d):scores(d);if(v!="scores")scrollTo(0,0)}
$m.addEventListener("click",e=>{const b=e.target.closest("[data-w]");if(b&&cur){wk[cur.id]=b.dataset.w;go()}});
addEventListener("hashchange",go);go();
</script></body></html>
'''

if __name__ == "__main__":
    main()
