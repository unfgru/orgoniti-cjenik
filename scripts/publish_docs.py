#!/usr/bin/env python3
"""
Takes the CSV generate_price_list.py just produced and publishes it:
  - docs/cjenik-aktualni.csv   <- stable "current" link, overwritten each run
  - docs/archive/<filename>.csv <- dated snapshot, NEVER overwritten
Prunes archive files older than 30 days (the legal minimum retention is a
floor, not a ceiling, but we don't need to keep them forever either).
Also writes docs/archive_index.html, a small pre-rendered HTML fragment the
Shopify theme page embeds directly (avoids needing JSON parsing in Liquid).

This script assumes generate_price_list.py already succeeded and validated
its output — if it hadn't, this step is simply not invoked (see workflow),
so a failed generation can never touch what's already public.
"""
import glob
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Zagreb")
RETENTION_DAYS = 30
BASE_URL = os.environ.get("PAGES_BASE_URL", "https://unfgru.github.io/orgoniti-cjenik/")

DOCS_DIR = "docs"
ARCHIVE_DIR = os.path.join(DOCS_DIR, "archive")
CURRENT_PATH = os.path.join(DOCS_DIR, "cjenik-aktualni.csv")
ARCHIVE_INDEX_PATH = os.path.join(DOCS_DIR, "archive_index.html")

# Matches the trailing _YYYYMMDD_HHMM.csv the generator appends
DATE_RE = re.compile(r"_(\d{8})_(\d{4})\.csv$")


def parse_date_from_filename(path):
    m = DATE_RE.search(os.path.basename(path))
    if not m:
        return None
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M").replace(tzinfo=TZ)


def main():
    with open("generation_result.json", encoding="utf-8") as f:
        result = json.load(f)

    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    src = result["csv_path"]
    archive_dest = os.path.join(ARCHIVE_DIR, result["csv_filename"])
    shutil.copyfile(src, archive_dest)
    shutil.copyfile(src, CURRENT_PATH)
    print(f"Published: {CURRENT_PATH} and {archive_dest}")

    now = datetime.now(TZ)
    cutoff = now - timedelta(days=RETENTION_DAYS)
    removed = 0
    for path in glob.glob(os.path.join(ARCHIVE_DIR, "*.csv")):
        dt = parse_date_from_filename(path)
        if dt and dt < cutoff:
            os.remove(path)
            removed += 1
    if removed:
        print(f"Pruned {removed} archive file(s) older than {RETENTION_DAYS} days.")

    archive_files = sorted(glob.glob(os.path.join(ARCHIVE_DIR, "*.csv")), reverse=True)
    items = []
    for path in archive_files:
        dt = parse_date_from_filename(path)
        label = dt.strftime("%-d.%-m.%Y. %H:%M") if dt else os.path.basename(path)
        rel_url = f"archive/{os.path.basename(path)}"
        items.append(f'<li><a href="{BASE_URL}{rel_url}">{label}</a></li>')
    html = "<ul>\n" + "\n".join(items) + "\n</ul>" if items else ""
    with open(ARCHIVE_INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Archive index written ({len(archive_files)} entries).")


if __name__ == "__main__":
    main()
