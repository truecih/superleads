#!/usr/bin/env python3
"""Send a bounded outbound email over SMTP for scheduled Superleads reports.

SECURITY CONTRACT
- Credentials are read ONLY from environment variables. They are never written
  to disk, never accepted as a CLI flag, and never embedded in a config file.
  A config file that contains a password/token-like key is rejected.
- The script does NOT modify mailboxes: no read, reply, mark-read, move,
  archive, or delete. It only opens an SMTP submission session and sends one
  message, so the read-only mail.read evidence boundary stays intact.
- Dry-run is the default. A real send happens only with an explicit ``--send``
  AND a password present in the environment. Recipient count is capped.
- No third-party dependencies; standard library only.

Environment variables:
  SUPERLEADS_SMTP_USER       SMTP login / username (required to send)
  SUPERLEADS_SMTP_PASSWORD   SMTP password or Gmail App Password (required to send)
  SUPERLEADS_SMTP_HOST       SMTP host (optional override)
  SUPERLEADS_SMTP_PORT       SMTP port (optional override)
  SUPERLEADS_SMTP_SECURITY   "starttls" or "ssl" (optional override)
  SUPERLEADS_SMTP_FROM       From address (optional override)

Usage examples:
  python scripts/send_mail.py --self-test
  python scripts/send_mail.py --provider gmail --to a@example.com \
      --subject "日报" --body "内容" --dry-run
  # real send (scheduled task supplies env credentials):
  python scripts/send_mail.py --provider gmail --config mail-config.json --send
"""
from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path
from typing import Any

try:
    from _superleads_common import EMAIL_RE
except ImportError:  # pragma: no cover - allows standalone use outside scripts/
    EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


# Non-secret provider presets. Credentials are never part of a preset.
PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "gmail": {"smtp_host": "smtp.gmail.com", "smtp_port": 587, "security": "starttls"},
}

ENV_KEYS = {
    "smtp_host": "SUPERLEADS_SMTP_HOST",
    "smtp_port": "SUPERLEADS_SMTP_PORT",
    "security": "SUPERLEADS_SMTP_SECURITY",
    "from_addr": "SUPERLEADS_SMTP_FROM",
    "user": "SUPERLEADS_SMTP_USER",
    "password": "SUPERLEADS_SMTP_PASSWORD",
}

# Any of these keys in a user config file is a secret leak attempt and is rejected.
FORBIDDEN_CONFIG_KEYS = {
    "password", "passwd", "pwd", "secret", "token", "access_token", "refresh_token",
    "authorization", "auth", "oauth", "api_key", "apikey", "private_key", "credential",
}

ALLOWED_CONFIG_KEYS = {
    "smtp_host", "smtp_port", "security", "from_addr", "to", "cc", "bcc",
    "subject", "body", "body_file", "html",
}

MAX_RECIPIENTS = 20
MAX_SUBJECT_CHARS = 500
MAX_BODY_CHARS = 200_000
SMTP_TIMEOUT_SECONDS = 30


class MailConfigError(ValueError):
    """Raised for invalid or unsafe mail configuration."""


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error_code": code, "message": message}


