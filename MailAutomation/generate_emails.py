# -*- coding: utf-8 -*-
r"""Generate personalized outreach emails from a lead list and a config file.

This is a GENERIC tool: all company-specific wording (sender name, products,
certifications, signature, subject lines, body template) comes from config.json,
which you create by copying config.example.json. No real customer data or
company identity is bundled with this repository.

Input lead list (--leads) may be CSV, JSONL, or XLSX. Configure which columns
hold the company name, email, city, role, products and notes via the
"columns" map in config.json (defaults below fit the common case).

Output (in --out, default: current directory):
  campaign.jsonl        one JSON object per recipient (seq, company, to,
                        subject, body, ...) - the input to send_campaign.py
  emails_preview.md     human-readable preview of every email
  send_plan.csv         proposed throttled sending schedule

Usage:
  python generate_emails.py --leads leads.csv --config config.json --out ./out
  python generate_emails.py --leads leads.csv --dry-run
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

try:
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
DEFAULT_COLUMNS = {
    "company": ["company", "name", "公司名称", "company_name"],
    "to": ["email", "to", "邮箱", "e-mail"],
    "city": ["city", "城市", "location"],
    "role": ["role", "可能角色", "type"],
    "products": ["products", "产品", "signals"],
    "notes": ["notes", "备注", "note"],
    "source": ["source", "来源", "website"],
}


def pick_email(value):
    """Pick the first plausible email address from a free-form cell."""
    found = EMAIL_RE.findall(str(value or ""))
    if not found:
        return ""
    for addr in found:
        low = addr.lower()
        if low.startswith(("info@", "sales@", "contact@", "export@")):
            return addr
    return found[0]


def load_leads(path, columns):
    path = Path(path)
    rows = []
    if path.suffix.lower() in (".jsonl", ".ndjson"):
        with path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
    elif path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    elif path.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else "" for h in next(it)]
        for values in it:
            rows.append(dict(zip(header, values)))
    else:
        raise SystemExit("Unsupported lead file type: %s" % path.suffix)
    return normalize_rows(rows, columns)


def normalize_rows(rows, columns):
    cols = {**DEFAULT_COLUMNS, **(columns or {})}
    out = []
    for r in rows:
        def get(key):
            for name in cols.get(key, []):
                if name in r and r[name] not in (None, ""):
                    return r[name]
            return ""

        company = str(get("company") or "").strip()
        to = pick_email(get("to"))
        if not company or not to:
            continue
        out.append({
            "company": company,
            "to": to,
            "city": str(get("city") or "").strip(),
            "role": str(get("role") or "").strip(),
            "products": str(get("products") or "").strip(),
            "notes": str(get("notes") or "").strip(),
            "source": str(get("source") or "").strip(),
        })
    return out


def personal_line(lead):
    """Build one factual, non-inferential personalization sentence."""
    bits = []
    if lead["city"]:
        bits.append("We note that your company is based in %s" % lead["city"])
    if lead["products"]:
        bits.append("your listed products include %s" % lead["products"].lower())
    if not bits:
        return "We would be glad to learn more about your business and requirements."
    return "We noted " + " and ".join(bits) + "."


def fill(template, lead, cfg):
    s = cfg["sender"]
    values = {
        "company": lead["company"],
        "products": s.get("products", ""),
        "sender_name": s.get("name", ""),
        "sender_name_local": s.get("name_local", s.get("name", "")),
        "tagline": s.get("tagline", ""),
        "certifications": s.get("certifications", ""),
        "applications": s.get("applications", ""),
        "personal_line": personal_line(lead),
        "phone": s.get("phone", ""),
        "email": s.get("email", ""),
        "website": s.get("website", ""),
    }
    for k, v in values.items():
        template = template.replace("{{%s}}" % k, str(v))
    return template


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leads", required=True, help="lead list: CSV / JSONL / XLSX")
    ap.add_argument("--config", default="config.json", help="config file (copy of config.example.json)")
    ap.add_argument("--out", default=".", help="output directory")
    ap.add_argument("--columns", default="", help="optional JSON override for the column map")
    ap.add_argument("--dry-run", action="store_true", help="print a preview without writing files")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    if args.columns:
        cfg["columns"] = json.loads(args.columns)
    leads = load_leads(args.leads, cfg.get("columns"))
    if not leads:
        raise SystemExit("No usable rows found (need a company name and an email).")

    subjects = cfg["campaign"].get("subjects") or ["About {{products}} - {{sender_name}}"]
    body_tpl = cfg["campaign"].get("body_template", "")
    greeting = cfg["campaign"].get("greeting", "")
    if greeting and "{{company}}" not in body_tpl:
        body_tpl = greeting + "\n\n" + body_tpl

    cap = int(cfg["campaign"].get("daily_cap", 20))
    spacing_min = int(cfg["campaign"].get("min_delay_seconds", 240)) // 60 or 28
    start_h = int(cfg["campaign"].get("start_hour", 8))

    campaign = []
    for i, lead in enumerate(leads, 1):
        subject = fill(subjects[(i - 1) % len(subjects)], lead, cfg)
        body = fill(body_tpl, lead, cfg)
        campaign.append({
            "seq": i,
            "company": lead["company"],
            "city": lead["city"],
            "to": lead["to"],
            "subject": subject,
            "body": body,
            "source": lead["source"],
        })

    if args.dry_run:
        print(json.dumps({"leads": len(leads), "sample": campaign[0]}, ensure_ascii=False, indent=2))
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "campaign.jsonl").open("w", encoding="utf-8") as f:
        for r in campaign:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (out / "emails_preview.md").open("w", encoding="utf-8") as f:
        f.write("# Email preview\n\n")
        for r in campaign:
            f.write("## %d. %s <%s>\n\n**Subject:** %s\n\n%s\n\n---\n\n" %
                    (r["seq"], r["company"], r["to"], r["subject"], r["body"]))
    with (out / "send_plan.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["seq", "company", "to", "subject", "day", "time"])
        for r in campaign:
            slot = (r["seq"] - 1) % cap
            day = (r["seq"] - 1) // cap + 1
            minute = start_h * 60 + slot * spacing_min
            w.writerow([r["seq"], r["company"], r["to"], r["subject"],
                        day, "%02d:%02d" % (minute // 60, minute % 60)])

    print(json.dumps({"generated": len(campaign), "out": str(out),
                      "files": ["campaign.jsonl", "emails_preview.md", "send_plan.csv"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
