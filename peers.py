"""Three extra public sources, used with their owners' permission. Their addresses live in data/private.json
(or the PEER_B_URL / PEER_C_URL / PEER_D_URL secrets), never in this file, and they are only ever called "Tracker B", "C" and "D".

What we take: GMP values and when they were updated, subscription totals, lot size, issue facts, and for source D the
day-by-day GMP history plus the numbers from each IPO's offer document (revenue, profit, valuation ratios), and only after
those numbers pass consistency checks. What we leave out: their AI predictions, flags, reviews and any written text.
A zero GMP from these sites means "no quote yet", so it is ignored. Everything here is optional: a failed fetch or a
changed page never breaks the build, it just means we keep what we already have.
"""
import datetime as dt
import html as H
import json
import pathlib
import re

UA = "IPOShopBot/1.0 (+https://iposhop.in/)"
B, C, D = "Tracker B", "Tracker C", "Tracker D"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
MAX_AGE_HOURS = 12                                  # a cached copy older than this is not used


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _strip_suffix(name):
    return re.sub(r"\s+(limited|ltd\.?|pvt\.?|private)\b\.?\s*$", "", (name or "").strip(), flags=re.I).strip()


def _slug(n):
    return re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-")


def _keys(*names):
    out = set()
    for n in names:
        if not n:
            continue
        out.add(_norm(n))
        out.add(_norm(_strip_suffix(n)))
        out.add(_norm(re.sub(r"-ipo$", "", n)))
    out.discard("")
    return out


GENERIC = {"limited", "ltd", "private", "pvt", "ipo", "india", "the", "and", "of", "sme"}


def _words(s):
    return {w for w in re.split(r"[^a-z0-9]+", (s or "").lower()) if w and w not in GENERIC}


def _near(name, slug, recs):
    """Same IPO written two ways ('Maxwell Engineering' vs 'Maxwell Engineering Solutions'). Only an unambiguous fit counts."""
    for cand in (name, slug):
        w, n = _words(cand), _norm(cand)
        if len(n) < 8 or not w:
            continue
        hits = {}
        for r in recs:
            for k in (r.get("name"), r.get("id"), r.get("symbol")):
                if not k:
                    continue
                kn, kw = _norm(k), _words(k)
                if len(kn) < 8:
                    continue
                if kn.startswith(n) or n.startswith(kn) or (len(w) >= 2 and w <= kw) or (len(kw) >= 2 and kw <= w):
                    hits[id(r)] = r
                    break
        if len(hits) == 1:
            return list(hits.values())[0]
    return None


def _num(v):
    if v is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group(0)) if m else None


def _tidy(n):
    return int(n) if float(n).is_integer() else round(float(n), 2)


def _ist_from_iso(s):
    """'2026-10-28T08:41:15.118+00:00' -> '2026-10-28T14:11' (India time)."""
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(IST).strftime("%Y-%m-%dT%H:%M")
    except Exception:
        return None


# ------------------------------------------------------------------ fetching
def _robots_ok(requests, url):
    import urllib.robotparser as rp
    from urllib.parse import urlparse
    u = urlparse(url)
    try:
        q = requests.get(f"{u.scheme}://{u.netloc}/robots.txt", headers={"User-Agent": UA}, timeout=20)
    except Exception:
        return False
    if q.status_code == 404:
        return True
    if q.status_code != 200:
        return False
    r = rp.RobotFileParser()
    r.parse(q.text.splitlines())
    return r.can_fetch(UA, url)


def fetch(url):
    import requests
    if not _robots_ok(requests, url):
        print("  peers: robots.txt does not allow this page for our bot, skipped")
        return None
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "text/html"}, timeout=30)
    r.raise_for_status()
    return r.text


