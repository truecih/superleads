# -*- coding: utf-8 -*-
r"""Throttled, resumable bulk email sender for a personalized outreach campaign.

Security / pacing contract:
- Credentials are read ONLY from environment variables; never from a file or CLI.
- DRY RUN by default. Real transmission requires --send AND env credentials.
- One email per recipient (each body is personalized), sent via its own SMTP
  submission, with conservative pacing to avoid provider rate limits:
    * daily cap (default 20), reset per local calendar day
    * minimum delay between sends (default 240s) with random jitter
    * optional business-hours-only window (default 08:00-18:00)
- A JSONL state log records each send so runs can resume without duplicates.

Env vars (same as send_mail.py):
  SUPERLEADS_SMTP_USER / SUPERLEADS_SMTP_PASSWORD (required to send)
  SUPERLEADS_SMTP_HOST (default smtp.gmail.com), SUPERLEADS_SMTP_PORT (default 587)
  SUPERLEADS_SMTP_SECURITY (starttls|ssl, default starttls)
  SUPERLEADS_SMTP_FROM (default = USER)

Usage:
  python send_campaign.py --dry-run --limit 3        # validate, no network
  python send_campaign.py --send                      # real send, throttled, resumable
"""
import argparse
import json
import os
import random
import smtplib
import ssl
import sys
import time
from datetime import datetime, date
from email.message import EmailMessage
from pathlib import Path

sys.stdout = sys.stderr = None
_OUT = sys.__stdout__
import io
sys.stdout = io.TextIOWrapper(_OUT.buffer, encoding="utf-8")
sys.stderr = sys.stdout

BASE = Path(__file__).resolve().parent
CAMPAIGN = BASE / "campaign.jsonl"
STATE = BASE / "sent_log.jsonl"

DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 587
DEFAULT_SECURITY = "starttls"


def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def load_state():
    sent = {}
    if STATE.exists():
        for line in STATE.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                sent[int(r["seq"])] = r
            except Exception:
                continue
    return sent


def record(rec):
    with STATE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def env_settings():
    return {
        "user": os.environ.get("SUPERLEADS_SMTP_USER", ""),
        "password": os.environ.get("SUPERLEADS_SMTP_PASSWORD", ""),
        "host": os.environ.get("SUPERLEADS_SMTP_HOST") or DEFAULT_HOST,
        "port": int(os.environ.get("SUPERLEADS_SMTP_PORT") or DEFAULT_PORT),
        "security": (os.environ.get("SUPERLEADS_SMTP_SECURITY") or DEFAULT_SECURITY).lower(),
        "from_addr": os.environ.get("SUPERLEADS_SMTP_FROM") or os.environ.get("SUPERLEADS_SMTP_USER", ""),
    }


def build_message(s, rec):
    msg = EmailMessage()
    msg["From"] = s["from_addr"]
    msg["To"] = rec["to"]
    msg["Subject"] = rec["subject"]
    msg.set_content(rec["body"], charset="utf-8")
    return msg


def _send_once(s, msg, rec):
    if s["security"] == "ssl":
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(s["host"], s["port"], timeout=45, context=ctx) as server:
            server.login(s["user"], s["password"])
            server.send_message(msg, from_addr=s["from_addr"], to_addrs=[rec["to"]])
    else:
        with smtplib.SMTP(s["host"], s["port"], timeout=45) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(s["user"], s["password"])
            server.send_message(msg, from_addr=s["from_addr"], to_addrs=[rec["to"]])


def send_one(s, rec, attempts=4):
    """Send with retry: SMTP egress can time out intermittently on the first try.
    Each attempt opens a fresh connection; brief backoff between attempts."""
    msg = build_message(s, rec)
    last = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            _send_once(s, msg, rec)
            return attempt
        except Exception as exc:
            last = exc
            out({"retry": seq_label(rec), "attempt": attempt, "error": type(exc).__name__})
            if attempt < attempts:
                time.sleep(3 * attempt)
    raise last


def seq_label(rec):
    return rec.get("seq")


def in_business_hours(now, start_h, end_h):
    return start_h <= now.hour + now.minute / 60.0 < end_h


