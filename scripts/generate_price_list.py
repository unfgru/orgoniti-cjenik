#!/usr/bin/env python3
"""
Generate the public price list for goods (cjenik robe) required by
Pravilnik o nacinu objave cjenika proizvoda (NN 132/2026), in force from
17.11.2026.

Format (clanak 3. st. 2., clanak 6.): CSV, UTF-8, columns separated by
semicolon (;), the 11 columns in the order of clanak 3. st. 2. (column
titles follow HOK's official template). Price list file name (clanak 6.
st. 3.): ID trgovca _ vrsta prodajnog mjesta _ adresa _ oznaka prodajnog
mjesta _ ROBA _ broj pohrane _ GGGGMMDD_SSMM.

Reads from Shopify Admin API only; writes a local CSV file plus a
completeness report. Publishing (deciding what becomes the public "current"
file vs archive) is a separate step - see publish_docs.py - so a script
failure here can never corrupt what's already public.

Required env vars: SHOPIFY_STORE_DOMAIN, SHOPIFY_ADMIN_API_TOKEN
Optional env vars (file name parts):
    TRGOVAC_ID (maticni broj obrta),
    VRSTA_PRODAJNOG_MJESTA, ADRESA_SLUG, OZNAKA_PRODAJNOG_MJESTA, BROJ_POHRANE
"""
import csv
import json
import os
import sys
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

import urllib.request

SHOP = os.environ["SHOPIFY_STORE_DOMAIN"]
TOKEN = os.environ["SHOPIFY_ADMIN_API_TOKEN"]
API_VERSION = "2025-01"
URL = f"https://{SHOP}/admin/api/{API_VERSION}/graphql.json"
TZ = ZoneInfo("Europe/Zagreb")

# File name parts (Pravilnik NN 132/2026, clanak 6. st. 3.). Override via env
# vars, no code change needed.
# TRGOVAC_ID: an obrt counts as a natural person, so the matični broj obrta
# is required (matični broj obrta, from the obrtnica / Obrtni registar).
TRGOVAC_ID = os.environ.get("TRGOVAC_ID", "98015982")
VRSTA_PRODAJNOG_MJESTA = os.environ.get("VRSTA_PRODAJNOG_MJESTA", "PRODAJA_NA_DALJINU")
ADRESA_SLUG = os.environ.get("ADRESA_SLUG", "MirnoveckaCesta48_Samobor")
OZNAKA_PRODAJNOG_MJESTA = os.environ.get("OZNAKA_PRODAJNOG_MJESTA", "001")
BROJ_POHRANE = os.environ.get("BROJ_POHRANE", "0001")

# Column order is fixed by clanak 3. st. 2. Titles follow HOK's template.
CSV_FIELDNAMES = [
    "Naziv robe",
    "Šifra robe, ako je primjenjivo",
    "Marka robe, ako je primjenjivo",
    "Neto količina kada je riječ o robi za koju je posebnim propisom uređena obveza isticanja cijene za jedinicu mjere",
    "Jedinica mjere robe, ako je primjenjivo",
    "Maloprodajna cijena robe",
    "Poseban oblik prodaje robe (DA/NE)",
    "Cijena za jedinicu mjere robe, ako je primjenjivo",
    "Barkod robe, ako je primjenjivo",
    "EAN barkod robe, ako je primjenjivo",
    "Bazna cijena robe",
]
(C_NAZIV, C_SIFRA, C_MARKA, C_NETO, C_JM, C_CIJENA, C_POSEBNI, C_CIJENA_JM, C_BARKOD, C_EAN, C_BAZNA) = CSV_FIELDNAMES


def money(value):
    """Croatian decimal format (comma), always two decimals, EUR implied."""
    return f"{float(value):.2f}".replace(".", ",")


PRODUCTS_QUERY = """
query ($cursor: String) {
  products(first: 50, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        title
        vendor
        status
        variants(first: 100) {
          edges {
            node {
              id
              title
              sku
              barcode
              price
              compareAtPrice
              availableForSale
              unitPrice { amount }
              unitPriceMeasurement { quantityUnit quantityValue }
              metafields(namespace: "sidrena_cijena", first: 10) {
                edges { node { key value } }
              }
            }
          }
        }
      }
    }
  }
}
"""


