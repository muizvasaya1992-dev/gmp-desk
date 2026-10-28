"""IPO Shop: one static, crawlable page per IPO, a hub page at /ipo/, and the sitemap.

The page text holds only stable facts (name, dates, price band, lot size, status), so a page is rewritten
only when one of those changes. Live numbers (subscription, GMP, latest price) are filled in by a small
script in the page, so the repository does not change every 20 minutes."""
import html as H
import json
import pathlib
import re
import shutil

SITE = "https://iposhop.in"
WA_CHANNEL = "https://whatsapp.com/channel/0029VbEHHkoCcW4ped17YH0r"
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
                 "open", "close", "listing", "price", "price_low", "status", "symbol", "sub", "gmp", "gmp_pct", "listing_price", "reservation") and v not in (None, "", []):
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
.btn.wa{background:#0e7a4b}
.mark{background:none;overflow:visible;box-shadow:0 4px 14px rgba(47,111,237,.3)}.mark svg{width:100%;height:100%;display:block}
.muted{color:var(--muted);font-size:14px}footer{margin-top:36px;background:var(--navy);color:#aab6dd;padding:28px 0;font-size:13.5px;line-height:1.6}
ul.list{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:8px 22px}
ul.list li{padding:6px 0;border-bottom:1px solid var(--line)}ul.list small{color:var(--muted);display:block}
.dm{font-size:12.5px;font-weight:700;color:#2a3558;margin-left:auto}.dm .meter{margin-right:4px}
.hub [hidden]{display:none!important}
.tick{display:flex;align-items:stretch;height:40px;overflow:hidden;background:#111735;border-bottom:1px solid rgba(255,255,255,.08);visibility:hidden}.tick.on{visibility:visible}.tick.off{display:none}
.tk-live{flex:none;display:flex;align-items:center;gap:8px;padding:0 16px;font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;background:#070b20;color:#fff;position:relative;z-index:1}
.tk-live i{width:8px;height:8px;border-radius:50%;background:#2fc58a;animation:tkp 1.8s infinite}@keyframes tkp{0%{box-shadow:0 0 0 0 rgba(47,197,138,.6)}70%{box-shadow:0 0 0 7px rgba(47,197,138,0)}100%{box-shadow:0 0 0 0 rgba(47,197,138,0)}}
.tk-view{flex:1;min-width:0;overflow:hidden;-webkit-mask-image:linear-gradient(90deg,transparent,#000 22px,#000 calc(100% - 22px),transparent);mask-image:linear-gradient(90deg,transparent,#000 22px,#000 calc(100% - 22px),transparent)}
.tk-track{display:inline-flex;height:100%;white-space:nowrap;animation:tkm var(--dur,60s) linear infinite;will-change:transform}
.tick:hover .tk-track,.tick:focus-within .tk-track{animation-play-state:paused}@keyframes tkm{from{transform:translateX(0)}to{transform:translateX(-50%)}}
.tk-g{display:inline-flex;align-items:center}
.tk-lane{display:inline-flex;align-items:center;margin:0 6px 0 14px;font-size:10.5px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;padding:3px 10px;border-radius:99px;background:#2f6fed;color:#fff}.tk-lane.l-open{background:#0f7b4f}.tk-lane.l-up{background:#2f6fed}
.tk-item{display:inline-flex;align-items:center;gap:10px;padding:0 20px;font-size:13px;color:#b9c3e6;border-right:1px solid rgba(255,255,255,.1);text-decoration:none;height:100%}.tk-item:hover{text-decoration:none;background:rgba(255,255,255,.06)}.tk-item:focus-visible{outline:2px solid #fff;outline-offset:-2px}
.tk-item b{color:#fff;font-weight:600}.tk-item .p{color:#7be0b4;font-weight:600}.tk-chip{font-size:11px;font-weight:600;padding:2px 9px;border-radius:99px;background:rgba(255,255,255,.12);color:#e6ecff}
@media(prefers-reduced-motion:reduce){.tk-track{animation:none}.tk-view{overflow-x:auto;-webkit-mask-image:none;mask-image:none}.tk-live i{animation:none}}
@media(max-width:640px){.tk-live{padding:0 12px}.tk-live span{display:none}.tk-item{padding:0 14px;font-size:12.5px}}
.hub .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0 14px}
.hub .stat{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px 16px;text-decoration:none;color:var(--ink)}.hub .stat:hover{border-color:#b9c6ee}
.hub .stat b{display:block;font-size:28px;line-height:1.1}.hub .stat span{color:var(--muted);font-size:13px}
.hub .quick{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 18px}.hub .qlink{padding:8px 14px;border-radius:999px;background:#eaf0ff;color:#1f4fc4;font-weight:600;font-size:14px;text-decoration:none}.hub .qlink:hover{background:#dbe6ff}
.hub .tools{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:6px}
.hub input[type=search]{flex:1 1 280px;padding:13px 16px;border:1px solid var(--line);border-radius:14px;font:inherit;background:#fff;color:var(--ink)}
.hub .fchips{display:flex;gap:8px}.hub .fchip{border:1px solid var(--line);background:#fff;border-radius:999px;padding:11px 16px;font:inherit;font-weight:600;color:var(--ink);cursor:pointer;min-height:44px}
.hub .fchip[aria-pressed=true]{background:#111735;border-color:#111735;color:#fff}
ul.hgrid{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:12px}
li.hc{position:relative;display:flex;flex-direction:column;gap:9px;background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px;transition:box-shadow .15s,transform .15s}
li.hc:hover{box-shadow:0 8px 22px rgba(17,23,53,.10);transform:translateY(-2px)}li.hc:focus-within{outline:3px solid #2f6fed;outline-offset:2px}.hc-main:focus,.hc-main:focus-visible{outline:none}
.hc-main{display:flex;gap:12px;align-items:center;text-decoration:none;color:var(--ink)}.hc-main::after{content:"";position:absolute;inset:0;border-radius:16px}
.av{width:42px;height:42px;border-radius:50%;display:grid;place-items:center;color:#fff;font-weight:700;font-size:14px;flex:none}
.hc-t b{display:block;font-size:16px;line-height:1.25}.hc-t small{display:block;color:var(--muted);font-size:13px;margin-top:2px}
.hc-s{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:4px 10px;min-height:34px;border-top:1px dashed var(--line);padding-top:8px;font-size:13.5px;color:#2a3558}
.hubsec{scroll-margin-top:14px}details.more{margin-top:14px}details.more summary{cursor:pointer;font-weight:700;color:#1f4fc4;padding:10px 0;margin-bottom:8px}
@media(max-width:700px){.hub .stats{grid-template-columns:repeat(2,1fr)}ul.hgrid{grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){li.hc:hover{transform:none}}
.w.wide,body:has(main.wide) .w{max-width:1100px}.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.dist{display:flex;height:14px;border-radius:7px;overflow:hidden;margin:10px 0 8px;background:#eef0f6}.dist i{display:block;height:100%}.distkey{list-style:none;margin:0 0 4px;padding:0;display:flex;flex-wrap:wrap;gap:4px 16px;font-size:13px;color:var(--muted)}.distkey span{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px}.distkey b{color:var(--ink,#111735)}
.card h3{font-size:16px;margin:16px 0 4px}.card h3:first-of-type{margin-top:0}.card p{margin:0 0 8px}
ol.tl{list-style:none;margin:0;padding:0;display:grid;gap:10px}ol.tl li{display:flex;justify-content:space-between;gap:12px;padding:10px 14px;background:#f4f6fb;border-radius:12px}
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:14px;background:#fff;margin:0 0 6px}
table.t{border-collapse:collapse;width:100%;font-size:14.5px}table.t th{background:#f4f6fb;text-align:left;font-size:12px;letter-spacing:.05em;text-transform:uppercase;color:#3d4a6b}
table.t th,table.t td{padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap;vertical-align:top}table.t td:first-child{white-space:normal;min-width:190px;font-weight:600}
table.t small{display:block;color:var(--muted);font-weight:400}table.t tr:last-child td{border-bottom:0}
.links a{display:inline-block;margin:0 14px 6px 0}.swipe{display:none;margin:0 0 6px!important}@media(max-width:700px){.swipe{display:block}}
@media(max-width:560px){.card{padding:18px}nav{margin-left:0}}
"""


TICK = r"""<div class="tick" id="tick" role="region" aria-label="Live IPO ticker"><div class="tk-live"><i></i><span>Live</span></div><div class="tk-view"><div class="tk-track" id="tktrack"></div></div></div>
<noscript><style>.tick{display:none}</style></noscript>
<script>(function(){var el=document.getElementById("tick");if(!el)return;
function esc(t){return String(t).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function dd(iso){return new Date(iso+"T12:00:00Z").toLocaleDateString("en-GB",{day:"numeric",month:"short",timeZone:"UTC"})}
function n(v){return Number(v).toLocaleString("en-IN",{maximumFractionDigits:2})}
function band(i){if(i.price==null)return"";return i.price_low!=null&&i.price_low!==i.price?"\u20b9"+n(i.price_low)+" \u2013 "+n(i.price):"\u20b9"+n(i.price)}
fetch("/ipos.json?"+Date.now(),{cache:"no-store"}).then(function(r){return r.json()}).then(function(a){
  var L=(a.ipos||[]).filter(function(i){return !i.unconfirmed});
  var open=L.filter(function(i){return i.status==="open"||i.status==="closing_today"}).sort(function(x,y){return (x.close||"").localeCompare(y.close||"")||x.name.localeCompare(y.name)}).slice(0,10);
  var up=L.filter(function(i){return i.status==="upcoming"}).sort(function(x,y){return (x.open||"").localeCompare(y.open||"")||x.name.localeCompare(y.name)}).slice(0,6);
  if(!open.length&&!up.length){el.classList.add("off");return}
  function item(i,txt){var b=band(i);return '<a class="tk-item" href="/ipo/'+esc(i.id)+'/"><b>'+esc(i.name)+'</b><span>'+txt+'</span>'+(b?'<span class="p">'+b+'</span>':'')+'<span class="tk-chip">'+(i.segment==="SME"?"SME":"Mainboard")+'</span></a>'}
  var h="";
  if(open.length)h+='<span class="tk-lane l-open">Open now</span>'+open.map(function(i){return item(i,i.status==="closing_today"?"Closes today":"Closes "+dd(i.close))}).join("");
  if(up.length)h+='<span class="tk-lane l-up">Upcoming</span>'+up.map(function(i){return item(i,i.open?"Opens "+dd(i.open):"Soon")}).join("");
  var track=document.getElementById("tktrack");track.innerHTML='<span class="tk-g">'+h+'</span>';
  var w=track.firstElementChild.getBoundingClientRect().width||1,vw=(track.parentNode.clientWidth)||1200,k=Math.max(1,Math.ceil(vw*1.1/w)),G=new Array(k+1).join(h),
      dup=G.replace(/<a class="tk-item" href="[^"]*"/g,'<span class="tk-item" aria-hidden="true"').replace(/<\/a>/g,"</span>");
  track.innerHTML='<span class="tk-g">'+G+'</span><span class="tk-g" aria-hidden="true">'+dup+'</span>';
  track.style.setProperty("--dur",Math.max(30,Math.round(w*k/70))+"s");el.classList.add("on")}).catch(function(){el.classList.add("off")})})();
</script>"""

def head(title, desc, url, robots=None, extra="", tick=False):
    r = f'<meta name="robots" content="{robots}">\n' if robots else ""
    return f"""<!doctype html><html lang="en-IN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{url}">
{r}<meta property="og:type" content="article"><meta property="og:site_name" content="IPO Shop"><meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}"><meta property="og:url" content="{url}">
<meta property="og:image" content="https://iposhop.in/brand/og-image.jpg"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630"><meta property="og:image:alt" content="IPO Shop: details of every IPO in India"><meta name="twitter:image" content="https://iposhop.in/brand/og-image.jpg"><meta name="twitter:card" content="summary_large_image">
<link rel="preload" href="/fonts/inter-latin.woff2" as="font" type="font/woff2" crossorigin><link rel="icon" type="image/svg+xml" href="/brand/favicon.svg"><link rel="icon" type="image/png" sizes="192x192" href="/brand/icon-192.png"><link rel="apple-touch-icon" href="/brand/apple-touch-icon.png"><style>{CSS}</style>{extra}</head><body>
<header><div class="w bar"><a class="brand" href="/"><span class="mark" aria-hidden="true"><svg viewBox="0 0 96 96" focusable="false"><defs><linearGradient id="mg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4b86f5"/><stop offset="1" stop-color="#1f4fc4"/></linearGradient></defs><rect width="96" height="96" rx="22" fill="url(#mg)"/><path d="M34 38v-6a14 14 0 0 1 28 0v6" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round"/><path d="M24 38h48a3 3 0 0 1 3 3.3l-3.3 33a6 6 0 0 1-6 5.4H30.3a6 6 0 0 1-6-5.4L21 41.3A3 3 0 0 1 24 38z" fill="#fff"/><path d="M34 66l10-10 7 6 12-14" fill="none" stroke="#0f7b4f" stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M54 48h9v9" fill="none" stroke="#0f7b4f" stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span>IPO <b>Shop</b></span></a>
<nav aria-label="Main"><a href="/">Home</a><a href="/ipo/">All IPOs</a><a href="/#mainboard">Mainboard</a><a href="/#sme">SME</a><a href="/#calendar">Calendar</a><a href="/#contact">Contact</a></nav></div></header>{TICK if tick else ""}
"""


FOOT = f"""<footer><div class="w">IPO Shop is an information website. It does not sell, apply for or recommend IPOs, and nothing here is investment advice.
GMP (grey market premium) is unofficial and often differs from the actual listing price. Read the offer document (RHP) and speak to a SEBI-registered advisor before investing.
<br>Browse: <a href="/open-ipos/" style="color:#c4cdee">Open IPOs</a> · <a href="/upcoming-ipos/" style="color:#c4cdee">Upcoming IPOs</a> · <a href="/ipo-gmp-today/" style="color:#c4cdee">IPO GMP today</a> · <a href="/ipo-allotment-status/" style="color:#c4cdee">Allotment status</a> · <a href="/sme-ipos/" style="color:#c4cdee">SME IPOs</a> · <a href="/mainboard-ipos/" style="color:#c4cdee">Mainboard IPOs</a> · <a href="/ipo-calendar/" style="color:#c4cdee">IPO calendar</a> · <a href="/listed-ipos-2026/" style="color:#c4cdee">Listed IPOs 2026</a>
<br>Dates, prices and subscription come from NSE and BSE. Some GMP and SME subscription figures also come from <a href="https://ipogyani.com/" target="_blank" rel="noopener" style="color:#c4cdee">IPOGyani</a> and <a href="https://www.investorgain.com/" target="_blank" rel="noopener" style="color:#c4cdee">InvestorGain</a>, with their permission. <a href="/#contact" style="color:#c4cdee">Spotted a mistake? Tell us.</a> · <a href="{WA_CHANNEL}" target="_blank" rel="noopener" style="color:#c4cdee">WhatsApp channel</a></div></footer>
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


def wa_share(d):
    """A ready-to-send WhatsApp message about this IPO (facts only), as a wa.me link."""
    from urllib.parse import quote
    o, c = dl(d.get("open")), dl(d.get("close"))
    when = f"Bidding {o} to {c}" if o and c else ""
    bits = [b for b in (when, band(d), f"Lot {d['lot']}" if d.get("lot") is not None else "") if b]
    text = "\n".join([f"{d['name']} IPO", " · ".join(bits), f"Details: {SITE}/ipo/{d['id']}/"])
    return "https://wa.me/?text=" + quote(text, safe="")


def timeline(d):
    ev = [(k, dl(d.get(f))) for k, f in (("Opens", "open"), ("Closes", "close"), ("Allotment", "boa"), ("Listing", "listing")) if d.get(f)]
    if len(ev) < 2:
        return ""
    return (f'<section class="card"><h2>{esc(d["name"])} IPO timeline</h2><ol class="tl">'
            + "".join(f"<li><b>{k}</b><span>{esc(v)}</span></li>" for k, v in ev) + "</ol></section>")


def faq(d):
    n, st = d["name"], d.get("status")
    qa = []
    if st == "listed":
        if d.get("price") is not None:
            qa.append((f"What was the {n} IPO issue price?", f"The issue price of {n} was {rs(d['price'])} per share."))
    else:
        qa.append((f"What is the {n} IPO price band?", f"The price band is {band(d)}." if band(d) else "The price band has not been announced yet. It is usually announced a few days before the IPO opens."))
    if d.get("lot"):
        a = f"The lot size is {inr(d['lot'])} shares."
        if d.get("price") is not None:
            a += f" At {rs(d['price'])} per share, one lot costs about {rs(d['lot'] * d['price'])}."
        qa.append((f"What is the {n} IPO lot size?", a))
    if d.get("open") and d.get("close"):
        qa.append((f"When does the {n} IPO open and close?", f"The {n} IPO opens on {dl(d['open'])} and closes on {dl(d['close'])}" + (f" at {d['cutoff_time']}" if d.get("cutoff_time") else "") + "."))
    if d.get("boa") or d.get("listing"):
        bits = ([f"allotment is expected on {dl(d['boa'])}"] if d.get("boa") else []) + ([f"the shares are due to list on {dl(d['listing'])}"] if d.get("listing") else [])
        qa.append((f"When is the {n} IPO allotment and listing date?", "For this IPO, " + " and ".join(bits) + "."))
    if st in ("closed", "listed") and d.get("registrar"):
        qa.append((f"How do I check {n} IPO allotment status?", f"{d['registrar']} is the registrar. Open its IPO allotment page, choose {n}, and enter your PAN, application number or demat (DP) ID."))
    if not qa:
        return ""
    return (f'<section class="card"><h2>{esc(n)} IPO: common questions</h2>'
            + "".join(f"<h3>{esc(q)}</h3><p>{esc(a)}</p>" for q, a in qa) + "</section>")


def near_block(near):
    if not near:
        return ""
    def li(d):
        emo, lab = STATUS.get(d.get("status") or "listed", STATUS["listed"])
        return f'<li><a href="/ipo/{esc(d["id"])}/">{esc(d["name"])} IPO</a><small>{emo} {lab}</small></li>'
    return f'<section class="card"><h2>Other IPOs around the same time</h2><ul class="list">{"".join(li(d) for d in near)}</ul></section>'


BUCKETS = [("Fell 10%+", None, -10, "#e5484d"), ("Down 0 to 10%", -10, 0, "#f5a3a6"), ("Up 0 to 10%", 0, 10, "#9ad7b3"),
           ("Up 10 to 30%", 10, 30, "#3fb97a"), ("Up 30%+", 30, None, "#12805c")]


def base_stats(rows):
    """Plain history: how this year's listed IPOs closed on day one, split by SME / mainboard. No forecast, no score."""
    import statistics
    out = {}
    for seg in ("SME", "MAIN"):
        r = sorted([x for x in rows if (x.get("segment") or "MAIN") == seg and x.get("listing_gain_pct") is not None and x.get("listing")],
                   key=lambda x: x["listing"])
        if len(r) < 15:
            continue
        def pack(g):
            n = len(g)
            return {"n": n, "up": round(100 * sum(1 for v in g if v > 0) / n), "med": round(statistics.median(g), 1),
                    "deep": round(100 * sum(1 for v in g if v <= -10) / n),
                    "b": [round(100 * sum(1 for v in g if (lo is None or v >= lo) and (hi is None or v < hi)) / n) for _, lo, hi, _c in BUCKETS]}
        allg = [x["listing_gain_pct"] for x in r]
        out[seg] = {"all": pack(allg), "recent": pack(allg[-30:]), "first": r[0]["listing"], "last": r[-1]["listing"]}
    return out


def mon(iso):
    import datetime as _dt
    return _dt.date.fromisoformat(iso[:10]).strftime("%B %Y")


def base_card(d, stats):
    """Shown on IPOs that have not listed yet. Most useful where no GMP exists, so the wording says what is missing."""
    if (d.get("status") or "listed") == "listed" or not stats:
        return ""
    seg = d.get("segment") or "MAIN"
    b = stats.get(seg)
    if not b:
        return ""
    name = "SME" if seg == "SME" else "mainboard"
    a, rc = b["all"], b["recent"]
    miss = ""
    if d.get("gmp") is None:
        miss = f"<p>No grey-market (GMP) quote has been reported for {esc(d['name'])} yet, and this is common for {name} issues. Here is what actually happened to {name} IPOs this year instead.</p>"
    bar = "".join(f'<i style="width:{v}%;background:{c}" title="{esc(l)}: {v}%"></i>' for (l, _lo, _hi, c), v in zip(BUCKETS, a["b"]) if v)
    leg = "".join(f'<li><span style="background:{c}"></span>{esc(l)} <b>{v}%</b></li>' for (l, _lo, _hi, c), v in zip(BUCKETS, a["b"]))
    return f"""<section class="card"><h2>How {name} IPOs have listed this year</h2>{miss}
<p>Across <b>{a['n']}</b> {name} IPOs that listed between {mon(b['first'])} and {mon(b['last'])}, <b>{a['up']}%</b> closed their first day above the issue price. The middle result was <b>{a['med']:+.1f}%</b>, and <b>{a['deep']}%</b> closed 10% or more below it.</p>
<div class="dist" role="img" aria-label="Share of {name} IPOs by first-day result">{bar}</div><ul class="distkey">{leg}</ul>
<p class="muted" style="margin:10px 0 0">In the last {rc['n']} {name} listings, {rc['up']}% closed up and the middle result was {rc['med']:+.1f}%. This is past data about other companies. It does not predict how {esc(d['name'])} will list, and it is not advice or a rating.</p></section>"""


def render_page(d, near=(), base=None):
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
{base_card(d, base)}{timeline(d)}{faq(d)}{near_block(near)}
<section class="card"><h2>Share or follow</h2><a class="btn wa" href="{wa_share(d)}" target="_blank" rel="noopener">Share this IPO on WhatsApp ↗</a><a class="btn alt" href="{WA_CHANNEL}" target="_blank" rel="noopener">Follow our WhatsApp channel ↗</a></section>
<section class="card"><h2>About this page</h2><p class="muted" style="margin:0 0 10px">Facts on this page come from NSE and BSE and are refreshed automatically. GMP is unofficial and comes from public trackers and our own checks. This is information, not advice. See the offer document before you invest.</p>
<p class="muted" style="margin:0"><b>Demand meter:</b> 1 means under 1x subscribed, 2 is 1x to 3x, 3 is 3x to 10x, 4 is 10x to 50x and 5 is 50x or more. It shows how much demand there is. It does not say whether the IPO is a good one.</p></section>
</main>{LIVE_JS.replace("__ID__", d['id']).replace("__ST__", st)}"""
    return head(title, description(d), url, extra=f'<script type="application/ld+json">{ld}</script>', tick=True) + body + FOOT


HUB_JS = r"""<script>
(function(){
var q=document.getElementById("hq"),chips=[].slice.call(document.querySelectorAll(".fchip")),items=[].slice.call(document.querySelectorAll("li.hc")),secs=[].slice.call(document.querySelectorAll("[data-sec]")),det=[].slice.call(document.querySelectorAll("details.more")),cnt=document.getElementById("hcount"),none=document.getElementById("hnone"),seg="all",was=det.map(function(d){return d.open});
function apply(){
  var t=q.value.trim().toLowerCase(),act=!!t||seg!=="all",shown=0;
  items.forEach(function(li){var ok=(!t||li.getAttribute("data-name").indexOf(t)>-1)&&(seg==="all"||li.getAttribute("data-seg")===seg);li.hidden=!ok;if(ok)shown++});
  det.forEach(function(d,i){d.open=act?true:was[i]});
  secs.forEach(function(s){s.hidden=!s.querySelector("li.hc:not([hidden])")});
  cnt.textContent=act?("Showing "+shown+" of "+items.length+" IPOs"):(items.length+" IPOs");
  none.hidden=shown>0}
q.addEventListener("input",apply);
chips.forEach(function(c){c.addEventListener("click",function(){seg=c.getAttribute("data-f");chips.forEach(function(x){x.setAttribute("aria-pressed",x===c?"true":"false")});apply()})});
fetch("/ipos.json?"+Date.now(),{cache:"no-store"}).then(function(r){return r.json()}).then(function(a){
  var m={};(a.ipos||[]).forEach(function(i){m[i.id]=i});
  document.querySelectorAll(".dm").forEach(function(el){
    var i=m[el.getAttribute("data-id")];if(!i)return;var h="",t=i.sub&&i.sub.total!=null?i.sub.total:null;
    if(t!=null){var l=t>=50?5:t>=10?4:t>=3?3:t>=1?2:1,x='<span class="meter" title="Demand '+l+' of 5: how heavily subscribed. Not a rating of the IPO.">';for(var k=1;k<=5;k++)x+='<i'+(k<=l?' class="on"':'')+'></i>';h+=x+'</span>'+t.toFixed(1)+'x subscribed'}
    if(i.gmp!=null)h+=(h?' \u00b7 ':'')+'<span title="GMP is unofficial">GMP '+(i.gmp<0?'\u2212':'')+'\u20b9'+Math.abs(i.gmp).toLocaleString("en-IN")+'</span>';
    el.innerHTML=h})}).catch(function(){})})();
</script>"""

AVC = ("#2f6fed", "#0f7b4f", "#20306b", "#9a5b00", "#7b5cd6", "#0b6f7c", "#b83b34", "#3d4a6b")


def initials(name):
    w = re.findall(r"[A-Za-z0-9]+", name)
    t = (w[0][0] + w[1][0]) if len(w) > 1 else (w[0][:2] if w else "IP")
    return t.upper()


def render_hub(ds):
    def card(d):
        st = d.get("status") or "listed"
        emo, lab = STATUS.get(st, STATUS["listed"])
        seg = "SME" if d.get("segment") == "SME" else "MAIN"
        kind = "SME" if seg == "SME" else "Mainboard"
        if st == "listed":
            when = f"Listed {dl(d.get('listing'))}" if d.get("listing") else "Listed"
            g = d.get("listing_gain_pct")
            foot = (f"Issue price {rs(d['price'])}" if d.get("price") is not None else "") + (f' · <b>{"−" if g < 0 else "+"}{abs(g):.1f}%</b> on day one' if g is not None else "")
        elif st == "closed":
            when = f"Closed {dl(d.get('close'))}" if d.get("close") else "Closed"
            foot = " · ".join(x for x in ((f"Allotment {dsh(d['boa'])}" if d.get("boa") else ""), (f"Listing {dsh(d['listing'])}" if d.get("listing") else "")) if x)
        else:
            when = f"{dsh(d.get('open'))} to {dl(d.get('close'))}" if d.get("open") and d.get("close") else lab
            foot = " · ".join(x for x in ((band(d) or ""), (f"Lot {inr(d['lot'])}" if d.get("lot") else "")) if x)
        col = AVC[sum(map(ord, d["name"])) % len(AVC)]
        return (f'<li class="hc" data-name="{esc(d["name"].lower())}" data-seg="{seg}"><a class="hc-main" href="/ipo/{esc(d["id"])}/">'
                f'<span class="av" style="background:{col}" aria-hidden="true">{esc(initials(d["name"]))}</span>'
                f'<span class="hc-t"><b>{esc(d["name"])}</b><small>{kind} · {emo} {lab} · {esc(when)}</small></span></a>'
                f'<div class="hc-s"><span>{foot}</span><span class="dm" data-id="{esc(d["id"])}"></span></div></li>')
    key = lambda d: (d.get("listing") or d.get("close") or d.get("open") or "", d["name"])
    live = sorted([d for d in ds if d.get("status") in ("open", "closing_today")], key=lambda d: (d.get("close") or "", d["name"]))
    up = sorted([d for d in ds if d.get("status") == "upcoming"], key=lambda d: (d.get("open") or "", d["name"]))
    closed = sorted([d for d in ds if d.get("status") == "closed"], key=lambda d: (d.get("boa") or d.get("close") or "", d["name"]))
    listed = sorted([d for d in ds if d.get("status") == "listed"], key=key, reverse=True)

    def sec(sid, title, lst, more_after=None, more_label=""):
        if not lst:
            return ""
        shown, rest = (lst, []) if more_after is None else (lst[:more_after], lst[more_after:])
        h = f'<section class="card hubsec" id="{sid}" data-sec><h2>{title} ({len(lst)})</h2><ul class="hgrid">{"".join(card(d) for d in shown)}</ul>'
        if rest:
            h += f'<details class="more"><summary>{more_label} ({len(rest)} more)</summary><ul class="hgrid">{"".join(card(d) for d in rest)}</ul></details>'
        return h + "</section>"
    stat = lambda href, n, t: (f'<a class="stat" href="{href}">' if n else '<div class="stat">') + f'<b>{n}</b><span>{t}</span>' + ('</a>' if n else '</div>')
    quick = "".join(f'<a class="qlink" href="{u}">{esc(t)}</a>' for u, t in PAGE_LINKS if u != "/ipo/")
    desc = "Every IPO in India: open, upcoming and listed Mainboard and SME IPOs, with dates, price band, lot size, subscription and GMP. Official NSE and BSE data."
    ld = json.dumps({"@context": "https://schema.org", "@graph": [
        {"@type": "BreadcrumbList", "itemListElement": [{"@type": "ListItem", "position": 1, "name": "IPO Shop", "item": SITE + "/"}, {"@type": "ListItem", "position": 2, "name": "All IPOs", "item": SITE + "/ipo/"}]},
        {"@type": "ItemList", "name": "All IPOs in India", "itemListElement": [{"@type": "ListItem", "position": k + 1, "name": d["name"] + " IPO", "url": f"{SITE}/ipo/{d['id']}/"} for k, d in enumerate((live + up + closed)[:40])]}]}, ensure_ascii=False)
    body = (f'<main class="w wide hub"><div class="crumbs"><a href="/">Home</a> › All IPOs</div><h1>All IPOs in India</h1>'
            f'<p class="lead">{len(ds)} IPOs with their dates, price band, lot size, subscription, GMP and listing details. Search, filter, or pick any IPO to open its page.</p>'
            '<div class="tools"><label class="sr" for="hq">Search IPOs by name</label><input id="hq" type="search" placeholder="Search any IPO…" autocomplete="off">'
            '<div class="fchips" role="group" aria-label="Filter by type"><button class="fchip" type="button" data-f="all" aria-pressed="true">All</button>'
            '<button class="fchip" type="button" data-f="MAIN" aria-pressed="false">Mainboard</button><button class="fchip" type="button" data-f="SME" aria-pressed="false">SME</button></div></div>'
            f'<p class="muted" id="hcount" role="status" aria-live="polite">{len(ds)} IPOs</p>'
            '<p class="muted" id="hnone" hidden>No IPO matches. Try a shorter name.</p>'
            '<div class="stats">' + stat("#open", len(live), "Open now") + stat("#soon", len(up), "Coming up") + stat("#closed", len(closed), "Closed, awaiting listing") + stat("#listed", len(listed), "Listed so far") + "</div>"
            f'<nav class="quick" aria-label="Browse IPO pages">{quick}</nav>'
            + sec("open", "🟢 Open now", live) + sec("soon", "🚀 Coming up", up) + sec("closed", "🔒 Closed, awaiting listing", closed)
            + sec("listed", "🎉 Listed IPOs", listed, 30, "Show all listed IPOs")
            + '<p class="muted">The dots show the <b>demand meter</b> from 1 to 5: how heavily an IPO is subscribed, not a rating. GMP is unofficial. Facts only, not investment advice.</p></main>')
    return head("All IPOs in India: open, upcoming and listed | IPO Shop", desc, SITE + "/ipo/", extra=f'<script type="application/ld+json">{ld}</script>', tick=True) + body + HUB_JS + FOOT


def render_404():
    body = ('<main class="w"><h1 style="margin-top:40px">That page is not here</h1>'
            '<p class="lead">The address may be old or mistyped. Here are some good places to go next.</p>'
            '<section class="card"><a class="btn" href="/">Home</a><a class="btn alt" href="/ipo/">All IPOs</a>'
            '<a class="btn alt" href="/#calendar">Calendar</a><a class="btn alt" href="/#contact">Contact us</a></section></main>')
    return head("Page not found | IPO Shop", "This page could not be found. Browse every IPO in India on IPO Shop.", SITE + "/404.html", robots="noindex,follow") + body + FOOT


ALERT_JS = r"""<script>
(function(){
var CH="__CH__", box=document.getElementById("msg"), note=document.getElementById("stamp"), mode=null;
function n(v,d){return Number(v).toLocaleString("en-IN",{maximumFractionDigits:d==null?2:d})}
function ist(off){return new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Kolkata"}).format(new Date(Date.now()+off*864e5))}
function add(iso,k){var a=iso.split("-").map(Number);return new Date(Date.UTC(a[0],a[1]-1,a[2]+k)).toISOString().slice(0,10)}
function dd(iso){return new Date(iso+"T12:00:00Z").toLocaleDateString("en-GB",{day:"numeric",month:"short",timeZone:"UTC"})}
function wd(iso){return new Date(iso+"T12:00:00Z").toLocaleDateString("en-GB",{weekday:"short",day:"numeric",month:"short",timeZone:"UTC"})}
function money(i){if(i.price==null)return"";var lo=i.price_low,hi=i.price;return lo!=null&&lo!==hi?"\u20b9"+n(lo)+"\u2013"+n(hi):"\u20b9"+n(hi)}
function seg(i){return i.segment==="SME"?" (SME)":""}
function line(i,extra,live){
  var bits=[money(i)];if(i.lot!=null)bits.push("Lot "+n(i.lot));if(extra)bits.push(extra);
  if(live){if(i.sub&&i.sub.total!=null)bits.push("Subscribed "+n(i.sub.total)+"x");if(i.gmp!=null)bits.push("GMP \u20b9"+n(i.gmp)+" (unofficial)")}
  bits=bits.filter(Boolean);return "\u2022 "+i.name+seg(i)+(bits.length?" \u00b7 "+bits.join(" \u00b7 "):"")}
function order(a,b){return (a.segment==="SME")-(b.segment==="SME")||a.name.localeCompare(b.name)}
function build(list,off){
  var D=ist(off),when=off?"tomorrow":"today",out=[];
  function pick(f){return list.filter(f).sort(order)}
  function sec(title,items,fn){if(items.length){out.push("","*"+title+"*");items.forEach(function(i){out.push(fn(i))})}}
  sec("\u23f0 Closing "+when,pick(function(i){return i.close===D}),function(i){return line(i,"closes "+when+(i.cutoff_time?", "+i.cutoff_time:""),true)});
  sec("\ud83d\udfe2 Open for bidding",pick(function(i){return i.open&&i.close&&i.open<D&&i.close>D}),function(i){return line(i,"closes "+dd(i.close),true)});
  sec("\ud83d\ude80 Opening "+when,pick(function(i){return i.open===D}),function(i){return line(i,"closes "+dd(i.close||D))});
  sec("\ud83d\udd1c Coming up",pick(function(i){return i.open&&i.open>D&&i.open<=add(D,4)}),function(i){return line(i,"opens "+dd(i.open))});
  sec("\ud83d\udccb Allotment "+when,pick(function(i){return i.boa===D}),function(i){return line(i,"")+"\n  Check status: https://iposhop.in/ipo/"+i.id+"/"});
  sec("\ud83d\udd14 Listing "+when,pick(function(i){return i.listing===D}),function(i){return line(i,"lists "+when)});
  var head="\ud83d\udcc5 *IPO Shop* \u00b7 "+wd(D)+(off?" (tomorrow)":"");
  if(!out.length){
    out.push("","No IPO closes, opens, lists or gets allotment "+when+".");
    var nx=list.filter(function(i){return i.open&&i.open>D}).sort(function(a,b){return a.open.localeCompare(b.open)||order(a,b)}).slice(0,3);
    if(nx.length){out.push("","*Next to open*");nx.forEach(function(i){out.push(line(i,"opens "+dd(i.open)))})}
  }
  return [head].concat(out,["","More: https://iposhop.in","_Facts only, not investment advice. GMP is unofficial._"]).join("\n")}
var data=null;
function show(off){mode=off;box.value=data?build(data,off):"Could not load the IPO data. Try again in a minute.";
  document.getElementById("b0").setAttribute("aria-pressed",off===0);document.getElementById("b1").setAttribute("aria-pressed",off===1)}
function ok(t){var s=document.getElementById("done");s.textContent=t;setTimeout(function(){s.textContent=""},2500)}
document.getElementById("b0").onclick=function(){show(0)};document.getElementById("b1").onclick=function(){show(1)};
function copyNow(){var t=box.value;
  function fb(){box.select();try{document.execCommand("copy");ok("Copied. Now paste it into your Channel.")}catch(e){ok("Select the text and copy it by hand.")}}
  if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(t).then(function(){ok("Copied. Now paste it into your Channel.")},fb);else fb()}
document.getElementById("copy").onclick=copyNow;
document.getElementById("copyopen").addEventListener("click",copyNow);
document.getElementById("share").onclick=function(){window.open("https://wa.me/?text="+encodeURIComponent(box.value),"_blank","noopener")};
fetch("/ipos.json?"+Date.now(),{cache:"no-store"}).then(function(r){return r.json()}).then(function(a){
  data=(a.ipos||[]).filter(function(i){return !i.unconfirmed});
  if(a.generated_at){var t=new Date(a.generated_at);note.textContent="Data last changed "+t.toLocaleString("en-GB",{timeZone:"Asia/Kolkata",day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"})+" IST."}
  var h=Number(new Intl.DateTimeFormat("en-GB",{timeZone:"Asia/Kolkata",hour:"2-digit",hour12:false}).format(new Date(Date.now())));
  show(h>=17?1:0)}).catch(function(){data=null;show(0)});
})();
</script>"""


def render_alert():
    body = ("<main class=\"w\"><h1>Your daily alert</h1>"
            "<p class=\"lead\">The message for your WhatsApp Channel, written from today's IPO data. Check it, tap Copy, then paste it into your Channel.</p>"
            "<section class=\"card\"><div role=\"group\" aria-label=\"Which day\"><button class=\"btn alt\" id=\"b0\" type=\"button\" aria-pressed=\"true\">Today</button>"
            "<button class=\"btn alt\" id=\"b1\" type=\"button\" aria-pressed=\"false\">Tomorrow</button></div>"
            "<label for=\"msg\" class=\"muted\" style=\"display:block;margin:14px 0 6px\">Message (you can edit it before copying)</label>"
            "<textarea id=\"msg\" rows=\"18\" spellcheck=\"false\" style=\"width:100%;box-sizing:border-box;padding:14px;border:1px solid var(--line);border-radius:12px;font:15px/1.5 Inter,system-ui,sans-serif;color:var(--ink);background:#fff\"></textarea>"
            f"<div style=\"margin-top:10px\"><a class=\"btn wa\" id=\"copyopen\" href=\"{WA_CHANNEL}\" target=\"_blank\" rel=\"noopener\">Copy and open my Channel ↗</a>"
            "<button class=\"btn alt\" id=\"copy\" type=\"button\">Copy message</button>"
            "<button class=\"btn alt\" id=\"share\" type=\"button\">Send to a chat ↗</button></div>"
            "<p id=\"done\" class=\"muted\" role=\"status\" style=\"min-height:1.4em;margin:10px 0 0\"></p>"
            "<p id=\"stamp\" class=\"muted\" style=\"margin:4px 0 0\"></p></section>"
            "<section class=\"card\"><h2>How to post it</h2><p style=\"margin:0\">1. Read the message above. 2. Tap <b>Copy and open my Channel</b>. WhatsApp opens your Channel. "
            "3. Paste the message and send. The bold text uses WhatsApp's own formatting, so it looks right once pasted. "
            "The message states facts only. Read it once before you send, because the data comes from public sources.</p></section></main>")
    js = ALERT_JS.replace("__CH__", WA_CHANNEL)
    return head("Daily alert | IPO Shop", "A private page that writes the daily IPO message for the WhatsApp channel.", SITE + "/alert/", robots="noindex,nofollow",
                extra='<meta name="apple-mobile-web-app-title" content="IPO Alert"><meta name="theme-color" content="#111735"><link rel="manifest" href="/alert/manifest.webmanifest">') + body + js + FOOT


# ============================================================ landing pages for the searches people actually make
SLUGS = ("open-ipos", "upcoming-ipos", "ipo-gmp-today", "ipo-allotment-status", "sme-ipos", "mainboard-ipos", "ipo-calendar", "listed-ipos-2026")
ASOF_JS = """<script>(function(){var el=document.getElementById("asof");if(!el)return;fetch("/ipos.json?"+Date.now(),{cache:"no-store"}).then(function(r){return r.json()}).then(function(a){if(!a.generated_at)return;var t=new Date(a.generated_at);el.textContent="Live data last changed "+t.toLocaleString("en-GB",{timeZone:"Asia/Kolkata",day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"})+" IST. This page refreshes automatically."}).catch(function(){})})();</script>"""
ORDER = {"closing_today": 0, "open": 1, "upcoming": 2, "closed": 3, "listed": 4}


def dsh(iso):
    return f"{int(iso[8:10])} {MONTHS[int(iso[5:7]) - 1]}" if iso else ""


def link(d):
    return f'<a href="/ipo/{esc(d["id"])}/">{esc(d["name"])}</a>' + ("<small>SME IPO</small>" if d.get("segment") == "SME" else "<small>Mainboard IPO</small>")


def C_dates(d):
    return f"{dsh(d.get('open'))} – {dsh(d.get('close'))}" if d.get("open") and d.get("close") else "–"


def C_band(d):
    return esc(band(d)) or "To be announced"


def C_lot(d):
    if d.get("lot") and d.get("price") is not None:
        return f"{inr(d['lot'])} shares<small>One lot {rs(d['lot'] * d['price'])}</small>"
    return "–"


def C_sub(d):
    t = (d.get("sub") or {}).get("total")
    return f"{t:.2f}x" if t is not None else "–"


def srs(n):
    return ("−" if n < 0 else "") + rs(abs(n))


def spct(p):
    return ("−" if p < 0 else "+") + f"{abs(p):.1f}%"


def C_gmp(d):
    if d.get("gmp") is None:
        return "–"
    pct = d.get("gmp_pct")
    return srs(d["gmp"]) + (f"<small>{spct(pct)} of price</small>" if pct is not None else "")


def C_est(d):
    return rs(d["price"] + d["gmp"]) if d.get("gmp") is not None and d.get("price") is not None else "–"


def C_status(d):
    emo, lab = STATUS.get(d.get("status") or "listed", STATUS["listed"])
    return f"{emo} {lab}"


def C_size(d):
    return f"₹{inr(d['size_cr'])} Cr" if d.get("size_cr") is not None else "–"


def C_allot(d):
    return dsh(d.get("boa")) or ("–" if d.get("status") == "listed" else "To be announced")


def C_listing(d):
    return dsh(d.get("listing")) or "–"


def C_reg(d):
    return esc(d.get("registrar") or "–")


def C_closed(d):
    return dsh(d.get("close")) or "–"


def C_type(d):
    return "SME" if d.get("segment") == "SME" else "Mainboard"


def C_gain(d):
    g = d.get("listing_gain_pct")
    return (f"{rs(d['listing_close'])}<small>{'−' if g < 0 else '+'}{abs(g):.2f}% vs issue price</small>" if g is not None and d.get("listing_close") is not None else "–")


def C_issue(d):
    return rs(d["price"]) if d.get("price") is not None else "–"


def table(rows, cols, caption):
    return (f'<div class="tw"><table class="t"><caption class="sr">{esc(caption)}</caption><thead><tr>'
            + "".join(f'<th scope="col">{esc(h)}</th>' for h, _ in cols) + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{fn(d)}</td>" for _, fn in cols) + "</tr>" for d in rows) + "</tbody></table></div>")


def block(title, rows, cols, note=""):
    return (f'<section class="card"><h2>{esc(title)} ({len(rows)})</h2>' + (f'<p class="muted">{note}</p>' if note else "") + '<p class="muted swipe">Swipe the table sideways to see every column.</p>' + table(rows, cols, title) + "</section>") if rows else ""


NAME = ("IPO", link)
COLS_LIVE = [NAME, ("Opens – closes", C_dates), ("Price band", C_band), ("Lot size", C_lot), ("Subscribed", C_sub), ("GMP (unofficial)", C_gmp)]
COLS_UP = [NAME, ("Opens – closes", C_dates), ("Price band", C_band), ("Lot size", C_lot), ("Issue size", C_size), ("GMP (unofficial)", C_gmp)]
COLS_CLOSED = [NAME, ("Closed", C_closed), ("Allotment", C_allot), ("Listing", C_listing), ("Registrar", C_reg), ("Subscribed", C_sub)]
COLS_LISTED = [NAME, ("Issue price", C_issue), ("Listed on", C_listing), ("Listing-day close", C_gain)]

PAGE_LINKS = (("/open-ipos/", "Open IPOs"), ("/upcoming-ipos/", "Upcoming IPOs"), ("/ipo-gmp-today/", "IPO GMP today"), ("/ipo-allotment-status/", "Allotment status"),
              ("/sme-ipos/", "SME IPOs"), ("/mainboard-ipos/", "Mainboard IPOs"), ("/ipo-calendar/", "IPO calendar"), ("/listed-ipos-2026/", "Listed IPOs 2026"), ("/ipo/", "All IPOs"))
HOWTO = ("Open the IPO section of your broker or bank app, pick the IPO, choose the number of lots and enter your UPI ID. Approve the payment request in your UPI app before the cut-off on the last day. "
         "The money stays blocked in your account until allotment. IPO Shop does not take applications.")
SUBQ = "Subscription shows how many times the shares on offer have been bid for. 5x means investors asked for five times the shares available. It measures demand and does not predict the listing price."
GMPQ = "GMP, the grey market premium, is an unofficial price in the grey market. The exchanges do not publish it, it can change quickly, and it often differs from the real listing price."


def landing(slug, title, h1, desc, intro, blocks, faqs, items):
    url = f"{SITE}/{slug}/"
    ld = json.dumps({"@context": "https://schema.org", "@graph": [
        {"@type": "BreadcrumbList", "itemListElement": [{"@type": "ListItem", "position": 1, "name": "IPO Shop", "item": SITE + "/"}, {"@type": "ListItem", "position": 2, "name": h1, "item": url}]},
        {"@type": "ItemList", "name": h1, "itemListElement": [{"@type": "ListItem", "position": k + 1, "name": d["name"] + " IPO", "url": f"{SITE}/ipo/{d['id']}/"} for k, d in enumerate(items[:40])]}]}, ensure_ascii=False)
    more = "".join(f'<a href="{u}">{esc(t)}</a>' for u, t in PAGE_LINKS if u != f"/{slug}/")
    body = (f'<main class="w wide"><div class="crumbs"><a href="/">Home</a> › {esc(h1)}</div><h1>{esc(h1)}</h1><p class="lead">{intro}</p>'
            '<p class="muted" id="asof">Figures refresh automatically through the day.</p>' + blocks
            + '<section class="card"><h2>Common questions</h2>' + "".join(f"<h3>{esc(q)}</h3><p>{esc(a)}</p>" for q, a in faqs) + "</section>"
            + f'<section class="card links"><h2>More on IPO Shop</h2>{more}</section>'
            '<p class="muted">Facts only, not investment advice. GMP is unofficial. Read the offer document (RHP) before investing.</p></main>')
    return head(title, desc, url, extra=f'<script type="application/ld+json">{ld}</script>', tick=True) + body + ASOF_JS + FOOT


def by(ds, f, key=None):
    return sorted([d for d in ds if f(d)], key=key or (lambda d: (d.get("close") or d.get("open") or "", d["name"])))


def lp_open(ds):
    rows = by(ds, lambda d: d.get("status") in ("open", "closing_today"))
    nxt = by(ds, lambda d: d.get("status") == "upcoming", lambda d: (d.get("open") or "9999", d["name"]))[:4]
    if rows:
        n = len(rows)
        intro = f"{n} IPO{'s are' if n > 1 else ' is'} open for bidding in India right now: " + ", ".join(esc(d["name"]) for d in rows[:6]) + (" and more" if n > 6 else "") + ". See the price band, lot size, how many times each issue is subscribed so far and the unofficial GMP."
    else:
        intro = "No IPO is open for bidding in India at the moment. The IPOs that open next are listed below with their dates and price band."
    blocks = block("Open for bidding", rows, COLS_LIVE) + block("Opening next", nxt, COLS_UP)
    return landing("open-ipos", "Open IPOs Today in India: Price Band, GMP, Subscription | IPO Shop", "Open IPOs today in India",
                   "IPOs open for bidding today in India with price band, lot size, live subscription, GMP and closing date. Mainboard and SME, from NSE and BSE data.",
                   intro, blocks, [("How do I apply for an open IPO?", HOWTO), ("What does subscription mean?", SUBQ), ("Is the GMP reliable?", GMPQ)], rows + nxt)


def lp_upcoming(ds):
    rows = by(ds, lambda d: d.get("status") == "upcoming", lambda d: (d.get("open") or "9999", d["name"]))
    intro = (f"{len(rows)} upcoming IPO{'s' if len(rows) != 1 else ''} in India: " + ", ".join(esc(d["name"]) for d in rows[:6]) + (" and more" if len(rows) > 6 else "") + ". Check the opening and closing dates, price band and lot size before the bidding starts.") if rows else "No upcoming IPO has been announced at the moment. Check back soon, this page updates automatically."
    return landing("upcoming-ipos", "Upcoming IPOs in India: Dates, Price Band, Lot Size | IPO Shop", "Upcoming IPOs in India",
                   "Upcoming Mainboard and SME IPOs in India with opening and closing dates, price band, lot size, issue size and unofficial GMP. From NSE and BSE data.",
                   intro, block("Upcoming IPOs", rows, COLS_UP),
                   [("How are the price band and lot size decided?", "The company announces the price band a few days before the IPO opens. The lot size is the minimum number of shares you must bid for, so the minimum investment is the lot size times the price."),
                    ("How do I apply when an IPO opens?", HOWTO), ("Where do the dates come from?", "Dates, price band and lot size come from NSE and BSE and are refreshed automatically. Always confirm in the offer document (RHP).")], rows)


def lp_gmp(ds):
    rows = by(ds, lambda d: d.get("gmp") is not None and d.get("status") in ("upcoming", "open", "closing_today", "closed"), lambda d: (ORDER.get(d.get("status"), 9), d.get("close") or "", d["name"]))
    cols = [NAME, ("Status", C_status), ("Price band", C_band), ("GMP (unofficial)", C_gmp), ("Price + GMP (unofficial)", C_est), ("Subscribed", C_sub)]
    intro = (f"The grey market premium is being reported for {len(rows)} IPO{'s' if len(rows) != 1 else ''} right now. GMP is unofficial and is not a promise of the listing price." if rows
             else "No grey market premium is being reported for open or upcoming IPOs right now. GMP is unofficial, and it appears here when a public tracker reports it.")
    return landing("ipo-gmp-today", "IPO GMP Today: Grey Market Premium of Open IPOs | IPO Shop", "IPO GMP today",
                   "IPO GMP today: grey market premium of open and upcoming IPOs in India with price band and subscription. GMP is unofficial and not a listing guarantee.",
                   intro, block("IPOs with a reported GMP", rows, cols, "GMP is unofficial and changes quickly. Price + GMP is a simple sum, not a forecast."),
                   [("What is GMP in an IPO?", GMPQ), ("Does a high GMP guarantee a listing gain?", "No. The GMP can fall or rise before listing, and the final price depends on how the shares trade on the first day."),
                    ("Where does the GMP on IPO Shop come from?", "GMP figures come from public trackers and our own checks. They are unofficial and shown for information only.")], rows)


def lp_allot(ds):
    closed = by(ds, lambda d: d.get("status") == "closed", lambda d: (d.get("boa") or d.get("close") or "9999", d["name"]))
    recent = by(ds, lambda d: d.get("status") == "listed" and d.get("listing"), lambda d: d["listing"])[-10:][::-1]
    reg = ('<section class="card"><h2>Check your allotment on the registrar website</h2><p class="muted">Each IPO has one registrar. Its name is on the IPO page.</p><ul class="list">'
           + "".join(f'<li><a href="{u}" target="_blank" rel="noopener noreferrer">{esc(n)} ↗</a></li>' for n, u in REG.values()) + "</ul></section>")
    cols = [NAME, ("Closed", C_closed), ("Allotment", C_allot), ("Listing", C_listing), ("Registrar", C_reg)]
    cols_done = [NAME, ("Closed", C_closed), ("Listed on", C_listing), ("Registrar", C_reg)]
    intro = ("Find out how to check your IPO allotment status, and see the latest IPOs with their allotment and listing dates. " + (f"{len(closed)} IPO{'s are' if len(closed) != 1 else ' is'} closed and waiting for allotment or listing." if closed else "Open any IPO page for its registrar and dates."))
    return landing("ipo-allotment-status", "IPO Allotment Status: How to Check, Latest IPOs | IPO Shop", "IPO allotment status",
                   "How to check IPO allotment status with the registrar, plus allotment and listing dates of the latest IPOs in India. Mainboard and SME.",
                   intro, block("Closed IPOs, allotment and listing", closed, cols) + reg + block("Recently listed IPOs", recent, cols_done),
                   [("How do I check my IPO allotment status?", "Open the registrar's IPO allotment page named for your IPO, choose the IPO, and enter your PAN, application number or demat (DP) ID. Your broker app may also show the result."),
                    ("When will I get the shares or the refund?", "Allotment is usually finalised a day after the IPO closes. Shares are credited to your demat account and unused money is released, usually about a day before listing."),
                    ("What if I did not get any shares?", "Many IPOs are oversubscribed, so not every applicant is allotted shares. The money blocked through UPI is released after allotment.")], closed + recent)


def lp_segment(ds, seg):
    sme = seg == "SME"
    mine = [d for d in ds if (d.get("segment") == "SME") == sme]
    live = by(mine, lambda d: d.get("status") in ("open", "closing_today"))
    up = by(mine, lambda d: d.get("status") == "upcoming", lambda d: (d.get("open") or "9999", d["name"]))
    closed = by(mine, lambda d: d.get("status") == "closed", lambda d: (d.get("boa") or d.get("close") or "9999", d["name"]))
    listed = by(mine, lambda d: d.get("status") == "listed" and d.get("listing"), lambda d: d["listing"])[-15:][::-1]
    word, slug = ("SME", "sme-ipos") if sme else ("Mainboard", "mainboard-ipos")
    intro = (f"{word} IPOs in India: {len(live)} open now, {len(up)} upcoming and {len(closed)} waiting for listing. " + ("SME IPOs come from smaller companies on BSE SME and NSE Emerge, with bigger lots and thinner trading." if sme else "Mainboard IPOs come from larger companies that list on the main boards of NSE and BSE."))
    q1 = ("What is an SME IPO?", "An SME IPO is a public issue by a small or medium company that lists on the SME platform of BSE (BSE SME) or NSE (NSE Emerge). The lot size is bigger, so the minimum investment is usually higher than for Mainboard IPOs, and trading can be thinner.") if sme else \
         ("What is a Mainboard IPO?", "A Mainboard IPO is a public issue by a larger company that lists on the main boards of NSE and BSE. Retail investors can bid for up to ₹2 lakh in one application.")
    return landing(slug, f"{word} IPOs in India: Open, Upcoming and Listed | IPO Shop", f"{word} IPOs in India",
                   f"{word} IPOs in India: open, upcoming and recently listed, with dates, price band, lot size, subscription and GMP. From NSE and BSE data.",
                   intro, block(f"Open {word} IPOs", live, COLS_LIVE) + block(f"Upcoming {word} IPOs", up, COLS_UP) + block("Closed, waiting to list", closed, COLS_CLOSED) + block(f"Recently listed {word} IPOs", listed, COLS_LISTED),
                   [q1, ("How do I apply?", HOWTO), ("What does subscription mean?", SUBQ)], live + up + closed + listed)


def lp_calendar(ds):
    ev = []
    for d in ds:
        st = d.get("status")
        if st == "upcoming":
            fields = (("open", "Opens"), ("close", "Closes"), ("boa", "Allotment"), ("listing", "Listing"))
        elif st in ("open", "closing_today"):
            fields = (("close", "Closes"), ("boa", "Allotment"), ("listing", "Listing"))
        elif st == "closed":
            fields = (("boa", "Allotment"), ("listing", "Listing"))
        else:
            continue
        ev += [(d[f], lab, d) for f, lab in fields if d.get(f)]
    ev.sort(key=lambda e: (e[0], e[1], e[2]["name"]))
    rows = "".join(f"<tr><td>{esc(dl(dt_))}</td><td>{lab}</td><td>{link(d)}</td></tr>" for dt_, lab, d in ev)
    tbl = (f'<section class="card"><h2>Key dates ({len(ev)})</h2><div class="tw"><table class="t"><caption class="sr">IPO key dates</caption><thead><tr><th scope="col">Date</th><th scope="col">Event</th><th scope="col">IPO</th></tr></thead><tbody>{rows}</tbody></table></div></section>') if ev else ""
    items, seen = [], set()
    for _, _, d in ev:
        if d["id"] not in seen:
            seen.add(d["id"]); items.append(d)
    intro = "Opening, closing, allotment and listing dates of the IPOs that are open, upcoming or waiting to list in India, in date order." if ev else "No IPO dates are scheduled at the moment. This calendar fills in when new IPOs are announced."
    return landing("ipo-calendar", "IPO Calendar India: Opening, Closing and Listing Dates | IPO Shop", "IPO calendar India",
                   "IPO calendar for India: opening, closing, allotment and listing dates of open and upcoming Mainboard and SME IPOs, in date order.",
                   intro, tbl, [("How do I read the calendar?", "Each row is one date for one IPO: when bidding opens, when it closes, the expected allotment date or the listing date."),
                                ("Are the dates final?", "Dates come from NSE and BSE and can change if the company or the exchange revises them. Check the IPO page and the offer document before you plan."),
                                ("How do I apply?", HOWTO)], items)


def lp_listed(ds):
    rows = by(ds, lambda d: d.get("status") == "listed" and d.get("listing"), lambda d: (d["listing"], d["name"]))[::-1]
    with_g = [d for d in rows if d.get("listing_gain_pct") is not None]
    up = sum(1 for d in with_g if d["listing_gain_pct"] > 0)
    intro = (f"{len(rows)} IPOs have listed in 2026 so far." + (f" Of the {len(with_g)} with listing-day data, {up} closed above the issue price on the first day and {len(with_g) - up} closed at or below it." if with_g else "")
             + " The table shows each IPO's issue price and its close on the listing day.")
    return landing("listed-ipos-2026", "IPOs Listed in 2026: Listing-Day Gain and Loss | IPO Shop", "IPOs listed in 2026",
                   "All IPOs listed in India in 2026 with issue price, listing date and listing-day close, gain or loss. Mainboard and SME, from NSE and BSE data.",
                   intro, block("Listed in 2026", rows, COLS_LISTED),
                   [("What does listing-day gain mean?", "It compares the share's close on the first trading day with the IPO issue price. A positive figure means it closed above the issue price."),
                    ("Does the listing-day result show how the share did later?", "No. It covers the first day only. Prices keep moving after listing."),
                    ("Where does this data come from?", "Listing details come from NSE and BSE. Some IPOs have no listing-day figure yet.")], rows)


LANDERS = (("open-ipos", lp_open), ("upcoming-ipos", lp_upcoming), ("ipo-gmp-today", lp_gmp), ("ipo-allotment-status", lp_allot),
           ("sme-ipos", lambda ds: lp_segment(ds, "SME")), ("mainboard-ipos", lambda ds: lp_segment(ds, "MAIN")), ("ipo-calendar", lp_calendar), ("listed-ipos-2026", lp_listed))


def write_sitemap(root, ds):
    urls = [SITE + "/", SITE + "/ipo/"] + [f"{SITE}/{sl}/" for sl in SLUGS] + [f"{SITE}/ipo/{d['id']}/" for d in sorted(ds, key=lambda d: d["id"])]
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
    by_seg = {}
    for d in sorted(ds, key=lambda d: (d.get("open") or d.get("listing") or "", d["id"])):
        by_seg.setdefault(d.get("segment") or "MAIN", []).append(d)
    near = {}
    for lst in by_seg.values():
        for i, d in enumerate(lst):
            near[d["id"]] = lst[max(0, i - 3):i] + lst[i + 1:i + 4]
    bstats = base_stats(ds)
    for d in ds:
        keep.add(d["id"])
        save(base / d["id"] / "index.html", render_page(d, near.get(d["id"], []), bstats))
    for slug, fn in LANDERS:
        save(root / slug / "index.html", fn(ds))
    save(base / "index.html", render_hub(ds))
    save(root / "404.html", render_404())
    save(root / "alert" / "index.html", render_alert())
    save(root / "alert" / "manifest.webmanifest", json.dumps({"name": "IPO Shop daily alert", "short_name": "IPO Alert", "start_url": "/alert/", "scope": "/alert/",
        "display": "standalone", "background_color": "#ffffff", "theme_color": "#111735",
        "icons": [{"src": "/brand/icon-192.png", "sizes": "192x192", "type": "image/png"}, {"src": "/brand/icon-512.png", "sizes": "512x512", "type": "image/png"}]}, indent=2) + "\n")
    removed = 0
    for sub in base.iterdir():
        if sub.is_dir() and sub.name not in keep:
            shutil.rmtree(sub)
            removed += 1
    sm = write_sitemap(root, ds)
    log(f"IPO pages: {len(ds)} pages ({wrote} written, {same} unchanged, {removed} removed); sitemap {'updated' if sm else 'unchanged'}")
    return len(ds)
