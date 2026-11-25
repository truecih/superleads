# -*- coding: utf-8 -*-
"""Export campaign.jsonl to a Google-Sheets-ready CSV for Apps Script sending.

The output has the exact header the bundled APPS_SCRIPT.gs expects:
  seq | company | to | subject | body | status | sent_at

Usage:
  python export_for_gsheet.py --in campaign.jsonl --out google_sheet_campaign.csv
"""
import argparse
import csv
import json
import sys
from pathlib import Path

try:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default="campaign.jsonl")
    ap.add_argument("--out", dest="out", default="google_sheet_campaign.csv")
    args = ap.parse_args()

    rows = []
    with Path(args.src).open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    header = ["seq", "company", "to", "subject", "body", "status", "sent_at"]
    with Path(args.out).open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(header)
        for r in rows:
            w.writerow([
                r.get("seq", ""), r.get("company", ""), r.get("to", ""),
                r.get("subject", ""), r.get("body", ""), "pending", "",
            ])
    print("exported=%d file=%s" % (len(rows), args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
