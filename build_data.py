"""
GMP Desk data pipeline.  Reads official NSE data + your own facts + your own GMP readings,
and writes ipos.json for the website.

  python build_data.py                 normal run (fetch NSE, merge, write ipos.json)
  python build_data.py --offline       skip NSE, use the saved cache only
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
import copy, csv, io, json, re, sys, time, pathlib, datetime as dt
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
    return got


def load_probe():
    d = DATA / "probe"
    files = {"current": "nse_api_ipo-current-issue.json",
             "upcoming": "nse_api_all-upcoming-issues_category_ipo.json"}
    out = {k: json.loads((d / f).read_text(encoding="utf-8")) for k, f in files.items() if (d / f).exists()}
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
    return out


def load_probe_bse():
    d, out = DATA / "probe", {}
    for key, fname in (("issues", "bse_GetPublicIssue_par_w.json"), ("tracker", "bse_IPOTrackerN_w.json")):
        if (d / fname).exists():
            out[key] = json.loads((d / fname).read_text(encoding="utf-8"))
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
        for k in ("lot", "registrar", "lead_managers", "reservation", "rhp_url", "retail_max", "anchor_shares"):
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


def parse_gmp_csv(text, default_source="GMP Desk"):
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
               else "'at' should look like 2026-10-23 11:30" if len(at) < 10 else "")
        if why:
            print(f"  GMP: skipped row [{raw}] -> {why}")
            continue
        if len(at) == 10:
            at += "T17:00"
        rows.append({"ipo": row["ipo"], "gmp": tidy(g), "at": at[:16], "source": row.get("source") or default_source})
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
    return rows


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
        recs = merge(doc["ipos"], cache)
        bsec = load_json(bse_path, {"ipos": {}})
        bbefore = copy.deepcopy(bsec)
        if "--offline" not in ARGS:
            try:
                rawb = load_probe_bse() if "--from-probe" in ARGS else fetch_bse(bsec, now)
                update_bse(rawb, bsec, now)
                new_status["bse_ok_at"] = stamp
                print(f"BSE: {len(bsec['ipos'])} IPOs in cache")
            except Exception as e:                          # BSE sometimes refuses: keep going with what we have
                print("BSE fetch FAILED, using cached data:", e)
        if strip_ts(bsec) == strip_ts(bbefore):
            bsec = bbefore
        else:
            bse_path.write_text(json.dumps(bsec, indent=1, ensure_ascii=False), encoding="utf-8")
        recs = merge_bse(recs, bsec)
        feeds_ok = bool(cache) and bool(bsec.get("ipos"))
        try:
            rows = load_gmp_rows()
        except Exception as e:
            print("GMP: could not read GMP data, continuing without it:", e)
            rows = []
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
    ipos = [enrich(r, now.date()) for r in recs]
    if not SAMPLE:                                          # upcoming/open but on neither exchange's official list
        for i in ipos:
            i["unconfirmed"] = bool(feeds_ok and not i.get("official") and i["status"] in ("upcoming", "open", "closing_today"))
    payload = {"generated_at": stamp, "dataset": dataset, "sources": sources,
               "market_stats": (bsec.get("tracker") if not SAMPLE else None), "feeds": None, "ipos": ipos}

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