def graphql(query, variables=None):
    req = urllib.request.Request(
        URL,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    if "errors" in result:
        raise RuntimeError(json.dumps(result["errors"]))
    return result["data"]


def fetch_all_products():
    products, cursor = [], None
    while True:
        data = graphql(PRODUCTS_QUERY, {"cursor": cursor})["products"]
        products.extend(e["node"] for e in data["edges"])
        if not data["pageInfo"]["hasNextPage"]:
            break
        cursor = data["pageInfo"]["endCursor"]
    return products


def extract_anchor_price(metafield_edges):
    """Mirrors the theme's price.liquid fallback: the money metafield
    sometimes resolves as raw stored JSON depending on API context."""
    mf = {e["node"]["key"]: e["node"]["value"] for e in metafield_edges}
    raw = mf.get("anchor_price")
    if not raw:
        return None, mf.get("verification_status")
    try:
        amount = json.loads(raw)["amount"]
    except (json.JSONDecodeError, KeyError, TypeError):
        amount = raw
    return amount, mf.get("verification_status")


def build_rows(products):
    rows = []
    completeness_issues = []
    only_active = [p for p in products if p["status"] == "ACTIVE"]
    for product in only_active:
        variants = [e["node"] for e in product["variants"]["edges"]]
        multi_variant = len(variants) > 1
        for v in variants:
            variant_label = v["title"] if v["title"] and v["title"] != "Default Title" else None
            if variant_label:
                parts = [p.strip() for p in variant_label.split("/")]
                parts = [p for p in parts if p != "Black"]
                variant_label = " / ".join(parts) or None
            naziv = f'{product["title"]} - {variant_label}' if (multi_variant and variant_label) else product["title"]

            sifra = v["sku"] or v["id"].rsplit("/", 1)[-1]

            neto, jedinica, cijena_jm = "", "", ""
            if v["unitPrice"] and v["unitPrice"]["amount"] and v["unitPriceMeasurement"] and v["unitPriceMeasurement"]["quantityUnit"]:
                q = v["unitPriceMeasurement"]
                neto = str(q["quantityValue"]).replace(".", ",")
                jedinica = q["quantityUnit"]
                cijena_jm = money(v["unitPrice"]["amount"])

            price = float(v["price"])
            compare_at = float(v["compareAtPrice"]) if v["compareAtPrice"] else None
            on_sale = compare_at is not None and compare_at > price

            anchor_price, verification_status = extract_anchor_price(v["metafields"]["edges"])
            if not anchor_price:
                completeness_issues.append(f'{naziv} (sifra {sifra}): nedostaje bazna cijena')
            elif verification_status == "procjena":
                completeness_issues.append(f'{naziv} (sifra {sifra}): bazna cijena jos nije potvrdjena (status=procjena)')

            barkod = v["barcode"] or ""
            ean = barkod if (barkod.isdigit() and len(barkod) == 13) else ""

            rows.append({
                C_NAZIV: naziv,
                C_SIFRA: sifra,
                C_MARKA: product["vendor"] or "",
                C_NETO: neto,
                C_JM: jedinica,
                C_CIJENA: money(price),
                C_POSEBNI: "DA" if on_sale else "NE",
                C_CIJENA_JM: cijena_jm,
                C_BARKOD: barkod,
                C_EAN: ean,
                C_BAZNA: money(anchor_price) if anchor_price else "",
            })
    return rows, completeness_issues


def validate(rows, expected_min_rows):
    errors = []
    if len(rows) < expected_min_rows:
        errors.append(f"Broj redaka ({len(rows)}) manji od ocekivanog ({expected_min_rows}) - moguce nepotpuno generiranje.")
    for i, row in enumerate(rows):
        if not row[C_NAZIV] or not row[C_SIFRA]:
            errors.append(f"Redak {i}: nedostaje naziv ili sifra.")
        try:
            float(row[C_CIJENA].replace(",", "."))
        except ValueError:
            errors.append(f'Redak {i} ({row[C_NAZIV]}): neispravna maloprodajna cijena.')
    return errors


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def main():
    out_dir = sys.argv[sys.argv.index("--out-dir") + 1] if "--out-dir" in sys.argv else "."
    os.makedirs(out_dir, exist_ok=True)

    now = datetime.now(TZ)
    filename = (
        f"{TRGOVAC_ID}_{VRSTA_PRODAJNOG_MJESTA}_{ADRESA_SLUG}_{OZNAKA_PRODAJNOG_MJESTA}_"
        f"ROBA_{BROJ_POHRANE}_{now.strftime('%Y%m%d_%H%M')}.csv"
    )

    products = fetch_all_products()
    rows, completeness_issues = build_rows(products)
    total_active_variants = sum(
        len(p["variants"]["edges"]) for p in products if p["status"] == "ACTIVE"
    )
    errors = validate(rows, expected_min_rows=total_active_variants)

    if errors:
        print("VALIDACIJA NEUSPJESNA:", file=sys.stderr)
        for e in errors:
            print(" -", e, file=sys.stderr)
        sys.exit(1)

    csv_path = os.path.join(out_dir, filename)
    write_csv(rows, csv_path)

    completeness_path = os.path.join(out_dir, "completeness_report.txt")
    with open(completeness_path, "w", encoding="utf-8") as f:
        f.write(f"Izvjestaj o potpunosti podataka - {now.isoformat()}\n")
        f.write(f"Ukupno redaka u cjeniku: {len(rows)}\n")
        f.write(f"Stavki s otvorenim pitanjem: {len(completeness_issues)}\n\n")
        for issue in completeness_issues:
            f.write(f"- {issue}\n")

    # Machine-readable handoff to publish_docs.py
    meta_path = os.path.join(out_dir, "generation_result.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "csv_filename": filename,
            "csv_path": csv_path,
            "row_count": len(rows),
            "generated_at": now.isoformat(),
            "completeness_issues_count": len(completeness_issues),
        }, f)

    print(f"OK: {csv_path}")
    print(f"Redaka: {len(rows)}, otvorena pitanja: {len(completeness_issues)}")


if __name__ == "__main__":
    main()
