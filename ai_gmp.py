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
MODEL = "gemini-2.5-flash"       # change it in data/private.json ("gemini_model") if your key offers a different one
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_PER_RUN = 15                 # at most this many IPOs asked about in one run
EVERY_HOURS = 20                 # and each IPO at most once a day
PAUSE_S = 2.0
MAX_PCT_OF_PRICE = 60            # a premium above this share of the issue price is treated as a bad answer
MAX_AGE_DAYS = 4                 # an answer older than this is not worth showing
TIMEOUT = 60

PROMPT = """You are a careful data assistant for an Indian IPO information website. Today is {today} (IST).

Search the web for the latest grey market premium (GMP) for this IPO in India:
  Name: {name}
  Type: {seg}
  Issue price: Rs {price} per share
  Bidding: {open} to {close}

Rules you must follow:
- Report only a GMP figure you have actually seen on a page you found in your search.
- If several pages disagree, use the most recent one.
- If you cannot find a GMP for this exact IPO, answer with "found": false. Never guess, never estimate from
  similar IPOs, and never carry over a figure from another company.
- A GMP of 0 means the grey market shows no premium. That is a real answer: report it as found with 0.

Reply with JSON only, no other text, in exactly this shape:
{{"found": true or false, "gmp_rupees": number or null, "as_of": "YYYY-MM-DD" or null,
  "sources": ["full url", ...], "note": "under 15 words, what the page said"}}"""


def _private(root):
    try:
        return json.loads((pathlib.Path(root) / "data" / "private.json").read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _key(root):
    return (os.environ.get("GEMINI_API_KEY") or str(_private(root).get("gemini_api_key") or "")).strip()


def _model(root):
    return (os.environ.get("GEMINI_MODEL") or str(_private(root).get("gemini_model") or "") or MODEL).strip()


def _ask(key, prompt, model=MODEL):
    """One grounded request. Returns (text, [source urls]) or (None, []) if the reply is unusable."""
    import requests
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0}}
    r = requests.post(ENDPOINT.format(model=model), params={"key": key},
                      json=body, timeout=TIMEOUT, headers={"Content-Type": "application/json"})
    r.raise_for_status()
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
    note = str(answer.get("note") or "")[:120]
    srcs = _clean_sources(answer.get("sources"), grounding_urls)
    if not srcs:
        return None, "the reply did not say where the figure came from"
    return {"gmp": int(g) if float(g).is_integer() else g, "as_of": day.isoformat(),
            "note": note, "sources": srcs}, ""


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
    model = _model(root)
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
        if not fresh and (fixtures or (key and not offline and asked < MAX_PER_RUN)):
            prompt = PROMPT.format(today=today.isoformat(), name=r["name"],
                                   seg="SME IPO" if r.get("segment") == "SME" else "Mainboard IPO",
                                   price=r["price"], open=r.get("open") or "to be announced",
                                   close=r.get("close") or "to be announced")
            try:
                if fixtures:
                    f = pathlib.Path(fixtures) / f"ai_{r['id']}.json"
                    text, urls = _read_reply(json.loads(f.read_text(encoding="utf-8"))) if f.exists() else (None, [])
                else:
                    if asked:
                        time.sleep(PAUSE_S)
                    text, urls = _ask(key, prompt, model)
                    asked += 1
                got, why = check(_json_from(text), urls, r.get("price"), today)
                was = {"checked": now.isoformat(timespec="minutes"), "model": model}
                if got:
                    was.update(got)
                    ok += 1
                else:
                    was["none"] = why
                store[r["id"]] = was
            except Exception as e:                               # a bad key, a timeout, a changed API: never break the build
                log(f"  AI GMP: {r['id']}: {type(e).__name__}, keeping what we had")
        if was and was.get("gmp") is not None:
            r["ai_gmp"] = {k: was[k] for k in ("gmp", "as_of", "note", "sources", "model", "checked") if k in was}
            if r.get("price"):
                r["ai_gmp"]["pct"] = round(was["gmp"] / r["price"] * 100, 2)
        elif was:
            r["ai_gmp_none"] = {"why": was.get("none") or "no estimate", "checked": was.get("checked")}
    if asked or fixtures:
        log(f"  AI GMP ({model}): {asked} IPO(s) asked about, {ok} estimate(s) kept")
    try:
        path.write_text(json.dumps(store, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    except OSError:
        pass
    return recs
