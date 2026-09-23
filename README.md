# orgoniti-cjenik

Daily public price list (sidrena cijena / cjenik proizvoda) for orgoniti.hr,
required by Odluka o objavi cjenika proizvoda i usluga (NN 101/2026-1213),
in force 1.10.2026.

## How it works

A GitHub Actions workflow (`.github/workflows/cjenik.yml`) runs daily at
05:30 UTC (safely before 08:00 Europe/Zagreb time year-round) plus on manual
trigger:

1. `scripts/generate_price_list.py` reads the current catalog from Shopify's
   Admin API and builds a CSV in the required format. It validates its own
   output and **exits non-zero without touching anything public if the data
   looks incomplete or corrupted**.
2. `scripts/publish_docs.py` copies the result to `docs/cjenik-aktualni.csv`
   (stable "current" link, overwritten each run) and `docs/archive/<dated
   filename>.csv` (never overwritten), pruning archive files older than
   30 days.
3. `scripts/update_shopify_status.py` reports the outcome back to the
   Shopify store as shop metafields (`sidrena_cijena.cjenik_*`), which the
   theme's "Cjenik proizvoda" page reads directly. On failure, the current
   link is deliberately left pointing at yesterday's last-known-good file.

`docs/` is served publicly via GitHub Pages (Settings → Pages → Deploy from
branch → `main` / `/docs`).

## Required repo secrets

Settings → Secrets and variables → Actions:

- `SHOPIFY_STORE_DOMAIN` — e.g. `v0mewm-fa.myshopify.com`
- `SHOPIFY_ADMIN_API_TOKEN` — Admin API access token (`shpat_...`), scopes:
  `read_products`, `write_products`

## Pending configuration

`OBLIK_PRODAJNOG_OBJEKTA` and `BROJ_POHRANE` (filename components per
NN 101/2026-1213 tocka VI.) are currently placeholders
(`OBLIK-POTVRDITI` / `POHRANA-POTVRDITI`) — no authoritative source was
found for what these should be for an online-only obrt. Once confirmed, set
them as repository variables (Settings → Secrets and variables → Actions →
Variables) rather than editing the script.

## Manual run

Actions tab → "Objava dnevnog cjenika" → Run workflow.
