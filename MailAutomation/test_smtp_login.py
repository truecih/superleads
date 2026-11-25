# -*- coding: utf-8 -*-
"""SMTP login-only test. Never sends mail and never prints credentials."""
import json
import os
import smtplib
import ssl
import sys

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

if not user or not pw:
    print(json.dumps({"ok": False, "error": "credentials_missing"}, ensure_ascii=False))
    sys.exit(2)

try:
    if security == "ssl":
        with smtplib.SMTP_SSL(host, port, timeout=45, context=ssl.create_default_context()) as s:
            s.login(user, pw)
    else:
        with smtplib.SMTP(host, port, timeout=45) as s:
            s.ehlo()
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
            s.login(user, pw)
    print(json.dumps({"ok": True, "login": "success", "host": host, "port": port,
                      "security": security}, ensure_ascii=False))
    sys.exit(0)
except Exception as exc:
    print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)[:200]},
                     ensure_ascii=False))
    sys.exit(1)
