"""Press coverage for each IPO.

Reads Google News' public search feed for each live IPO and keeps only the headline, the publisher, the date and
the link. The article text is never copied and never stored: every item sends the reader to the publisher's own page.

It checks robots.txt before fetching and skips politely if the feed is not allowed for our bot, so the build says
plainly when nothing can be collected instead of quietly going around it.
"""
import datetime as dt
import html as H
import json
import pathlib
import re
import time
from urllib.parse import quote_plus, urlparse

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
UA = "IPOShopBot/1.0 (+https://iposhop.in/)"
FEED = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
MAX_PER_RUN = 15                 # at most this many IPOs looked up in one run
EVERY_HOURS = 20                 # and each IPO at most once a day
MAX_ITEMS = 6                    # headlines kept per IPO
MAX_AGE_DAYS = 75                # anything older than this is not news
PAUSE_S = 1.5
TIMEOUT = 20

STOP_WORDS = {"limited", "ltd", "private", "pvt", "ipo", "india", "the", "and", "of", "sme", "industries",
              "projects", "technologies", "solutions", "enterprises", "company", "corporation"}


def _keys(name):
    w = [x for x in re.split(r"[^A-Za-z0-9]+", (name or "").lower()) if len(x) > 2]
    return [x for x in w if x not in STOP_WORDS] or w


def _robots_ok(requests, url):
    import urllib.robotparser as rp
    u = urlparse(url)
    try:
        q = requests.get(f"{u.scheme}://{u.netloc}/robots.txt", headers={"User-Agent": UA}, timeout=10)
    except Exception:
        return False
    if q.status_code == 404:
        return True
    if q.status_code != 200:
        return False
    p = rp.RobotFileParser()
    p.parse(q.text.splitlines())
    return p.can_fetch(UA, url)


def _date(rfc):
    """'Fri, 31 Oct 2026 06:30:00 GMT' -> '2026-10-31'."""
    m = re.search(r"(\d{1,2}) ([A-Za-z]{3}) (\d{4})", rfc or "")
    if not m:
        return None
    try:
        return dt.datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y").date().isoformat()
    except ValueError:
        return None


def parse(xml, name, today):
    """Headlines that are really about this company, newest first."""
    keys = _keys(name)
    out = []
    for block in re.findall(r"<item>(.*?)</item>", xml or "", re.S):
        get = lambda tag: (lambda m: H.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))).strip() if m else "")(
            re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.S))
        title, url = get("title"), get("link")
        if not title or not url.startswith("http"):
            continue
        source = get("source") or ""
        day = _date(get("pubDate"))
        if day and (today - dt.date.fromisoformat(day)).days > MAX_AGE_DAYS:
            continue
        low = title.lower()
        if keys and keys[0] not in low:                       # a story about some other company
            continue
        if len(keys) > 1 and keys[1] not in low and len(keys[0]) < 6:
            continue                                          # a short first word needs the second one too
        title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title).strip()   # the feed appends " - Publisher"
        out.append({"title": title[:160], "url": url, "source": source[:60], "date": day})
    out.sort(key=lambda x: x["date"] or "", reverse=True)
    seen, uniq = set(), []
    for x in out:
        k = x["title"].lower()[:70]
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq[:MAX_ITEMS]


def run(root, recs, today, now=None, fixtures=None, offline=False, log=print):
    """Fills r['news'] for live IPOs. Safe to call on every build; each IPO is looked up once a day."""
    root = pathlib.Path(root)
    now = now or dt.datetime.now(IST)
    path = root / "data" / "news.json"
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        store = {}
    if not isinstance(store, dict):
        store = {}
    live = [r for r in recs if r.get("status") in ("upcoming", "open", "closing_today", "closed") and r.get("name")]
    ids = {r["id"] for r in live}
    for k in [k for k in store if k not in ids]:
        store.pop(k)
    requests = None
    if not fixtures and not offline:
        try:
            import requests as _rq
            requests = _rq
        except Exception:
            requests = None
    looked = found = 0
    blocked = False
    for r in live:
        was = store.get(r["id"])
        fresh = False
        if was:
            try:
                fresh = now - dt.datetime.fromisoformat(was["checked"]) < dt.timedelta(hours=EVERY_HOURS)
            except Exception:
                fresh = False
        if not fresh and not blocked and (fixtures or (requests and looked < MAX_PER_RUN)):
            url = FEED.format(q=quote_plus(f'"{r["name"]}" IPO'))
            try:
                if fixtures:
                    f = pathlib.Path(fixtures) / f"news_{r['id']}.xml"
                    xml = f.read_text(encoding="utf-8") if f.exists() else ""
                else:
                    if not _robots_ok(requests, url):
                        log("  News: the news feed does not allow our bot, skipping press coverage")
                        blocked = True
                        continue
                    if looked:
                        time.sleep(PAUSE_S)
                    resp = requests.get(url, headers={"User-Agent": UA, "Accept": "application/rss+xml,text/xml"}, timeout=TIMEOUT)
                    xml = resp.text if resp.status_code == 200 else ""
                    looked += 1
                items = parse(xml, r["name"], today)
                was = {"checked": now.isoformat(timespec="minutes"), "items": items}
                store[r["id"]] = was
                found += len(items)
            except Exception as e:                            # never let news stop the build
                log(f"  News: {r['id']}: {type(e).__name__}, keeping what we had")
                store[r["id"]] = was = {"checked": now.isoformat(timespec="minutes"), "items": (was or {}).get("items", [])}
        if was and was.get("items"):
            r["news"] = was["items"]
    if looked or fixtures:
        log(f"  News: {looked} IPO(s) looked up, {found} headline(s) kept")
    try:
        path.write_text(json.dumps(store, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    except OSError:
        pass
    return recs
