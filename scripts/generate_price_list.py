#!/usr/bin/env python3
"""
Generate the public product price list (javni cjenik) required by
Odluka o objavi cjenika proizvoda i usluga (NN 101/2026-1213), effective
1.10.2026, in the CSV format/columns specified by HOK's official template.

Reads from Shopify Admin API only; writes a local CSV file plus a
completeness report and an append-only generation log. Publishing (deciding
what becomes the public "current" file vs archive) is a separate step —
see publish_docs.py — so a script failure here can never corrupt what's
already public.

Required env vars: SHOPIFY_STORE_DOMAIN, SHOPIFY_ADMIN_API_TOKEN
Optional env vars (filename convention, NN 101/2026-1213 tocka VI.):
    OBLIK_PRODAJNOG_OBJEKTA, OZNAKA_PRODAJNOG_OBJEKTA, BROJ_POHRANE, ADRESA_SLUG
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

# Pending confirmation from the merchant (flagged explicitly, not invented) —
# see business-info.json in the Shopify theme project for the open questions
# this maps to. Override via env vars once confirmed; no code change needed.
OBLIK_PRODAJNOG_OBJEKTA = os.environ.get("OBLIK_PRODAJNOG_OBJEKTA", "OBRT")
OZNAKA_PRODAJNOG_OBJEKTA = os.environ.get("OZNAKA_PRODAJNOG_OBJEKTA", "001")
BROJ_POHRANE = os.environ.get("BROJ_POHRANE", "001")
ADRESA_SLUG = os.environ.get("ADRESA_SLUG", "MirnoveckaCesta48")

CSV_FIELDNAMES = [
    "Naziv proizvoda",
    "Šifra",
    "Marka",
    "Jedinica mjere",
    "Cijena za jedinicu mjere",
    "Maloprodajna cijena",
    "Poseban oblik prodaje (DA/NE)",
    "Naziv posebnog oblika prodaje",
    "Cijena 10.9.2026.",
    "Barkod",
    "Dostupnost",
]

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

            unit_amount = v["unitPrice"]["amount"] if v["unitPrice"] else ""
            unit_measure = ""
            if unit_amount and v["unitPriceMeasurement"] and v["unitPriceMeasurement"]["quantityUnit"]:
                q = v["unitPriceMeasurement"]
                unit_measure = f'{q["quantityValue"]} {q["quantityUnit"]}'

            price = float(v["price"])
            compare_at = float(v["compareAtPrice"]) if v["compareAtPrice"] else None
            on_sale = compare_at is not None and compare_at > price
            special_sale_name = "Snizenje" if on_sale else ""

            anchor_price, verification_status = extract_anchor_price(v["metafields"]["edges"])
            if not anchor_price:
                completeness_issues.append(f'{naziv} (sifra {sifra}): nedostaje sidrena cijena')
            elif verification_status == "procjena":
                completeness_issues.append(f'{naziv} (sifra {sifra}): sidrena cijena jos nije potvrdjena (status=procjena)')

            rows.append({
                "Naziv proizvoda": naziv,
                "Šifra": sifra,
                "Marka": product["vendor"] or "",
                "Jedinica mjere": unit_measure,
                "Cijena za jedinicu mjere": f"{float(unit_amount):.2f}" if unit_amount else "",
                "Maloprodajna cijena": f"{price:.2f}",
                "Poseban oblik prodaje (DA/NE)": "DA" if on_sale else "NE",
                "Naziv posebnog oblika prodaje": special_sale_name,
                "Cijena 10.9.2026.": f"{float(anchor_price):.2f}" if anchor_price else "",
                "Barkod": v["barcode"] or "",
                "Dostupnost": "dostupan" if v["availableForSale"] else "nedostupan",
            })
    return rows, completeness_issues


def validate(rows, expected_min_rows):
    errors = []
    if len(rows) < expected_min_rows:
        errors.append(f"Broj redaka ({len(rows)}) manji od ocekivanog ({expected_min_rows}) - moguce nepotpuno generiranje.")
    for i, row in enumerate(rows):
        if not row["Naziv proizvoda"] or not row["Šifra"]:
            errors.append(f"Redak {i}: nedostaje naziv ili sifra.")
        try:
            float(row["Maloprodajna cijena"])
        except ValueError:
            errors.append(f'Redak {i} ({row["Naziv proizvoda"]}): neispravna maloprodajna cijena.')
    return errors


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main():
    out_dir = sys.argv[sys.argv.index("--out-dir") + 1] if "--out-dir" in sys.argv else "."
    os.makedirs(out_dir, exist_ok=True)

    now = datetime.now(TZ)
    filename = (
        f"{OBLIK_PRODAJNOG_OBJEKTA}_{ADRESA_SLUG}_{OZNAKA_PRODAJNOG_OBJEKTA}_"
        f"{BROJ_POHRANE}_{now.strftime('%Y-%m-%dT%H%M')}.csv"
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
