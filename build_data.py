"""
IPO Shop data pipeline.  Reads official NSE data + your own facts + your own GMP readings,
and writes ipos.json for the website.

  python build_data.py                 normal run (fetch NSE, merge, write ipos.json)
  python build_data.py --offline       skip NSE and BSE, use the saved caches only
  python build_data.py --no-bse        fetch NSE but not BSE (what the cloud job uses: BSE blocks cloud servers)
  python build_data.py --from-probe    use the saved files in data/probe/ instead of calling NSE
  python build_data.py --sample        build the made-up sample set -> ipos_sample.json
  python build_data.py --local-test    also load data/gmp_local_testing.csv -> ipos_localtest.json
                                       (third-party numbers: for your eyes only, never publish)

Data sources
  data/nse_cache.json     official NSE data, remembered between runs (closed IPOs stay after NSE drops them)
  data/ipos_manual.json   facts NSE does not give: lot size, issue size (Rs Cr), allotment/listing dates, anchor, rating
  data/gmp.csv            YOUR GMP readings (or set data/config.json "gmp_csv_url" to a published Google Sheet CSV)

Works from the project folder or from a scripts/ subfolder.
"""
import copy, csv, difflib, io, json, re, sys, time, pathlib, datetime as dt
from collections import defaultdict

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE if (HERE / "data").exists() else HERE.parent
DATA = ROOT / "data"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
ARGS = set(sys.argv[1:])
SAMPLE = "--sample" in ARGS
LOCAL_TEST = "--local-test" in ARGS
WINDOW_DAYS = 365

NSE = "https://www.nseindia.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
ENDPOINTS = {"current": "/api/ipo-current-issue",
             "upcoming": "/api/all-upcoming-issues?category=ipo"}
OPTIONAL = {"upcoming_sme": "/api/all-upcoming-issues?category=sme"}   # untested guess: skipped if it fails


# ----------------------------------------------------------------- NSE
def fetch_nse(cache, now):
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                      "Accept-Language": "en-US,en;q=0.9",
                      "Referer": NSE + "/market-data/all-upcoming-issues-ipo"})
    s.get(NSE, timeout=20)                      # visit home first: NSE hands out cookies here
    got = {}
    for key, ep in {**ENDPOINTS, **OPTIONAL}.items():
        time.sleep(2)                           # be polite
        try:
            r = s.get(NSE + ep, timeout=20)
            r.raise_for_status()
            got[key] = r.json()
        except Exception as e:
            if key in ENDPOINTS:
                raise
            print(f"  (optional {key} skipped: {e})")

    def one(path, label):
        time.sleep(2)
        try:
            r = s.get(NSE + path, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"  ({label} skipped: {e})")
            return None

    rows = (got.get("current") or []) + (got.get("upcoming") or [])
    series = {r["symbol"]: (r.get("series") or "EQ") for r in rows if r.get("symbol")}
    got["active_category"], got["detail"] = {}, {}
    for r in got.get("current") or []:              # open issues: NSE + BSE combined subscription, every run
        sym = r.get("symbol")
        if sym:
            d = one(f"/api/ipo-active-category?symbol={sym}", f"subscription detail for {sym}")
            if d:
                got["active_category"][sym] = d
    today = now.date().isoformat()
    for sym, ser in series.items():                 # issue facts: once a day per IPO is plenty
        if (cache.get(sym) or {}).get("details_at", "")[:10] == today:
            continue
        d = one(f"/api/ipo-detail?symbol={sym}&series={ser}", f"details for {sym}")
        if d:
            got["detail"][sym] = d
    year = now.year
    if load_json(DATA / "nse_history.json", {}).get("fetched_at", "")[:10] != today:     # whole-year list: once a day
        d = one(f"/api/public-past-issues?from_date=01-01-{year}&to_date=31-12-{year}&security_type=all", "full-year list")
        if d:
            got["history"] = d
    return got


def load_probe():
    d = DATA / "probe"
    files = {"current": "nse_api_ipo-current-issue.json",
             "upcoming": "nse_api_all-upcoming-issues_category_ipo.json"}
    out = {k: json.loads((d / f).read_text(encoding="utf-8")) for k, f in files.items() if (d / f).exists()}
    h = d / "hist_nse_api_public-past-issues_236.json"
    if h.exists():
        out["history"] = json.loads(h.read_text(encoding="utf-8"))
    for key, fname in (("active_category", "v2_api_ipo-active-category_symbol_JPL.json"),
                       ("detail", "v2_api_ipo-detail_symbol_JPL_series_EQ.json")):
        if (d / fname).exists():
            out[key] = {"JPL": json.loads((d / fname).read_text(encoding="utf-8"))}
    return out


def load_json(path, default):
    """Read a saved file; if it is missing or half-written, start fresh instead of crashing the build."""
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        if pathlib.Path(path).exists():
            print(f"  ({pathlib.Path(path).name} was unreadable, starting it fresh)")
        return default


