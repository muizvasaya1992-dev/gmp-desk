"""AI GMP estimate.

Asks Gemini, with Google Search switched on, what the grey market premium is for each live IPO, and keeps
the answer as a separate figure next to the GMP our usual sources report. It never replaces that figure.

Rules the code enforces, so a made-up number cannot reach the site:
  * the model must have actually searched: the reply has to carry Google's grounding data, or we drop it;
  * the number has to be sane against the issue price;
  * the date it gives has to be recent;
  * every answer is stored with the pages it came from, and those are shown to the reader.
If any of that fails we keep "no estimate" rather than publishing a guess.

The key lives in the GEMINI_API_KEY secret or in data/private.json ("gemini_api_key"), never in this file.
"""
import datetime as dt
import json
import os
import pathlib
import re
import time

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
UA = "IPOShopBot/1.0 (+https://iposhop.in/)"
# Tried in this order until one answers. Put your own choice in data/private.json ("gemini_model") to pin it.
# A newer Flash follows the "report only what you found" rule better; the older one is the safety net.
MODELS = ["gemini-3.8-flash", "gemini-flash-latest", "gemini-2.5-flash"]
MODEL = MODELS[0]
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
PASSES = 2                       # ask twice, independently, and publish only if the two answers agree
MAX_PER_RUN = 15                 # at most this many IPOs asked about in one run
EVERY_HOURS = 20                 # and each IPO at most once a day
PAUSE_S = 2.0
MAX_PCT_OF_PRICE = 60            # a premium above this share of the issue price is treated as a bad answer
MAX_AGE_DAYS = 4                 # an answer older than this is not worth showing
TIMEOUT = 30
FAIL_RETRY_H = 3                 # after a failure, wait this long before asking about that IPO again

PROMPT = """You are a careful data assistant for an Indian IPO information website. Today is {today} (IST).
Accuracy matters far more than giving an answer. An answer of "not found" is correct and useful.

Find the latest grey market premium (GMP) for this exact IPO in India:
  Company: {name}
  Type: {seg}
  Issue price: Rs {price} per share
  Bidding: {open} to {close}

Rules:
1. Report a rupee figure only if you have read it on a page you found in this search, stated as the GMP for
   this company's IPO. Copy it exactly as written: do not round it, average it or convert a percentage into rupees.
2. Give the date the page itself puts on that figure, not today's date, unless the page says it is today's.
3. If pages disagree, use the most recent. If two recent pages disagree by more than Rs 5, say so in "disagreement".
4. Never work a GMP back from a predicted listing price, a subscription figure or a rating.
5. Never use a figure for a different company, a different year, or an IPO with a similar name. Check the
   company name and the issue price on the page match the ones above.
6. If you cannot find a GMP for this exact IPO, reply with "found": false. Do not guess.
7. A GMP of 0 is a real answer when a page states the grey market shows no premium.
8. "quote" must be the exact wording from the page that contains the figure, copied, not paraphrased.

Reply with JSON only, no other text, in exactly this shape:
{{"found": true or false,
  "gmp_rupees": number or null,
  "as_of": "YYYY-MM-DD" or null,
  "quote": "the exact sentence from the page containing the figure, under 160 characters",
  "pages_agreeing": whole number of separate pages you saw this same figure on,
  "disagreement": "other figures you saw, or empty",
  "sources": ["direct url of each page you used"],
  "note": "under 15 words"}}"""


def _private(root):
    try:
        return json.loads((pathlib.Path(root) / "data" / "private.json").read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _key(root):
    return (os.environ.get("GEMINI_API_KEY") or str(_private(root).get("gemini_api_key") or "")).strip()


def _models(root):
    want = os.environ.get("GEMINI_MODEL") or _private(root).get("gemini_model") or ""
    if isinstance(want, str):
        want = [w.strip() for w in want.split(",") if w.strip()]
    want = [w for w in (want or []) if isinstance(w, str)]
    return want + [m for m in MODELS if m not in want]


class ApiError(Exception):
    """Carries what Google actually said, so the log is useful instead of just 'HTTPError'."""

    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}"[:300])
        self.status, self.message = status, message

    @property
    def permanent(self):
        return self.status in (400, 401, 403, 404)      # a wrong model, tool or key: trying again will not help


