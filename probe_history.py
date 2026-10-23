"""
Run from the main GMP Desk folder:   python probe_history.py      (needs: pip install requests)

Looks for the NSE / BSE calls that list ALL of this year's IPOs (not just open and upcoming ones),
so the site can show every 2026 issue. Saves what comes back to data/probe/ and prints the field names.
The calls below are GUESSES taken from public projects: some will come back empty. Changes nothing on your site.
"""
import json, pathlib, sys, time

try:
    import requests
except ImportError:
    sys.exit("Run:  pip install requests")

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "data" / "probe"; OUT.mkdir(parents=True, exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
YEAR = 2026

NSE = "https://www.nseindia.com"
NSE_CALLS = [
    "/api/public-past-issues",
    f"/api/public-past-issues?from_date=01-01-{YEAR}&to_date=31-12-{YEAR}&security_type=all",
    f"/api/public-past-issues?from_date=01-01-{YEAR}&to_date=31-12-{YEAR}&security_type=EQ",
    "/api/ipo-past-security-type",
]
BSE = "https://api.bseindia.com/BseIndiaAPI/api/"
BSE_CALLS = [
    "IPOYear/w",
    f"MoreCompanyN/w?Fromdt={YEAR}&company=&flag=1&type=2",
    f"MoreCompanyN/w?Fromdt={YEAR}&company=&flag=2&type=2",
]
BSE_HEADERS = {
    "User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.bseindia.com", "Referer": "https://www.bseindia.com/",
    "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-site",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
}


def rows_of(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def probe(session, base, call, prefix):
    time.sleep(2)
    tag = prefix + "_" + call.split("?")[0].strip("/").replace("/", "_") + ("_" + str(abs(hash(call)) % 1000) if "?" in call else "")
    try:
        r = session.get(base + call, timeout=30)
    except Exception as e:
        print(f"  {call}: failed ({e})"); return
    try:
        data = r.json()
    except ValueError:
        print(f"  {call}: {r.status_code}, {len(r.content)} bytes, not JSON"); return
    rows = rows_of(data)
    if not rows and not data:
        print(f"  {call}: {r.status_code}, empty answer"); return
    (OUT / f"{tag}.json").write_text(json.dumps(data, indent=2))
    keys = list(rows[0].keys())[:30] if rows else (list(data.keys())[:30] if isinstance(data, dict) else type(data).__name__)
    print(f"  {call}\n      {r.status_code}, {len(r.content)} bytes, {len(rows)} rows -> data/probe/{tag}.json\n      fields: {keys}")


def main():
    print("== NSE ==")
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
                      "Referer": NSE + "/market-data/all-upcoming-issues-ipo"})
    try:
        s.get(NSE, timeout=20)
        for c in NSE_CALLS:
            probe(s, NSE, c, "hist_nse")
    except Exception as e:
        print("  NSE unreachable:", e)
    print("\n== BSE ==")
    b = requests.Session(); b.headers.update(BSE_HEADERS)
    for c in BSE_CALLS:
        probe(b, BSE, c, "hist_bse")
    print("\nDone. Send me the printed output, and attach any saved hist_*.json file that has many rows.")


if __name__ == "__main__":
    main()
