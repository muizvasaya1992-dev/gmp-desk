"""
Run from the main project folder:   python probe_bse_demand.py      (needs: pip install requests)

Looks for the BSE call that gives live subscription ("demand") for an issue. BSE's site is a JavaScript app,
so this opens its issue page, downloads the scripts that page uses, and lists every API name inside them that
looks related to bidding or demand. It only READS public pages and changes nothing on your site.
Send the printed output (and data/probe/bse_js_endpoints.txt if it is not too long).
"""
import pathlib, re, sys, time
from urllib.parse import urljoin
try:
    import requests
except ImportError:
    sys.exit("Run:  pip install requests")

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "data" / "probe"; OUT.mkdir(parents=True, exist_ok=True)
PAGE = "https://www.bseindia.com/markets/PublicIssues/IPOIssues_new.aspx?id=1&Type=p"
HEAD = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.bseindia.com/",
}
WORDS = re.compile(r"bid|demand|subscri|cover|ipo|issue|public", re.I)


def main():
    s = requests.Session(); s.headers.update(HEAD)
    print("Opening the BSE issue page...")
    r = s.get(PAGE, timeout=30)
    print(f"  page: {r.status_code}, {len(r.text)} characters")
    (OUT / "bse_issue_page.html").write_text(r.text, encoding="utf-8")
    found = set(re.findall(r"BseIndiaAPI/api/([A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)?)", r.text))
    srcs = [urljoin(PAGE, u) for u in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', r.text)]
    srcs = [u for u in srcs if "bseindia" in u and not re.search(r"jquery|bootstrap|google|analytics|gtag", u, re.I)]
    print(f"  scripts to read: {len(srcs)}")
    for u in srcs[:40]:
        time.sleep(1)
        try:
            t = s.get(u, timeout=40).text
        except Exception as e:
            print(f"  skipped {u[-60:]}: {e}"); continue
        names = set(re.findall(r"BseIndiaAPI/api/([A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)?)", t))
        names |= set(re.findall(r"[\"']([A-Za-z0-9_]{6,40}/w)[\"']", t))
        found |= names
        print(f"  {u.split('/')[-1][:50]:50} {len(t):>9} chars, {len(names)} API names")
    (OUT / "bse_js_endpoints.txt").write_text("\n".join(sorted(found)), encoding="utf-8")
    hits = sorted(n for n in found if WORDS.search(n))
    print(f"\nAPI names found in total: {len(found)}")
    print("Names that look related to bids, demand or issues:")
    for n in hits:
        print("   ", n)
    if not found:
        print("None found. BSE may load its data in a way that hides the names. Send me bse_issue_page.html's first 40 lines instead.")


if __name__ == "__main__":
    main()
