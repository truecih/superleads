# -*- coding: utf-8 -*-
r"""Local agent-side reply reader.

Reads the Gmail (or any IMAP) INBOX directly via IMAP, matches senders against
the outreach campaign recipients, and writes the results to local files next
to this script:

  replies_local.jsonl   (one JSON object per reply)
  replies_local.xlsx    (human-readable table)

Deduplication is by RFC Message-ID, persisted in replies_local.jsonl, so repeated
runs never duplicate rows. No credentials are printed or written to disk; the
mail password is read only from User/Process environment variables.

Usage (credentials are loaded from User scope automatically):
  python read_replies.py                 # read inbox, refresh local reply table
  python read_replies.py --days 30       # limit to last N days
  python read_replies.py --max 200       # max messages to scan
"""
import argparse
import email
import imaplib
import json
import os
import re
import ssl
import sys
import time
from datetime import datetime, timezone, timedelta
from email.header import decode_header, make_header

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from classify_reply import classify, format_brief  # noqa: E402

WORK = BASE  # overridden by --dir in main()
CAMPAIGN = os.path.join(BASE, "campaign.jsonl")
OUT_JSONL = os.path.join(BASE, "replies_local.jsonl")
OUT_XLSX = os.path.join(BASE, "replies_local.xlsx")


def env(name):
    v = os.environ.get(name, "")
    if v:
        return v
    # PowerShell User scope is the persistent store for these credentials.
    try:
        import subprocess
        out = subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-Command",
             "[Environment]::GetEnvironmentVariable('%s','User')" % name],
            stderr=subprocess.DEVNULL, timeout=20,
        )
        return out.decode("utf-8", "replace").strip()
    except Exception:
        return ""


def dec(raw):
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def addr_of(raw_from):
    m = re.search(r"<([^>]+)>", raw_from or "")
    return (m.group(1) if m else (raw_from or "")).strip().lower()