# ------------------------------------------------------------------ source B: the records are embedded in the page
def _next_text(page):
    out = []
    for m in re.finditer(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', page, re.S):
        try:
            out.append(json.loads('"' + m.group(1) + '"'))
        except Exception:
            pass
    return "".join(out)


CATS = ("total", "qib", "nii", "bnii", "snii", "retail", "employee", "shareholder", "shareholders", "anchor")


def _cats(live):
    """[{category, subscriptionTimes}] -> {qib: 12.11, ...}. Anchor is left out: it is not a bidding category."""
    if not isinstance(live, list):
        return None
    out = {}
    for x in live:
        if not isinstance(x, dict):
            continue
        k = str(x.get("category") or "").lower()
        v = _num(x.get("subscriptionTimes"))
        if k in CATS and k != "anchor" and v is not None:
            out["shareholders" if k == "shareholder" else k] = round(v, 2)
    return out or None


def _gmp_days(hist):
    """[{date, gmp}] -> [(YYYY-MM-DD, gmp)], newest kept for each day. Their source labels are not stored."""
    if not isinstance(hist, list):
        return []
    best = {}
    for x in hist:
        if not isinstance(x, dict):
            continue
        at, g = _ist_from_iso(str(x.get("date") or "")), _num(x.get("gmp"))
        if at and g is not None and (at[:10] not in best or at > best[at[:10]][0]):
            best[at[:10]] = (at, g)
    return sorted((d, v[1]) for d, v in best.items())


def _doc(u):
    return u if isinstance(u, str) and u.startswith("http") else None


def parse_b(page):
    s, dec, seen = _next_text(page), json.JSONDecoder(), {}
    for m in re.finditer(r'"ipos":\[', s):
        try:
            arr, _ = dec.raw_decode(s[m.end() - 1:])
        except Exception:
            continue
        for r in arr:
            if isinstance(r, dict) and r.get("slug") and r.get("name"):
                seen[r["slug"]] = r
    out = []
    for r in seen.values():
        und = lambda v: None if (v is None or (isinstance(v, str) and v.startswith("$"))) else v
        sub = und(r.get("subscription")) if isinstance(und(r.get("subscription")), dict) else {}
        exch = und(r.get("exchange")) or ""
        det = und(r.get("issueDetails")) if isinstance(und(r.get("issueDetails")), dict) else {}
        quota = {k: _num(det.get(f)) for k, f in (("qib", "qibQuotaPercent"), ("nii", "niiQuotaPercent"), ("retail", "retailQuotaPercent"),
                                                  ("employee", "employeeQuotaPercent"), ("shareholders", "shareholderQuotaPercent"))}
        quota = {k: v for k, v in quota.items() if v} if sum(v or 0 for v in quota.values()) > 0 else None
        sector = und(r.get("sector"))
        sector = sector.strip() if isinstance(sector, str) and sector.strip().upper() not in ("", "BSE", "NSE", "SME", "N/A", "NA") else None
        out.append({
            "src": B, "slug": re.sub(r"-ipo$", "", r["slug"]), "name": r["name"],
            "segment": "SME" if "SME" in exch.upper() else "MAIN",
            "exchange": exch if "SME" in exch.upper() else None,
            "open": und(r.get("openDate")), "close": und(r.get("closeDate")), "boa": und(r.get("allotmentDate")), "listing": und(r.get("listDate")),
            "price": _num(und(r.get("priceMax"))), "price_low": _num(und(r.get("priceMin"))), "size_cr": _num(und(r.get("issueSizeCr"))),
            "gmp": _num(und(r.get("gmp"))), "gmp_at": _ist_from_iso(und(r.get("gmpLastUpdated")) or ""),
            "sub_total": _num(sub.get("total")), "sub_retail": _num(sub.get("retail")),
            "sub_at": _ist_from_iso(und(r.get("subscriptionLastScraped")) or "") or _ist_from_iso(und(r.get("subscriptionLastUpdated")) or ""),
            "sub_cats": _cats(und(r.get("subscriptionLive"))), "gmp_days": _gmp_days(und(r.get("gmpHistory"))),
            "rhp_url": _doc(und(r.get("rhpUrl"))), "drhp_url": _doc(und(r.get("drhpUrl"))),
            "lot_raw": _num(und(r.get("lotSize"))), "sector": sector, "quota": quota,
            "fresh_cr": _num(det.get("freshIssueCr")), "ofs_cr": _num(det.get("ofsCr")),
            "registrar": und(r.get("registrar")) if isinstance(und(r.get("registrar")), str) else None,
            "lead_manager": und(r.get("leadManager")) if isinstance(und(r.get("leadManager")), str) else None,
            "live": True,
        })
    return out


# ------------------------------------------------------------------ source C: the report table is in the page
def _date(txt, today):
    m = re.match(r"\s*(\d{1,2})-([A-Za-z]{3})", txt or "")
    if not m:
        return None
    try:
        d = dt.datetime.strptime(f"{m.group(1)} {m.group(2)} {today.year}", "%d %b %Y").date()
    except ValueError:
        return None
    if (d - today).days > 200:
        d = d.replace(year=d.year - 1)
    elif (today - d).days > 200:
        d = d.replace(year=d.year + 1)
    return d.isoformat()


def parse_c(page, today):
    i = page.find('<table class="report-data-table"')
    if i < 0:
        return []
    tb = page[i:]
    tb = tb[:tb.find("</table>")]
    out = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", tb, re.S):
        href = re.search(r'href="/gmp/([a-z0-9\-]+)/\d+/?"[^>]*title="([^"]*)"', r) or re.search(r'href="/gmp/([a-z0-9\-]+)/\d+/?"[^>]*>([^<]+)<', r)
        if not href:
            continue
        cells = {k: re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", v))).strip()
                 for k, v in re.findall(r'<td[^>]*data-label="([^"]*)"[^>]*>(.*?)</td>', r, re.S)}
        badges = re.findall(r'badge[^>]*>([^<]+)<', r)
        seg_badge = next((b for b in badges if "SME" in b.upper()), None)
        g = re.search(r"₹\s*(-?\d+(?:\.\d+)?)", cells.get("GMP", ""))
        upd = re.match(r"(\d{1,2})-([A-Za-z]{3})\s+(\d{1,2}):(\d{2})", cells.get("Updated-On", ""))
        gmp_at = None
        if upd:
            d = _date(f"{upd.group(1)}-{upd.group(2)}", today)
            gmp_at = f"{d}T{int(upd.group(3)):02d}:{upd.group(4)}" if d else None
        flag = next((b for b in badges if b in ("U", "O", "C", "CT")), None)
        size = re.search(r"([\d,]+(?:\.\d+)?)\s*Cr", cells.get("IPO Size", ""))
        fires = next((v.count("\U0001F525") for k, v in cells.items() if "ating" in k), 0)
        out.append({
            "src": C, "slug": re.sub(r"-ipo$", "", href.group(1)), "name": H.unescape(href.group(2)).strip(),
            "segment": "SME" if seg_badge else "MAIN",
            "exchange": ("NSE Emerge" if "NSE" in seg_badge.upper() else "BSE SME") if seg_badge else None,
            "open": _date(cells.get("Open"), today), "close": _date(cells.get("Close"), today),
            "boa": _date(cells.get("BoA Dt"), today), "listing": _date(cells.get("Listing"), today),
            "price": _num(cells.get("Price (₹)")), "price_low": None,
            "size_cr": _num(size.group(1)) if size else None,
            "gmp": float(g.group(1)) if g else None, "gmp_at": gmp_at,
            "sub_total": _num(re.sub(r"[^\d.]", "", cells.get("Sub", "")) or None), "sub_retail": None, "sub_at": None,
            "lot": _num(cells.get("Lot")), "rating": min(fires, 5) or None,
            "live": flag in ("U", "O", "C", "CT"),
        })
    return out


# ------------------------------------------------------------------ source D: cards on the list page, numbers on each IPO page
def _tx(h):
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", h or ""))).strip()


def _dm(txt, today):
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})", txt or "")
    return _date(f"{m.group(1)}-{m.group(2)}", today) if m else None