def _ask(key, prompt, model=MODEL):
    """One grounded request. Returns (text, [source urls]); raises ApiError with Google's own message."""
    import requests
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0}}
    r = requests.post(ENDPOINT.format(model=model), params={"key": key},
                      json=body, timeout=TIMEOUT, headers={"Content-Type": "application/json"})
    if r.status_code >= 400:
        try:
            msg = ((r.json() or {}).get("error") or {}).get("message") or r.text[:200]
        except ValueError:
            msg = r.text[:200]
        raise ApiError(r.status_code, msg)
    return _read_reply(r.json())


def _read_reply(data):
    try:
        cand = (data.get("candidates") or [])[0]
    except IndexError:
        return None, []
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
    gm = cand.get("groundingMetadata") or {}
    urls = []
    for c in gm.get("groundingChunks") or []:
        u = ((c or {}).get("web") or {}).get("uri")
        if u:
            urls.append(u)
    for q in gm.get("webSearchQueries") or []:        # proof it searched even when no chunk is returned
        if q and not urls:
            urls.append("")
    return text, urls


class GiveUp(Exception):
    """No model is going to work this run, so stop asking instead of waiting on every IPO in turn."""


def _first_that_answers(key, prompt, models, chosen, dead, log=print):
    """Uses whichever model worked last; a model that fails for good is dropped for the rest of the run."""
    order = ([chosen] if chosen and chosen not in dead else []) + [m for m in models if m not in dead and m != chosen]
    last = None
    for m in order:
        try:
            text, urls = _ask(key, prompt, m)
            if text:
                if m != chosen:
                    log(f"  AI GMP: using model {m}")
                return text, urls, m
            last = ApiError(0, "empty reply")
        except ApiError as e:
            last = e
            if e.status == 429:
                raise GiveUp(f"the daily quota or rate limit is used up ({e.message[:120]})")
            if e.permanent:
                dead.add(m)
                log(f"  AI GMP: {m} cannot be used — {e.message[:160]}")
            else:
                log(f"  AI GMP: {m} failed ({e}), trying the next model")
        except Exception as e:                           # a timeout or a dropped connection: try the next model
            last = e
            log(f"  AI GMP: {m} failed ({type(e).__name__}), trying the next model")
    if all(m in dead for m in models):
        raise GiveUp("none of the models would answer, see the messages above")
    if last:
        raise last
    return None, [], chosen


def _json_from(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, dict) else None
    except ValueError:
        return None


def _clean_sources(model_urls, grounding_urls):
    out = []
    for u in list(model_urls or []) + list(grounding_urls or []):
        if isinstance(u, str) and u.startswith("http") and u not in out:
            out.append(u)
    return out[:4]


def check(answer, grounding_urls, price, today):
    """Turns one reply into a figure we are willing to publish, or None with the reason."""
    if not isinstance(answer, dict):
        return None, "the reply was not readable"
    if not grounding_urls:
        return None, "the model did not search, so the answer is not evidence"
    if not answer.get("found"):
        return None, "no GMP found for this IPO"
    g = answer.get("gmp_rupees")
    if isinstance(g, str):
        try:
            g = float(re.sub(r"[^\d.\-]", "", g))
        except ValueError:
            g = None
    if not isinstance(g, (int, float)):
        return None, "no number in the reply"
    g = round(float(g), 2)
    if price and abs(g) > price * MAX_PCT_OF_PRICE / 100:
        return None, f"the figure is more than {MAX_PCT_OF_PRICE}% of the issue price"
    at = answer.get("as_of")
    day = None
    if isinstance(at, str):
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", at.strip())
        if m:
            try:
                day = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                day = None
    if day is None:
        day = today
    if abs((today - day).days) > MAX_AGE_DAYS:
        return None, "the figure is out of date"
    quote = re.sub(r"\s+", " ", str(answer.get("quote") or "")).strip()[:200]
    if quote and not _quote_backs(quote, g):
        return None, "the wording it quoted does not contain that figure"
    note = str(answer.get("note") or "")[:120]
    srcs = _clean_sources(answer.get("sources"), grounding_urls)
    if not srcs:
        return None, "the reply did not say where the figure came from"
    pages = answer.get("pages_agreeing")
    return {"gmp": int(g) if float(g).is_integer() else g, "as_of": day.isoformat(), "quote": quote,
            "pages": int(pages) if isinstance(pages, (int, float)) and pages > 0 else None,
            "disagreement": str(answer.get("disagreement") or "")[:120],
            "note": note, "sources": srcs}, ""


