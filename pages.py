"""IPO Shop: one static, crawlable page per IPO, a hub page at /ipo/, and the sitemap.

The page text holds only stable facts (name, dates, price band, lot size, status), so a page is rewritten
only when one of those changes. Live numbers (subscription, GMP, latest price) are filled in by a small
script in the page, so the repository does not change every 20 minutes."""
import html as H
import json
import pathlib
import shutil

SITE = "https://iposhop.in"
BEACON = ("<!-- Cloudflare Web Analytics --><script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' "
          "data-cf-beacon='{\"token\": \"d53dfd3e4bc94b64b0e2312949a8a61e\"}'></script><!-- End Cloudflare Web Analytics -->")
ICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' rx='24' "
        "fill='%232f6fed'/%3E%3Ctext x='50' y='70' font-size='62' text-anchor='middle'%3E%F0%9F%93%88%3C/text%3E%3C/svg%3E")
STATUS = {"upcoming": ("🚀", "Upcoming"), "open": ("🟢", "Open"), "closing_today": ("⏰", "Closing today"),
          "closed": ("🔒", "Closed"), "listed": ("🎉", "Listed")}
REG = {"kfin": ("KFintech", "https://kosmic.kfintech.com/ipostatus/"),
       "mufg": ("MUFG Intime", "https://linkintime.co.in/initial_offer/public-issues.html"),
       "bigshare": ("Bigshare", "https://ipo.bigshareonline.com/IPO_Status.html"),
       "skyline": ("Skyline", "https://www.skylinerta.com/ipo.php"),
       "cameo": ("Cameo", "https://ipostatus.cameoindia.com")}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def esc(s):
    return H.escape(str(s), quote=True)