def parse_d(page, today):
    out = []
    for attrs, b in re.findall(r'<article class="hmcardsep-card[^"]*"([^>]*)>(.*?)</article>', page, re.S):
        a = dict(re.findall(r'data-([a-z\-]+)="([^"]*)"', attrs))
        h = re.search(r'<h3><a href="([^"]+)">([^<]+)</a>', b)
        if not h:
            continue
        cell = lambda cls: (lambda m: _tx(m.group(1)) if m else "")(re.search(r'class="[^"]*%s[^"]*"[^>]*>(.*?)</div>' % cls, b, re.S))
        band = [_num(x) for x in re.findall(r"₹\s*([\d,]+(?:\.\d+)?)", cell("hmcardsep-price-band"))]
        per = re.search(r'hmcardsep-period"><div[^>]*>[^<]*</div><div[^>]*>(.*?)</div>', b, re.S)
        days = re.findall(r"\d{1,2}\s+[A-Za-z]{3}", _tx(per.group(1)) if per else "")
        lst = re.search(r'hmcardsep-listing"><div[^>]*>[^<]*</div><div[^>]*>(.*?)</div>', b, re.S)
        size = re.search(r"([\d,]+(?:\.\d+)?)\s*Cr", cell("hmcardsep-issue"))
        sub = re.search(r'sub-value">([\d.]+)x', b)
        key = a.get("status-key", "")
        out.append({
            "src": D, "slug": re.sub(r"-(?:sme-)?ipo$", "", h.group(1).rstrip("/").split("/")[-1]), "name": H.unescape(h.group(2)).strip(),
            "segment": "SME" if a.get("type") == "sme" else "MAIN", "exchange": None,
            "open": _dm(days[0], today) if days else None, "close": _dm(days[-1], today) if days else None,
            "boa": None, "listing": _dm(_tx(lst.group(1)), today) if lst else None,
            "price": max(band) if band else None, "price_low": min(band) if len(band) > 1 else None,
            "size_cr": _num(size.group(1)) if size else None,
            "gmp": _num(a.get("gmp")), "gmp_at": None,
            "sub_total": _num(sub.group(1)) if sub else None, "sub_retail": None, "sub_at": None,
            "lot": _num(cell("hmcardsep-lot")), "sector": cell("hmcardsep-sector") or None,
            "detail_url": h.group(1), "live": key in ("open", "closing-today", "upcoming", "closed"),
        })
    return out


