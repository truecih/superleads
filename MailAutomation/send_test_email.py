# -*- coding: utf-8 -*-
"""Send ONE test email to the configured sender address itself (self-test).

Credentials are read ONLY from the environment (never from a file):
  SUPERLEADS_SMTP_USER / SUPERLEADS_SMTP_PASSWORD
  SUPERLEADS_SMTP_HOST (default smtp.gmail.com), SUPERLEADS_SMTP_PORT (default 587)
"""
import json
import os
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage

try:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

user = os.environ.get("SUPERLEADS_SMTP_USER", "")
pw = os.environ.get("SUPERLEADS_SMTP_PASSWORD", "")
host = os.environ.get("SUPERLEADS_SMTP_HOST") or "smtp.gmail.com"
port = int(os.environ.get("SUPERLEADS_SMTP_PORT") or 587)
security = (os.environ.get("SUPERLEADS_SMTP_SECURITY") or "starttls").lower()

msg = EmailMessage()
msg["From"] = user
msg["To"] = user  # send to self
msg["Subject"] = "[TEST] Outreach mail channel self-test"
msg.set_content(
    "Hello,\n\n"
    "This is a test email to verify that the SMTP channel and formatting work.\n"
    "No customer data is involved.\n\n"
    "-- MailAutomation self-test",
    charset="utf-8",
)


def send_once():
    if security == "ssl":
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, timeout=45, context=ctx) as srv:
            srv.login(user, pw)
            srv.send_message(msg, from_addr=user, to_addrs=[user])
    else:
        with smtplib.SMTP(host, port, timeout=45) as srv:
            srv.ehlo()
            srv.starttls(context=ssl.create_default_context())
            srv.ehlo()
            srv.login(user, pw)
            srv.send_message(msg, from_addr=user, to_addrs=[user])


if not user or not pw:
    print(json.dumps({"ok": False, "error": "credentials_missing"}, ensure_ascii=False))
    sys.exit(2)

last = None
for attempt in range(1, 5):
    try:
        send_once()
        print(json.dumps({"ok": True, "sent_to_self": user, "attempt": attempt,
                          "note": "Check your inbox (and Spam folder)."}, ensure_ascii=False))
        sys.exit(0)
    except Exception as exc:
        last = {"attempt": attempt, "error": type(exc).__name__, "detail": str(exc)[:160]}
        print(json.dumps(last, ensure_ascii=False))
        time.sleep(5)
print(json.dumps({"ok": False, "last": last}, ensure_ascii=False))
sys.exit(1)