def _quote_backs(quote, value):
    """The figure has to appear in the wording the model says it copied."""
    nums = {n.rstrip(".").rstrip("0").rstrip(".") if "." in n else n for n in re.findall(r"-?\d[\d,]*(?:\.\d+)?", quote.replace(",", ""))}
    want = {str(int(value)) if float(value).is_integer() else str(value), str(value)}
    return bool(nums & {w.rstrip(".") for w in want})


def agree(a, b, price):
    """Two separate answers are only published when they land on the same figure."""
    if not a and not b:
        return None, "neither check found a figure"
    if not a or not b:
        return None, "only one of the two checks found a figure, so it is not confirmed"
    gap = abs(a["gmp"] - b["gmp"])
    tol = max(2.0, abs(a["gmp"]) * 0.12, (price or 0) * 0.01)
    if gap > tol:
        return None, f"the two checks disagreed (₹{a['gmp']:g} and ₹{b['gmp']:g})"
    best = a if (a.get("pages") or 0) >= (b.get("pages") or 0) else b
    v = (a["gmp"] + b["gmp"]) / 2
    out = dict(best)
    out["gmp"] = int(round(v)) if float(round(v, 2)).is_integer() else round(v, 2)
    out["as_of"] = max(a["as_of"], b["as_of"])
    out["sources"] = _clean_sources(a["sources"], b["sources"])
    out["checks"] = 2
    out["spread"] = round(gap, 2)
    return out, ""


def cross_check(value, reported, price):
    """An AI figure that contradicts what our real sources report is not published."""
    if reported is None:
        return True, ""
    tol = max(5.0, abs(reported) * 0.35, (price or 0) * 0.02)
    if abs(value - reported) <= tol:
        return True, ""
    return False, f"it disagrees with the ₹{reported:g} our own sources report"


def page_shows(urls, value, log=print):
    """Opens the page the figure was taken from and looks for that number next to the words GMP or premium.
    True / False / None when the page could not be opened. It never changes the figure, only how we describe it."""
    try:
        import requests
    except Exception:
        return None
    want = str(int(value)) if float(value).is_integer() else str(value)
    for u in (urls or [])[:2]:
        try:
            if not _robots_ok(requests, u):
                continue
            r = requests.get(u, headers={"User-Agent": UA, "Accept": "text/html"}, timeout=15)
            if r.status_code != 200:
                continue
            text = re.sub(r"<[^>]+>", " ", r.text)
            text = re.sub(r"\s+", " ", text).lower()
            for m in re.finditer(r"(?<![\d.])" + re.escape(want) + r"(?![\d.])", text):
                near = text[max(0, m.start() - 140):m.end() + 140]
                if "gmp" in near or "premium" in near:
                    return True
        except Exception:
            continue
    return None if not urls else False


def _robots_ok(requests, url):
    import urllib.robotparser as rp
    from urllib.parse import urlparse
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


