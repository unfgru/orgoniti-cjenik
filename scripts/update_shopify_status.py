#!/usr/bin/env python3
"""Reports the outcome of a generation/publish run back to Shopify, via the
shop-level metafields the theme's 'Cjenik proizvoda' page reads directly.
Called with either "success" or "failed" as argv[1].

On failure, current_csv_url/last_updated are deliberately left untouched —
so the page keeps pointing at yesterday's last-known-good file — only
last_status/last_error_message are updated, so a failure is visible without
ever breaking the public download link.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

SHOP = os.environ["SHOPIFY_STORE_DOMAIN"]
TOKEN = os.environ["SHOPIFY_ADMIN_API_TOKEN"]
URL = f"https://{SHOP}/admin/api/2025-01/graphql.json"
TZ = ZoneInfo("Europe/Zagreb")
BASE_URL = os.environ.get("PAGES_BASE_URL", "https://unfgru.github.io/orgoniti-cjenik/")


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


SET_MF = """
mutation ($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    metafields { key }
    userErrors { field message }
  }
}
"""


def main():
    status = sys.argv[1] if len(sys.argv) > 1 else "failed"
    shop_id = graphql("{ shop { id } }")["shop"]["id"]
    now = datetime.now(TZ)

    metafields = [
        {"ownerId": shop_id, "namespace": "sidrena_cijena", "key": "cjenik_last_status",
         "type": "single_line_text_field", "value": status.upper()},
    ]

    if status == "success":
        with open("archive_index.html", encoding="utf-8") as f:
            archive_html = f.read()
        metafields += [
            {"ownerId": shop_id, "namespace": "sidrena_cijena", "key": "cjenik_current_url",
             "type": "url", "value": f"{BASE_URL}cjenik-aktualni.csv"},
            {"ownerId": shop_id, "namespace": "sidrena_cijena", "key": "cjenik_last_updated",
             "type": "date_time", "value": now.isoformat()},
            {"ownerId": shop_id, "namespace": "sidrena_cijena", "key": "cjenik_archive_html",
             "type": "multi_line_text_field", "value": archive_html},
        ]
    else:
        error_message = sys.argv[2] if len(sys.argv) > 2 else "Nepoznata greska."
        metafields.append({
            "ownerId": shop_id, "namespace": "sidrena_cijena", "key": "cjenik_last_error_message",
            "type": "multi_line_text_field", "value": f"{now.isoformat()}: {error_message}",
        })

    result = graphql(SET_MF, {"metafields": metafields})["metafieldsSet"]
    for err in result["userErrors"]:
        print("userError:", err, file=sys.stderr)
    print(f"Shopify status updated: {status}")


if __name__ == "__main__":
    main()
