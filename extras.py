"""Extra pages for IPO Shop, built by pages.write_all():
  - dashboard(ds, seg)        summary cards + monthly table for the SME and Mainboard pages
  - /ipo-subscription-status/ every open or recently closed IPO with all categories and the time of each figure
  - /gmp-accuracy/            how the last GMP before listing compared with the real first-day close
  - /compare-ipos/            pick 2 or 3 IPOs and read them side by side
  - /learn/ and its articles  plain-English guides
Also update_final(): remembers the last GMP seen before each IPO lists, so the accuracy page can be scored honestly.
"""
import json
import pathlib
import statistics
import datetime as dt

import pages as P
from pages import esc, rs, inr, dl, link, SITE

LAST_REVIEWED = "October 2026"

CSS_X = """<style>
.kp{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:12px 0}
.kp div{background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.kp b{display:block;font-size:24px;line-height:1.2}.kp span{font-size:13px;color:var(--muted)}
.pos{color:#0a7a3f}.neg{color:#b42318}
.cmp td:first-child,.cmp th:first-child{white-space:nowrap;color:var(--muted);font-weight:600}
.cmpbar{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}.cmpbar select{flex:1 1 200px;min-width:0;padding:10px;border:1px solid var(--line);border-radius:10px;font:inherit;background:#fff}
.art h2{margin-top:26px}.art li{margin:4px 0}.lh{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px;margin:14px 0}
.lh a{display:block;background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 16px;text-decoration:none;color:var(--ink)}.lh a b{display:block;color:var(--blue);margin-bottom:4px}.lh a span{font-size:14px;color:var(--muted)}
.mini{font-size:14px;color:var(--muted)}
@media(max-width:640px){.kp{grid-template-columns:repeat(2,1fr)}}
</style>"""