def _after(text, head, span=3000):
    i = text.find(head)
    return text[i:i + span] if i >= 0 else ""


def parse_detail(page, price):
    """Day-wise GMP, offer-document numbers and valuation. Anything that fails a check is left out."""
    t = H.unescape(page)
    res = {}
    # day-wise GMP history
    seg = _tx(_after(t, "Day-wise GMP History", 4000))
    seg = seg.split("GMP is unofficial")[0]
    hist = [(dt.datetime.strptime(d, "%d %b %Y").date().isoformat(), float(g.replace(",", "")))
            for d, g in re.findall(r"(\d{1,2} [A-Za-z]{3} \d{4})\s+₹\s*(-?[\d,.]+)\s+[+\-]?[\d.]+%", seg)]
    if hist:
        res["gmp_history"] = hist
    # subscription by investor category, with the time the source last updated it
    i = t.find("Subscription Breakdown")
    if i >= 0:
        i = max(0, i - 2000)
        sec = t[i:i + 12000]
        sub, names = {}, {"qib": "qib", "nii": "nii", "niihni": "nii", "snii": "snii", "bnii": "bnii",
                          "retail": "retail", "retailrii": "retail", "rii": "retail",
                          "employee": "employee", "employees": "employee", "shareholder": "shareholders", "shareholders": "shareholders"}
        m = re.search(r"overall-badge[^>]*>\s*Overall\s*(\d+(?:\.\d+)?)x", sec)
        if m:
            sub["total"] = float(m.group(1))
        for lab, val in re.findall(r'sub-tile-label"[^>]*>\s*([^<]+?)\s*</span>\s*<span[^>]*sub-tile-value"[^>]*>\s*(\d+(?:\.\d+)?)x', sec):
            k = names.get(re.sub(r"[^a-z]", "", lab.lower()))
            if k:
                sub[k] = float(val)
        if "total" in sub and len(sub) > 1:
            res["sub"] = sub
            ts = re.search(r'timestamp"[^>]*>\s*(\d{1,2}) ([A-Za-z]{3}) (\d{4}),\s*(\d{1,2}):(\d{2})\s*([AaPp])', sec)
            if ts:
                try:
                    hh = int(ts.group(4)) % 12 + (12 if ts.group(6).lower() == "p" else 0)
                    d0 = dt.datetime.strptime(f"{ts.group(1)} {ts.group(2)} {ts.group(3)}", "%d %b %Y").date()
                    res["sub_at"] = f"{d0.isoformat()}T{hh:02d}:{ts.group(5)}"
                except ValueError:
                    pass
    # reservation as the offer document sets it, including the anchor and SME market-maker portions
    i = t.find("Reservation")
    if i >= 0:
        sec = t[i:i + 12000]
        names = {"qib": "qib", "qibexclanchor": "qib_ex_anchor", "anchorinvestor": "anchor", "niihni": "nii", "nii": "nii",
                 "bnii10l": "bnii", "snii10l": "snii", "retail": "retail", "employeereservation": "employee",
                 "employee": "employee", "shareholder": "shareholders", "marketmaker": "market_maker", "total": "total"}
        resv = {}
        pat = r'dt-label[^>]*>((?:(?!</span>).){2,200})</span>\s*<span class="[^"]*dd-value[^"]*"[^>]*>\s*([\d,]+)\s*<span class="[^"]*dd-percent[^"]*">\s*\(([\d.]+)%\)'
        for lab, sh, pc in re.findall(pat, sec):
            k = names.get(re.sub(r"[^a-z0-9]", "", _tx(lab).lower()))
            if k and k != "total":
                resv[k] = {"shares": int(sh.replace(",", "")), "pct": float(pc)}
        if resv:
            res["reservation"] = resv
    # where the money goes, as the offer document lists it
    i = t.find("Utilisation of net proceeds")
    if i >= 0:
        sec = t[i:i + 7000].split("Total Fund Requirement")[0]
        uses = []
        pat = r'object-title">\s*([^<]{2,30}?)\s*</span>.*?object-amount">\s*₹\s*([\d,.]+)'
        for lab, amt in re.findall(pat, sec, re.S):
            v = _num(amt)
            if v:
                uses.append({"use": lab.strip(), "cr": v})
        if uses:
            res["uses"] = uses[:8]
    # offer-document numbers: embedded array, accepted only if the page's own text agrees with it
    para = _tx(_after(t, "Financial View", 2200)).split("Total Income")[0]
    said = [float(x.replace(",", "")) for x in re.findall(r"₹\s*([\d,]+(?:\.\d+)?)\s*crore", para)]
    m = re.search(r'\[\{"year":', t)
    years = []
    if m:
        try:
            arr, _ = json.JSONDecoder().raw_decode(t[m.start():])
            years = [y for y in arr if isinstance(y, dict) and y.get("totalIncome") is not None and y.get("pat") is not None]
        except Exception:
            years = []
    close = lambda v, ref, tol: ref is not None and abs(v - ref) <= tol * max(abs(ref), 1e-9)
    if years and said:
        last = years[-1]
        ok_inc = any(close(last["totalIncome"], x, 0.015) for x in said)
        ok_pat = any(close(last["pat"], x, 0.015) for x in said)
        if ok_inc and ok_pat:
            rows = []
            for y in years[-3:]:
                num = lambda k: round(y[k], 2) if y.get(k) is not None else None
                rows.append({"y": y.get("year"), "income": round(y["totalIncome"], 2), "pat": round(y["pat"], 2),
                             "ebitda": num("ebitda"), "net_worth": num("netWorth"), "debt": num("debt"),
                             "assets": num("assets"), "roe": num("roe"), "roce": num("roce")})
            res["years"] = rows
    # valuation ratios, each checked against the price
    val = _tx(_after(t, "IPO Valuation", 1800)).split("Strengths")[0]
    r = {}
    pe = re.search(r"price to earnings ratio[^.]*?(\d+(?:\.\d+)?) times based on[^₹]*?(?:(FY\d\d)[^₹]*)?₹\s*(\d+(?:\.\d+)?)", val)
    if pe and price:
        v, eps = float(pe.group(1)), float(pe.group(3))
        if eps and abs(price / eps - v) <= 0.03 * v:
            r["pe"], r["eps"] = v, eps
            fy = re.search(r"(FY\d\d)", pe.group(0))
            if fy:
                r["fy"] = fy.group(1)
    pb = re.search(r"price to book value ratio is (\d+(?:\.\d+)?) times[^₹]*₹\s*(\d+(?:\.\d+)?)", val, re.I)
    if pb and price:
        v, nav = float(pb.group(1)), float(pb.group(2))
        if nav and abs(price / nav - v) <= 0.03 * v:
            r["pbv"], r["nav"] = v, nav
    ro = re.search(r"return on net worth is (\d+(?:\.\d+)?) percent", val, re.I)
    if ro:
        r["ronw"] = float(ro.group(1))
    rc = re.search(r"(?:ROCE|return on capital employed) is (\d+(?:\.\d+)?) percent", val, re.I)
    if rc:
        r["roce"] = float(rc.group(1))
    if r:
        res["ratios"] = r
    return res


