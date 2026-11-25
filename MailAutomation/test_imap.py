# -*- coding: utf-8 -*-
"""IMAP login/inbox probe. Prints only counts, never credentials or message content."""
import imaplib
import json
import os
import ssl
import sys
import time

try:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

user = os.environ.get("SUPERLEADS_SMTP_USER", "")
pw = os.environ.get("SUPERLEADS_SMTP_PASSWORD", "")
host = os.environ.get("SUPERLEADS_IMAP_HOST", "imap.gmail.com")
port = int(os.environ.get("SUPERLEADS_IMAP_PORT") or 993)

if not user or not pw:
    print(json.dumps({"ok": False, "error": "credentials_missing"}, ensure_ascii=False))
    sys.exit(2)

last = None
for attempt in range(1, 6):
    try:
        m = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=30)
        m.login(user, pw)
        typ, data = m.select("INBOX", readonly=True)
        count = int(data[0]) if typ == "OK" and data and data[0] else -1
        m.logout()
        print(json.dumps({"ok": True, "inbox_count": count, "attempt": attempt}, ensure_ascii=False))
        sys.exit(0)
    except Exception as exc:
        last = {"attempt": attempt, "error": type(exc).__name__, "detail": str(exc)[:160]}
        print(json.dumps(last, ensure_ascii=False))
        time.sleep(3 * attempt)
print(json.dumps({"ok": False, "last": last}, ensure_ascii=False))
sys.exit(1)
