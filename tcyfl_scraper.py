#!/usr/bin/env python3
"""TCYFL Game Center builder.

Scrapes every division on tcyfl.net (schedules, scores, standings, playoffs)
and regenerates two files in the output folder:
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
import argparse, datetime, json, re, sys, time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.tcyfl.net/"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "Mozilla/5.0 (compatible; tcyfl-game-center/1.0)"


def get(url, tries=3):
    for i in range(tries):
        try:
            r = SESSION.get(url, timeout=30)
            r.raise_for_status()
            time.sleep(0.7)  # be polite to the league server
            return BeautifulSoup(r.text, "html.parser")
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


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
            found[f"{co}-{dv}"] = {"id": f"{co}-{dv}", "name": f"{co.capitalize()} {dv.capitalize()}", "url": urljoin(BASE, h)}
        elif "myschedules7man.php" in h:
            found["7man"] = {"id": "7man", "name": "7-Man", "url": urljoin(BASE, h)}
    return list(found.values())


def division_links(url):
    """Read the tab links (Schedule / Scores / Standings / Playoff Schedule)."""
    soup = get(url)
    out = {}
    pats = (("schedule", r"^schedule$"), ("scores", r"^scores$"), ("standings", r"^standings$"), ("playoffs", r"^playoff schedule$"))
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


def scrape(div):
    L = division_links(div["url"])
    results = parse_games(get(L["scores"])) if "scores" in L else []
    sched = parse_games(get(L["schedule"])) if "schedule" in L else []
    fields = {(g["d"], g["a"], g["h"]): g["f"] for g in sched}
    for g in results:
        g["f"] = fields.get((g["d"], g["a"], g["h"]), g["f"])
    # The schedule page can be stale, so only use it for weeks the results page lacks.
    have = {g["w"] for g in results}
    games = results + [g for g in sched if g["w"] not in have]
    if "playoffs" in L:
        for g in parse_games(get(L["playoffs"])):
            g["p"] = 1
            games.append(g)
    tiers = parse_standings(get(L["standings"])) if "standings" in L else []
    return {"id": div["id"], "name": div["name"], "games": merge(games), "tiers": tiers}


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
tr.cut td{border-top:2px solid var(--ac)}tr.out td{color:var(--mu)}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:700;background:var(--w);color:#fff}
.note{margin:0 0 12px;color:var(--mu);font-size:13px}
@media(max-width:520px){.kp{grid-template-columns:repeat(2,1fr)}.tg{grid-template-columns:1fr auto}.tg .mu:first-child{grid-column:1/-1}}
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
D.forEach(d=>{d.S={};d.B={};
 const g=n=>d.S[n]||(d.B[slug(n)]=n,d.S[n]={n,w:0,l:0,t:0,pf:0,pa:0,f:[]});
 d.games.forEach(x=>{g(x.a);g(x.h);if(x.as==null||x.hs==null)return;
  [[x.a,x.as,x.hs],[x.h,x.hs,x.as]].forEach(([n,f,o])=>{const s=d.S[n],r=f>o?"W":f<o?"L":"T";s.pf+=f;s.pa+=o;s[r.toLowerCase()]++;s.f.push(r)})});
 if(!d.tiers.length)d.tiers=[{name:"Standings (calculated from scores)",cols:["W","L"],rows:Object.values(d.S).filter(s=>s.n!="TBD").sort((a,b)=>b.w-a.w||a.l-b.l||(b.pf-b.pa)-(a.pf-a.pa)).map(s=>({t:s.n,v:[s.w,s.l]}))}];
 d.tiers.forEach(t=>t.rows.forEach(r=>{const s=g(r.t);s.tier=t.name;s.st=r.v;s.cols=t.cols}));
 d.weeks=[...new Set(d.games.map(x=>x.w))];
 d.cw=([...d.games].reverse().find(x=>x.as!=null)||{}).w||d.weeks[0];});
$dv.innerHTML=D.map(d=>`<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("");
$dv.onchange=()=>{location.hash=`d.${$dv.value}.scores`};
document.getElementById("gen").textContent=DATA.generated.replace("T"," ").slice(0,16);
const wk={};let cur=null;
const rec=s=>s.w+"-"+s.l+(s.t?"-"+s.t:"");
const tl=(d,n)=>n=="TBD"||!d.S[n]?esc(n):`<a href="#d.${d.id}.team.${slug(n)}">${esc(n)}</a>`;
const none=m=>`<p class="mu">${m}</p>`;
function card(d,g){const aw=g.as!=null&&g.as>g.hs,hw=g.hs!=null&&g.hs>g.as;
 return `<div class="g"><div class="d">${esc(g.d)}${g.t?" · "+esc(g.t):""}${g.ot?" · OT":""}${g.p?" · Playoffs":""}</div><div class="r${aw?" win":""}"><span>${tl(d,g.a)}</span><b>${g.as??"–"}</b></div><div class="r${hw?" win":""}"><span>${tl(d,g.h)}</span><b>${g.hs??"–"}</b></div>${g.f?`<div class="d">${esc(g.f)}</div>`:""}</div>`}
function scores(d){if(!d.games.length)return none("No games posted yet.");const w=wk[d.id]||d.cw;
 return `<div class="chips">${d.weeks.map(x=>`<button class="${x==w?"on":""}" data-w="${esc(x)}">${esc(x)}</button>`).join("")}</div><div class="grid">${d.games.filter(g=>g.w==w).map(g=>card(d,g)).join("")}</div>`}
function standings(d){return d.tiers.map(t=>`<div class="box"><div class="tier">${esc(t.name)}</div><table><tr><th>Team</th>${t.cols.map(c=>`<th>${esc(c)}</th>`).join("")}<th>PF</th><th>PA</th></tr>${t.rows.map(r=>{const s=d.S[r.t];return `<tr><td>${tl(d,r.t)}</td>${r.v.map(v=>`<td>${esc(v)}</td>`).join("")}<td>${s.pf}</td><td>${s.pa}</td></tr>`}).join("")}</table></div>`).join("")}
function teams(d){const grp={};Object.values(d.S).filter(s=>s.n!="TBD").forEach(s=>(grp[s.tier||"Teams"]=grp[s.tier||"Teams"]||[]).push(s));
 return Object.entries(grp).map(([k,a])=>`<h3>${esc(k)}</h3><div class="grid">${a.map(s=>`<a class="tc" href="#d.${d.id}.team.${slug(s.n)}"><b>${esc(s.n)}</b><span class="mu">${rec(s)}</span></a>`).join("")}</div>`).join("")}
function team(d,n){const s=d.S[n],df=s.pf-s.pa;
 return `<a class="back" href="#d.${d.id}.standings">← ${esc(d.name)} standings</a><h2>${esc(n)}</h2><div class="mu">${esc(s.tier||d.name)}${s.st?" · "+s.cols.map((c,i)=>esc(c)+" "+esc(s.st[i])).join(" · "):""}</div>
<div class="kp"><div><b>${rec(s)}</b>Record</div><div><b>${s.pf}</b>Points for</div><div><b>${s.pa}</b>Points against</div><div><b>${df>0?"+":""}${df}</b>Differential</div></div>
<div class="form">${s.f.map(x=>`<i class="${x}">${x}</i>`).join("")}</div><h3>Schedule and results</h3>`+
 d.games.filter(g=>g.a==n||g.h==n).map(g=>{const hm=g.h==n,o=hm?g.a:g.h,f=hm?g.hs:g.as,x=hm?g.as:g.hs,ok=f!=null&&x!=null;
  return `<div class="tg"><span class="mu">${esc(g.w)} · ${esc(g.d)}${g.t?" · "+esc(g.t):""}</span><span>${hm?"vs":"at"} ${tl(d,o)}${g.f?` <span class="mu">· ${esc(g.f)}</span>`:""}</span><span class="${ok?(f>x?"W":f<x?"L":""):"mu"}">${ok?(f>x?"W ":f<x?"L ":"T ")+f+"-"+x+(g.ot?" OT":""):"Pending"}</span></div>`}).join("")}
/* ---- Playoffs: edit PO to change qualifier rules. Mac10 divisions never get a playoffs tab. ---- */
const PO={
 "big10":{},
 "pac10-middleweight":{per:4},
 "pac10-featherweight":{per:4},
 "pac10-bantam":{top:8,pairs:[[1,5],[2,6],[3,7],[4,8]]},
 "pac10-flyweight":{none:"Flyweight finishes with the Fly Bowl, so there is no playoff bracket."}
};
const poCfg=d=>d.id.startsWith("mac10")?null:PO[d.id]||PO[d.id.split("-")[0]]||{};
const pct=s=>{const n=s.w+s.l+s.t;return n?(s.w+s.t/2)/n:0};
function recs(d){const R={},r=n=>R[n]||(R[n]={w:0,l:0,t:0,opp:[],h:{}});
 d.games.filter(g=>!g.p&&g.as!=null&&g.hs!=null&&g.a!="TBD"&&g.h!="TBD").forEach(g=>{
  [[g.a,g.h,g.as,g.hs],[g.h,g.a,g.hs,g.as]].forEach(([n,o,f,x])=>{const s=r(n);r(o);const k=f>x?"w":f<x?"l":"t";
   s[k]++;s.opp.push(o);const h=s.h[o]||(s.h[o]={w:0});if(k=="w")h.w++}) });
 return R}
/* Order: winning % -> head to head (only when exactly 2 teams are tied) -> opponent wins -> playoff points */
function order(d,names){const R=recs(d);
 const info=names.map(n=>{const s=R[n]||{w:0,l:0,t:0,opp:[],h:{}},t=d.S[n],pi=t.cols?t.cols.findIndex(c=>/^pts/i.test(c)):-1;
  return {n,s,p:pct(s),ow:s.opp.reduce((a,o)=>a+(R[o]?R[o].w:0),0),pts:pi>=0?(+t.st[pi]||0):0,why:""}});
 info.sort((a,b)=>b.p-a.p);
 const lab=(a,b)=>a.ow!=b.ow?"Opponent wins":a.pts!=b.pts?"Playoff points":"Unresolved tie",out=[];
 for(let i=0;i<info.length;){let j=i;while(j<info.length&&Math.abs(info[j].p-info[i].p)<1e-9)j++;
  let grp=info.slice(i,j),done=false;
  if(grp.length==2){const[a,b]=grp,ha=(a.s.h[b.n]||{w:0}).w,hb=(b.s.h[a.n]||{w:0}).w;
   if(ha!=hb){grp=ha>hb?[a,b]:[b,a];grp.forEach(x=>x.why="Head to head");done=true}}
  if(grp.length>1&&!done){grp.sort((a,b)=>b.ow-a.ow||b.pts-a.pts);
   grp.forEach((x,k)=>{x.why=lab(x,grp[k+1]||grp[k-1])})}
  out.push(...grp);i=j}
 return out}
function poTable(d,rows,q){const hasPts=rows.some(x=>x.pts);
 return `<div class="box"><table><tr><th>#</th><th>Team</th><th>Record</th><th>Win %</th><th>Opp wins</th><th>Pts</th><th>Tie-break</th>${q?"<th></th>":""}</tr>`+
 rows.map((x,i)=>`<tr class="${q&&i==q?"cut":""}${q&&i>=q?" out":""}"><td>${i+1}</td><td>${tl(d,x.n)}</td><td>${x.s.w}-${x.s.l}${x.s.t?"-"+x.s.t:""}</td><td>${x.p.toFixed(3).replace(/^0/,"")}</td><td>${x.ow}</td><td>${hasPts?x.pts:"–"}</td><td class="mu">${esc(x.why||"")}</td>${q?`<td>${i<q?'<span class="badge">IN</span>':""}</td>`:""}</tr>`).join("")+`</table></div>`}
function poGames(d){const g=d.games.filter(x=>x.p);if(!g.length)return "";
 return `<h3>Playoff schedule and results</h3><div class="grid">${g.map(x=>card(d,x)).join("")}</div>`}
function playoffs(d){const c=poCfg(d);if(c.none)return none(c.none)+poGames(d);
 const all=Object.keys(d.S).filter(n=>n!="TBD"),note=`<p class="note">Ranked by winning %, then head to head (only when exactly two teams are tied), then opponent wins, then playoff points (the league's Pts column). Regular-season games only.</p>`;
 if(c.per){const groups=d.tiers.length>1?d.tiers.map(t=>({name:t.name,names:t.rows.map(r=>r.t)})):[{name:"Overall",names:all}];
  return note+`<p class="note">Top ${c.per} in each conference qualify; each conference has its own championship.</p>`+groups.map(g=>`<h3>${esc(g.name)}</h3>`+poTable(d,order(d,g.names),c.per)).join("")+poGames(d)}
 const rows=order(d,all);
 if(c.top)return note+`<p class="note">Top ${c.top} overall qualify. Round 1: ${c.pairs.map(([a,b])=>`${a} vs ${b}`).join(", ")}; round 2 puts the lowest remaining seed against the highest.</p>`+poTable(d,rows,c.top)+
  `<h3>Round 1 matchups</h3><div class="grid">${c.pairs.map(([a,b])=>`<div class="g"><div class="d">Seeds ${a} vs ${b}</div><div class="r"><span>${rows[a-1]?tl(d,rows[a-1].n):"TBD"}</span><b>#${a}</b></div><div class="r"><span>${rows[b-1]?tl(d,rows[b-1].n):"TBD"}</span><b>#${b}</b></div></div>`).join("")}</div>`+poGames(d);
 return note+`<p class="note">Qualifier count and bracket classes follow the league's playoff sheets (Downloads on tcyfl.net); this is the full ranking.</p>`+poTable(d,rows,0)+poGames(d)}
function go(){const p=(location.hash.slice(1)||"").split(".");let d=p[0]=="d"&&D.find(x=>x.id==p[1]);
 if(!d)d=D[0];if(!d){$m.textContent="No data yet. Run tcyfl_scraper.py.";return}
 cur=d;let v=p[2]||"scores";if(v=="playoffs"&&!poCfg(d))v="scores";$dv.value=d.id;
 document.querySelectorAll("#nv a").forEach(a=>{a.href=`#d.${d.id}.${a.dataset.v}`;a.style.display=a.dataset.v=="playoffs"&&!poCfg(d)?"none":"";a.classList.toggle("on",a.dataset.v==(v=="team"?"standings":v))});
 const n=v=="team"&&d.B[p[3]];
 $m.innerHTML=n?team(d,n):v=="standings"?standings(d):v=="teams"?teams(d):v=="playoffs"?playoffs(d):scores(d);if(v!="scores")scrollTo(0,0)}
$m.addEventListener("click",e=>{const b=e.target.closest("[data-w]");if(b&&cur){wk[cur.id]=b.dataset.w;go()}});
addEventListener("hashchange",go);go();
</script></body></html>
'''

if __name__ == "__main__":
    main()
