"""Two extra public sources, used with their owners' permission. Their addresses live in data/private.json
(or the PEER_B_URL / PEER_C_URL secrets), never in this file, and they are only ever called "Tracker B" and "Tracker C".

What we take: GMP values and when they were updated, subscription totals, and the basic facts (name, dates, price band, size)
of an IPO that the exchange feeds do not list yet. What we leave out: their AI predictions, ratings, flags and any text.
A zero GMP from these sites means "no quote yet", so it is ignored. Everything here is optional: a failed fetch or a
changed page never breaks the build, it just means we keep what we already have.
"""
import datetime as dt
import html as H
import json
import pathlib
import re

UA = "IPOShopBot/1.0 (+https://iposhop.in/)"
B, C = "Tracker B", "Tracker C"
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
            "sub_at": _ist_from_iso(und(r.get("subscriptionLastScraped")) or ""),
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
            "boa": None, "listing": _date(cells.get("Listing"), today),
            "price": _num(cells.get("Price (₹)")), "price_low": None,
            "size_cr": _num(size.group(1)) if size else None,
            "gmp": float(g.group(1)) if g else None, "gmp_at": gmp_at,
            "sub_total": None, "sub_retail": None, "sub_at": None,
            "lot": _num(cells.get("Lot")), "rating": min(fires, 5) or None,
            "live": flag in ("U", "O", "C", "CT"),
        })
    return out


# ------------------------------------------------------------------ one call for the build
def _urls(root):
    import os
    try:
        pj = json.loads((pathlib.Path(root) / "data" / "private.json").read_text(encoding="utf-8")) or {}
    except Exception:
        pj = {}
    return {"b": (os.environ.get("PEER_B_URL") or pj.get("peer_b_url") or "").strip(),
            "c": (os.environ.get("PEER_C_URL") or pj.get("peer_c_url") or "").strip()}


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
    for key, parser in (("b", lambda p: parse_b(p)), ("c", lambda p: parse_c(p, today))):
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
    order = {B: 0, C: 1}
    by_key, rows, added, subs, cand = {}, [], 0, 0, {}
    for p in sorted(peers, key=lambda x: order.get(x["src"], 9)):
        mine = None
        for k in _keys(p["slug"], p["name"]):
            if k in idx:
                mine = idx[k]
                break
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
        if p.get("gmp") not in (None, 0):                                   # 0 means "no quote yet" on these sites
            rows.append({"ipo": mine["id"], "gmp": _tidy(p["gmp"]), "at": p["gmp_at"] or stamp[:16], "source": p["src"]})
        if p["src"] == B and p.get("sub_total"):
            if not mine.get("sub") or mine.get("sub_basis") == B:        # never replace the exchanges' own figures
                sub = {"total": round(p["sub_total"], 2)}
                if p.get("sub_retail") is not None:
                    sub["retail"] = round(p["sub_retail"], 2)
                mine["sub"], mine["sub_basis"], mine["sub_asof"] = sub, B, p.get("sub_at")
                subs += 1
    lots = 0
    for k, c in cand.items():                                               # lot size: our own first, then the direct figure, then the doubled one
        mine = by_key[k]
        if mine.get("lot"):
            continue
        lot = c.get("c") or c.get("b")
        if lot:
            mine["lot"], mine["lot_basis"] = _tidy(lot), "tracker"
            lots += 1
    if added or subs or rows or lots:
        print(f"  peers: {added} IPO(s) added from them, {subs} subscription figure(s) filled, {lots} lot size(s) filled, {len(rows)} GMP reading(s)")
    return rows


def extra_fill(mine, p, cand):
    """Fills only what we have no figure for. The exchanges' own numbers are never replaced."""
    c = cand.setdefault(mine["id"], {})
    if p["src"] == C and p.get("lot"):
        c["c"] = p["lot"]                                                   # one lot, as the table shows it
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
    if not mine.get("reservation") and p.get("quota"):
        mine["reservation"] = p["quota"]
    if p.get("sector") and not mine.get("sector"):
        mine["sector"] = p["sector"]
    if p.get("fresh_cr") is not None and p.get("ofs_cr") is not None and (p["fresh_cr"] or p["ofs_cr"]):
        mine["issue_split"] = {"fresh_cr": p["fresh_cr"], "ofs_cr": p["ofs_cr"]}
    if p["src"] == C and p.get("rating"):
        mine["tracker_rating"] = p["rating"]


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
    last = {}
    for r in store["rows"]:
        last[(r["ipo"], r["source"])] = r
    changed = False
    for r in rows:
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