def run(root, recs, today, now=None, fixtures=None, offline=False, log=print):
    """Fills r['ai_gmp'] on every live IPO we have an answer for. Safe to call on every build."""
    root = pathlib.Path(root)
    now = now or dt.datetime.now(IST)
    path = root / "data" / "gmp_ai.json"
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        store = {}
    if not isinstance(store, dict):
        store = {}
    live = [r for r in recs
            if r.get("status") in ("upcoming", "open", "closing_today", "closed")
            and r.get("price") and r.get("name")]
    ids = {r["id"] for r in live}
    for k in [k for k in store if k not in ids]:                 # forget IPOs that have listed
        store.pop(k)
    key = "" if fixtures else _key(root)
    models, model, dead, stop = _models(root), None, set(), ""
    if not key and not fixtures and not offline:
        log("  AI GMP: no key set, using what is already saved")
    asked = ok = 0
    for r in live:
        was = store.get(r["id"])
        fresh = False
        if was:
            try:
                fresh = now - dt.datetime.fromisoformat(was["checked"]) < dt.timedelta(hours=EVERY_HOURS)
            except Exception:
                fresh = False
        if was and not fresh and was.get("failed"):      # after a failure, leave it a while before trying again
            try:
                fresh = now - dt.datetime.fromisoformat(was["checked"]) < dt.timedelta(hours=FAIL_RETRY_H)
            except Exception:
                fresh = False
        if not stop and not fresh and (fixtures or (key and not offline and asked < MAX_PER_RUN)):
            prompt = PROMPT.format(today=today.isoformat(), name=r["name"],
                                   seg="SME IPO" if r.get("segment") == "SME" else "Mainboard IPO",
                                   price=r["price"], open=r.get("open") or "to be announced",
                                   close=r.get("close") or "to be announced")
            try:
                tries, why = [], ""
                for pass_no in range(1 if fixtures else PASSES):
                    if fixtures:
                        f = pathlib.Path(fixtures) / f"ai_{r['id']}{'' if not pass_no else '_2'}.json"
                        text, urls = _read_reply(json.loads(f.read_text(encoding="utf-8"))) if f.exists() else (None, [])
                    else:
                        if asked:
                            time.sleep(PAUSE_S)
                        text, urls, model = _first_that_answers(key, prompt, models, model, dead, log)
                        asked += 1
                    one, why1 = check(_json_from(text), urls, r.get("price"), today)
                    tries.append(one)
                    why = why or why1
                if fixtures:
                    got, why2 = (tries[0], "") if tries[0] else (None, why)
                else:
                    got, why2 = agree(tries[0], tries[-1], r.get("price"))
                    why = why2 or why
                if got:
                    fits, why3 = cross_check(got["gmp"], r.get("gmp"), r.get("price"))
                    if not fits:
                        got, why = None, why3
                was = {"checked": now.isoformat(timespec="minutes"), "model": model or models[0]}
                if got:
                    got["seen"] = page_shows(got["sources"], got["gmp"]) if not fixtures else None
                    was.update(got)
                    ok += 1
                else:
                    was["none"] = why or why2 or "no figure we could confirm"
                store[r["id"]] = was
            except GiveUp as e:                                  # stop for this run rather than waiting on every IPO
                stop = str(e)
                log(f"  AI GMP: stopping for this run — {stop}")
            except Exception as e:                               # a timeout or odd reply: note it and move on
                log(f"  AI GMP: {r['id']}: {type(e).__name__}, keeping what we had")
                store[r["id"]] = was = {"checked": now.isoformat(timespec="minutes"), "failed": True,
                                        "none": f"the request failed ({type(e).__name__})"}
        if was and was.get("gmp") is not None:
            r["ai_gmp"] = {k: was[k] for k in ("gmp", "as_of", "note", "quote", "sources", "model",
                                               "checked", "checks", "spread", "pages", "seen", "disagreement")
                           if was.get(k) not in (None, "")}
            if r.get("price"):
                r["ai_gmp"]["pct"] = round(was["gmp"] / r["price"] * 100, 2)
        elif was:
            r["ai_gmp_none"] = {"why": was.get("none") or "no estimate", "checked": was.get("checked")}
    if asked or fixtures:
        log(f"  AI GMP ({model or models[0]}): {asked} IPO(s) asked about, {ok} estimate(s) kept")
    try:
        path.write_text(json.dumps(store, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    except OSError:
        pass
    return recs
