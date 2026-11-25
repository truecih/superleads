# MailAutomation — generic outreach send/receive toolkit

A small, dependency-light toolkit for **personalized cold-email outreach** and for
**reading and classifying replies**, with conservative throttling so you do not
get rate-limited or flagged by your mail provider.

Everything company-specific (sender identity, products, signature, subject
lines, body wording) lives in a **config file that is NOT included** — copy
`config.example.json` to `config.json` and fill in your own details. **No real
customer lists, emails, or credentials are shipped in this repository.**

---

## What it does

| Stage | Script | Output |
|-------|--------|--------|
| 1. Generate personalized emails | `generate_emails.py` | `campaign.jsonl`, `emails_preview.md`, `send_plan.csv` |
| 2a. Send (local, throttled) | `send_campaign.py` | `sent_log.jsonl` (resumable state) |
| 2b. Send (cloud, no PC) | `APPS_SCRIPT.gs` + `export_for_gsheet.py` | Google Sheet status column |
| 3. Read replies (local IMAP) | `read_replies.py` | `replies_local.jsonl`, `replies_local.xlsx` |
| 3b. Read replies (cloud) | `APPS_SCRIPT.gs` `checkReplies()` | "Replies" tab in the Sheet |
| — Classify reply intent | `classify_reply.py` (used by `read_replies.py`) | category / intent level per reply |
| — Connectivity tests | `test_smtp_login.py`, `test_imap.py`, `send_test_email.py` | pass/fail only, no secrets printed |

---

## 1. Install

- Python 3.9+ (standard library only for most scripts).
- Optional: `openpyxl` if you want the reply table as `.xlsx`
  (`pip install openpyxl`). Without it, replies are still written to JSONL.

## 2. Configure (no secrets in files)

Credentials are read **only from environment variables**, never from a config
file or the command line:

| Variable | Meaning |
|----------|---------|
| `SUPERLEADS_SMTP_USER` | mailbox address (e.g. `you@gmail.com`) |
| `SUPERLEADS_SMTP_PASSWORD` | SMTP password / app password |
| `SUPERLEADS_SMTP_HOST` | SMTP host (default `smtp.gmail.com`) |
| `SUPERLEADS_SMTP_PORT` | SMTP port (default `587`) |
| `SUPERLEADS_SMTP_SECURITY` | `starttls` (default) or `ssl` |
| `SUPERLEADS_SMTP_FROM` | From address (default = USER) |
| `SUPERLEADS_IMAP_HOST` | IMAP host for replies (default `imap.gmail.com`) |
| `SUPERLEADS_IMAP_PORT` | IMAP port (default `993`) |

> Gmail: use an **App password**, not your login password. Never commit these
> variables. `.gitignore` already excludes `config.json`, `campaign.jsonl`,
> `sent_log.jsonl`, `replies_local.*` and any `*credential*`/`*token*` files.

## 3. Prepare a lead list

Any of CSV / JSONL / XLSX. It needs at least a **company name** and an **email**;
optional columns: city, role, products, notes, source. Column names are
auto-detected (English and Chinese), or set the `"columns"` map in `config.json`.

Copy the example config and edit it:

```bash
cp config.example.json config.json
# edit config.json: your sender facts, subjects, body_template, pacing
```

## 4. Generate the emails

```bash
python generate_emails.py --leads leads.csv --config config.json --out ./out
# preview only, no files written:
python generate_emails.py --leads leads.csv --config config.json --dry-run
```

Each email is personalized using only factual fields present in the lead row
(city, listed products, role) — the generator does **not** invent purchase
intent, certifications, or relationships. Review `emails_preview.md` before sending.

## 5. Send — choose one path

### Option A: local sender (needs the PC on)

```bash
# validate first (no network):
python send_campaign.py --dry-run --limit 3
# real, throttled, resumable:
python send_campaign.py --send --daily-cap 20 --min-delay 240 \
  --jitter 60 --biz-hours-only --start-hour 8 --end-hour 18
```

Pacing contract: at most `daily_cap` sends per calendar day, at least
`min_delay` seconds (plus random `jitter`) between sends, only during business
hours. Each send is recorded in `sent_log.jsonl`; re-running skips already-sent
rows, so a long campaign can be resumed safely. SMTP egress can time out
intermittently, so each send is retried with a fresh connection and backoff.

### Option B: cloud sender (PC can be off)

1. `python export_for_gsheet.py --in ./out/campaign.jsonl --out google_sheet_campaign.csv`
2. Create a Google Sheet named exactly `SHEET_NAME` in `APPS_SCRIPT.gs` and
   import the CSV into its first tab (header row included).
3. Paste `APPS_SCRIPT.gs` into a standalone Apps Script project, edit the
   CONFIG block at the top (sheet name, hours, timezone, caps), save.
4. Run `setUp()` once and approve the permissions. Two triggers are created:
   one sends **one** pending email every 30 minutes during business hours
   (≤20/day), the other checks replies every 4 hours.
5. Run `stopAll()` to disable everything. `statusSummary()` logs a status count.

Why one email per firing: Apps Script caps a single execution at ~6 minutes, so
sleeping between many sends is not viable; a recurring short trigger is.

## 6. Read and classify replies

Local (works with any IMAP mailbox):

```bash
python read_replies.py --days 30 --max 300
```

It matches sender addresses against `campaign.jsonl`, deduplicates by RFC
Message-ID, classifies each reply (EN/VI/ZH keywords) into a primary category
and intent level, and writes `replies_local.jsonl` + `replies_local.xlsx`.

Cloud: `APPS_SCRIPT.gs` `checkReplies()` appends inbound replies from known
recipients to a "Replies" tab and flags the source row in the main sheet.

> Reply classification is rule-based and advisory. Treat keyword-derived
> intent as a hint to review, not as a fact. `needs_review` marks uncertain cases.

## Privacy & compliance

- Do not bulk-spam. Respect unsubscribe requests and applicable anti-spam laws
  (CAN-SPAM, GDPR, local regulations). Keep send volume conservative.
- This repo contains **no** customer data, real emails, or credentials. Keep it
  that way: put all real lists and `config.json` outside version control.

## License

See the repository root `LICENSE`. This toolkit is provided as-is.