def inr(n):
    """1234567.5 -> 12,34,567.5 (Indian digit grouping)."""
    if n is None:
        return "–"
    n = float(n)
    neg, n = n < 0, abs(n)
    whole, _, frac = (f"{n:.2f}".rstrip("0").rstrip(".")).partition(".")
    if len(whole) > 3:
        head, tail, parts = whole[:-3], whole[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return ("-" if neg else "") + whole + ("." + frac if frac else "")


def rs(n):
    return "₹" + inr(n)


def dl(iso):
    """2026-10-21 -> 21 Oct 2026"""
    if not iso:
        return ""
    y, m, d = iso[:10].split("-")
    return f"{int(d)} {MONTHS[int(m) - 1]} {y}"


def band(d):
    lo, hi = d.get("price_low"), d.get("price")
    if lo is not None and hi is not None and lo != hi:
        return f"{rs(lo)} to {rs(hi)}"
    return rs(hi) if hi is not None else ""


def seg_label(d):
    return "SME IPO" if d.get("segment") == "SME" else "Mainboard IPO"


def merge(row, rec):
    """The year-list row, enriched with everything the live feed knows about the same IPO."""
    d = dict(row)
    for k, v in (rec or {}).items():
        if k in ("lot", "registrar", "lead_managers", "size_cr", "boa", "cutoff_time", "retail_max", "rhp_url",
                 "open", "close", "listing", "price", "price_low", "status", "symbol") and v not in (None, "", []):
            d[k] = v
    return d


def reg_key(name):
    t = (name or "").lower()
    for k, pats in (("kfin", ("kfin", "karvy")), ("mufg", ("mufg", "intime")), ("bigshare", ("bigshare",)), ("skyline", ("skyline",)), ("cameo", ("cameo",))):
        if any(x in t for x in pats):
            return k
    return None


def allot_card(d):
    """After bidding closes, the thing most people search for: where to check their allotment."""
    if d.get("status") not in ("closed", "listed"):
        return ""
    k = reg_key(d.get("registrar"))
    regs = [REG[k]] if k else [REG[x] for x in ("kfin", "mufg", "bigshare")]
    who = f"The registrar is {esc(d['registrar'])}." if d.get("registrar") else "The registrar is named in the offer document."
    when = f" Allotment is expected on {dl(d['boa'])}." if d.get("boa") and d.get("status") == "closed" else ""
    btns = "".join(f'<a class="btn" href="{u}" target="_blank" rel="noopener noreferrer">Check on {n} ↗</a>' for n, u in regs)
    return (f'<section class="card"><h2>Check your allotment</h2><p style="margin:0 0 8px">{who}{when} Enter your PAN, application number or demat (DP) ID '
            f"on the registrar's page and pick this IPO. IPO Shop never sees your application details.</p>{btns}</section>")


def story(d):
    """A short plain-English summary: the first thing a reader (and a search engine) sees."""
    n, st, seg = d["name"], d.get("status"), seg_label(d)
    o, c, li, cut = dl(d.get("open")), dl(d.get("close")), dl(d.get("listing")), d.get("cutoff_time")
    if st == "upcoming":
        s = f"{n} IPO is an upcoming {seg}" + (f" that opens on {o} and closes on {c}." if o and c else ".")
    elif st in ("open", "closing_today"):
        s = f"{n} IPO is open for bidding" + (f" until {c}" if c else "") + (f" ({cut})" if cut else "") + "."
    elif st == "closed":
        s = f"{n} IPO closed" + (f" on {c}" if c else "") + (f" and is due to list on {li}." if li else ".")
        if d.get("boa"):
            s += f" Allotment is expected on {dl(d['boa'])}."
    else:
        s = f"{n} was listed" + (f" on {li}" if li else "") + "."
    if d.get("price") is not None:
        if st == "listed":
            s += f" The issue price was {rs(d['price'])}."
            if d.get("listing_close") is not None and d.get("listing_gain_pct") is not None:
                s += f" It closed its first day at {rs(d['listing_close'])} ({d['listing_gain_pct']:+.2f}% from the issue price)."
        else:
            s += f" The price band is {band(d)}."
            if d.get("lot"):
                s += f" The lot size is {inr(d['lot'])} shares, so one lot costs about {rs(d['lot'] * d['price'])} at the top of the band."
    elif st in ("upcoming", "open", "closing_today"):
        s += " The price band has not been announced yet."
    return s


def description(d):
    st, n = d.get("status"), d["name"]
    if st == "closed":
        t = f"{n} IPO ({'SME' if d.get('segment') == 'SME' else 'Mainboard'}) allotment status: bidding closed {dl(d.get('close'))}"
        t += (f", allotment {dl(d['boa'])}" if d.get("boa") else "") + (f", listing {dl(d['listing'])}" if d.get("listing") else "")
        t += ". Registrar link, subscription and GMP."
        return t if len(t) <= 158 else t[:155].rstrip(" ,.") + "..."
    bits = [f"{n} IPO ({'SME' if d.get('segment') == 'SME' else 'Mainboard'})"]
    if st == "listed" and d.get("listing"):
        bits.append(f"listed {dl(d['listing'])}")
    elif d.get("open") and d.get("close"):
        bits.append(f"{dl(d['open'])} to {dl(d['close'])}")
    if d.get("price") is not None and st != "listed":
        bits.append(f"price band {band(d).replace(' to ', '-')}")
    elif d.get("price") is not None:
        bits.append(f"issue price {rs(d['price'])}")
    if d.get("lot") and st != "listed":
        bits.append(f"lot size {inr(d['lot'])}")
    text = ": ".join([bits[0], ", ".join(bits[1:])]) if len(bits) > 1 else bits[0]
    text += ". Dates, subscription, GMP and listing details."
    return text if len(text) <= 158 else text[:155].rstrip(" ,.") + "..."


def lead(names):
    names = [n for n in names if n]
    return ", ".join(names) if len(names) <= 4 else ", ".join(names[:4]) + f" and {len(names) - 4} more"


def facts(d):
    st = d.get("status")
    rows = [("Type", seg_label(d)), ("Exchange", d.get("exchange")), ("Symbol", d.get("symbol"))]
    if st == "listed":
        rows.append(("Issue price", rs(d["price"]) if d.get("price") is not None else None))
        if d.get("listing_close") is not None:
            g = d.get("listing_gain_pct")
            rows.append(("Listing-day close", rs(d["listing_close"]) + (f" ({g:+.2f}%)" if g is not None else "")))
    else:
        rows.append(("Price band", band(d) or "To be announced"))
    if d.get("lot"):
        rows.append(("Lot size", f"{inr(d['lot'])} shares"))
        if d.get("price") is not None and st != "listed":
            rows.append(("One lot costs", rs(d["lot"] * d["price"]) + " at the top of the band"))
    if d.get("size_cr") is not None:
        rows.append(("Issue size", f"₹{inr(d['size_cr'])} Cr"))
    rows += [("Opens", dl(d.get("open"))), ("Closes", dl(d.get("close")) + (f", {d['cutoff_time']}" if d.get("cutoff_time") and d.get("close") else "")),
             ("Allotment", dl(d.get("boa"))), ("Listing date", dl(d.get("listing"))),
             ("Registrar", d.get("registrar")), ("Lead managers", lead(d.get("lead_managers") or []))]
    out = []
    for k, v in rows:
        if v:
            wide = ' style="grid-column:1/-1"' if k == "Lead managers" else ""
            out.append(f"<div{wide}><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>")
    if d.get("rhp_url") and str(d["rhp_url"]).startswith("http"):
        out.append(f'<div><dt>Offer document</dt><dd><a href="{esc(d["rhp_url"])}" target="_blank" rel="noopener noreferrer">Read the RHP ↗</a></dd></div>')
    return "".join(out)


CSS = """
@font-face{font-family:Inter;font-style:normal;font-weight:400 700;font-display:optional;src:url(/fonts/inter-latin.woff2) format("woff2");unicode-range:U+0000-00FF,U+00B7,U+2013-2014,U+2018-201D,U+2022,U+2026,U+2039-203A,U+2212}
@font-face{font-family:Inter;font-style:normal;font-weight:400 700;font-display:swap;src:url(/fonts/inter-rupee.woff2) format("woff2");unicode-range:U+20B9}
:root{--ink:#111735;--muted:#59647f;--line:#e4e8f1;--bg:#f4f6fb;--blue:#2f6fed;--navy:#111735}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--blue)}header{background:#fff;border-bottom:1px solid var(--line)}
.w{max-width:900px;margin:0 auto;padding:0 20px}.bar{display:flex;align-items:center;gap:18px;flex-wrap:wrap;min-height:64px}
.brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:19px;color:var(--ink);text-decoration:none}.brand b{color:var(--blue)}
.mark{width:34px;height:34px;border-radius:10px;background:var(--blue);display:grid;place-items:center;color:#fff;font-size:18px}
nav{display:flex;gap:4px;flex-wrap:wrap;margin-left:auto}nav a{padding:8px 12px;border-radius:10px;color:#3d4a6b;text-decoration:none;font-weight:600;font-size:14.5px}nav a:hover{background:#e8f1ff}
.crumbs{font-size:13.5px;color:var(--muted);padding:18px 0 4px}.crumbs a{color:var(--muted)}
h1{font-size:clamp(26px,4vw,38px);line-height:1.15;letter-spacing:-.03em;margin:6px 0 10px}
.pill{display:inline-block;font-size:13px;font-weight:700;padding:4px 12px;border-radius:99px;background:#e8f1ff;color:#1f55c9;vertical-align:middle;margin-left:8px}
.lead{font-size:17px;color:#2a3558;margin:0 0 20px}
.card{background:#fff;border:1px solid var(--line);border-radius:22px;padding:22px 24px;margin:0 0 16px;box-shadow:0 6px 18px rgba(15,23,54,.05)}
.card h2{font-size:19px;margin:0 0 12px;letter-spacing:-.01em}
dl{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px 24px;margin:0}dt{font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}dd{margin:2px 0 0;font-weight:600}
.live{background:linear-gradient(135deg,#e8f1ff,#e4f7ee);border-color:#d3e3ff}.live dl{grid-template-columns:repeat(auto-fill,minmax(200px,1fr))}
.meter{display:inline-flex;gap:3px;vertical-align:middle;margin-right:6px}.meter i{width:10px;height:10px;border-radius:50%;background:#e4e8f1}.meter i.on{background:#14905d}
.btn{display:inline-block;padding:10px 18px;border-radius:12px;background:var(--navy);color:#fff;font-weight:700;text-decoration:none;margin:4px 8px 0 0}
.btn.alt{background:#fff;color:var(--ink);border:1px solid var(--line)}
.muted{color:var(--muted);font-size:14px}footer{margin-top:36px;background:var(--navy);color:#aab6dd;padding:28px 0;font-size:13.5px;line-height:1.6}
ul.list{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:8px 22px}
ul.list li{padding:6px 0;border-bottom:1px solid var(--line)}ul.list small{color:var(--muted);display:block}
.dm{display:none;margin-left:10px;font-size:12.5px;font-weight:700;color:#2a3558;white-space:nowrap}.dm .meter{margin-right:4px}
@media(max-width:560px){.card{padding:18px}nav{margin-left:0}}
"""


def head(title, desc, url, robots=None, extra=""):
    r = f'<meta name="robots" content="{robots}">\n' if robots else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{url}">
{r}<meta property="og:type" content="article"><meta property="og:site_name" content="IPO Shop"><meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}"><meta property="og:url" content="{url}">
<link rel="preload" href="/fonts/inter-latin.woff2" as="font" type="font/woff2" crossorigin><link rel="icon" href="{ICON}"><style>{CSS}</style>{extra}</head><body>
<header><div class="w bar"><a class="brand" href="/"><span class="mark">📈</span><span>IPO <b>Shop</b></span></a>
<nav aria-label="Main"><a href="/">Home</a><a href="/ipo/">All IPOs</a><a href="/#mainboard">Mainboard</a><a href="/#sme">SME</a><a href="/#calendar">Calendar</a><a href="/#contact">Contact</a></nav></div></header>
"""


FOOT = f"""<footer><div class="w">IPO Shop is an information website. It does not sell, apply for or recommend IPOs, and nothing here is investment advice.
GMP (grey market premium) is unofficial and often differs from the actual listing price. Read the offer document (RHP) and speak to a SEBI-registered advisor before investing.
<br>Dates, prices and subscription come from NSE and BSE. <a href="/#contact" style="color:#c4cdee">Spotted a mistake? Tell us.</a></div></footer>
{BEACON}</body></html>"""

LIVE_JS = """<script>
(function(){var id="__ID__",st="__ST__",box=document.getElementById("live-body");
function n(v,d){return Number(v).toLocaleString("en-IN",{maximumFractionDigits:d==null?2:d})}
function p(v){return(v>=0?"+":"")+Number(v).toFixed(2)+"%"}
function get(u){return fetch(u+"?"+Date.now(),{cache:"no-store"}).then(function(r){return r.json()}).catch(function(){return null})}
Promise.all([get("/ipos.json"),st==="listed"?get("/history.json"):Promise.resolve(null)]).then(function(r){
var a=r[0],b=r[1],live=a&&a.ipos&&a.ipos.filter(function(x){return x.id===id})[0],row=b&&b.rows&&b.rows.filter(function(x){return x.id===id})[0],out=[];
if(live){
 if(live.sub&&live.sub.total!=null){out.push(["Subscription",n(live.sub.total)+"x"+(live.sub.retail!=null?" (retail "+n(live.sub.retail)+"x)":"")]);
  var t=live.sub.total,l=t>=50?5:t>=10?4:t>=3?3:t>=1?2:1,m='<span class="meter" title="Demand '+l+' of 5: how heavily subscribed. Not a rating of the IPO.">';
  for(var k=1;k<=5;k++)m+='<i'+(k<=l?' class="on"':'')+'></i>';
  out.push(["Demand meter",m+"</span>"+l+" of 5"]);}
 if((live.status==="open"||live.status==="closing_today")&&!(live.sub&&live.sub.total!=null))out.push(["Demand meter","Not available yet for this issue"]);
 if(live.gmp!=null)out.push(["GMP (unofficial)","₹"+n(live.gmp)+(live.gmp_pct!=null?" ("+p(live.gmp_pct)+")":"")+(live.gmp_source_latest&&live.gmp_source_latest!=="IPO Shop"?" via "+live.gmp_source_latest.toLowerCase():"")]);
 if(live.gmp!=null&&live.price!=null)out.push(["Estimated listing price","₹"+n(live.price+live.gmp)]);
}
if(row&&row.gain_pct!=null)out.push(["Latest price","₹"+n(row.current)+" ("+p(row.gain_pct)+" since issue)"]);
if(!out.length){box.innerHTML='<p class="muted">No live numbers for this IPO yet. Subscription starts once bidding opens, and GMP appears when a tracker reports one.</p>';return}
box.innerHTML="<dl>"+out.map(function(x){return"<div><dt>"+x[0]+"</dt><dd>"+x[1]+"</dd></div>"}).join("")+"</dl>"+(a&&a.generated_at?'<p class="muted" style="margin:12px 0 0">Data last changed '+new Date(a.generated_at).toLocaleString("en-IN",{day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"})+".</p>":"");
});})();
</script>"""


def render_page(d):
    url = f"{SITE}/ipo/{d['id']}/"
    st = d.get("status") or "listed"
    emo, lab = STATUS.get(st, STATUS["listed"])
    first = f"{d['name']} IPO allotment status, dates, GMP | IPO Shop" if st == "closed" else f"{d['name']} IPO: dates, price band, lot size, GMP | IPO Shop"
    title = next((t for t in (first, f"{d['name']} IPO: dates, price band, GMP | IPO Shop",
                              f"{d['name']} IPO | IPO Shop") if len(t) <= 66), f"{d['name'][:48].rstrip()}... IPO | IPO Shop")
    ld = json.dumps({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "IPO Shop", "item": SITE + "/"},
        {"@type": "ListItem", "position": 2, "name": "All IPOs", "item": SITE + "/ipo/"},
        {"@type": "ListItem", "position": 3, "name": d["name"] + " IPO", "item": url}]}, ensure_ascii=False)
    apply_card = ""
    if st in ("upcoming", "open", "closing_today"):
        apply_card = ('<div class="card"><h2>How to apply</h2><p style="margin:0">Open the IPO section of your broker or bank app, pick this IPO, choose the number of lots '
                      'and enter your UPI ID. Approve the payment request in your UPI app before the cut-off on the last day. The money is only blocked in your account until allotment. '
                      'Retail applications can go up to ₹2 lakh. IPO Shop does not take applications, so you apply through your own broker.</p></div>')
    live_link = f'/#ipo={d["id"]}' if st != "listed" else "/#year"
    body = f"""<main class="w"><div class="crumbs"><a href="/">Home</a> › <a href="/ipo/">All IPOs</a> › {esc(d['name'])}</div>
<h1>{esc(d['name'])} IPO<span class="pill">{emo} {lab}</span></h1>
<p class="lead">{esc(story(d))}</p>
<section class="card"><h2>Key facts</h2><dl>{facts(d)}</dl></section>
<section class="card live"><h2>Live numbers</h2><div id="live-body"><p class="muted">Loading live subscription, GMP and price…</p></div>
<noscript><p class="muted">Turn on JavaScript to see live subscription and GMP.</p></noscript>
<a class="btn" href="{live_link}">Open in the live tracker</a><a class="btn alt" href="/ipo/">All IPOs</a></section>
{allot_card(d)}{apply_card}
<section class="card"><h2>About this page</h2><p class="muted" style="margin:0 0 10px">Facts on this page come from NSE and BSE and are refreshed automatically. GMP is unofficial and comes from public trackers and our own checks. This is information, not advice. See the offer document before you invest.</p>
<p class="muted" style="margin:0"><b>Demand meter:</b> 1 means under 1x subscribed, 2 is 1x to 3x, 3 is 3x to 10x, 4 is 10x to 50x and 5 is 50x or more. It shows how much demand there is. It does not say whether the IPO is a good one.</p></section>
</main>{LIVE_JS.replace("__ID__", d['id']).replace("__ST__", st)}"""
    return head(title, description(d), url, extra=f'<script type="application/ld+json">{ld}</script>') + body + FOOT


HUB_JS = """<script>
(function(){fetch("/ipos.json?"+Date.now(),{cache:"no-store"}).then(function(r){return r.json()}).then(function(a){
var m={};(a.ipos||[]).forEach(function(i){if(i.sub&&i.sub.total!=null)m[i.id]=i.sub.total});
document.querySelectorAll(".dm").forEach(function(el){var t=m[el.getAttribute("data-id")];if(t==null)return;
var l=t>=50?5:t>=10?4:t>=3?3:t>=1?2:1,h='<span class="meter" title="Demand '+l+' of 5: how heavily subscribed. Not a rating of the IPO.">';
for(var k=1;k<=5;k++)h+='<i'+(k<=l?' class="on"':'')+'></i>';el.innerHTML=h+'</span>'+t.toFixed(1)+'x';el.style.display='inline-block'})}).catch(function(){})})();
</script>"""


def render_hub(ds):
    def item(d):
        emo, lab = STATUS.get(d.get("status") or "listed", STATUS["listed"])
        when = dl(d.get("listing")) if d.get("status") == "listed" else (f"{dl(d.get('open'))} to {dl(d.get('close'))}" if d.get("open") and d.get("close") else lab)
        return f'<li><a href="/ipo/{esc(d["id"])}/">{esc(d["name"])}</a><span class="dm" data-id="{esc(d["id"])}"></span><small>{emo} {lab} · {esc(when)}</small></li>'
    key = lambda d: d.get("listing") or d.get("close") or d.get("open") or ""
    live = sorted([d for d in ds if d.get("status") in ("open", "closing_today")], key=lambda d: d.get("close") or "")
    up = sorted([d for d in ds if d.get("status") == "upcoming"], key=lambda d: d.get("open") or "")
    closed = [d for d in ds if d.get("status") == "closed"]
    main = sorted([d for d in ds if d.get("segment") != "SME" and d.get("status") == "listed"], key=key, reverse=True)
    sme = sorted([d for d in ds if d.get("segment") == "SME" and d.get("status") == "listed"], key=key, reverse=True)
    def block(title, lst):
        return f'<section class="card"><h2>{title} ({len(lst)})</h2><ul class="list">{"".join(item(d) for d in lst)}</ul></section>' if lst else ""
    desc = "Every IPO in India: open, upcoming and listed Mainboard and SME IPOs, with dates, price band, lot size, subscription and GMP. Official NSE and BSE data."
    body = (f'<main class="w"><div class="crumbs"><a href="/">Home</a> › All IPOs</div><h1>All IPOs in India</h1>'
            f'<p class="lead">{len(ds)} IPOs with their dates, price band, lot size, subscription, GMP and listing details. Pick any IPO to see its page.</p>'
            '<p class="muted" style="margin:-8px 0 16px">The dots next to an IPO are its <b>demand meter</b>, from 1 to 5, showing how heavily it is subscribed. They appear for IPOs that have live subscription numbers.</p>'
            + block("🟢 Open now", live) + block("🚀 Coming up", up) + block("🔒 Closed, awaiting listing", closed)
            + block("🎉 Listed Mainboard IPOs", main) + block("🎉 Listed SME IPOs", sme) + "</main>")
    return head("All IPOs in India: open, upcoming and listed | IPO Shop", desc, SITE + "/ipo/") + body + HUB_JS + FOOT


def render_404():
    body = ('<main class="w"><h1 style="margin-top:40px">That page is not here</h1>'
            '<p class="lead">The address may be old or mistyped. Here are some good places to go next.</p>'
            '<section class="card"><a class="btn" href="/">Home</a><a class="btn alt" href="/ipo/">All IPOs</a>'
            '<a class="btn alt" href="/#calendar">Calendar</a><a class="btn alt" href="/#contact">Contact us</a></section></main>')
    return head("Page not found | IPO Shop", "This page could not be found. Browse every IPO in India on IPO Shop.", SITE + "/404.html", robots="noindex,follow") + body + FOOT


def write_sitemap(root, ds):
    urls = [SITE + "/", SITE + "/ipo/"] + [f"{SITE}/ipo/{d['id']}/" for d in sorted(ds, key=lambda d: d["id"])]
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + "".join(f"  <url><loc>{esc(u)}</loc></url>\n" for u in urls) + "</urlset>\n")
    p = root / "sitemap.xml"
    if not p.exists() or p.read_text(encoding="utf-8") != xml:
        p.write_text(xml, encoding="utf-8", newline="\n")
        return True
    return False


def write_all(hist_rows, ipos, root, log=print):
    root = pathlib.Path(root)
    recs = {i["id"]: i for i in ipos}
    ds = [merge(r, recs.get(r["id"])) for r in hist_rows if r.get("id") and r.get("name") and not r.get("unconfirmed")]
    base = root / "ipo"
    base.mkdir(exist_ok=True)
    wrote = same = 0
    keep = set()
    def save(path, text):
        nonlocal wrote, same
        if path.exists() and path.read_text(encoding="utf-8") == text:
            same += 1
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        wrote += 1
    for d in ds:
        keep.add(d["id"])
        save(base / d["id"] / "index.html", render_page(d))
    save(base / "index.html", render_hub(ds))
    save(root / "404.html", render_404())
    removed = 0
    for sub in base.iterdir():
        if sub.is_dir() and sub.name not in keep:
            shutil.rmtree(sub)
            removed += 1
    sm = write_sitemap(root, ds)
    log(f"IPO pages: {len(ds)} pages ({wrote} written, {same} unchanged, {removed} removed); sitemap {'updated' if sm else 'unchanged'}")
    return len(ds)