def load_campaign():
    """recipient email -> {seq, company, city, subject}"""
    m = {}
    with open(CAMPAIGN, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            a = addr_of(r.get("to", ""))
            if a:
                m[a] = {
                    "seq": r.get("seq"),
                    "company": r.get("company", ""),
                    "short_name": r.get("short_name", ""),
                    "city": r.get("city", ""),
                    "sent_subject": r.get("subject", ""),
                    "source": r.get("source", ""),
                }
    return m


def load_seen():
    seen = set()
    rows = []
    if os.path.exists(OUT_JSONL):
        with open(OUT_JSONL, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                rows.append(r)
                if r.get("message_id"):
                    seen.add(r["message_id"])
    return seen, rows


def connect_with_retry(user, pw, attempts=5):
    ctx = ssl.create_default_context()
    last = None
    for i in range(1, attempts + 1):
        try:
            host = os.environ.get("SUPERLEADS_IMAP_HOST", "imap.gmail.com")
            port = int(os.environ.get("SUPERLEADS_IMAP_PORT") or 993)
            m = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=30)
            m.login(user, pw)
            return m
        except Exception as e:
            last = e
            print("connect attempt %d failed: %s" % (i, type(e).__name__))
            time.sleep(3 * i)
    raise last


def body_text(msg):
    parts = []
    if msg.is_multipart():
        for p in msg.walk():
            ct = p.get_content_type()
            if ct == "text/plain" and "attachment" not in str(p.get("Content-Disposition", "")):
                try:
                    parts.append(p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "replace"))
                except Exception:
                    pass
    else:
        try:
            parts.append(msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", "replace"))
        except Exception:
            pass
    text = "\n".join(parts)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fmt_date(raw):
    if not raw:
        return ""
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(raw)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return raw[:40]


def main():
    global CAMPAIGN, OUT_JSONL, OUT_XLSX
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=BASE, help="working folder holding campaign.jsonl (default: script folder)")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--max", type=int, default=300)
    args = ap.parse_args()
    CAMPAIGN = os.path.join(args.dir, "campaign.jsonl")
    OUT_JSONL = os.path.join(args.dir, "replies_local.jsonl")
    OUT_XLSX = os.path.join(args.dir, "replies_local.xlsx")

    user, pw = env("SUPERLEADS_SMTP_USER"), env("SUPERLEADS_SMTP_PASSWORD")
    if not user or not pw:
        print("ERROR: mail credentials not found")
        return 2

    camp = load_campaign()
    seen, rows = load_seen()
    print("campaign recipients: %d, previously recorded replies: %d" % (len(camp), len(rows)))

    m = connect_with_retry(user, pw)
    try:
        m.select("INBOX", readonly=True)
        typ, data = m.search(None, "ALL")
        ids = data[0].split() if data and data[0] else []
        ids = ids[-args.max:]
        print("scanning %d most recent messages (INBOX total %d)" % (len(ids), len(ids)))

        added = 0
        for mid in ids:
            typ, hdr = m.fetch(mid, "(BODY.PEEK[HEADER])")
            if typ != "OK" or not hdr or not hdr[0]:
                continue
            head = hdr[0][1]
            if isinstance(head, bytes):
                head = head.decode("utf-8", "replace")
            msg = email.message_from_string(head)
            msgid = (msg.get("Message-ID") or "").strip()
            frm = dec(msg.get("From"))
            addr = addr_of(frm)
            subj = dec(msg.get("Subject"))
            date = fmt_date(msg.get("Date"))

            hit = camp.get(addr)
            if not hit:
                continue
            if msgid and msgid in seen:
                continue

            rec = {
                "message_id": msgid,
                "对方邮箱": addr,
                "对方名称": frm,
                "公司": hit["company"],
                "对应序号": hit["seq"],
                "城市": hit["city"],
                "回件主题": subj,
                "回件时间": date,
                "我方原主题": hit["sent_subject"],
                "来源": hit["source"],
                "匹配方式": "邮箱直接匹配",
                "记录时间": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
                "摘要": "",
                "正文": "",
                "主分类": "",
                "意向等级": "",
                "关键词": "",
                "建议复核": "",
            }
            # Fetch full body only for new matched replies.
            tb, bdata = m.fetch(mid, "(RFC822)")
            if tb == "OK" and bdata and bdata[0]:
                full = email.message_from_bytes(bdata[0][1])
                body = body_text(full)
                rec["正文"] = body[:8000]
                rec["摘要"] = re.sub(r"\s+", " ", body)[:300]
            c = classify(rec["正文"], rec["回件主题"])
            rec["主分类"] = c["primary"]
            rec["意向等级"] = c["level"]
            rec["关键词"] = ", ".join(c["signals"][:6])
            rec["建议复核"] = "是" if c["needs_review"] else "否"
            rows.append(rec)
            if msgid:
                seen.add(msgid)
            added += 1
            print("NEW REPLY: seq=%s %s <%s> - %s | %s" % (
                hit["seq"], hit["company"], addr, subj[:60], format_brief(c)))
        try:
            m.logout()
        except Exception:
            pass
    except Exception as e:
        print("IMAP scan failed: %s %s" % (type(e).__name__, str(e)[:200]))
        return 3

    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Write human-readable Excel table.
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        wb = Workbook()
        ws = wb.active
        ws.title = "Replies"
        cols = ["序号", "对应序号", "公司", "对方名称", "对方邮箱", "主分类",
                "意向等级", "建议复核", "关键词", "回件主题", "回件时间", "城市",
                "匹配方式", "摘要", "正文", "记录时间"]
        ws.append(cols)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="2F5496")
            c.alignment = Alignment(vertical="center")
        level_colors = {"高意向": "C6EFCE", "中意向": "FFEB9C", "低意向": "DDEBF7",
                        "无意向": "F2F2F2", "待判断": "FFF2CC"}
        for i, r in enumerate(rows, 1):
            ws.append([i, r.get("对应序号"), r.get("公司"), r.get("对方名称"),
                       r.get("对方邮箱"), r.get("主分类"), r.get("意向等级"),
                       r.get("建议复核"), r.get("关键词"), r.get("回件主题"),
                       r.get("回件时间"), r.get("城市"), r.get("匹配方式"),
                       r.get("摘要"), r.get("正文"), r.get("记录时间")])
            cell = ws.cell(row=i + 1, column=7)
            fill = level_colors.get(r.get("意向等级"))
            if fill:
                cell.fill = PatternFill("solid", fgColor=fill)
        widths = [6, 10, 34, 30, 30, 12, 12, 11, 30, 40, 18, 20, 16, 60, 90, 18]
        for j, w in enumerate(widths, 1):
            col = ws.cell(row=1, column=j).column_letter
            ws.column_dimensions[col].width = w
        ws.freeze_panes = "A2"
        wb.save(OUT_XLSX)
        print("xlsx written: %s" % OUT_XLSX)
    except Exception as e:
        print("xlsx write skipped: %s %s" % (type(e).__name__, str(e)[:160]))

    print("DONE: new replies=%d, total recorded=%d" % (added, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