def _looks_like_email(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(EMAIL_RE.fullmatch(text))


def _as_recipient_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        raise MailConfigError(f"{field}_invalid_type")
    for item in items:
        if not _looks_like_email(item):
            raise MailConfigError(f"{field}_invalid_address")
    return items


def load_config(path: str | None) -> dict[str, Any]:
    """Load a non-secret JSON config file, rejecting any secret-looking key."""
    if not path:
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MailConfigError(f"config_unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise MailConfigError("config_not_object")
    for key in payload:
        lowered = str(key).strip().lower()
        if lowered in FORBIDDEN_CONFIG_KEYS:
            # Never echo the value; the key name alone is enough to diagnose.
            raise MailConfigError(f"config_secret_field_forbidden: {lowered}")
        if lowered not in ALLOWED_CONFIG_KEYS:
            raise MailConfigError(f"config_unknown_field: {lowered}")
    return {str(key).strip().lower(): value for key, value in payload.items()}


def resolve_settings(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    """Merge config < environment < CLI, with secrets strictly env-only."""
    preset = dict(PROVIDER_PRESETS.get(args.provider or "", {}))

    def value(name: str, cli_value: Any) -> Any:
        if cli_value is not None:
            return cli_value
        env_value = os.environ.get(ENV_KEYS[name])
        if env_value is not None and env_value != "":
            return env_value
        return config.get(name, preset.get(name))

    settings: dict[str, Any] = {
        "smtp_host": value("smtp_host", args.host),
        "smtp_port": value("smtp_port", args.port),
        "security": str(value("security", args.security) or "starttls").strip().lower(),
        "from_addr": value("from_addr", args.from_addr),
        "user": value("user", None),
        "password": value("password", None),
    }

    if settings["security"] not in {"starttls", "ssl"}:
        raise MailConfigError("security_invalid")
    if settings["smtp_port"] is not None:
        try:
            settings["smtp_port"] = int(settings["smtp_port"])
        except (TypeError, ValueError) as exc:
            raise MailConfigError("smtp_port_invalid") from exc
        if not 1 <= settings["smtp_port"] <= 65535:
            raise MailConfigError("smtp_port_out_of_range")
    else:
        settings["smtp_port"] = 465 if settings["security"] == "ssl" else 587

    subject = args.subject if args.subject is not None else config.get("subject")
    body = args.body if args.body is not None else config.get("body")
    body_file = args.body_file if args.body_file is not None else config.get("body_file")
    if body_file:
        try:
            body = Path(body_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise MailConfigError(f"body_file_unreadable: {exc}") from exc
    settings["subject"] = str(subject or "").strip()
    settings["body"] = str(body or "")
    settings["html"] = bool(args.html or config.get("html", False))

    recipients: list[str] = []
    for field in ("to", "cc", "bcc"):
        cli_value = getattr(args, field, None)
        configured = config.get(field)
        source = cli_value if cli_value is not None else configured
        recipients.extend(_as_recipient_list(source, field))
    # Deduplicate while preserving order.
    settings["recipients"] = list(dict.fromkeys(recipients))
    settings["to"] = recipients

    if not settings["recipients"]:
        raise MailConfigError("no_recipients")
    if len(settings["recipients"]) > MAX_RECIPIENTS:
        raise MailConfigError(f"too_many_recipients (max {MAX_RECIPIENTS})")
    if not settings["subject"]:
        raise MailConfigError("subject_missing")
    if len(settings["subject"]) > MAX_SUBJECT_CHARS:
        raise MailConfigError("subject_too_long")
    if not settings["body"].strip():
        raise MailConfigError("body_missing")
    if len(settings["body"]) > MAX_BODY_CHARS:
        raise MailConfigError("body_too_long")

    from_addr = settings["from_addr"] or settings["user"]
    if not from_addr or not _looks_like_email(from_addr):
        raise MailConfigError("from_invalid")
    settings["from_addr"] = str(from_addr)

    return settings


def build_message(settings: dict[str, Any]) -> EmailMessage:
    """Build an EmailMessage; never includes credentials or file paths."""
    msg = EmailMessage()
    msg["From"] = settings["from_addr"]
    msg["To"] = ", ".join(settings["recipients"])
    msg["Subject"] = settings["subject"]
    msg.set_content(settings["body"], subtype="html" if settings["html"] else "plain", charset="utf-8")
    return msg


def send_message(settings: dict[str, Any], msg: EmailMessage) -> None:
    """Open one SMTP submission session and send one message. No mailbox mutation."""
    if not settings["user"] or not settings["password"]:
        raise MailConfigError("smtp_credentials_missing")
    host = str(settings["smtp_host"] or "")
    if not host:
        raise MailConfigError("smtp_host_missing")
    context = ssl.create_default_context()
    port = int(settings["smtp_port"])
    if settings["security"] == "ssl":
        with smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT_SECONDS, context=context) as server:
            server.login(str(settings["user"]), str(settings["password"]))
            server.send_message(msg, from_addr=settings["from_addr"], to_addrs=settings["recipients"])
    else:
        with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(str(settings["user"]), str(settings["password"]))
            server.send_message(msg, from_addr=settings["from_addr"], to_addrs=settings["recipients"])


def run(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args.config)
    settings = resolve_settings(args, config)
    msg = build_message(settings)

    # Dry-run always wins: an accidental --dry-run --send combination must not transmit.
    should_send = bool(args.send) and not bool(args.dry_run)
    summary = {
        "ok": True,
        "mode": "send" if should_send else "dry-run",
        "smtp_host": settings["smtp_host"],
        "smtp_port": settings["smtp_port"],
        "security": settings["security"],
        "from": settings["from_addr"],
        "recipient_count": len(settings["recipients"]),
        "subject": settings["subject"],
    }
    if should_send:
        send_message(settings, msg)
        summary["sent"] = True
    else:
        # Return only a bounded preview; never echo a password.
        summary["sent"] = False
        summary["preview"] = settings["body"][:200]
    return summary


def self_test(temp_dir: str | None = None) -> dict[str, Any]:
    """Local, network-free checks of config parsing and message building."""
    checks: list[str] = []

    cfg = load_config(None)
    assert cfg == {}
    checks.append("empty_config")

    # Secret-in-config rejection: write a small JSON and ensure it is refused.
    # Use a caller-supplied directory (inside the workspace) by default so this
    # test works under a restricted file sandbox with no %TEMP% access.
    base = Path(temp_dir) if temp_dir else Path(__file__).resolve().parent
    marker = Path(base) / ".send_mail_selftest.json"
    try:
        marker.write_text(json.dumps({"smtp_host": "x", "password": "hunter2"}), encoding="utf-8")
        try:
            load_config(str(marker))
        except MailConfigError as exc:
            assert "config_secret_field_forbidden" in str(exc)
        else:
            raise AssertionError("secret config key was not rejected")
        checks.append("secret_config_rejected")

        marker.write_text(
            json.dumps({
                "smtp_host": "smtp.gmail.com", "smtp_port": 587,
                "to": ["a@example.com"], "subject": "s", "body": "b",
            }),
            encoding="utf-8",
        )
        loaded = load_config(str(marker))
        assert loaded["smtp_host"] == "smtp.gmail.com"
        checks.append("good_config_loaded")
    finally:
        try:
            marker.unlink()
        except OSError:
            pass

    args = argparse.Namespace(
        provider="gmail", host=None, port=None, security=None, from_addr="me@gmail.com",
        subject="测试", body="正文", body_file=None, html=False,
        to=["a@example.com"], cc=None, bcc=None, config=None, send=False,
    )
    settings = resolve_settings(args, {})
    assert settings["smtp_host"] == "smtp.gmail.com"
    assert settings["security"] == "starttls"
    msg = build_message(settings)
    assert msg["Subject"] == "测试"
    assert "a@example.com" in msg["To"]
    checks.append("gmail_preset_and_message")

    # Credentials must not be embedded in the message.
    rendered = msg.as_string()
    assert "SUPERLEADS_SMTP_PASSWORD" not in rendered
    checks.append("no_secret_in_message")

    # Too many recipients must be rejected before any network action.
    many = argparse.Namespace(**vars(args))
    many.to = [f"u{i}@example.com" for i in range(MAX_RECIPIENTS + 1)]
    try:
        resolve_settings(many, {})
    except MailConfigError as exc:
        assert "too_many_recipients" in str(exc)
    else:
        raise AssertionError("recipient cap was not enforced")
    checks.append("recipient_cap")

    return {"ok": True, "self_test": "passed", "checks": checks}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true", help="run local checks without network")
    parser.add_argument("--self-test-dir", help="writable directory used by --self-test (defaults to scripts/)")
    parser.add_argument("--provider", choices=sorted(PROVIDER_PRESETS), help="non-secret SMTP preset, e.g. gmail")
    parser.add_argument("--config", help="path to a non-secret JSON config file")
    parser.add_argument("--host", help="SMTP host (overrides config/preset/env)")
    parser.add_argument("--port", type=int, help="SMTP port")
    parser.add_argument("--security", choices=("starttls", "ssl"), help="SMTP security mode")
    parser.add_argument("--from", dest="from_addr", help="From address")
    parser.add_argument("--to", help="recipient address(es), comma-separated")
    parser.add_argument("--cc", help="cc address(es), comma-separated")
    parser.add_argument("--bcc", help="bcc address(es), comma-separated")
    parser.add_argument("--subject", help="email subject")
    parser.add_argument("--body", help="plain-text body")
    parser.add_argument("--body-file", help="read body from a UTF-8 file")
    parser.add_argument("--html", action="store_true", help="treat the body as HTML")
    parser.add_argument("--dry-run", action="store_true", help="validate and build without sending (default)")
    parser.add_argument("--send", action="store_true", help="actually transmit (requires env credentials)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.self_test:
            payload = self_test(args.self_test_dir)
        else:
            payload = run(args)
    except MailConfigError as exc:
        print(json.dumps(_error(exc.args[0] if exc.args else "mail_config_error", str(exc)), ensure_ascii=False))
        return 2
    except Exception as exc:  # pragma: no cover - defensive
        print(json.dumps(_error("mail_send_failed", type(exc).__name__), ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
