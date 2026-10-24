"""
Run from the main project folder:   python probe_bse_demand3.py      (needs: pip install requests)

Third pass. BSE's own site has a "bid details" call for book-building issues. This reads the BSE script you
already saved (data/probe/bse_main-*.js), pulls out the real addresses of the bid and issue calls, and then
tries them for the IPOs that are open right now. It only READS public data and changes nothing on your site.
Send the printed output.
"""
import glob, json, pathlib, re, sys, time
try:
    import requests
except ImportError:
    sys.exit("Run:  pip install requests")

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "data" / "probe"; OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://api.bseindia.com/BseIndiaAPI/api"
HEAD = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.bseindia.com", "Referer": "https://www.bseindia.com/",
    "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-site",
}
NAMES = re.compile(r"([A-Za-z0-9_]*(?:Bid|Demand|Subscri|Cover|IPO|PublicIssue)[A-Za-z0-9_]*)\s*:\s*[`\"']([^`\"']{1,120})[`\"']")


def short(obj, n=420):
    t = json.dumps(obj, ensure_ascii=False)
    return t if len(t) <= n else t[:n] + " ..."


def rows_of(d):
    if isinstance(d, list): return d
    if isinstance(d, dict):
        for v in d.values():
            if isinstance(v, list): return v
    return []


def call(s, path, label=None):
    time.sleep(2)
    url = BASE + (path if path.startswith("/") else "/" + path)
    try:
        r = s.get(url, timeout=30)
    except Exception as e:
        print(f"  {label or path}: failed ({e})"); return None
    try:
        d = r.json()
    except ValueError:
        print(f"  {label or path}: HTTP {r.status_code}, {len(r.content)} bytes, not JSON"); return None
    rows = rows_of(d)
    tag = re.sub(r"[^A-Za-z0-9]+", "_", label or path)[:60]
    (OUT / f"bse_demand_{tag}.json").write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"  {label or path}: HTTP {r.status_code}, {len(rows)} rows" + (f", fields: {sorted(rows[0].keys())}" if rows and isinstance(rows[0], dict) else f", body: {short(d, 200)}"))
    if rows: print("      first row:", short(rows[0], 500))
    return d


def main():
    files = sorted(glob.glob(str(OUT / "bse_main-*.js")))
    if not files:
        sys.exit("Run probe_bse_demand2.py first: it saves the BSE script this one reads.")
    text = pathlib.Path(files[0]).read_text(encoding="utf-8", errors="ignore")
    print(f"Reading {pathlib.Path(files[0]).name} ({len(text):,} characters)\n")
    print("== addresses defined in the script for bid, demand, subscription and issue calls:")
    seen = set()
    for m in NAMES.finditer(text):
        k = (m.group(1), m.group(2))
        if k in seen: continue
        seen.add(k); print(f"   {m.group(1)} = {m.group(2)}")
        if len(seen) >= 60: break
    for key in ("getBidDetBookBuilding:", "BSE_Domain_APIUrl:"):
        for m in list(re.finditer(re.escape(key), text))[:2]:
            print(f"\n== context for {key}\n   ...{re.sub(chr(10), ' ', text[max(0, m.start()-150):m.end()+260])}...")
    for m in list(re.finditer(r"cType\s*[=:]", text))[:4]:
        print(f"\n== where cType is set\n   ...{re.sub(chr(10), ' ', text[max(0, m.start()-120):m.end()+160])}...")

    s = requests.Session(); s.headers.update(HEAD)
    print("\n== trying the calls from this PC:")
    for st in ("L", "F", "C"):
        call(s, f"/GetPublicIssue_par_updated/w?flag=1&status={st}", f"issue list, status {st}")
    call(s, "/IPO_HomePageDetail/w", "IPO home page detail")
    lst = call(s, "/GetPublicIssue_par/w", "current issue list")
    live = [r for r in rows_of(lst) if str(r.get("Status", "")).upper() == "L" and r.get("IR_flag") == "IPO"][:3]
    print(f"\n== bid details for {len(live)} issue(s) that are open now:")
    bid = next((v for k, v in seen if k == "getBidDetBookBuilding"), None)
    if not bid:
        print("   The address of getBidDetBookBuilding was not found in the list above.")
    for r in live:
        for ct in ("", "C", "T"):
            q = f"{bid}?Scripcode={r['Scrip_cd']}&ir_flag={r['IR_flag']}&CType={ct}" if bid else None
            if q: call(s, q, f"bid details {r.get('Scrip_Name', '')[:22]} CType={ct or 'none'}")


if __name__ == "__main__":
    main()
