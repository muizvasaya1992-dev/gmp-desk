"""Press coverage for each IPO.

Reads the news feeds that publishers put out for exactly this purpose (RSS), then matches each headline to the
IPOs we track. Only the headline, publisher, date and link are kept: the article text is never fetched or stored,
and every link sends the reader to the publisher's own page.

Why publishers' own feeds and not a news search engine: Google News' feed disallows our bot in robots.txt, and we
respect that rather than working around it. An RSS feed is published to be read by other sites.

The feed list can be changed without touching this file: put "news_feeds": ["https://...", ...] in data/config.json.
Each feed is fetched at most every 90 minutes, and robots.txt is checked before every fetch.
"""
import datetime as dt
import html as H
import json
import pathlib
import re
import time
from urllib.parse import urlparse

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
UA = "IPOShopBot/1.0 (+https://iposhop.in/)"

# Indian business press, IPO and markets sections. Any that refuses or disappears is skipped quietly.
FEEDS = [
    "https://www.moneycontrol.com/rss/iponews.xml",
    "https://www.moneycontrol.com/rss/marketreports.xml",
    "https://economictimes.indiatimes.com/markets/ipos/fpos/rssfeeds/14655708.cms",
    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "https://www.business-standard.com/rss/markets-106.rss",
    "https://www.livemint.com/rss/markets",
    "https://www.thehindubusinessline.com/markets/feeder/default.rss",
    "https://www.valueresearchonline.com/stories/type/22/ipo-analysis/rss",
    # further candidates: run "python news.py" to see which of these your side can use
    "https://www.financialexpress.com/market/ipo-news/feed/",
    "https://www.financialexpress.com/market/feed/",
    "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml",
    "https://www.ndtvprofit.com/stories.rss",
    "https://www.zeebiz.com/latest.xml/feed",
    "https://www.goodreturns.in/rss/ipo-news-fb.xml",
    "https://www.freepressjournal.in/stories.rss?section=business",
]

FEED_EVERY_MIN = 90              # a feed is re-read no more often than this
QUIET_HOURS = 12                 # a feed that refused us, or gave nothing, is left alone this long
MAX_ITEMS = 6                    # headlines kept per IPO
KEEP_PER_FEED = 80               # how many headlines from each feed we hold on to
MAX_AGE_DAYS = 75                # anything older than this is not news
PAUSE_S = 1.0
TIMEOUT = 20

STOP_WORDS = {"limited", "ltd", "private", "pvt", "ipo", "india", "the", "and", "of", "sme", "industries",
              "projects", "technologies", "solutions", "enterprises", "company", "corporation", "services"}


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


def _date(raw):
    """'Fri, 31 Oct 2026 06:30:00 GMT' or '2026-10-31T06:30:00+05:30' -> '2026-10-31'."""
    raw = raw or ""
    m = re.search(r"(\d{1,2}) ([A-Za-z]{3}) (\d{4})", raw)
    if m:
        try:
            return dt.datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y").date().isoformat()
        except ValueError:
            return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    return m.group(0) if m else None


def _tag(block, tag):
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.S | re.I)
    if not m:
        return ""
    return H.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))).strip()


def parse_feed(xml, publisher=""):
    """Every headline in one feed, as {title, url, source, date}."""
    out = []
    blocks = re.findall(r"<item[^>]*>(.*?)</item>", xml or "", re.S | re.I) or \
             re.findall(r"<entry[^>]*>(.*?)</entry>", xml or "", re.S | re.I)
    for b in blocks:
        title = re.sub(r"\s+", " ", _tag(b, "title")).strip()
        url = _tag(b, "link")
        if not url:
            m = re.search(r'<link[^>]*href="([^"]+)"', b)
            url = m.group(1) if m else ""
        if not title or not url.startswith("http"):
            continue
        day = _date(_tag(b, "pubDate") or _tag(b, "published") or _tag(b, "updated") or _tag(b, "dc:date"))
        src = publisher or urlparse(url).netloc.replace("www.", "")
        out.append({"title": title[:180], "url": url, "source": src[:60], "date": day})
    return out


def _publisher(url):
    host = urlparse(url).netloc.replace("www.", "")
    known = {"moneycontrol.com": "Moneycontrol", "indiatimes.com": "Economic Times",
             "business-standard.com": "Business Standard", "livemint.com": "Mint",
             "thehindubusinessline.com": "BusinessLine"}
    for k, v in known.items():
        if host.endswith(k):
            return v
    return host


def about(item, name, today):
    """Is this headline really about this company? Two words of the name must appear, not just one."""
    keys = _keys(name)
    if not keys:
        return False
    low = item["title"].lower()
    hits = sum(1 for k in keys if k in low)
    if hits < min(2, len(keys)):
        return False
    if item.get("date"):
        try:
            if (today - dt.date.fromisoformat(item["date"])).days > MAX_AGE_DAYS:
                return False
        except ValueError:
            pass
    return True


