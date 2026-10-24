"""
Run from the main project folder:   python probe_bse_demand2.py      (needs: pip install requests)

Second pass. The first probe found no data calls by name, so this one reads inside BSE's main script
(about 860 KB) and prints the quoted text that mentions bids, demand, subscription or API paths, with a
little context around each. It only READS public files and changes nothing on your site.
Send the printed output.
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
    "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.bseindia.com/",
}
KEY = re.compile(r"bid|demand|subscri|cover|apiurl|api/|bseindiaapi|\.aspx|/w[\"'`?]", re.I)
STR = re.compile(r"""(["'`])((?:(?!\1)[^\\\n]){6,110})\1""")


def get(s, url):
    r = s.get(url, timeout=60)
    return r.status_code, r.text


def strings_of(text):
    seen, out = set(), []
    for m in STR.finditer(text):
        t = m.group(2).strip()
        if t in seen or not KEY.search(t) or re.search(r"\s{3,}|[{};]{2,}", t):
            continue
        seen.add(t); out.append((m.start(), t))
    return out


def main():
    s = requests.Session(); s.headers.update(HEAD)
    print("Opening the BSE issue page...")
    code, html = get(s, PAGE)
    srcs = [urljoin(PAGE, u) for u in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)]
    print(f"  page {code}; script addresses:")
    for u in srcs: print("   ", u)
    found_any = False
    for u in srcs:
        if not re.search(r"main|env|scripts", u): continue
        time.sleep(1)
        try:
            code, text = get(s, u)
        except Exception as e:
            print(f"\n== {u.split('/')[-1]}: failed ({e})"); continue
        name = u.split("/")[-1].split("?")[0]
        (OUT / f"bse_{name}").write_text(text, encoding="utf-8", errors="ignore")
        print(f"\n== {name}: HTTP {code}, {len(text):,} characters (saved to data/probe/)")
        if name.startswith("env"):
            print("   first 600 characters:", re.sub(r"\s+", " ", text[:600]))
            continue
        hits = strings_of(text)
        print(f"   {len(hits)} quoted strings mention bids, demand, subscription or API paths. First 70:")
        for pos, t in hits[:70]:
            print(f"   - {t}")
            found_any = True
        for word in ("Demand", "Subscri", "BidDet", "Bidding"):
            for m in list(re.finditer(word, text))[:4]:
                a, b = max(0, m.start() - 90), min(len(text), m.end() + 110)
                print(f"   [{word}] ...{re.sub(chr(10), ' ', text[a:b])}...")
    if not found_any:
        print("\nNothing useful found. BSE's page may not show demand at all; next we would use your browser's Network tab instead.")


if __name__ == "__main__":
    main()
