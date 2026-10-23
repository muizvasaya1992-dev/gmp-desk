# GMP Desk: free IPO + GMP tracker

Static site. No server, no database, no paid service.

```
index.html               the website
ipos.json                what the site reads (generated)
build_data.py            the pipeline: NSE data + your facts + your GMP -> ipos.json
data/ipos_manual.json    FACTS you edit: lot size, issue size (Rs Cr), allotment/listing dates, anchor, rating
data/gmp.csv             YOUR GMP readings (or point data/config.json at a published Google Sheet CSV)
data/nse_cache.json      official NSE data, remembered between runs (generated)
.github/workflows/       scheduled run (GitHub Actions)
probe_sources.py         one-off check of what NSE returns
```

## Daily use
```
python build_data.py          # fetch NSE, merge, write ipos.json
python -m http.server 8000    # http://localhost:8000
```
- **New IPO:** appears by itself once NSE lists it. Add its lot size, issue size, allotment and listing dates
  to `data/ipos_manual.json` (give it the NSE `symbol` so they link up).
- **GMP:** add a row to `data/gmp.csv`:  `JPL,240,2026-10-23 11:30,`  then rebuild.

## Other modes
- `--offline` skip NSE, use the cache.   `--from-probe` use files in data/probe/ (no network).
- `--sample` builds the made-up sample set; open the site with `?data=sample`.
- `--local-test` also loads `data/gmp_local_testing.csv` (third-party GMP readings) into `ipos_localtest.json`;
  open the site with `?data=local`. That file is git-ignored. Do not publish those numbers.

## Google Sheet for GMP (optional)
Make a sheet with columns `ipo, gmp, at, source`, then File > Share > Publish to web > CSV.
Paste the link into `data/config.json` as `gmp_csv_url`. The scheduled run then reads it.

## Where each field comes from
NSE: name, symbol, open/close dates, upper price, subscription (total). Note: NSE `issueSize` is the number of
shares offered for bidding, not Rs Cr, so issue size stays a manual field.
You: lot size, issue size, allotment/listing dates, anchor, rating, GMP.