def rows_of(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return v
    return []


def strip_ts(o):
    """Ignore clock-only changes when deciding whether a file really changed."""
    if isinstance(o, dict):
        return {k: (v[:10] if k in ("details_at", "fetched_at") and isinstance(v, str) else strip_ts(v))
                for k, v in o.items() if k != "last_seen"}
    if isinstance(o, list):
        return [strip_ts(x) for x in o]
    return o


def hours_between(a, b):
    try:
        return abs((dt.datetime.fromisoformat(b) - dt.datetime.fromisoformat(a)).total_seconds()) / 3600
    except (TypeError, ValueError):
        return 99


def nse_date(s):
    try:
        return dt.datetime.strptime((s or "").strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def to_num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def tidy(n):                                    # 1065.0 -> 1065
    return None if n is None else (int(n) if float(n).is_integer() else n)


def nse_price(s):                               # "Rs.660 to Rs.693" -> (660, 693);  "Rs.100" -> (100, 100)
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", (s or "").replace(",", ""))]
    return (tidy(nums[0]), tidy(nums[-1])) if nums else (None, None)


def clean_name(n):
    return re.sub(r"\s+(Limited|Ltd\.?)$", "", (n or "").strip(), flags=re.I)


def parse_categories(ac):
    """NSE category table -> ({subscription multiples}, {shares offered per category}, asof)."""
    times, shares = {}, {}
    for r in (ac or {}).get("dataList") or []:
        name = (r.get("category") or "").strip()
        t = to_num(r.get("noOfTotalMeant") if "noOfTotalMeant" in r else r.get("noOfTime"))
        n = to_num(r.get("noOfShareOffered") if "noOfShareOffered" in r else r.get("noOfSharesOffered"))
        key = None
        if name == "Total": key = "total"
        elif name.startswith("Qualified Institutional"): key = "qib"
        elif name == "Non Institutional Investors": key = "nii"
        elif "more than Ten Lakh" in name: key = "bnii"
        elif "Two Lakh" in name and "Ten Lakh" in name: key = "snii"
        elif name.startswith("Retail Individual"): key = "retail"
        elif name == "Employees": key = "employee"
        elif name == "Shareholders": key = "shareholders"
        if key:
            if t is not None: times[key] = round(t, 2)
            if n is not None: shares[key] = n
    m = re.search(r"(\d{2}-[A-Za-z]{3}-\d{4})\s+(\d{2}:\d{2})", (ac or {}).get("updateTime") or "")
    asof = None
    if m:
        try: asof = dt.datetime.strptime(m.group(1), "%d-%b-%Y").date().isoformat() + "T" + m.group(2)
        except ValueError: pass
    return times, shares, asof


def parse_detail(det):
    """NSE ipo-detail -> the facts we used to type by hand."""
    out, info, last, managers = {}, {}, None, []
    for r in (det.get("issueInfo") or {}).get("dataList") or []:
        t = (r.get("title") or "").strip()
        v = (r.get("value") or "").strip().strip('"').strip()
        if t:
            last = t
            info[t] = v
            if t == "Book Running Lead Managers" and v:
                managers.append(v)
        elif last == "Book Running Lead Managers" and v:
            managers.append(v)
    m = re.search(r"\d[\d,]*", info.get("Bid Lot") or info.get("Minimum Order Quantity") or "")
    if m: out["lot"] = int(m.group(0).replace(",", ""))
    if info.get("Name of the Registrar"): out["registrar"] = info["Name of the Registrar"].strip()
    if managers: out["lead_managers"] = [re.sub(r"\s+", " ", x).strip() for x in managers]
    m = re.search(r"\d[\d,]*", info.get("Maximum Subscription Amount for Retail Investor") or "")
    if m: out["retail_max"] = int(m.group(0).replace(",", ""))
    m = re.search(r"upto\s*(\d{1,2}:\d{2}\s*[APap][Mm])", info.get("Cut-off time for UPI Mandate Confirmation") or "")
    if m: out["cutoff_time"] = re.sub(r"\s+", " ", m.group(1)).upper().replace("  ", " ")
    rhp = info.get("Red Herring Prospectus") or ""
    m = re.search(r"https?://[^\s\"'<>]+", rhp)
    if m: out["rhp_url"] = m.group(0)
    m = re.search(r"[Aa]nchor[^0-9]{0,80}?([\d,]{4,})\s*(?:equity\s*)?shares", info.get("Issue Size") or "")
    anchor = int(m.group(1).replace(",", "")) if m else 0
    if anchor: out["anchor_shares"] = anchor
    # reservation split, from the shares each category is offered (anchor sits inside the QIB share)
    _, shares, _ = parse_categories(det.get("activeCat"))
    parts = {k: shares[k] for k in ("qib", "nii", "retail", "employee", "shareholders") if shares.get(k)}
    total = shares.get("total")
    if parts and total and abs(sum(parts.values()) - total) / total <= 0.01:
        base = sum(parts.values()) + anchor
        out["reservation"] = {k: round((v + (anchor if k == "qib" else 0)) / base * 100, 2) for k, v in parts.items()}
    return out


def update_cache(raw, cache, now):
    seen = now.isoformat(timespec="minutes")

    def entry(row, segment=None):
        sym = (row.get("symbol") or "").strip()
        if not sym:
            return None
        e = cache.setdefault(sym, {"symbol": sym, "first_seen": seen})
        lo, hi = nse_price(row.get("issuePrice"))
        e.update(name=clean_name(row.get("companyName")), open=nse_date(row.get("issueStartDate")),
                 close=nse_date(row.get("issueEndDate")), price=hi, price_low=lo, series=row.get("series"),
                 nse_status=row.get("status"), shares_offered=to_num(row.get("issueSize")), last_seen=seen)
        if segment:
            e["segment"] = segment
        elif "segment" not in e:
            e["segment"] = "MAIN" if (row.get("series") or "EQ") == "EQ" else "SME"
        return e

    for key in ("upcoming", "upcoming_sme"):
        for row in raw.get(key) or []:
            entry(row, "SME" if key.endswith("sme") else None)
    touched = set()
    for row in raw.get("current") or []:
        e = entry(row)
        if not e:
            continue
        touched.add(e["symbol"])
        cat = (row.get("category") or "").lower()
        field = ("total" if "total" in cat else "qib" if ("qualified" in cat or "qib" in cat)
                 else "nii" if ("non" in cat or "nii" in cat) else "retail" if ("retail" in cat or "rii" in cat) else None)
        t = to_num(row.get("noOfTime"))
        if field and t is not None:
            e.setdefault("sub", {})[field] = round(t, 2)
    # NSE + BSE combined subscription replaces the NSE-only totals when we have it
    for sym in touched:
        e = cache[sym]
        times, _, asof = parse_categories((raw.get("active_category") or {}).get(sym))
        if times.get("total") is not None:
            e["sub"], e["sub_basis"], e["sub_asof"] = times, "NSE+BSE", asof
        elif e.get("sub_basis") != "NSE+BSE":      # never downgrade a combined figure to NSE-only
            e["sub_basis"] = "NSE only"
    for sym, det in (raw.get("detail") or {}).items():
        if sym in cache:
            try:
                cache[sym].update(parse_detail(det))
                cache[sym]["details_at"] = seen
            except Exception as ex:
                print(f"  (details for {sym} unreadable: {ex})")
    # remember how subscription moved: one snapshot whenever the numbers change
    for sym in touched:
        e = cache[sym]
        if not e.get("sub"):
            continue
        hist = e.setdefault("sub_hist", [])
        if not hist or {k: v for k, v in hist[-1].items() if k != "at"} != e["sub"]:
            hist.append({"at": seen, **e["sub"]})
            del hist[:-400]


# ----------------------------------------------------------------- BSE (SME issues + year statistics)
BSE = "https://api.bseindia.com/BseIndiaAPI/api/"
BSE_HEADERS = {
    "User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.bseindia.com", "Referer": "https://www.bseindia.com/",
    "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-site",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
}


def fetch_bse(bsec, now):
    import requests
    s = requests.Session()
    s.headers.update(BSE_HEADERS)
    out = {}
    time.sleep(2)
    r = s.get(BSE + "GetPublicIssue_par/w", timeout=25)
    r.raise_for_status()
    out["issues"] = r.json()
    if (bsec.get("tracker") or {}).get("fetched_at", "")[:10] != now.date().isoformat():    # statistics: once a day
        time.sleep(2)
        try:
            y = now.year
            r = s.get(BSE + f"IPOTrackerN/w?Fromdt={y}0101&Todt={y}1231", timeout=25)
            r.raise_for_status()
            out["tracker"] = r.json()
        except Exception as e:
            print(f"  (BSE year statistics skipped: {e})")
    year = now.year
    if load_json(DATA / "bse_history.json", {}).get("fetched_at", "")[:10] != now.date().isoformat():   # performance list: once a day
        h = {}
        for key, flag in (("mainboard", 1), ("sme", 2)):
            time.sleep(2)
            try:
                r = s.get(BSE + f"MoreCompanyN/w?Fromdt={year}&company=&flag={flag}&type=2", timeout=30)
                r.raise_for_status()
                h[key] = rows_of(r.json())
            except Exception as e:
                print(f"  (BSE {key} performance list skipped: {e})")
        if h:
            out["history"] = h
    return out


def load_probe_bse():
    d, out = DATA / "probe", {}
    for key, fname in (("issues", "bse_GetPublicIssue_par_w.json"), ("tracker", "bse_IPOTrackerN_w.json")):
        if (d / fname).exists():
            out[key] = json.loads((d / fname).read_text(encoding="utf-8"))
    hist = {}
    for f in sorted(d.glob("hist_bse_MoreCompanyN_w_*.json")):       # mainboard rows carry a short name, SME rows do not
        rows = rows_of(json.loads(f.read_text(encoding="utf-8")))
        if rows:
            hist["mainboard" if "Company_Short_Name" in rows[0] else "sme"] = rows
    if hist:
        out["history"] = hist
    return out


def bse_price(s):                               # "88.00 - 93.00" -> (88, 93)
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", (s or "").replace(",", ""))]
    return (tidy(nums[0]), tidy(nums[-1])) if nums else (None, None)


def nice_name(n):
    n = clean_name(n)
    if n.isupper():                             # BSE shouts: "CORE4 ENGINEERS" -> "Core4 Engineers"
        n = " ".join(w if len(w) <= 3 and w.isalpha() and w in ("BLS", "SFC", "EAAA", "IT") else w.capitalize() for w in n.split())
    return n


def update_bse(raw, bsec, now):
    seen = now.isoformat(timespec="minutes")
    bsec.setdefault("ipos", {})
    for r in ((raw.get("issues") or {}).get("Table") or []):
        if r.get("IR_flag") != "IPO" or r.get("eXCHANGE_PLATFORM") not in ("MainBoard", "SME"):
            continue                                        # skip rights issues, buybacks, open offers, debt
        key = str(r.get("IPO_NO"))
        lo, hi = bse_price(r.get("Price_Band"))
        e = bsec["ipos"].setdefault(key, {"ipo_no": key, "first_seen": seen})
        e.update(name=nice_name(r.get("Scrip_Name")), segment="SME" if r.get("eXCHANGE_PLATFORM") == "SME" else "MAIN",
                 open=(r.get("Start_Dt") or "")[:10] or None, close=(r.get("End_Dt") or "")[:10] or None,
                 price=hi, price_low=lo, face_value=r.get("Face_Val"), scrip=r.get("Scrip_cd"),
                 bse_status=r.get("Status"), last_seen=seen)
    rows = ((raw.get("tracker") or {}).get("Table") or [])
    if rows:
        t = rows[0]
        bsec["tracker"] = {"year": now.year, "total": t.get("TotalIPO"), "mainboard": t.get("NoOfIpo"), "sme": t.get("NoOfSMEIpo"),
                           "listing_gain": t.get("IPOWithPositiveListingGain"), "listing_loss": t.get("IPOWithListingLosses"),
                           "day_gain": t.get("IPOWithPositiveListingDayGains"), "day_loss": t.get("IPOWithListingDayLosses"),
                           "fetched_at": seen}
    bsec["fetched_at"] = seen


def slug(n):
    return re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-")


def merge_bse(recs, bsec):
    by_name = {norm(r["name"]): r for r in recs}
    for e in (bsec.get("ipos") or {}).values():
        r = by_name.get(norm(e["name"]))
        if r is None:                                       # an issue only BSE lists (typically SME)
            r = {"id": slug(e["name"]), "name": e["name"], "lot": None, "size_cr": None, "boa": None,
                 "listing": None, "anchor": None, "rating": None}
            recs.append(r)
            by_name[norm(e["name"])] = r
        r["bse_ipo_no"] = e["ipo_no"]
        r["official"] = True
        if not r.get("symbol"):                             # NSE-linked issues keep NSE's numbers; the rest take BSE's
            for k in ("open", "close", "price", "price_low", "segment"):
                if e.get(k) is not None:
                    r[k] = e[k]
            if e["segment"] == "SME":
                r["exchange"] = "BSE SME"
        r["updated_on"] = r.get("updated_on") or e["last_seen"]
    return recs


# ----------------------------------------------------------------- full-year list
BAD_NAME = re.compile(r"issue\s*(postponed|withdrawn|cancel)|withdraw|postponed", re.I)


def slim_nse_history(rows):
    keep = ("company", "symbol", "securityType", "ipoStartDate", "ipoEndDate", "listingDate", "issuePrice", "priceRange")
    notes = re.compile(r"ncd|bond|debenture|debt", re.I)       # NSE tags some debentures as equity: their page code gives them away
    return [{k: r.get(k) for k in keep} for r in rows
            if (r.get("securityType") or "").upper() in ("EQ", "BE", "SME") and not notes.search(r.get("htmSym") or "")]


def pct_of(x, base):
    return round(x / base * 100, 2) if x is not None and base else None


def history_rows(nse_hist, bse_hist):
    """Every issue of the year that has listed: NSE for dates and prices, BSE for how each one performed."""
    key = lambda n: norm(clean_name(n or ""))
    bse = []
    for seg, bucket in (("MAIN", "mainboard"), ("SME", "sme")):
        for b in bse_hist.get(bucket) or []:
            bse.append((seg, b))
    used, rows = set(), []

    def perf(b):
        price = to_num(b.get("IssuePrice"))
        return {"listing_close": b.get("ListingDayClose"), "listing_gain_pct": pct_of(to_num(b.get("ListingDayGain")), price),
                "current": b.get("CurrentPrice"), "gain_pct": pct_of(to_num(b.get("GainLoss")), price),
                "listing_gain": b.get("ListingDayGain"), "gain": b.get("GainLoss"), "bse_url": b.get("IMAGE")}

    def find_bse(seg, name, listing):
        k = key(name)
        for i, (sg, b) in enumerate(bse):
            if i not in used and sg == seg and key(b.get("CompanyName")) == k:
                return i
        if seg == "MAIN" and listing:                       # same listing day and nearly the same name
            for i, (sg, b) in enumerate(bse):
                if i in used or sg != seg:
                    continue
                bd = (b.get("ListedOn") or "")[:10]
                if bd and abs((dt.date.fromisoformat(bd) - dt.date.fromisoformat(listing)).days) <= 1 and \
                        difflib.SequenceMatcher(None, k, key(b.get("CompanyName"))).ratio() >= 0.82:
                    return i
        return None

    for r in nse_hist.get("rows") or []:
        raw_name = r.get("company") or ""
        if BAD_NAME.search(raw_name):
            continue                                        # postponed / withdrawn
        seg = "SME" if (r.get("securityType") or "").upper() == "SME" else "MAIN"
        listing = nse_date((r.get("listingDate") or "").strip().title())
        j = find_bse(seg, raw_name, listing)
        if listing is None and j is None:
            continue                                        # no listing date and not on BSE: never listed
        lo, hi = nse_price(r.get("priceRange"))
        row = {"name": clean_name(raw_name), "segment": seg, "symbol": r.get("symbol"),
               "open": nse_date((r.get("ipoStartDate") or "").strip().title()), "close": nse_date((r.get("ipoEndDate") or "").strip().title()),
               "listing": listing, "price": tidy(to_num((r.get("issuePrice") or "").strip())) or hi, "price_low": lo,
               "exchange": "NSE Emerge" if seg == "SME" else "NSE"}
        if j is not None:
            used.add(j)
            b = bse[j][1]
            row.update(perf(b))
            row["exchange"] = "NSE · BSE" if seg == "MAIN" else "BSE SME"
            row["listing"] = row["listing"] or (b.get("ListedOn") or "")[:10] or None
        rows.append(row)
    for i, (seg, b) in enumerate(bse):                      # listed on BSE only
        if i in used:
            continue
        price = to_num(b.get("IssuePrice"))
        row = {"name": nice_name(b.get("CompanyName") or ""), "segment": seg, "symbol": None, "open": None, "close": None,
               "listing": (b.get("ListedOn") or "")[:10] or None, "price": tidy(price), "price_low": None,
               "exchange": "BSE SME" if seg == "SME" else "BSE"}
        row.update(perf(b))
        rows.append(row)

    return rows


def finish_history(rows, ipos, nse_hist, bse_hist, now):
    """Add today's open and upcoming issues, give every row an id and a status, newest first."""
    today = now.date().isoformat()
    key = lambda n: norm(clean_name(n or ""))
    index = {}
    for row in rows:
        index[(row["segment"], key(row["name"]))] = row
    for i in ipos:
        seg = "SME" if i.get("segment") == "SME" else "MAIN"
        row = index.get((seg, key(i["name"])))
        if row is None:
            row = {"name": i["name"], "segment": seg, "symbol": i.get("symbol"), "listing": None, "exchange": i.get("exchange") or ("NSE · BSE" if seg == "MAIN" else "SME")}
            rows.append(row)
            index[(seg, key(i["name"]))] = row
        row.update({"live": True, "id": i["id"], "status": i["status"], "open": i.get("open") or row.get("open"), "close": i.get("close") or row.get("close"),
                    "listing": i.get("listing") or row.get("listing"), "price": i.get("price") or row.get("price"),
                    "price_low": i.get("price_low") or row.get("price_low"), "symbol": i.get("symbol") or row.get("symbol")})
        if i.get("unconfirmed"):
            row["unconfirmed"] = True
    taken = {r["id"] for r in rows if r.get("id")}
    for row in rows:                                        # ids and status
        if not row.get("id"):
            base = slug(row["name"]) or "ipo"
            n, cand = 1, base
            while cand in taken:
                n += 1
                cand = f"{base}-{n}"
            row["id"] = cand
            taken.add(cand)
        if not row.get("status"):
            row["status"] = "listed" if row.get("listing") and row["listing"] <= today else "closed"
    rows.sort(key=lambda r: r.get("listing") or r.get("close") or r.get("open") or "", reverse=True)
    return {"year": now.year, "bse_as_of": bse_hist.get("fetched_at"), "nse_as_of": nse_hist.get("fetched_at"), "rows": rows}


def fill_listing_dates(recs, hrows):
    """NSE/BSE know the listing date of every closed issue: use it instead of a hand-typed one."""
    key = lambda n: norm(clean_name(n or ""))
    idx = {(r["segment"], key(r["name"])): r for r in hrows if r.get("listing")}
    for r in recs:
        seg = "SME" if r.get("segment") == "SME" else "MAIN"
        h = idx.get((seg, key(r["name"])))
        if h:
            r["listing"] = h["listing"]


# ----------------------------------------------------------------- merge
def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def merge(manual, cache):
    recs = [dict(r) for r in manual]
    by_sym = {r["symbol"].upper(): r for r in recs if r.get("symbol")}
    by_name = {norm(r["name"]): r for r in recs}
    for sym, e in cache.items():
        r = by_sym.get(sym.upper()) or by_name.get(norm(e["name"]))
        if r is None:                                       # brand-new IPO that appeared on NSE
            r = {"id": sym.lower(), "name": e["name"], "lot": None, "size_cr": None, "boa": None,
                 "listing": None, "anchor": None, "rating": None}
            recs.append(r)
        r["symbol"] = sym
        r["official"] = True
        for k in ("open", "close", "price", "segment"):     # NSE wins on these
            if e.get(k) is not None:
                r[k] = e[k]
        if e.get("sub"):
            r["sub"] = dict(e["sub"])
            r["sub_basis"], r["sub_asof"] = e.get("sub_basis"), e.get("sub_asof")
        for k in ("lot", "registrar", "lead_managers", "reservation", "rhp_url", "retail_max", "anchor_shares", "cutoff_time"):
            if e.get(k) not in (None, [], {}):
                r[k] = e[k]
        r["price_low"] = e.get("price_low")
        r["sub_history"] = e.get("sub_hist") or r.get("sub_history")
        r["series"] = e.get("series")
        r["nse_status"] = e.get("nse_status")
        r["shares_offered"] = e.get("shares_offered")
        r["updated_on"] = e["last_seen"]
    return recs


# ----------------------------------------------------------------- GMP readings (yours)
def read_text_any(path):
    """Read a text file saved by Notepad/Excel in any common encoding."""
    b = pathlib.Path(path).read_bytes()
    if b.startswith((b"\xff\xfe", b"\xfe\xff")):
        return b.decode("utf-16")
    try:
        return b.decode("utf-8-sig")
    except UnicodeDecodeError:
        return b.decode("cp1252", errors="replace")


def parse_gmp_csv(text, default_source="IPO Shop"):
    """Forgiving reader: ignores # lines, a BOM, extra commas, a missing header line."""
    rows = []
    text = text.lstrip("\ufeff")
    lines = [l for l in io.StringIO(text) if l.strip() and not l.lstrip().startswith("#")]
    header = None
    for fields in csv.reader(lines):
        f = [x.strip() for x in fields]
        if header is None:
            low = [x.lower() for x in f]
            header = ["ipo", "gmp", "at", "source"]
            if "gmp" in low and "ipo" in low:           # a real header line
                header = low
                continue                                # otherwise this line is data: fall through
        row = dict(zip(header, f))
        raw = ",".join(f)
        g = to_num(re.sub(r"[\u20b9,+\s]", "", row.get("gmp", "")))
        at = row.get("at", "").replace(" ", "T")
        why = ("no IPO name/symbol" if not row.get("ipo")
               else "gmp is not a number" if g is None
               else "'at' should look like 2026-10-23 11:30, or be left empty" if (at and len(at) < 10) else "")
        if why:
            print(f"  GMP: skipped row [{raw}] -> {why}")
            continue
        if len(at) == 10:
            at += "T17:00"
        rows.append({"ipo": row["ipo"], "gmp": tidy(g), "at": (at[:16] if at else None), "source": row.get("source") or default_source})
    return rows


# ----------------------------------------------------------------- GMP from a public tracker
GMP_UA = "IPOShopBot/1.0 (+https://iposhop.in/)"
GMP_WEB_LABEL = "Public trackers"


def tracker_url():
    """The tracker's address is kept out of the public code: set GMP_TRACKER_URL (a GitHub secret) or put
    {"gmp_tracker_url": "..."} in data/private.json, which is never published."""
    import os
    return (os.environ.get("GMP_TRACKER_URL") or (load_json(DATA / "private.json", {}) or {}).get("gmp_tracker_url") or "").strip()


def parse_tracker_html(html):
    """Read the GMP table: one quote per row, taken from the link to /ipo-gmp/<ipo-name> and the row's own timestamp."""
    import html as H
    out = []
    for seg in re.split(r"<tr\b", html)[1:]:
        seg = seg.split("</tr>")[0]
        m = re.search(r'href="(?:https?://[^/"]+)?/ipo-gmp/([a-z0-9\-]+)"[^>]*>(.*?)</a>', seg, re.S | re.I)
        if not m:
            continue
        slug, inner = m.group(1), H.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
        v = re.search(r"([+\-\u2212\u2013]?)\s*\u20b9\s*([\d,]+(?:\.\d+)?)", inner)
        if not v:
            continue
        val = float(v.group(2).replace(",", ""))
        if v.group(1) in ("-", "\u2212", "\u2013"):
            val = -val
        text = H.unescape(re.sub(r"<[^>]+>", " ", seg))
        t = re.search(r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{4}),?\s+(\d{1,2}:\d{2}\s*[AP]M)", text)
        if not t:
            continue                                   # no reliable time: do not guess
        try:
            at = dt.datetime.strptime(t.group(1) + " " + t.group(2).replace(" ", ""), "%d %b %Y %I:%M%p").strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            continue
        out.append({"ipo": re.sub(r"-ipo$", "", slug), "gmp": tidy(val), "at": at, "source": GMP_WEB_LABEL})
    return out


def fetch_tracker(url):
    import requests, urllib.robotparser as rp
    from urllib.parse import urlparse
    u = urlparse(url)
    robots = rp.RobotFileParser()
    robots.set_url(f"{u.scheme}://{u.netloc}/robots.txt")
    robots.read()
    if not robots.can_fetch(GMP_UA, url):
        print("  GMP tracker: its robots.txt does not allow this, so we skip it")
        return None
    r = requests.get(url, headers={"User-Agent": GMP_UA, "Accept": "text/html"}, timeout=25)
    r.raise_for_status()
    return r.text


def web_gmp_rows(cfg):
    """GMP readings from a public tracker. Remembers what it has seen so a failed fetch never loses data.
    Log lines never print the tracker's address (logs on GitHub are public)."""
    flags = cfg.get("gmp_autofill") or {}
    if not any(flags.values()) or SAMPLE:
        return []
    path = DATA / "gmp_web.json"
    store = load_json(path, {"rows": []})
    changed = False
    for r in store["rows"]:                              # everything in this file came from the tracker: keep the label neutral
        if r.get("source") != GMP_WEB_LABEL:
            r["source"] = GMP_WEB_LABEL
            changed = True
    html = None
    if "--from-probe" in ARGS:
        files = [f for f in sorted((DATA / "probe").glob("*gmp*.html")) if not f.name.endswith("_last.html")]
        html = files[0].read_text(encoding="utf-8") if files else None
    elif "--offline" not in ARGS and "--no-web-gmp" not in ARGS:
        url = tracker_url()
        if not url:
            print("GMP tracker: no address set, using the last saved readings")
        else:
            try:
                html = fetch_tracker(url)
            except Exception as e:
                print("GMP tracker fetch failed (using the last saved readings):", type(e).__name__)
    if html:
        quotes = parse_tracker_html(html)
        print(f"GMP tracker: {len(quotes)} readings found")
        if not quotes:
            (DATA / "probe").mkdir(exist_ok=True)
            (DATA / "probe" / "tracker_gmp_last.html").write_text(html, encoding="utf-8")
            print("  none could be read: saved the page to data/probe/tracker_gmp_last.html so the reader can be adjusted")
        seen = {(r["ipo"], r["gmp"], r["at"]) for r in store["rows"]}
        new = [q for q in quotes if (q["ipo"], q["gmp"], q["at"]) not in seen]
        if new:
            store["rows"] = (store["rows"] + new)[-3000:]
            changed = True
    if changed:
        path.write_text(json.dumps(store, indent=1, ensure_ascii=False), encoding="utf-8")
    return store["rows"]


def stamp_unseen(rows):
    """Rows without a time (just 'ipo, gmp'): remember when this exact value first appeared, so an old
    reading never looks fresh. The memory lives in data/gmp_seen.json and only changes when a value changes."""
    path = DATA / "gmp_seen.json"
    seen = load_json(path, {})
    now = dt.datetime.now(IST).strftime("%Y-%m-%dT%H:%M")
    changed = False
    for r in rows:
        if r["at"]:
            continue
        k = f'{norm(r["ipo"])}|{r["gmp"]}|{r["source"]}'
        if k not in seen:
            seen[k] = now
            changed = True
        r["at"] = seen[k]
    if changed:
        path.write_text(json.dumps(seen, indent=1, sort_keys=True), encoding="utf-8")
    return rows


def load_gmp_rows():
    cfg = {}
    if (DATA / "config.json").exists():
        cfg = json.loads((DATA / "config.json").read_text(encoding="utf-8"))
    rows, url = [], (cfg.get("gmp_csv_url") or "").strip()
    if url:
        try:
            import requests
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            rows += parse_gmp_csv(r.content.decode("utf-8"))
            print("GMP: read your Google Sheet CSV")
        except Exception as e:
            print("GMP: sheet failed, falling back to data/gmp.csv:", e)
            url = ""
    if not url and (DATA / "gmp.csv").exists():
        rows += parse_gmp_csv(read_text_any(DATA / "gmp.csv"))
    if LOCAL_TEST and (DATA / "gmp_local_testing.csv").exists():
        rows += parse_gmp_csv(read_text_any(DATA / "gmp_local_testing.csv"))
    rows = stamp_unseen(rows)
    return rows + web_gmp_rows(cfg)


def attach_gmp(recs, rows):
    idx = {}
    for r in recs:
        for k in (r.get("id"), r.get("symbol"), r.get("name")):
            if k:
                idx[norm(k)] = r
    per, unmatched = defaultdict(list), set()
    for row in rows:
        r = idx.get(norm(row["ipo"]))
        if r is None:
            unmatched.add(row["ipo"])
        else:
            per[r["id"]].append(row)
    for r in recs:
        for k in ("history_source", "gmp_low", "gmp_high"):
            r.pop(k, None)
        r["gmp_sources"], r["gmp_history"] = [], []
        rr = per.get(r["id"])
        if not rr:
            continue
        by_src = defaultdict(list)
        for x in rr:
            by_src[x["source"]].append(x)
        r["gmp_sources"] = [{"source": s, "gmp": x["gmp"], "at": x["at"]}
                            for s, v in by_src.items() for x in [max(v, key=lambda y: y["at"])]]
        main = max(by_src, key=lambda s: (len({x["at"][:10] for x in by_src[s]}), max(x["at"] for x in by_src[s])))
        perday = {}
        for x in sorted(by_src[main], key=lambda y: y["at"]):
            perday[x["at"][:10]] = x["gmp"]
        r["gmp_history"] = [{"date": d, "gmp": g} for d, g in sorted(perday.items())]
        r["history_source"] = main
        vals = [x["gmp"] for x in rr]
        r["gmp_low"], r["gmp_high"] = min(vals), max(vals)
    return unmatched


# ----------------------------------------------------------------- derived fields
def status_for(ipo, today):
    d = lambda k: dt.date.fromisoformat(ipo[k]) if ipo.get(k) else None
    o, c, l = d("open"), d("close"), d("listing")
    if l and today >= l:
        return "listed"
    if c and today == c:
        return "closing_today"
    if o and today < o:
        return "upcoming"
    if o and c and o <= today <= c:
        return "open"
    return "closed"


def enrich(ipo, today):
    hist = sorted(ipo.get("gmp_history") or [], key=lambda h: h["date"])
    srcs = sorted(ipo.get("gmp_sources") or [], key=lambda s: s["at"])
    hv = [h["gmp"] for h in hist]
    if srcs:
        ipo["gmp"], ipo["gmp_asof"], ipo["gmp_source_latest"] = srcs[-1]["gmp"], srcs[-1]["at"], srcs[-1]["source"]
    else:
        ipo["gmp"] = hv[-1] if hv else None
    if hv and "gmp_low" not in ipo:
        ipo["gmp_low"], ipo["gmp_high"] = min(hv), max(hv)
    ipo["gmp_pct"] = round(ipo["gmp"] / ipo["price"] * 100, 2) if ipo["gmp"] is not None and ipo.get("price") else None
    vals = [s["gmp"] for s in srcs]
    if len(vals) >= 2:
        diff = max(vals) - min(vals)
        ipo["gmp_spread"] = {"min": min(vals), "max": max(vals), "diff": diff, "n": len(vals),
                             "diff_pct_of_price": round(diff / ipo["price"] * 100, 2) if ipo.get("price") else None}
    ipo["status"] = status_for(ipo, today)
    if ipo.get("listing_price") and ipo.get("price"):
        ipo["listing_gain_pct"] = round((ipo["listing_price"] / ipo["price"] - 1) * 100, 2)
    return ipo


# ----------------------------------------------------------------- main
def main():
    now = dt.datetime.now(IST)
    stamp = now.isoformat(timespec="minutes")
    if SAMPLE:
        doc = json.loads((DATA / "ipos_sample.json").read_text(encoding="utf-8"))
        recs, sources, dataset, out = doc["ipos"], [], "sample", "ipos_sample.json"
        status = new_status = {}
    else:
        doc = json.loads((DATA / "ipos_manual.json").read_text(encoding="utf-8"))
        cache_path, bse_path = DATA / "nse_cache.json", DATA / "bse_cache.json"
        status_path = DATA / "status.json"          # when each exchange last actually answered
        status = load_json(status_path, {})
        new_status = dict(status)
        cache = load_json(cache_path, {})
        before = copy.deepcopy(cache)
        raw = rawb = None
        if "--offline" not in ARGS:
            try:
                raw = load_probe() if "--from-probe" in ARGS else fetch_nse(cache, now)
                update_cache(raw, cache, now)
                new_status["nse_ok_at"] = stamp
                print(f"NSE: {len(cache)} IPOs in cache")
            except Exception as e:                      # a blocked or broken source must never break the build
                print("NSE fetch FAILED, using cached data:", e)
        if strip_ts(cache) == strip_ts(before):         # nothing new: keep old timestamps, leave the file alone
            cache = before
        else:
            cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
        nh_path, bh_path = DATA / "nse_history.json", DATA / "bse_history.json"
        nse_hist, bse_hist = load_json(nh_path, {}), load_json(bh_path, {})
        if raw and raw.get("history") is not None:
            new = {"fetched_at": stamp, "year": now.year, "rows": slim_nse_history(rows_of(raw["history"]))}
            if new["rows"] and strip_ts(new) != strip_ts(nse_hist):
                nse_hist = new
                nh_path.write_text(json.dumps(nse_hist, indent=1, ensure_ascii=False), encoding="utf-8")
        recs = merge(doc["ipos"], cache)
        bsec = load_json(bse_path, {"ipos": {}})
        bbefore = copy.deepcopy(bsec)
        if "--offline" not in ARGS and "--no-bse" not in ARGS:
            try:
                rawb = load_probe_bse() if "--from-probe" in ARGS else fetch_bse(bsec, now)
                update_bse(rawb, bsec, now)
                new_status["bse_ok_at"] = stamp
                print(f"BSE: {len(bsec['ipos'])} IPOs in cache")
            except Exception as e:                          # BSE sometimes refuses: keep going with what we have
                print("BSE fetch FAILED, using cached data:", e)
        if rawb and rawb.get("history"):
            new = {"fetched_at": stamp, "year": now.year, **{k: v for k, v in rawb["history"].items() if v}}
            if (new.get("mainboard") or new.get("sme")) and strip_ts(new) != strip_ts(bse_hist):
                bse_hist = new
                bh_path.write_text(json.dumps(bse_hist, indent=1, ensure_ascii=False), encoding="utf-8")
        if strip_ts(bsec) == strip_ts(bbefore):
            bsec = bbefore
        else:
            bse_path.write_text(json.dumps(bsec, indent=1, ensure_ascii=False), encoding="utf-8")
        recs = merge_bse(recs, bsec)
        feeds_ok = bool(cache) and bool(bsec.get("ipos"))
        peer_rows = []                                      # IPOGyani and InvestorGain, used with their owners' permission
        try:
            pflag = ((load_json(DATA / "config.json", {}) or {}).get("gmp_autofill") or {}).get("peers", True)
            if pflag and not LOCAL_TEST and "--no-peers" not in ARGS:
                import peers
                fx = next((a.split("=", 1)[1] for a in ARGS if a.startswith("--peers-from=")), None)
                plist = peers.load(ROOT, now.date(), offline="--offline" in ARGS, fixtures=fx)
                peer_rows = peers.keep_new(ROOT, peers.apply(recs, plist, now.date(), stamp))
        except Exception as e:                              # these sources are a bonus: never let them break the build
            print("Extra sources skipped:", type(e).__name__, e)
        try:
            rows = load_gmp_rows() + peer_rows
        except Exception as e:
            print("GMP: could not read GMP data, continuing without it:", e)
            rows = list(peer_rows)
        unmatched = attach_gmp(recs, rows)
        if unmatched:
            print("GMP: no matching IPO for:", ", ".join(sorted(unmatched)))
        sources = list(doc.get("sources", []))
        if LOCAL_TEST:
            sources.append({"name": "Third-party GMP readings (local testing only, do not publish)", "url": ""})
        dataset = "local-test" if LOCAL_TEST else "live"
        out = "ipos_localtest.json" if LOCAL_TEST else "ipos.json"
    # keep a rolling 12-month window: upcoming issues plus anything that listed/closed within the last year
    def ref_date(r):
        v = r.get("listing") or r.get("close") or r.get("open")
        return dt.date.fromisoformat(v) if v else None
    recs = [r for r in recs if ref_date(r) is None or (now.date() - ref_date(r)).days <= WINDOW_DAYS]
    for r in recs:                                     # where does an SME issue list?
        if r.get("segment") == "SME" and not r.get("exchange"):
            r["exchange"] = "NSE Emerge" if (r.get("series") or "").upper() in ("SM", "ST") else "SME"
    hrows = []
    if not SAMPLE:
        hrows = history_rows(nse_hist, bse_hist)
        fill_listing_dates(recs, hrows)
    ipos = [enrich(r, now.date()) for r in recs]
    if not SAMPLE:                                          # upcoming/open but on neither exchange's official list
        for i in ipos:
            i["unconfirmed"] = bool(feeds_ok and not i.get("official") and i["status"] in ("upcoming", "open", "closing_today"))
    payload = {"generated_at": stamp, "dataset": dataset, "sources": sources,
               "market_stats": (bsec.get("tracker") if not SAMPLE else None), "feeds": None, "ipos": ipos}

    if not SAMPLE and not LOCAL_TEST:                       # the whole-year list the "All 2026" tab reads
        hist = finish_history(hrows, ipos, nse_hist, bse_hist, now)
        old_h = load_json(ROOT / "history.json", None)
        hcore = lambda p: {k: v for k, v in (p or {}).items() if k != "generated_at"}
        if old_h is None or hcore(old_h) != hcore(hist):
            hist["generated_at"] = stamp
            (ROOT / "history.json").write_text(json.dumps(hist, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"Wrote history.json: {len(hist['rows'])} IPOs this year")
        else:
            print(f"history.json unchanged ({len(hist['rows'])} IPOs)")
        try:                                                # one crawlable page per IPO, plus the hub and the sitemap
            import pages
            pages.write_all(hist["rows"], ipos, ROOT)
        except Exception as e:                              # never let the pages stop the data from publishing
            print("IPO pages skipped:", e)

    # ---- quiet runs write nothing, so the repository only changes when the data does
    core = lambda p: {k: v for k, v in p.items() if k not in ("generated_at", "feeds")}
    old = load_json(ROOT / out, None)
    changed = old is None or core(old) != core(payload)
    persist_status = False
    if not SAMPLE:
        drift = any(new_status.get(k) and (not status.get(k) or hours_between(status[k], new_status[k]) > 2)
                    for k in ("nse_ok_at", "bse_ok_at"))
        persist_status = changed or drift               # freshness is recorded lazily: at most ~2 h behind
        final = new_status if persist_status else status
        payload["feeds"] = {"nse_ok_at": final.get("nse_ok_at"), "bse_ok_at": final.get("bse_ok_at")}
        if persist_status:
            status_path.write_text(json.dumps(final, indent=1), encoding="utf-8")
    if changed or persist_status:
        (ROOT / out).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {out} ({dataset}) with {len(ipos)} IPOs, {sum(1 for i in ipos if i['gmp'] is not None)} with GMP")
    else:
        print(f"No change since last run: {out} left as it was ({len(ipos)} IPOs)")


if __name__ == "__main__":
    main()