DETAIL_MAX_AGE_H = 6          # each IPO's page is fetched at most this often
DETAIL_MAX_PER_RUN = 14       # and never more than this many pages in one run
DETAIL_PAUSE_S = 2.0          # a pause between pages, so we stay gentle


def add_details(root, recs, now, fixtures=None, offline=False):
    """Attach the checked numbers from each live IPO's own page to its card record. Saved so pages are not asked for twice."""
    import time
    path = pathlib.Path(root) / "data" / "peers_detail.json"
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        store = {}
    live = {r["slug"] for r in recs if r.get("live") and r.get("detail_url")}
    changed = False
    for k in [k for k in store if k not in live]:                  # forget IPOs that are no longer live
        store.pop(k)
        changed = True
    fetched = 0
    for r in recs:
        if r["slug"] not in live:
            continue
        slug = r["slug"]
        c = store.get(slug)
        fresh = False
        if c:
            try:
                fresh = now - dt.datetime.fromisoformat(c["at"]) <= dt.timedelta(hours=DETAIL_MAX_AGE_H)
            except Exception:
                pass
        if not fresh and (fixtures or (not offline and fetched < DETAIL_MAX_PER_RUN)):
            try:
                if fixtures:
                    f = pathlib.Path(fixtures) / f"d_{slug}.html"
                    page = f.read_text(encoding="utf-8") if f.exists() else None
                else:
                    if fetched:
                        time.sleep(DETAIL_PAUSE_S)
                    page = fetch(r["detail_url"])
                    fetched += 1
                if page:
                    store[slug] = c = {"at": now.isoformat(timespec="minutes"), "detail": parse_detail(page, r.get("price"))}
                    changed = True
            except Exception as e:
                print(f"  peers: d: page for {slug} failed ({type(e).__name__}), using the saved copy")
        if c and c.get("detail"):
            r["detail"] = c["detail"]
    if changed:
        path.write_text(json.dumps(store, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    if fetched:
        print(f"  peers: d: {fetched} IPO page(s) read")
    return recs


# ------------------------------------------------------------------ one call for the build
def _urls(root):
    import os
    try:
        pj = json.loads((pathlib.Path(root) / "data" / "private.json").read_text(encoding="utf-8")) or {}
    except Exception:
        pj = {}
    return {"b": (os.environ.get("PEER_B_URL") or pj.get("peer_b_url") or "").strip(),
            "c": (os.environ.get("PEER_C_URL") or pj.get("peer_c_url") or "").strip(),
            "d": (os.environ.get("PEER_D_URL") or pj.get("peer_d_url") or "").strip()}


def load(root, today, offline=False, fixtures=None):
    """Returns the list of records from both sources (fresh, or the saved copy if a fetch failed)."""
    root = pathlib.Path(root)
    cache_path = root / "data" / "peers_cache.json"
    cache = {}
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    now = dt.datetime.now(IST)
    out, changed = [], False
    stale = [k for k in cache if k not in ("b", "c")]          # an earlier layout: drop it so older labels do not stay published
    for k in stale:
        cache.pop(k)
        changed = True
    urls = _urls(root)
    for key, parser in (("b", lambda p: parse_b(p)), ("c", lambda p: parse_c(p, today)), ("d", lambda p: parse_d(p, today))):
        url, recs, page = urls[key], None, None
        if not url and not fixtures and not offline:
            print(f"  peers: source {key.upper()}: no address set, using the saved copy")
        try:
            if fixtures:
                f = pathlib.Path(fixtures) / f"{key}.html"
                page = f.read_text(encoding="utf-8") if f.exists() else None
            elif not offline and url:
                page = fetch(url)
            if page:
                recs = parser(page)
                if recs and key == "d":
                    recs = add_details(root, recs, now, fixtures=fixtures, offline=offline)
                if recs:
                    cache[key] = {"fetched_at": now.isoformat(timespec="minutes"), "records": recs}
                    changed = True
                    print(f"  peers: {key}: {len(recs)} IPOs read")
                else:
                    print(f"  peers: {key}: page loaded but no rows could be read (the site may have changed)")
        except Exception as e:                       # blocked, offline, changed: never break the build
            print(f"  peers: {key}: fetch failed ({type(e).__name__}), using the saved copy")
        if not recs:
            c = cache.get(key)
            if c:
                try:
                    age = now - dt.datetime.fromisoformat(c["fetched_at"])
                    if age <= dt.timedelta(hours=MAX_AGE_HOURS):
                        recs = c["records"]
                except Exception:
                    pass
        out += recs or []
    if changed:
        cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    return out


# ------------------------------------------------------------------ apply to our records
def apply(recs, peers, today, stamp):
    """Adds IPOs that no official list has yet, fills subscription where we have none, and returns GMP rows."""
    idx = {}
    for r in recs:
        for k in _keys(r.get("id"), r.get("symbol"), r.get("name")):
            idx[k] = r
    order = {B: 0, C: 1, D: 2}
    by_key, rows, added, subs, cand, subcand, watch = {}, [], 0, 0, {}, {}, {}
    for p in sorted(peers, key=lambda x: order.get(x["src"], 9)):
        mine = None
        for k in _keys(p["slug"], p["name"]):
            if k in idx:
                mine = idx[k]
                break
        if mine is None:
            mine = _near(p["name"], p["slug"], recs)
        if mine is None:                                                    # not on any official list we read yet
            ref = p.get("close") or p.get("open")
            fresh = ref and (today - dt.date.fromisoformat(ref)).days <= 2 and (p.get("live") or not p.get("listing") or p["listing"] >= today.isoformat())
            if not (fresh and p.get("open") and p.get("close") and p.get("price")):
                continue
            nm = _strip_suffix(p["name"])
            mine = {"id": _slug(nm), "name": nm, "segment": p["segment"], "price": tidy_price(p["price"]), "price_low": tidy_price(p.get("price_low")),
                    "lot": None, "size_cr": p.get("size_cr"), "open": p["open"], "close": p["close"], "boa": p.get("boa"), "listing": p.get("listing"),
                    "anchor": None, "rating": None, "sub": None, "updated_on": stamp, "from_peers": True}
            if p.get("exchange"):
                mine["exchange"] = p["exchange"]
            recs.append(mine)
            for k in _keys(mine["id"], nm, p["slug"]):
                idx[k] = mine
            added += 1
        by_key.setdefault(mine["id"], mine)
        extra_fill(mine, p, cand)
        if not p.get("gmp"):                                                # listed by the source, but no quote: still worth saying
            seen_at = p.get("gmp_at") or p.get("sub_at") or stamp[:16]
            w = watch.setdefault(mine["id"], {"n": 0, "at": ""})
            w["n"] += 1
            w["at"] = max(w["at"], seen_at or "")
        if p.get("gmp") not in (None, 0):                                   # 0 means "no quote yet" on these sites
            rows.append({"ipo": mine["id"], "gmp": _tidy(p["gmp"]), "at": p["gmp_at"] or stamp[:16], "source": p["src"]})
        for day, g in list((p.get("detail") or {}).get("gmp_history", [])) + list(p.get("gmp_days") or []):
                                                                            # earlier days, stamped just after midnight so a live reading wins
            if g and day < today.isoformat():
                rows.append({"ipo": mine["id"], "gmp": _tidy(g), "at": f"{day}T00:01", "source": p["src"]})
        cand_sub = sub_from(p, mine, today)
        if cand_sub:
            best = subcand.get(mine["id"])
            rank = lambda c: (c[0], c[3] or "")                              # richer wins, then the newer reading
            if not best or rank(cand_sub) > rank(best):
                subcand[mine["id"]] = cand_sub
    for k, (_score, sub, basis, asof) in subcand.items():
        mine = by_key[k]
        cur = mine.get("sub") or {}
        # the exchanges' own figures stand, unless they are still showing nothing at all
        weak = not cur or str(mine.get("sub_basis") or "").startswith("Tracker") or not cur.get("total")
        if weak and sub.get("total"):
            mine["sub"], mine["sub_basis"], mine["sub_asof"] = sub, basis, asof
            subs += 1
    for k, w in watch.items():                                              # so a page can say "checked, no premium" instead of nothing
        by_key[k]["gmp_watch"] = {"sources": w["n"], "at": w["at"] or None}
    lots = 0
    for k, c in cand.items():                                               # lot size: our own first, then the direct figure, then the doubled one
        mine = by_key[k]
        if mine.get("lot"):
            continue
        lot = c.get("c") or c.get("d") or c.get("b")
        if lot:
            mine["lot"], mine["lot_basis"] = _tidy(lot), "tracker"
            lots += 1
    if added or subs or rows or lots:
        print(f"  peers: {added} IPO(s) added from them, {subs} subscription figure(s) filled, {lots} lot size(s) filled, {len(rows)} GMP reading(s)")
    return rows


def sub_from(p, mine, today):
    """(score, figures, label, time) from one source, or None. A richer breakdown beats a bare total."""
    det = (p.get("detail") or {}).get("sub")
    opened = (mine.get("open") or p.get("open") or "9999") <= today.isoformat()
    if not opened:
        return None
    if det and det.get("total") is not None:
        return (3, {k: round(v, 2) for k, v in det.items()}, p["src"], (p.get("detail") or {}).get("sub_at"))
    cats = p.get("sub_cats")
    if cats and cats.get("total") is not None:
        return (3, cats, p["src"], p.get("sub_at"))
    if p.get("sub_total") is None:
        return None
    sub = {"total": round(p["sub_total"], 2)}
    if p.get("sub_retail") is not None:
        sub["retail"] = round(p["sub_retail"], 2)
    return (2 if p["src"] == B else 1, sub, p["src"], p.get("sub_at"))


def extra_fill(mine, p, cand):
    """Fills only what we have no figure for. The exchanges' own numbers are never replaced."""
    c = cand.setdefault(mine["id"], {})
    if p["src"] in (C, D) and p.get("lot"):
        c["c" if p["src"] == C else "d"] = p["lot"]                         # one lot, as the table shows it
    if p["src"] == B and p.get("lot_raw"):                                  # SME issues list the 2-lot minimum here
        c["b"] = p["lot_raw"] / 2 if p["segment"] == "SME" else p["lot_raw"]
    if mine.get("size_cr") is None and p.get("size_cr"):
        mine["size_cr"] = p["size_cr"]
    if mine.get("price") is None and p.get("price"):
        mine["price"] = tidy_price(p["price"])
        if mine.get("price_low") is None and p.get("price_low"):
            mine["price_low"] = tidy_price(p["price_low"])
    if not mine.get("registrar") and p.get("registrar"):
        mine["registrar"] = p["registrar"]
    if not mine.get("lead_managers") and p.get("lead_manager"):
        mine["lead_managers"] = [p["lead_manager"]]
    if not mine.get("boa") and p.get("boa"):
        mine["boa"] = p["boa"]
    if not mine.get("listing") and p.get("listing"):
        mine["listing"] = p["listing"]
    for k, src in (("rhp_url", "rhp_url"), ("drhp_url", "drhp_url")):
        if not mine.get(k) and p.get(src):
            mine[k] = p[src]
    det = p.get("detail") or {}
    rv = det.get("reservation")
    if rv:                                                                  # the real split from the offer document
        mine["reservation_detail"] = rv
        plain = {k: rv[k]["pct"] for k in ("qib", "nii", "retail", "employee", "shareholders", "market_maker") if k in rv}
        if plain:
            mine["reservation"] = plain
        if rv.get("anchor"):
            mine.setdefault("anchor_shares", rv["anchor"]["shares"])
    elif not mine.get("reservation") and p.get("quota"):
        mine["reservation"] = p["quota"]
    if det.get("uses") and not mine.get("uses"):
        mine["uses"] = det["uses"]
    if p.get("sector") and not mine.get("sector"):
        mine["sector"] = p["sector"]
    if p.get("fresh_cr") is not None and p.get("ofs_cr") is not None and (p["fresh_cr"] or p["ofs_cr"]):
        mine["issue_split"] = {"fresh_cr": p["fresh_cr"], "ofs_cr": p["ofs_cr"]}
    if p["src"] == C and p.get("rating"):
        mine["tracker_rating"] = p["rating"]
    if det.get("years") or det.get("ratios"):                              # offer-document numbers that passed the checks
        mine["fin"] = {"years": det.get("years") or [], "ratios": det.get("ratios") or {}}


def tidy_price(v):
    return None if v is None else _tidy(v)


def keep_new(root, rows):
    """Append to data/gmp_extra.json only when a value changed or 3 hours passed, so the file stays small."""
    old = pathlib.Path(root) / "data" / "gmp_peers.json"      # an earlier name: remove it so the older labels do not stay published
    if old.exists():
        try:
            old.unlink()
        except OSError:
            pass
    path = pathlib.Path(root) / "data" / "gmp_extra.json"
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        store = {"rows": []}
    last, seen = {}, set()
    for r in store["rows"]:
        last[(r["ipo"], r["source"])] = r
        seen.add((r["ipo"], r["source"], r["at"]))
    changed = False
    for r in rows:
        if (r["ipo"], r["source"], r["at"]) in seen:
            continue
        seen.add((r["ipo"], r["source"], r["at"]))
        if r["at"].endswith("T00:01"):                                 # a past day's reading: keep every one, once
            store["rows"].append(r)
            changed = True
            continue
        prev = last.get((r["ipo"], r["source"]))
        if prev and prev["gmp"] == r["gmp"]:
            try:
                gap = dt.datetime.fromisoformat(r["at"]) - dt.datetime.fromisoformat(prev["at"])
            except Exception:
                gap = dt.timedelta(0)
            if gap < dt.timedelta(hours=3):
                continue
        store["rows"].append(r)
        last[(r["ipo"], r["source"])] = r
        changed = True
    if changed:
        store["rows"] = store["rows"][-3000:]
        path.write_text(json.dumps(store, indent=1, ensure_ascii=False), encoding="utf-8")
    return store["rows"]
