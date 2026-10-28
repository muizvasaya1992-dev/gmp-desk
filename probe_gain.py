"""One-off look at the GMP page, so the reader can be written to fit it. Saves into data/probe/ (never published).
Run:  python probe_gain.py
It reads robots.txt first and only fetches what that allows. If robots.txt blocks a page you have the owner's written
permission for, run:  python probe_gain.py --permitted
"""
import re
import sys
import json
import pathlib
import urllib.robotparser as rp
import requests

UA = "IPOShopBot/1.0 (+https://iposhop.in/)"
BASE = "https://www.investorgain.com"
PAGE = BASE + "/report/live-ipo-gmp/331/"
OUT = pathlib.Path(__file__).resolve().parent / "data" / "probe"
OUT.mkdir(parents=True, exist_ok=True)
PERMITTED = "--permitted" in sys.argv


def get(url, accept="text/html,application/json"):
    return requests.get(url, headers={"User-Agent": UA, "Accept": accept, "Referer": BASE + "/"}, timeout=30)


def main():
    robots = rp.RobotFileParser()
    robots.set_url(BASE + "/robots.txt")
    try:
        robots.read()
        r = get(BASE + "/robots.txt")
        (OUT / "gain_robots.txt").write_text(r.text, encoding="utf-8")
        print("robots.txt:", r.status_code, len(r.text), "characters (saved)")
    except Exception as e:
        print("robots.txt could not be read:", type(e).__name__)
    allowed = lambda u: PERMITTED or robots.can_fetch(UA, u)

    print("\n== GMP page")
    if not allowed(PAGE):
        print("   robots.txt does not allow this page for our bot. Stopping. (With written permission, run again with --permitted.)")
        return
    r = get(PAGE)
    html = r.text
    (OUT / "gain_page.html").write_text(html, encoding="utf-8")
    print("   status", r.status_code, "-", len(html), "characters (saved as gain_page.html)")
    print("   rows in the page's own table:", len(re.findall(r"<tr\b", html)))
    print("   mentions of SME:", len(re.findall(r"\bSME\b", html)))

    cands = sorted(set(re.findall(r"https?://[a-z0-9.\-]*investorgain\.com/[^\s\"'<>\\)]*", html, re.I)))
    api = [c for c in cands if re.search(r"(cloud|api|data|report)", c, re.I) and not re.search(r"\.(css|js|png|jpg|svg|ico|woff2?)(\?|$)", c, re.I)]
    print("   data addresses mentioned in the page (%d):" % len(api))
    for c in api[:25]:
        print("     ", c)

    # the table is usually filled by a script: try the addresses that look like data, and a few likely names
    tries = list(api)
    tries += [f"https://webnodejs.investorgain.com/cloud/report/data-read/331/1/{m}/2026/2026-27/0/all?search=&v=" for m in (10, 11)]
    done = 0
    for u in tries:
        if done >= 6 or not allowed(u):
            continue
        try:
            q = get(u, "application/json")
        except Exception as e:
            print("   ", u[:90], "->", type(e).__name__)
            continue
        ct = q.headers.get("content-type", "")
        if q.status_code == 200 and ("json" in ct or q.text.lstrip().startswith(("{", "["))):
            done += 1
            name = f"gain_data_{done}.json"
            (OUT / name).write_text(q.text, encoding="utf-8")
            print(f"   saved {name}: {len(q.text)} characters from {u[:90]}")
            try:
                j = json.loads(q.text)
                print("     top-level:", list(j)[:8] if isinstance(j, dict) else f"list of {len(j)}")
            except Exception:
                pass
    if not done:
        print("   no data address answered with JSON; the page file alone is enough to adjust the reader.")
    print("\nDone. Please send me the files in data/probe/ that start with gain_ (zip them or paste the first lines).")


main()
