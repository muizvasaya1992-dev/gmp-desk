"""One-off look at a few pages, so the reader can be written to fit them. Saves into data/probe/ (never published).
Run:  python probe_site.py
It reads each site's robots.txt first and only fetches what that allows. For a site whose owner has given you
permission and whose robots.txt blocks bots, run:  python probe_site.py --permitted
"""
import re
import sys
import json
import pathlib
import urllib.robotparser as rp
from urllib.parse import urlparse
import requests

UA = "IPOShopBot/1.0 (+https://iposhop.in/)"
PAGES = [
    "https://ipogyani.com/ipo-gmp-today",
    "https://ipogyani.com/sme",
    "https://ipogyani.com/ipo-subscription",
    "https://www.investorgain.com/report/live-ipo-gmp/331/",
]
OUT = pathlib.Path(__file__).resolve().parent / "data" / "probe"
OUT.mkdir(parents=True, exist_ok=True)
PERMITTED = "--permitted" in sys.argv
robots = {}


def allowed(url):
    if PERMITTED:
        return True
    u = urlparse(url)
    host = f"{u.scheme}://{u.netloc}"
    if host not in robots:
        r = None
        try:
            q = requests.get(host + "/robots.txt", headers={"User-Agent": UA}, timeout=20)
            (OUT / f"{u.netloc}_robots.txt").write_text(q.text, encoding="utf-8")
            print(f"robots.txt for {u.netloc}: status {q.status_code}, {len(q.text)} characters (saved)")
            if q.status_code == 200:
                r = rp.RobotFileParser()
                r.parse(q.text.splitlines())
                rules = [ln.strip() for ln in q.text.splitlines() if re.match(r"\s*(user-agent|disallow|allow)\s*:", ln, re.I)]
                print("   rules:", " | ".join(rules[:14]) or "(none)")
            elif q.status_code == 404:
                r = rp.RobotFileParser()
                r.parse([])                          # no robots.txt: nothing is disallowed
            else:
                print("   the site refused or errored on robots.txt: skipping it to be safe")
        except Exception as e:
            print(f"robots.txt for {u.netloc} could not be read ({type(e).__name__}): skipping this site to be safe")
        robots[host] = r
    r = robots[host]
    return bool(r and r.can_fetch(UA, url))


def get(url, accept="text/html,application/json"):
    return requests.get(url, headers={"User-Agent": UA, "Accept": accept, "Referer": f"{urlparse(url).scheme}://{urlparse(url).netloc}/"}, timeout=30)


def main():
    n = 0
    for url in PAGES:
        print("\n==", url)
        if not allowed(url):
            print("   robots.txt does not allow this for our bot (or could not be read): skipped")
            continue
        try:
            r = get(url)
        except Exception as e:
            print("   failed:", type(e).__name__)
            continue
        n += 1
        host = urlparse(url).netloc.replace("www.", "").split(".")[0]
        name = f"{host}_{re.sub(r'[^a-z0-9]+', '-', urlparse(url).path.lower()).strip('-') or 'home'}"
        html = r.text
        (OUT / f"{name}.html").write_text(html, encoding="utf-8")
        print(f"   status {r.status_code}, {len(html)} characters, saved as {name}.html")
        print("   table rows:", len(re.findall(r"<tr\b", html)), "| mentions of SME:", len(re.findall(r"\bSME\b", html)))
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if m:
            (OUT / f"{name}_nextdata.json").write_text(m.group(1), encoding="utf-8")
            print(f"   found the page's built-in data: saved {name}_nextdata.json ({len(m.group(1))} characters)")
        addrs = sorted(set(re.findall(r"""["'](/(?:api|_next/data)/[^"'\s<>]*)["']""", html)))
        for a in addrs[:8]:
            print("   data address in page:", a)
    print(f"\nDone: {n} page(s) saved in data/probe/. Please send me the files there that were just created "
          "(zip the folder, or attach the .html and .json files).")


main()