# ------------------------------------------------------------------ shared page shell
def shell(slug, title, h1, desc, body, faqs=(), ld_type="WebPage", crumbs=None, tick=False, script="", extra_ld=None):
    url = f"{SITE}/{slug}/"
    trail = crumbs or [("IPO Shop", SITE + "/"), (h1, url)]
    graph = [{"@type": "BreadcrumbList", "itemListElement": [{"@type": "ListItem", "position": k + 1, "name": n, "item": u} for k, (n, u) in enumerate(trail)]},
             {"@type": ld_type, "headline": h1, "name": h1, "description": desc, "url": url, "inLanguage": "en-IN",
              "publisher": {"@type": "Organization", "name": "IPO Shop", "url": SITE + "/"}}]
    if faqs:
        graph.append({"@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faqs]})
    if extra_ld:
        graph += extra_ld
    ld = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)
    more = "".join(f'<a href="{u}">{esc(t)}</a>' for u, t in P.PAGE_LINKS if u != f"/{slug}/")
    crumb_html = " › ".join(f'<a href="{u.replace(SITE, "") or "/"}">{esc(n)}</a>' for n, u in trail[:-1]) + (" › " if len(trail) > 1 else "") + esc(trail[-1][0])
    faq_html = ('<section class="card"><h2>Common questions</h2>' + "".join(f"<h3>{esc(q)}</h3><p>{esc(a)}</p>" for q, a in faqs) + "</section>") if faqs else ""
    page = (P.head(title, desc, url, extra=CSS_X + f'<script type="application/ld+json">{ld}</script>', tick=tick)
            + f'<main class="w wide"><div class="crumbs">{crumb_html}</div><h1>{esc(h1)}</h1>' + body + faq_html
            + f'<section class="card links"><h2>More on IPO Shop</h2>{more}</section>'
            '<p class="muted">Facts only, not investment advice. GMP is unofficial. Read the offer document (RHP) before investing.</p></main>'
            + script + P.FOOT)
    return page


def fmt_ts(iso):
    """'2026-10-28T14:11' -> '28 Oct, 2:11 pm' (the time is already IST)."""
    if not iso:
        return ""
    try:
        t = dt.datetime.fromisoformat(iso[:16])
    except ValueError:
        return ""
    h = t.hour % 12 or 12
    return f"{t.day} {t.strftime('%b')}, {h}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def x(v):
    return f"{v:.2f}x" if v is not None else "–"


# ------------------------------------------------------------------ 1. SME / Mainboard dashboard
def dashboard(ds, seg):
    sme = seg == "SME"
    rows = [d for d in ds if (d.get("segment") == "SME") == sme and d.get("status") == "listed" and d.get("listing_gain_pct") is not None and d.get("listing")]
    if len(rows) < 5:
        return ""
    word = "SME" if sme else "Mainboard"
    g = [d["listing_gain_pct"] for d in rows]
    up = sum(1 for v in g if v > 0)
    flat = sum(1 for v in g if v == 0)
    down = len(g) - up - flat
    deep = sum(1 for v in g if v <= -10)
    med = statistics.median(g)
    cards = (f'<div class="kp"><div><b>{len(g)}</b><span>{word} IPOs listed this year</span></div>'
             f'<div><b class="pos">{round(100 * up / len(g))}%</b><span>closed day one above issue price ({up})</span></div>'
             f'<div><b class="{"pos" if med >= 0 else "neg"}">{med:+.1f}%</b><span>middle first-day result</span></div>'
             f'<div><b class="neg">{round(100 * deep / len(g))}%</b><span>fell 10% or more ({deep})</span></div></div>')
    by_m = {}
    for d in rows:
        by_m.setdefault(d["listing"][:7], []).append(d["listing_gain_pct"])
    trs = ""
    for m in sorted(by_m, reverse=True):
        v = by_m[m]
        mu = sum(1 for a in v if a > 0)
        trs += (f"<tr><td>{esc(P.mon(m + '-01'))}</td><td>{len(v)}</td><td>{mu}</td><td>{len(v) - mu}</td>"
                f'<td class="{"pos" if statistics.median(v) >= 0 else "neg"}">{statistics.median(v):+.1f}%</td><td>{max(v):+.1f}%</td><td>{min(v):+.1f}%</td></tr>')
    tbl = ('<div class="tw"><table class="t"><caption class="sr">First-day results by listing month</caption><thead><tr>'
           '<th scope="col">Month</th><th scope="col">Listed</th><th scope="col">Closed up</th><th scope="col">Closed down or flat</th><th scope="col">Middle</th><th scope="col">Best</th><th scope="col">Worst</th>'
           f"</tr></thead><tbody>{trs}</tbody></table></div>")
    return (f'<section class="card"><h2>{word} IPO scorecard for {max(d["listing"] for d in rows)[:4]}</h2>'
            f'<p class="muted">How {word} IPOs that listed this year closed on their first trading day, compared with the issue price. Past results of other companies: they do not predict any future IPO.</p>'
            + cards + tbl + "</section>")


# ------------------------------------------------------------------ 2. subscription status
def odds(r):
    """Rough share of retail applicants who get a lot, from the retail subscription."""
    if r is None:
        return "–"
    if r < 1:
        return "not full yet"
    return f"about 1 in {round(r * 10) / 10 if r < 2 else round(r)}"


def _sub_row(d):
    s = d.get("sub") or {}
    nii = s.get("nii")
    if nii is None and (s.get("bnii") is not None or s.get("snii") is not None):
        nii = None
    cells = [x(s.get("qib")), x(nii), x(s.get("retail")), f"<b>{x(s.get('total'))}</b>", odds(s.get("retail"))]
    stamp = fmt_ts(d.get("sub_asof"))
    return (f"<tr><td>{link(d)}</td>"
            + "".join(f"<td>{c}</td>" for c in cells) + f"<td>{esc(stamp) if stamp else '–'}</td></tr>")


def _sub_table(rows, title):
    if not rows:
        return ""
    head = "".join(f'<th scope="col">{h}</th>' for h in ("IPO", "QIB", "NII (HNI)", "Retail", "Total", "Retail chance", "As of"))
    return (f'<section class="card"><h2>{esc(title)} ({len(rows)})</h2><p class="muted swipe">Swipe the table sideways to see every column.</p>'
            f'<div class="tw"><table class="t"><caption class="sr">{esc(title)}</caption><thead><tr>{head}</tr></thead><tbody>'
            + "".join(_sub_row(d) for d in rows) + "</tbody></table></div></section>")


SUB_JS = """<script>(function(){
function x(v){return v==null?"–":v.toFixed(2)+"x"}
function odds(r){if(r==null)return"–";if(r<1)return"not full yet";return"about 1 in "+(r<2?Math.round(r*10)/10:Math.round(r))}
function ts(s){if(!s)return"–";var m=/^(\\d{4})-(\\d\\d)-(\\d\\d)T(\\d\\d):(\\d\\d)/.exec(s);if(!m)return"–";var M=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"],h=+m[4],a=h<12?"am":"pm";h=h%12||12;return(+m[3])+" "+M[+m[2]-1]+", "+h+":"+m[5]+" "+a}
function esc(t){return String(t).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function tick(){fetch("/ipos.json?"+Date.now(),{cache:"no-store"}).then(function(r){return r.json()}).then(function(a){
  var by={};a.ipos.forEach(function(i){by[i.id]=i});
  document.querySelectorAll("table[data-sub] tbody tr").forEach(function(tr){var a0=tr.querySelector("td a");if(!a0)return;var id=(a0.getAttribute("href")||"").split("/").filter(Boolean).pop(),i=by[id];if(!i)return;var s=i.sub||{},td=tr.children;
    td[1].textContent=x(s.qib);td[2].textContent=x(s.nii);td[3].textContent=x(s.retail);td[4].innerHTML="<b>"+x(s.total)+"</b>";td[5].textContent=odds(s.retail);td[6].textContent=ts(i.sub_asof)});
  var el=document.getElementById("asof");if(el&&a.generated_at){el.textContent="Live data last changed "+new Date(a.generated_at).toLocaleString("en-GB",{timeZone:"Asia/Kolkata",day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"})+" IST. Figures on this page update by themselves every two minutes."}
}).catch(function(){})}
setInterval(tick,120000);tick();})();</script>"""


def lp_subscription(ds, root=None):
    live = P.by(ds, lambda d: d.get("status") in ("open", "closing_today"), lambda d: (d.get("close") or "", d["name"]))
    done = P.by(ds, lambda d: d.get("status") == "closed", lambda d: (d.get("listing") or d.get("close") or "", d["name"]))[::-1]
    n_live = len(live)
    intro = (f"{n_live} IPO{'s are' if n_live != 1 else ' is'} open for bidding. Below is how many times each has been subscribed so far, split by investor category, with the time of the last figure."
             if n_live else "No IPO is open for bidding right now. The IPOs that closed recently and are waiting to list are shown with their final subscription.")
    how = ('<section class="card"><h2>How to read these numbers</h2><ul class="list"><li><b>QIB</b>: qualified institutional buyers, such as mutual funds and insurers. Their bids mostly arrive on the last day.</li>'
           '<li><b>NII (HNI)</b>: non-institutional investors, who bid for more than ₹2 lakh in one application.</li>'
           '<li><b>Retail</b>: individuals bidding up to ₹2 lakh. Retail applicants get shares by draw of lots when this part is oversubscribed.</li>'
           '<li><b>Total</b>: all bids across categories divided by the shares on offer. 1x means fully subscribed.</li>'
           '<li><b>Retail chance</b>: when the retail part is oversubscribed, shares are given out by a draw of lots. &quot;About 1 in 5&quot; means roughly one retail application in five receives the minimum lot. It is arithmetic from the retail figure, not a prediction, and applying for more lots does not improve it.</li></ul>'
           '<p class="muted">A dash means the figure is not available for that IPO yet. Subscription measures demand. It does not predict the listing price.</p></section>')
    body = (f'<p class="lead">{esc(intro)}</p><p class="muted" id="asof">Figures update by themselves every two minutes.</p>'
            + _sub_table(live, "Open for bidding").replace("<table", "<table data-sub", 1)
            + _sub_table(done, "Closed, waiting to list").replace("<table", "<table data-sub", 1) + (how if (live or done) else how))
    faqs = [("What does 'subscribed 10x' mean?", "It means investors bid for ten times the shares on offer. If you bid for a share you might not get it: the more times an issue is subscribed, the smaller the share of applicants who are allotted shares."),
            ("When do QIB bids come in?", "Institutional investors usually place most of their bids on the last day of the issue, so QIB numbers often jump late on closing day."),
            ("Where do these figures come from?", "Subscription comes from NSE and BSE where they publish it. For some SME issues the exchanges do not give us the figure, so it is taken from public trackers and labelled on the IPO page."),
            ("Does high subscription mean the share will list higher?", "Not necessarily. Subscription shows demand during the issue. The listing price depends on how the shares trade on the first day."),
            ("What is my chance of getting an allotment?", "In the retail category shares are given out by a draw of lots when the part is oversubscribed, so the rough chance is one divided by the retail subscription. If retail is 10 times subscribed, about one application in ten receives the minimum lot. Applying for more lots does not improve the odds in the retail category.")]
    return shell("ipo-subscription-status", "IPO Subscription Status Today: QIB, NII, Retail | IPO Shop", "IPO subscription status today",
                 "Live IPO subscription status in India: QIB, NII and retail subscription of every open IPO and of IPOs waiting to list, with the time of each figure.",
                 body, faqs, tick=True, script=SUB_JS)


# ------------------------------------------------------------------ 3. GMP accuracy
def update_final(root, ipos, hist_rows, today, stamp):
    """Remembers the last GMP seen before each IPO listed (frozen once it lists) and the actual first-day result."""
    path = pathlib.Path(root) / "data" / "gmp_final.json"
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        store = {}
    if not isinstance(store, dict) or "ipos" not in store:
        store = {"started": today.isoformat(), "ipos": {}}
    before = json.dumps(store, sort_keys=True)
    S = store["ipos"]
    for i in ipos:
        if i.get("gmp") is None or not i.get("price") or i.get("unconfirmed") or i.get("status") == "listed":
            continue
        e = S.setdefault(i["id"], {})
        e.update({"name": i["name"], "segment": i.get("segment") or "MAIN", "price": i["price"], "gmp": i["gmp"],
                  "at": i.get("gmp_asof") or today.isoformat(), "listing": i.get("listing")})
    by = {r["id"]: r for r in hist_rows if r.get("id")}
    for k, e in S.items():
        r = by.get(k)
        if r and r.get("listing_gain_pct") is not None and r.get("listing_close") is not None and e.get("actual_pct") is None:
            e["actual_pct"], e["actual_close"], e["listed_on"] = r["listing_gain_pct"], r["listing_close"], r.get("listing")
    if json.dumps(store, sort_keys=True) != before:
        path.write_text(json.dumps(store, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return store


def lp_accuracy(ds, root=None):
    try:
        store = json.loads((pathlib.Path(root) / "data" / "gmp_final.json").read_text(encoding="utf-8"))
    except Exception:
        store = {"started": None, "ipos": {}}
    S = store.get("ipos") or {}
    scored = []
    for k, e in S.items():
        if e.get("actual_pct") is None or not e.get("price"):
            continue
        implied = e["gmp"] / e["price"] * 100
        scored.append({"id": k, **e, "implied": implied, "gap": e["actual_pct"] - implied,
                       "dir_ok": (implied > 0) == (e["actual_pct"] > 0) if (implied != 0 and e["actual_pct"] != 0) else (implied == 0 and e["actual_pct"] == 0),
                       "near": abs(e["actual_pct"] - implied) <= 10})
    scored.sort(key=lambda r: r.get("listed_on") or "", reverse=True)
    waiting = [e for e in S.values() if e.get("actual_pct") is None]
    started = store.get("started")
    n = len(scored)
    if n:
        dir_ok = sum(1 for r in scored if r["dir_ok"])
        near = sum(1 for r in scored if r["near"])
        mabs = statistics.median(abs(r["gap"]) for r in scored)
        cards = (f'<div class="kp"><div><b>{n}</b><span>IPOs scored</span></div><div><b>{round(100 * dir_ok / n)}%</b><span>right about up or down ({dir_ok})</span></div>'
                 f'<div><b>{round(100 * near / n)}%</b><span>within 10 points of the real close ({near})</span></div><div><b>{mabs:.1f}</b><span>typical miss, in percentage points</span></div></div>')
        trs = "".join(
            f"<tr><td>{link({'id': r['id'], 'name': r['name'], 'segment': r['segment']})}</td><td>{rs(r['price'])}</td>"
            f"<td>{P.srs(r['gmp'])} ({r['implied']:+.1f}%)</td><td>{rs(r['actual_close'])} ({r['actual_pct']:+.1f}%)</td>"
            f'<td class="{"pos" if r["gap"] >= 0 else "neg"}">{r["gap"]:+.1f}</td><td>{"Yes" if r["dir_ok"] else "No"}</td></tr>' for r in scored[:60])
        tbl = ('<section class="card"><h2>Scoreboard</h2><p class="muted swipe">Swipe the table sideways to see every column.</p><div class="tw"><table class="t"><caption class="sr">GMP against the real first-day close</caption><thead><tr>'
               '<th scope="col">IPO</th><th scope="col">Issue price</th><th scope="col">Last GMP</th><th scope="col">First-day close</th><th scope="col">Miss (pts)</th><th scope="col">Direction right</th>'
               f"</tr></thead><tbody>{trs}</tbody></table></div></section>")
        small = (f'<p class="verdict"><b>Small sample.</b> Only {n} IPO{"s" if n != 1 else ""} {"have" if n != 1 else "has"} been scored so far, so treat these percentages as a first look, not a verdict on GMP.</p>' if n < 30 else "")
        lead = f"We record the last GMP reported before each IPO lists, then compare it with how the share really closed on its first day. {n} IPO{'s have' if n != 1 else ' has'} been scored since {P.mon(started) if started else 'tracking began'}."
        body = f'<p class="lead">{esc(lead)}</p>{small}{cards}{tbl}'
    else:
        lead = "We record the last GMP reported before each IPO lists, then compare it with how the share really closed on its first day. The first results appear here after the IPOs now being tracked have listed."
        body = f'<p class="lead">{esc(lead)}</p>'
    if waiting:
        body += f'<section class="card"><h2>Being tracked now</h2><p>{len(waiting)} IPO{"s" if len(waiting) != 1 else ""} with a GMP on record {"are" if len(waiting) != 1 else "is"} waiting to list. They are added to the scoreboard once the first-day close is published.</p></section>'
    body += ('<section class="card"><h2>How we score</h2><ul class="list"><li><b>Last GMP</b> is the final reading we saw before listing, as a share of the issue price.</li>'
             '<li><b>First-day close</b> is the closing price on the listing day against the issue price, as published by the exchanges. GMP is usually talked about as a guide to the listing (opening) price, so a gap is normal.</li>'
             '<li><b>Miss</b> is the real result minus the GMP-implied result, in percentage points. A positive miss means the share did better than GMP suggested.</li>'
             '<li>An IPO counts as <b>within 10 points</b> when the miss is 10 points or less either way.</li></ul></section>')
    faqs = [("Is GMP accurate?", "Sometimes, and often not. GMP is an unofficial number from informal markets and it can change quickly. This page shows how it compared with real first-day closes for the IPOs we have tracked."),
            ("Can I use GMP to decide whether to apply?", "GMP is not a promise and this page is not a recommendation. Read the offer document (RHP) and speak to a SEBI-registered advisor before investing."),
            ("Why compare with the first-day close and not the opening price?", "The exchanges publish the first-day close for every listing in the data we read, so we use it consistently. It can differ from the opening price.")]
    return shell("gmp-accuracy", "How Accurate Is IPO GMP? Scoreboard of GMP vs Listing | IPO Shop", "How accurate is IPO GMP?",
                 "A running scoreboard of grey market premium (GMP) against the real first-day close of IPOs in India, for Mainboard and SME. Past results, not a forecast.",
                 body, faqs, tick=False)


# ------------------------------------------------------------------ 4. compare
COMPARE_JS = """<script>(function(){
var A=[],$=function(i){return document.getElementById(i)};
function esc(t){return String(t==null?"":t).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function n(v){return Number(v).toLocaleString("en-IN",{maximumFractionDigits:2})}
function d(s){if(!s)return"–";var m=/^(\\d{4})-(\\d\\d)-(\\d\\d)/.exec(s);if(!m)return"–";var M=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];return(+m[3])+" "+M[+m[2]-1]+" "+m[1]}
function ml(i){return i.segment==="SME"?2:1}
var ST={upcoming:"Upcoming",open:"Open",closing_today:"Closes today",closed:"Closed, waiting to list",listed:"Listed"};
var ROWS=[
["Segment",function(i){return i.segment==="SME"?"SME ("+(i.exchange||"SME platform")+")":"Mainboard"}],
["Status",function(i){return ST[i.status]||i.status}],
["Opens",function(i){return d(i.open)}],["Closes",function(i){return d(i.close)}],["Listing date",function(i){return d(i.listing)}],
["Price band",function(i){return i.price==null?"To be announced":(i.price_low&&i.price_low!==i.price?"₹"+n(i.price_low)+" to ":"")+"₹"+n(i.price)}],
["Lot size",function(i){return i.lot?n(i.lot)+" shares":"–"}],
["One lot costs",function(i){return i.lot&&i.price?"₹"+n(i.lot*i.price):"–"}],
["Minimum to apply",function(i){return i.lot&&i.price?(ml(i)>1?ml(i)+" lots · ":"")+"₹"+n(i.lot*i.price*ml(i)):"–"}],
["Issue size",function(i){return i.size_cr!=null?"₹"+n(i.size_cr)+" Cr":"–"}],
["Subscribed (total)",function(i){return i.sub&&i.sub.total!=null?i.sub.total.toFixed(2)+"x":"–"}],
["Retail subscribed",function(i){return i.sub&&i.sub.retail!=null?i.sub.retail.toFixed(2)+"x":"–"}],
["QIB subscribed",function(i){return i.sub&&i.sub.qib!=null?i.sub.qib.toFixed(2)+"x":"–"}],
["GMP (unofficial)",function(i){return i.gmp==null?"No quote yet":"₹"+n(i.gmp)+(i.gmp_pct!=null?" ("+(i.gmp_pct>=0?"+":"")+i.gmp_pct.toFixed(1)+"%)":"")}],
["GMP trend",function(i){var t=i.gmp_trend;return t?(t.dir==="rising"?"▲ Rising":t.dir==="falling"?"▼ Falling":"▬ Steady"):"–"}],
["Price + GMP",function(i){return i.gmp!=null&&i.price!=null?"₹"+n(i.price+i.gmp):"–"}],
["Registrar",function(i){return i.registrar||"–"}],
["Listing-day result",function(i){return i.status==="listed"&&i.listing_gain_pct!=null?(i.listing_gain_pct>=0?"+":"")+i.listing_gain_pct.toFixed(2)+"%":"–"}]
];
function render(){var ids=[$("c1").value,$("c2").value,$("c3").value].filter(Boolean),sel=ids.map(function(id){return A.filter(function(i){return i.id===id})[0]}).filter(Boolean);
  var out=$("out");if(sel.length<2){out.innerHTML='<p class="muted">Pick at least two IPOs to compare.</p>';return}
  var h='<div class="tw"><table class="t cmp"><caption class="sr">IPO comparison</caption><thead><tr><th scope="col"></th>'+sel.map(function(i){return'<th scope="col"><a href="/ipo/'+esc(i.id)+'/">'+esc(i.name)+'</a></th>'}).join("")+'</tr></thead><tbody>';
  ROWS.forEach(function(r){h+="<tr><td>"+r[0]+"</td>"+sel.map(function(i){return"<td>"+esc(r[1](i))+"</td>"}).join("")+"</tr>"});
  out.innerHTML=h+"</tbody></table></div>";
  try{history.replaceState(null,"","?ipos="+ids.join(","))}catch(e){}}
fetch("/ipos.json?"+Date.now(),{cache:"no-store"}).then(function(r){return r.json()}).then(function(a){
  A=(a.ipos||[]).filter(function(i){return !i.unconfirmed});
  var order={closing_today:0,open:1,upcoming:2,closed:3,listed:4},names={0:"Open now",1:"Open now",2:"Upcoming",3:"Closed, waiting to list",4:"Listed"};
  var s=A.slice().sort(function(x,y){return(order[x.status]-order[y.status])||String(x.name).localeCompare(y.name)});
  var opts='<option value="">Choose an IPO</option>',last="";
  s.forEach(function(i){var g=names[order[i.status]];if(g!==last){if(last)opts+="</optgroup>";opts+='<optgroup label="'+g+'">';last=g}opts+='<option value="'+esc(i.id)+'">'+esc(i.name)+(i.segment==="SME"?" (SME)":"")+"</option>"});
  opts+="</optgroup>";["c1","c2","c3"].forEach(function(k){$(k).innerHTML=opts;$(k).onchange=render});
  var q=/[?&]ipos=([^&]*)/.exec(location.search),pre=q?decodeURIComponent(q[1]).split(","):[];
  if(!pre.length){var o=s.filter(function(i){return i.status==="open"||i.status==="closing_today"||i.status==="upcoming"});pre=o.slice(0,2).map(function(i){return i.id})}
  pre.slice(0,3).forEach(function(id,k){var e=$("c"+(k+1));if(e)e.value=id});render()
}).catch(function(){$("out").innerHTML='<p class="muted">The IPO list could not be loaded. Please refresh the page.</p>'});})();</script>"""


def lp_compare(ds, root=None):
    body = ('<p class="lead">Pick two or three IPOs to see their dates, price band, lot size, subscription and GMP side by side. Open, upcoming and listed IPOs are all included.</p>'
            '<section class="card"><div class="cmpbar"><select id="c1" aria-label="First IPO"></select><select id="c2" aria-label="Second IPO"></select><select id="c3" aria-label="Third IPO (optional)"></select></div>'
            '<div id="out"><p class="muted">Loading the IPO list…</p></div>'
            '<noscript><p>This comparison needs JavaScript. You can still read every IPO on its own page from the <a href="/ipo/">All IPOs</a> list.</p></noscript></section>')
    faqs = [("What does this comparison show?", "Facts only: dates, price band, lot size, the minimum you must apply for, subscription, GMP and the listing result for IPOs that have listed. It does not rank IPOs or say which is better."),
            ("Why is the minimum to apply higher for SME IPOs?", "A retail application in an SME IPO has to be for at least two lots, and SME lots are usually bigger than Mainboard lots."),
            ("Is the GMP shown here reliable?", "GMP is unofficial and can change quickly. It is shown for information only and is never a promise of a listing price.")]
    return shell("compare-ipos", "Compare IPOs Side by Side: Price, Lot, Subscription, GMP | IPO Shop", "Compare IPOs",
                 "Compare two or three IPOs in India side by side: dates, price band, lot size, minimum application, subscription and GMP. Facts only, from NSE and BSE data.",
                 body, faqs, script=COMPARE_JS)


# ------------------------------------------------------------------ 5. learn
def _art(slug, title, h1, desc, lead, sections, faqs, related):
    body = f'<p class="lead">{esc(lead)}</p><article class="card art">'
    for h, parts in sections:
        body += f"<h2>{esc(h)}</h2>"
        for p in parts:
            body += (f'<ul class="list">' + "".join(f"<li>{i}</li>" for i in p[1]) + "</ul>") if isinstance(p, tuple) else f"<p>{p}</p>"
    body += f'</article><p class="mini">Last reviewed {LAST_REVIEWED}. General information, not investment advice.</p>'
    body += '<section class="card"><h2>Keep reading</h2><div class="lh">' + "".join(f'<a href="/learn/{s}/"><b>{esc(t)}</b><span>{esc(d)}</span></a>' for s, t, d in related) + "</div></section>"
    url = f"{SITE}/learn/{slug}/"
    art_ld = [{"@type": "Article", "headline": h1, "description": desc, "mainEntityOfPage": url, "inLanguage": "en-IN",
               "author": {"@type": "Organization", "name": "IPO Shop"}, "publisher": {"@type": "Organization", "name": "IPO Shop", "url": SITE + "/"}}]
    return shell(f"learn/{slug}", title, h1, desc, body, faqs, ld_type="WebPage",
                 crumbs=[("IPO Shop", SITE + "/"), ("Learn", SITE + "/learn/"), (h1, url)], extra_ld=art_ld)


def _b(*items):
    return ("ul", list(items))


ARTICLES = {}


def _define():
    A = ARTICLES
    A["what-is-ipo-gmp"] = dict(
        t="What Is IPO GMP? Grey Market Premium Explained | IPO Shop", h="What is IPO GMP?", short="Grey market premium in plain English.",
        d="What GMP (grey market premium) means in an IPO, how it is quoted, why it moves and why it often differs from the real listing price.",
        lead="GMP is an unofficial number people quote before an IPO lists. Here is what it means, what it does not mean, and how to read it with care.",
        s=[("The short answer", ["GMP stands for grey market premium. It is the extra amount, in rupees, that IPO shares or applications are said to trade for in an unofficial market before the shares list on the exchange. A GMP of ₹40 on an IPO priced at ₹200 suggests a listing near ₹240, about 20% above the issue price."]),
           ("Why it is called a grey market", ["The deals happen outside the stock exchanges. SEBI and the exchanges do not regulate this market and do not publish any GMP. Figures are collected by trackers from dealers and are not audited."]),
           ("How to work out the number", [_b("<b>Estimated listing price</b> = issue price + GMP.", "<b>GMP percentage</b> = GMP ÷ issue price × 100.", "At the top of a price band of ₹200 with a GMP of ₹40, the percentage is 20%.")]),
           ("Why GMP changes", ["It moves with demand during the issue, news about the company, the mood in the stock market and how heavily the IPO is subscribed. It can change within hours and differs between sources."]),
           ("Why it can be wrong", [_b("It is based on informal trading, not on the exchange.", "Few deals can move the figure, especially for small and SME issues.", "Many SME IPOs have no GMP at all.", "The first-day price depends on real trading, which can go either way."),
                                    'See how GMP has compared with real first-day results on our <a href="/gmp-accuracy/">GMP accuracy scoreboard</a>.']),
           ("How to use it sensibly", [_b("Treat it as one unofficial signal, never a promise.", "Compare several sources before reading too much into one number.", "Read the offer document (RHP) for the business, the risks and the valuation.", "Speak to a SEBI-registered advisor before you invest.")])],
        faqs=[("Is GMP official?", "No. GMP is not published by SEBI, the exchanges or the company. It comes from informal markets and IPO Shop only reports it for information."),
              ("Does a high GMP guarantee a listing gain?", "No. The GMP can fall or rise before listing and the real price depends on how the shares trade on the first day."),
              ("What does a negative GMP mean?", "It means the grey market expects the shares to list below the issue price. It is also only an unofficial view.")],
        rel=["ipo-subscription-explained", "ipo-listing-gain-explained", "sme-ipo-guide"])
    A["how-to-apply-for-an-ipo"] = dict(
        t="How to Apply for an IPO in India: UPI and ASBA Steps | IPO Shop", h="How to apply for an IPO in India", short="Steps, UPI, lots and the ₹2 lakh limit.",
        d="Step-by-step guide to applying for an IPO in India through your broker app or bank: demat account, UPI ID, lots, cut-off price and what happens to your money.",
        lead="You can apply for an IPO from your broker's app or your bank's net banking in a few minutes. Here is the process and the terms you will meet.",
        s=[("What you need first", [_b("A demat account and a trading account with a broker, or a bank that offers IPO applications.", "A PAN-linked bank account.", "A UPI ID linked to that account, if you apply through UPI.")]),
           ("Steps to apply", [_b("Open the IPO section in your broker or bank app and pick an IPO that is open for bidding.", "Choose the number of <b>lots</b>. A lot is the minimum set of shares you can bid for, so the amount is lots × lot size × price.", "Pick the price. Choosing the <b>cut-off price</b> means you accept whatever final price is set within the band.", "Enter your UPI ID and submit. You get a payment request in your UPI app.", "Approve the request in your UPI app before the cut-off on the last day. Without approval the bid is not valid.")]),
           ("Your money stays in your account", ["The amount is blocked, not paid out, under a facility called ASBA. If you are allotted shares, the money for them is debited. If not, the block is removed after allotment."]),
           ("Limits to know", [_b("Retail investors can bid up to ₹2 lakh in one IPO.", "In <b>SME IPOs</b> the minimum retail application is two lots, which usually costs more than ₹1 lakh.", "Only one application per PAN is allowed in a category. Duplicate applications from the same PAN can be rejected.")]),
           ("After you apply", ['The registrar finalises allotment after the issue closes. See <a href="/learn/ipo-allotment-process/">how the allotment process works</a> and check the status on our <a href="/ipo-allotment-status/">allotment status page</a>.'])],
        faqs=[("Can I apply without a demat account?", "No. Shares are credited to a demat account, so you need one to apply."),
              ("Can I cancel or change my bid?", "Most brokers let you modify or cancel a bid while the IPO is still open. After the issue closes you cannot."),
              ("Does IPO Shop take applications?", "No. IPO Shop is an information website. You apply through your own broker or bank.")],
        rel=["ipo-allotment-process", "sme-ipo-guide", "ipo-subscription-explained"])
    A["ipo-allotment-process"] = dict(
        t="IPO Allotment Process in India: Dates, Lottery, Refund | IPO Shop", h="How IPO allotment works", short="What happens after the IPO closes.",
        d="How IPO allotment works in India: the timeline after closing, how shares are allotted by lottery when oversubscribed, how to check status and when refunds arrive.",
        lead="After bidding closes, a registrar decides who gets shares. This guide explains the timeline, the lottery and what to do if you get nothing.",
        s=[("The usual timeline", ["Counting the closing day as day T, current rules aim for listing within three working days:", _b("<b>T</b>: bidding closes.", "<b>T+1</b>: the registrar finalises the basis of allotment.", "<b>T+2</b>: shares are credited to demat accounts and unused money is unblocked.", "<b>T+3</b>: shares list on the exchange."), "Dates can differ for an individual IPO, so check its page."]),
           ("Who decides allotment", ["The registrar to the issue handles allotment with the exchange. Each IPO names its registrar, and you check your status on the registrar's website."]),
           ("When there are more bids than shares", ["If the retail part is subscribed more than once, shares cannot go to everyone. Allotment is then made by a draw of lots among valid applications, in the minimum lot. Bidding for more lots does not improve the odds of being picked in the retail category.", "If the retail part is not fully subscribed, valid applicants generally get what they bid for."]),
           ("How to check your status", [_b("Open the registrar's allotment page for that IPO.", "Choose the IPO and enter your PAN, application number or demat ID.", "Your broker app may show the result too.")]),
           ("If you get nothing", ["Many IPOs are oversubscribed, so missing out is common. The blocked money is released and nothing is debited."])],
        faqs=[("Why did I not get any shares?", "When an IPO is oversubscribed, only some applications are picked in the draw. Not being picked does not mean your application was wrong."),
              ("When will I get my refund?", "Blocked funds are normally released around the day after allotment. If you applied by UPI, the block is removed from your account."),
              ("Does applying with more family members help?", "Each person can apply with their own PAN. Applying more than once on the same PAN is not allowed.")],
        rel=["how-to-apply-for-an-ipo", "ipo-subscription-explained", "ipo-listing-gain-explained"])
    A["sme-ipo-guide"] = dict(
        t="SME IPO Guide: What Is an SME IPO and Its Risks | IPO Shop", h="SME IPO guide for beginners", short="BSE SME, NSE Emerge, 2 lots and the risks.",
        d="A plain guide to SME IPOs in India: what they are, where they list, why the minimum application is 2 lots, and the extra risks of thin trading.",
        lead="SME IPOs come from smaller companies and list on separate platforms. They work like other IPOs, with some important differences.",
        s=[("What is an SME IPO?", ["It is a public issue by a small or medium company. The shares list on the SME platform of an exchange: <b>BSE SME</b> or <b>NSE Emerge</b>. They are separate from the main boards."]),
           ("How it differs from a Mainboard IPO", [_b("<b>Bigger lots.</b> The minimum application is two lots, and the cost is usually above ₹1 lakh.", "<b>Thinner trading.</b> Fewer buyers and sellers, so prices can jump and it can be hard to sell.", "<b>Less public information.</b> Smaller firms have shorter track records and fewer analysts follow them.", "<b>Often no GMP.</b> Many SME issues have no quote, which is why our pages show how SME IPOs have listed so far.")]),
           ("What does a market maker do?", ["SME issues have a market maker who is required to quote buy and sell prices for a period after listing, to keep trading going. This helps liquidity but does not remove price risk."]),
           ("Risks to weigh", [_b("A larger share of your money is tied up in one small company.", "Prices can fall sharply after listing and recovery may be slow.", "Allotment is by draw of lots when oversubscribed, like any IPO.")]),
           ("Look at the record", ['See how SME IPOs have listed this year on our <a href="/sme-ipos/">SME IPOs page</a>, including the share that closed up or down on day one.'])],
        faqs=[("What is the minimum investment in an SME IPO?", "At least two lots. Multiply the lot size by the issue price and by two to get the minimum application. Our IPO pages show this figure."),
              ("Are SME IPOs riskier than Mainboard IPOs?", "They usually carry more risk because of thinner trading, smaller companies and larger minimum amounts. That is not a reason to avoid or to choose them, only to read the offer document with extra care."),
              ("Can I sell SME shares quickly?", "You can place a sell order after listing, but with fewer buyers it may take longer or fill at a different price.")],
        rel=["how-to-apply-for-an-ipo", "what-is-ipo-gmp", "ipo-listing-gain-explained"])
    A["ipo-subscription-explained"] = dict(
        t="IPO Subscription Explained: QIB, NII, Retail and Times Subscribed | IPO Shop", h="IPO subscription explained", short="QIB, NII, retail and what 'x times' means.",
        d="What IPO subscription means in India, how the 'times subscribed' figure is worked out, what QIB, NII and retail categories are and what the numbers can and cannot tell you.",
        lead="Subscription is the headline number during an IPO. Here is how it is counted and how to read it.",
        s=[("What 'subscribed 5x' means", ["The shares offered are divided into portions. Investors bid for shares in each portion. If bids add up to five times the shares on offer, the issue is subscribed 5x. Anything above 1x means more people want shares than are available."]),
           ("The categories", [_b("<b>QIB</b>: qualified institutional buyers, such as mutual funds and insurers.", "<b>NII or HNI</b>: non-institutional investors bidding above ₹2 lakh. This is sometimes split into small NII and big NII.", "<b>Retail</b>: individuals bidding up to ₹2 lakh.", "<b>Employees and shareholders</b>: small reserved portions in some issues.")]),
           ("When the numbers arrive", ["Bids are collected through the open days and the exchange updates figures during the day. Institutions often bid late on the last day, so the final numbers can look very different from day one."]),
           ("What it does and does not tell you", [_b("It shows demand during the issue.", "It affects your chance of allotment: the higher the retail subscription, the smaller the share of retail applicants who get shares.", "It does not tell you how the share will trade after listing.")]),
           ("See it live", ['Our <a href="/ipo-subscription-status/">IPO subscription status page</a> shows every open IPO by category with the time of each figure.'])],
        faqs=[("Is a high subscription a good sign?", "It shows strong demand during the issue. It does not guarantee a listing gain or tell you whether the company is worth its price."),
              ("Why is the QIB number zero at first?", "Institutions usually bid on the last day, so QIB subscription often starts low and rises sharply near the close."),
              ("What is the demand meter on IPO Shop?", "A 1 to 5 scale based only on the total subscription. It is not a rating of the company.")],
        rel=["ipo-allotment-process", "what-is-ipo-gmp", "how-to-apply-for-an-ipo"])
    A["ipo-listing-gain-explained"] = dict(
        t="IPO Listing Gain Explained: Listing Price vs Issue Price | IPO Shop", h="IPO listing gain explained", short="Listing price, first-day close and how gain is counted.",
        d="How listing gain is worked out for an IPO in India, the difference between listing price and first-day close, and why the result can differ from GMP.",
        lead="Listing gain compares the price a share starts trading at with the price you paid in the IPO. Here is how to read it.",
        s=[("The formula", [_b("<b>Listing gain %</b> = (listing price − issue price) ÷ issue price × 100.", "If the issue price is ₹200 and shares list at ₹240, the listing gain is 20%.")]),
           ("Listing price vs first-day close", ["The <b>listing price</b> is where trading opens. The <b>first-day close</b> is where the share ends the day, and it can be higher or lower. IPO Shop shows the first-day close from the exchanges against the issue price."]),
           ("Gain per lot", ["Multiply the gain per share by the shares in your lot. For a lot of 100 shares and a gain of ₹40 per share, the gain on a lot is ₹4,000, before any charges or taxes."]),
           ("Why it can differ from GMP", [_b("GMP is an unofficial guess made before listing.", "Real trading is shaped by orders on the day.", "Market conditions can change between the last GMP and listing.", 'See our <a href="/gmp-accuracy/">GMP accuracy scoreboard</a> for how the two have compared.')]),
           ("Selling after listing", ["Some investors sell on listing day and others hold. This page does not tell you what to do. Prices keep moving after day one."])],
        faqs=[("Does a listing gain mean the IPO was a good investment?", "It only describes the first day. Later returns depend on the company and the market."),
              ("What if the share lists below the issue price?", "That is a listing loss. The shares can then trade above or below the issue price in later days."),
              ("Where can I see how IPOs listed?", "See the lists of IPOs listed this year, with first-day close and gain, on our listed IPOs page.")],
        rel=["what-is-ipo-gmp", "sme-ipo-guide", "ipo-allotment-process"])


_define()
ORDER_ARTICLES = ["what-is-ipo-gmp", "how-to-apply-for-an-ipo", "ipo-allotment-process", "ipo-subscription-explained", "sme-ipo-guide", "ipo-listing-gain-explained"]


def learn_pages():
    out = []
    for slug in ORDER_ARTICLES:
        a = ARTICLES[slug]
        related = [(r, ARTICLES[r]["h"], ARTICLES[r]["short"]) for r in a["rel"]]
        out.append((f"learn/{slug}", _art(slug, a["t"], a["h"], a["d"], a["lead"], a["s"], a["faqs"], related)))
    cards = "".join(f'<a href="/learn/{s}/"><b>{esc(ARTICLES[s]["h"])}</b><span>{esc(ARTICLES[s]["short"])}</span></a>' for s in ORDER_ARTICLES)
    body = ('<p class="lead">Short, plain-English guides to how IPOs work in India. Facts only, with no advice and no tips.</p>'
            f'<section class="card"><div class="lh">{cards}</div></section>')
    hub = shell("learn", "Learn About IPOs in India: Plain-English Guides | IPO Shop", "Learn about IPOs",
                "Plain-English guides to IPOs in India: GMP, how to apply, allotment, subscription, SME IPOs and listing gain.", body,
                crumbs=[("IPO Shop", SITE + "/"), ("Learn", SITE + "/learn/")])
    return [("learn", hub)] + out


def pages(ds, root):
    """(slug, html) for every extra page."""
    return [("ipo-subscription-status", lp_subscription(ds, root)), ("gmp-accuracy", lp_accuracy(ds, root)), ("compare-ipos", lp_compare(ds, root))] + learn_pages()


SLUGS = ["ipo-subscription-status", "gmp-accuracy", "compare-ipos", "learn"] + [f"learn/{s}" for s in ORDER_ARTICLES]