def main():
    global CAMPAIGN, STATE
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(BASE), help="working folder holding campaign.jsonl (default: script folder)")
    ap.add_argument("--send", action="store_true", help="actually transmit (needs env credentials)")
    ap.add_argument("--dry-run", action="store_true", help="validate only (default)")
    ap.add_argument("--limit", type=int, default=0, help="max sends this run (0 = daily cap)")
    ap.add_argument("--daily-cap", type=int, default=20, help="max sends per calendar day")
    ap.add_argument("--min-delay", type=int, default=240, help="minimum seconds between sends")
    ap.add_argument("--jitter", type=int, default=60, help="random extra seconds 0..jitter")
    ap.add_argument("--biz-hours-only", action="store_true", help="only send 08:00-18:00")
    ap.add_argument("--start-hour", type=int, default=8)
    ap.add_argument("--end-hour", type=int, default=18)
    args = ap.parse_args()
    work = Path(args.dir)
    CAMPAIGN = work / "campaign.jsonl"
    STATE = work / "sent_log.jsonl"

    should_send = args.send and not args.dry_run
    s = env_settings()

    if should_send and (not s["user"] or not s["password"]):
        out({"ok": False, "error": "smtp_credentials_missing",
             "message": "Set SUPERLEADS_SMTP_USER and SUPERLEADS_SMTP_PASSWORD in the environment before --send."})
        return 2

    campaign = [json.loads(x) for x in CAMPAIGN.read_text(encoding="utf-8").splitlines() if x.strip()]
    state = load_state()
    today = date.today().isoformat()
    sent_today = sum(1 for r in state.values() if r.get("date") == today and r.get("status") == "ok")
    remaining_cap = max(0, args.daily_cap - sent_today)
    limit = remaining_cap if args.limit == 0 else min(args.limit, remaining_cap)

    out({"mode": "send" if should_send else "dry-run", "total_campaign": len(campaign),
         "already_sent": len(state), "sent_today": sent_today, "remaining_cap": remaining_cap,
         "this_run_limit": limit, "from": s["from_addr"] or "(unset)",
         "host": s["host"], "security": s["security"]})

    done = 0
    skipped = 0
    last_send_ts = 0.0
    for rec in campaign:
        seq = rec["seq"]
        if done >= limit:
            break
        if seq in state and state[seq].get("status") == "ok":
            skipped += 1
            continue
        if should_send:
            # business-hours gate
            now = datetime.now()
            if args.biz_hours_only and not in_business_hours(now, args.start_hour, args.end_hour):
                out({"waiting": "outside_business_hours", "resume_at": f"{args.start_hour:02d}:00"})
                break
            wait = args.min_delay + random.randint(0, max(0, args.jitter)) - (time.time() - last_send_ts)
            if last_send_ts and wait > 0:
                out({"pacing_wait_seconds": round(wait, 1), "before_seq": seq})
                time.sleep(wait)
            try:
                send_one(s, rec)
                rec_result = {"seq": seq, "to": rec["to"], "company": rec["company"],
                              "date": date.today().isoformat(), "ts": datetime.now().isoformat(timespec="seconds"),
                              "status": "ok"}
                record(rec_result)
                last_send_ts = time.time()
                done += 1
                out({"sent": seq, "to": rec["to"]})
            except Exception as exc:
                rec_result = {"seq": seq, "to": rec["to"], "company": rec["company"],
                              "date": date.today().isoformat(), "ts": datetime.now().isoformat(timespec="seconds"),
                              "status": "error", "error": type(exc).__name__}
                record(rec_result)
                out({"error_seq": seq, "error": type(exc).__name__})
                # keep going but back off a bit on repeated failures
                time.sleep(min(30, args.min_delay))
        else:
            # dry-run: validate the message builds, no network
            try:
                msg = build_message(s if s["from_addr"] else {**s, "from_addr": "you@example.com"}, rec)
                assert msg["To"] == rec["to"] and msg["Subject"] == rec["subject"]
                done += 1
                out({"dryrun_ok": seq, "to": rec["to"], "subject": rec["subject"]})
            except Exception as exc:
                out({"dryrun_error": seq, "error": type(exc).__name__})

    out({"finished": True, "processed": done, "skipped_already_sent": skipped,
         "sent_this_run": done if should_send else 0,
         "note": "Resume by re-running; already-sent seqs are skipped automatically."})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
