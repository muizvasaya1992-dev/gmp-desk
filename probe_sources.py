"""
Run on YOUR machine:   python probe_sources.py
(needs:  pip install requests)

Round 2: finds the NSE calls that could give us
  - category-wise subscription (QIB / NII / Retail)
  - lot size, dates, registrar, lead managers, reservation
All responses are saved to data/probe/ so we can see the real field names. Nothing here changes your site.

The endpoint names below are GUESSES. The reliable way to find the real ones is:
  Chrome > open an NSE IPO page > F12 > Network tab > filter "Fetch/XHR" > reload > look for api/ calls.
Add any you find to EXTRA below and run again.
"""
import json, pathlib, sys, time

try:
    import requests
except ImportError:
    sys.exit("Run:  pip install requests")

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "data" / "probe"; OUT.mkdir(parents=True, exist_ok=True)
NSE = "https://www.nseindia.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

SYMBOL, SERIES = "JPL", "EQ"          # change to any IPO that is open or upcoming right now
GUESSES = [
    "/api/ipo-detail?symbol={s}&series={r}",
    "/api/ipo-active-category?symbol={s}",
    "/api/ipo-bid-details?symbol={s}&series={r}",
    "/api/ipo-current-issue?symbol={s}",
    "/api/all-upcoming-issues?category=sme",
    "/api/public-past-issues?symbol={s}",
]
EXTRA = []                            # paste real paths from DevTools here, e.g. "/api/xyz?symbol={s}"


def main():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                      "Accept-Language": "en-US,en;q=0.9",
                      "Referer": NSE + "/market-data/all-upcoming-issues-ipo"})
    print("home:", s.get(NSE, timeout=20).status_code)
    for ep in GUESSES + EXTRA:
        path = ep.format(s=SYMBOL, r=SERIES)
        time.sleep(2)
        try:
            r = s.get(NSE + path, timeout=20)
            tag = path.strip("/").replace("/", "_").replace("?", "_").replace("=", "_").replace("&", "_")
            try:
                data = r.json()
            except ValueError:
                print(f"  {path}: {r.status_code}, {len(r.content)} bytes, not JSON")
                continue
            (OUT / f"v2_{tag}.json").write_text(json.dumps(data, indent=2))
            first = data[0] if isinstance(data, list) and data else data
            keys = list(first.keys())[:30] if isinstance(first, dict) else type(first).__name__
            print(f"  {path}: {r.status_code}, {len(r.content)} bytes -> data/probe/v2_{tag}.json\n      fields: {keys}")
        except Exception as e:
            print(f"  {path}: failed ({e})")
    print("\nDone. Send me the printed output and the larger saved files (anything over about 300 bytes).")


if __name__ == "__main__":
    main()
