"""
Run from the main GMP Desk folder:   python probe_bse.py      (needs: pip install requests)

Tries the BSE calls that other developers use for the issue list, SME issues and offer documents,
and saves what comes back to data/probe/ so we can see the real fields. Changes nothing on your site.
These endpoints are UNOFFICIAL and BSE sometimes refuses them (403). A refusal is useful to know too.
"""
import json, pathlib, sys, time

try:
    import requests
except ImportError:
    sys.exit("Run:  pip install requests")

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "data" / "probe"; OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://api.bseindia.com/BseIndiaAPI/api/"
CALLS = [
    "GetPublicIssue_par_updated/w",
    "GetPublicIssue_par/w",
    "GetPublicIssue/w",
    "Pubissues_IPODRHP_par_ng/w",
    "IPOTrackerN/w?Fromdt=20260101&Todt=20261231",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.bseindia.com",
    "Referer": "https://www.bseindia.com/",
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


def main():
    s = requests.Session(); s.headers.update(HEADERS)
    for call in CALLS:
        time.sleep(2)
        tag = call.split("?")[0].replace("/", "_")
        try:
            r = s.get(BASE + call, timeout=25)
        except Exception as e:
            print(f"  {call}: failed ({e})"); continue
        try:
            data = r.json()
        except ValueError:
            print(f"  {call}: {r.status_code}, {len(r.content)} bytes, not JSON (blocked or wrong call)"); continue
        (OUT / f"bse_{tag}.json").write_text(json.dumps(data, indent=2))
        rows = rows_of(data)
        keys = list(rows[0].keys())[:30] if rows else (list(data.keys())[:30] if isinstance(data, dict) else "-")
        sme = [x for x in rows if "SME" in json.dumps(x).upper()]
        print(f"  {call}: {r.status_code}, {len(r.content)} bytes, {len(rows)} rows, {len(sme)} mention SME -> data/probe/bse_{tag}.json\n      fields: {keys}")
    print("\nDone. Send me the printed output and any saved bse_*.json file bigger than ~300 bytes.")


if __name__ == "__main__":
    main()