def run(root, recs, today, now=None, fixtures=None, offline=False, log=print):
    """Fills r['news'] for live IPOs from the publishers' feeds. Safe to call on every build."""
    root = pathlib.Path(root)
    now = now or dt.datetime.now(IST)
    path = root / "data" / "news.json"
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        store = {}
    if not isinstance(store, dict):
        store = {}
    cache = store.get("_feeds") if isinstance(store.get("_feeds"), dict) else {}

    try:
        cfg = json.loads((root / "data" / "config.json").read_text(encoding="utf-8")) or {}
    except Exception:
        cfg = {}
    feeds = [f for f in (cfg.get("news_feeds") or FEEDS) if isinstance(f, str) and f.startswith("http")]

    requests = None
    if not fixtures and not offline:
        try:
            import requests as _rq
            requests = _rq
        except Exception:
            requests = None

    fetched = 0
    for url in feeds:
        got = cache.get(url) or {}
        wait = dt.timedelta(minutes=FEED_EVERY_MIN) if got.get("items") else dt.timedelta(hours=QUIET_HOURS)
        try:
            age_ok = now - dt.datetime.fromisoformat(got["fetched"]) < wait
        except Exception:
            age_ok = False
        if age_ok:
            continue                                     # still fresh, or recently refused: do not ask again
        xml = ""
        try:
            if fixtures:
                f = pathlib.Path(fixtures) / (re.sub(r"[^a-z0-9]+", "-", urlparse(url).netloc.lower()).strip("-") + ".xml")
                xml = f.read_text(encoding="utf-8") if f.exists() else ""
            elif requests:
                if not _robots_ok(requests, url):
                    cache[url] = {"fetched": now.isoformat(timespec="minutes"), "items": got.get("items") or [],
                                  "note": "robots.txt does not allow our bot"}
                    log(f"  News: {_publisher(url)} does not allow our bot, skipped")
                    continue
                if fetched:
                    time.sleep(PAUSE_S)
                r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/rss+xml,application/xml,text/xml"}, timeout=TIMEOUT)
                fetched += 1
                xml = r.text if r.status_code == 200 else ""
                if r.status_code != 200:
                    cache[url] = {"fetched": now.isoformat(timespec="minutes"), "items": got.get("items") or [],
                                  "note": f"HTTP {r.status_code}"}
                    log(f"  News: {_publisher(url)} returned {r.status_code}, skipped")
                    continue
            items = parse_feed(xml, _publisher(url))
            cache[url] = {"fetched": now.isoformat(timespec="minutes"),
                          "items": (items or (got.get("items") or []))[:KEEP_PER_FEED]}
        except Exception as e:
            log(f"  News: {_publisher(url)} failed ({type(e).__name__}), using what we had")

    pool = []
    for url, got in cache.items():
        pool += got.get("items") or []
    seen, uniq = set(), []
    for x in sorted(pool, key=lambda y: y.get("date") or "", reverse=True):
        k = (x.get("title") or "").lower()[:70]
        if k and k not in seen:
            seen.add(k)
            uniq.append(x)

    live = [r for r in recs if r.get("status") in ("upcoming", "open", "closing_today", "closed") and r.get("name")]
    matched = 0
    for r in live:
        hits = [x for x in uniq if about(x, r["name"], today)][:MAX_ITEMS]
        if hits:
            r["news"] = hits
            store[r["id"]] = {"checked": now.isoformat(timespec="minutes"), "items": hits}
            matched += len(hits)
        elif store.get(r["id"], {}).get("items"):
            r["news"] = store[r["id"]]["items"]           # keep what was found earlier if this run came back empty
    for k in [k for k in store if k != "_feeds" and k not in {r["id"] for r in live}]:
        store.pop(k)
    store["_feeds"] = {u: v for u, v in cache.items() if u in feeds}
    if fetched or fixtures:
        log(f"  News: {fetched} feed(s) read, {len(uniq)} headline(s) available, {matched} matched to IPOs")
    try:
        path.write_text(json.dumps(store, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    except OSError:
        pass
    return recs


# ------------------------------------------------------------------ run "python news.py" to test the feeds
if __name__ == "__main__":
    import sys
    import requests
    root = pathlib.Path(__file__).resolve().parent
    try:
        cfg = json.loads((root / "data" / "config.json").read_text(encoding="utf-8")) or {}
    except Exception:
        cfg = {}
    feeds = [f for f in (cfg.get("news_feeds") or FEEDS) if isinstance(f, str)]
    print(f"Checking {len(feeds)} news feeds as {UA}\n")
    good = []
    for u in feeds:
        name = _publisher(u)
        try:
            if not _robots_ok(requests, u):
                print(f"  BLOCKED  {name:20} robots.txt does not allow our bot   {u}")
                continue
            r = requests.get(u, headers={"User-Agent": UA, "Accept": "application/rss+xml,application/xml,text/xml"}, timeout=TIMEOUT)
            if r.status_code != 200:
                print(f"  HTTP {r.status_code}  {name:20} {u}")
                continue
            items = parse_feed(r.text, name)
            if not items:
                print(f"  EMPTY    {name:20} loaded but no headlines could be read   {u}")
                continue
            good.append(u)
            print(f"  OK       {name:20} {len(items):3} headlines, newest: {items[0]['date']} - {items[0]['title'][:60]}")
        except Exception as e:
            print(f"  FAILED   {name:20} {type(e).__name__}: {str(e)[:70]}")
        time.sleep(PAUSE_S)
    print(f"\n{len(good)} of {len(feeds)} feeds usable.")
    if good and len(good) < len(feeds):
        print("\nTo keep only the ones that work, put this in data/config.json:")
        print('  "news_feeds": ' + json.dumps(good, indent=4))
    sys.exit(0 if good else 1)
