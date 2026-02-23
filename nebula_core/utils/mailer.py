# nebula_core/utils/mailer.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import os
import smtplib
import logging
from email.message import EmailMessage
from typing import Optional

from .config import load_yaml_config

logger = logging.getLogger("nebula_core.mailer")


def _as_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _load_mail_settings():
    yaml_cfg = load_yaml_config()
    mail_cfg = yaml_cfg.get("mail", {}) if isinstance(yaml_cfg, dict) else {}

    server = (os.getenv("NEBULA_MAIL_SERVER") or str(mail_cfg.get("server") or "")).strip()
    port = int(os.getenv("NEBULA_MAIL_PORT") or int(mail_cfg.get("port") or 0))
    mail_user = (os.getenv("NEBULA_MAIL_USERNAME") or str(mail_cfg.get("username") or "")).strip()
    mail_pass = (os.getenv("NEBULA_MAIL_PASSWORD") or str(mail_cfg.get("password") or "")).strip()
    use_tls = _as_bool(os.getenv("NEBULA_MAIL_USE_TLS"), default=bool(mail_cfg.get("use_tls", True)))
    use_ssl = _as_bool(os.getenv("NEBULA_MAIL_USE_SSL"), default=bool(mail_cfg.get("use_ssl", False)))
    from_email = (
        os.getenv("NEBULA_MAIL_FROM")
        or str(mail_cfg.get("from") or "")
        or (mail_user if "@" in mail_user else "no-reply@nebula.local")
    ).strip()
    return {
        "server": server,
        "port": port,
        "mail_user": mail_user,
        "mail_pass": mail_pass,
        "use_tls": use_tls,
        "use_ssl": use_ssl,
        "from_email": from_email,
    }


def _send_email(to_email: str, subject: str, text_body: str, html_body: str = "") -> bool:
    cfg = _load_mail_settings()
    server = cfg["server"]
    port = int(cfg["port"])
    mail_user = cfg["mail_user"]
    mail_pass = cfg["mail_pass"]
    use_tls = bool(cfg["use_tls"])
    use_ssl = bool(cfg["use_ssl"])
    from_email = cfg["from_email"]

    if not server or port <= 0:
        logger.warning("Mail config incomplete: server/port not configured")
        return False
    if not to_email or "@" not in to_email:
        logger.warning("Invalid recipient email for outgoing message")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    if use_ssl:
        with smtplib.SMTP_SSL(server, port, timeout=10) as smtp:
            if mail_user:
                smtp.login(mail_user, mail_pass)
            smtp.send_message(msg)
        return True

    with smtplib.SMTP(server, port, timeout=10) as smtp:
        if use_tls:
            smtp.starttls()
        if mail_user:
            smtp.login(mail_user, mail_pass)
        smtp.send_message(msg)
    return True


def send_password_reset_code(to_email: str, username: str, code: str, ttl_sec: int = 120) -> bool:
    safe_user = str(username or "user").strip() or "user"
    safe_code = str(code or "").strip()
    ttl_min = max(1, int(ttl_sec // 60))
    text_body = (
        f"Hello {safe_user},\n\n"
        f"Your Nebula password reset code is: {safe_code}\n"
        f"This code expires in {int(ttl_sec)} seconds.\n\n"
        "If you did not request this, ignore this message."
    )
    html_body = f"""
<!doctype html>
<html>
<body style="margin:0;padding:0;background:#0b0f1a;color:#eef2ff;font-family:Arial,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#0b0f1a;padding:24px 0;">
    <tr><td align="center">
      <table role="presentation" width="560" cellspacing="0" cellpadding="0" style="max-width:560px;background:#131a2a;border:1px solid #27314a;border-radius:14px;overflow:hidden;">
        <tr>
          <td style="padding:20px 24px;background:linear-gradient(90deg,#1f2b46,#1a365d);font-size:18px;font-weight:700;color:#ffffff;">
            Nebula Systems
          </td>
        </tr>
        <tr>
          <td style="padding:24px;">
            <p style="margin:0 0 14px;color:#dbe7ff;font-size:15px;">Hello <b>{safe_user}</b>,</p>
            <p style="margin:0 0 18px;color:#b9c7e8;font-size:14px;line-height:1.6;">
              We received a password reset request for your Nebula account.
            </p>
            <div style="margin:0 0 18px;padding:14px 16px;background:#0f1626;border:1px dashed #3d5a99;border-radius:10px;text-align:center;">
              <div style="font-size:12px;color:#9db4e8;letter-spacing:0.06em;text-transform:uppercase;margin-bottom:6px;">Reset Code</div>
              <div style="font-size:30px;font-weight:700;letter-spacing:0.18em;color:#ffffff;">{safe_code}</div>
            </div>
            <p style="margin:0 0 8px;color:#9db4e8;font-size:13px;">
              Expires in about {ttl_min} minute(s).
            </p>
            <p style="margin:0;color:#8da2cf;font-size:12px;line-height:1.6;">
              If you did not request this reset, ignore this message.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
""".strip()
    try:
        return _send_email(
            to_email=to_email,
            subject="Nebula password reset code",
            text_body=text_body,
            html_body=html_body,
        )
    except Exception as exc:
        logger.warning("Failed to send password reset email: %s", exc)
        return False


def send_test_email(to_email: str) -> bool:
    text_body = (
        "Nebula SMTP test successful.\n\n"
        "This message confirms your mail configuration is working."
    )
    html_body = """
<!doctype html>
<html>
<body style="margin:0;padding:24px;background:#0b0f1a;color:#eef2ff;font-family:Arial,sans-serif;">
  <div style="max-width:560px;margin:0 auto;background:#131a2a;border:1px solid #27314a;border-radius:14px;padding:24px;">
    <h2 style="margin:0 0 10px;color:#ffffff;">Nebula Mail Check</h2>
    <p style="margin:0;color:#b9c7e8;">SMTP test successful. Your outbound mail configuration is operational.</p>
  </div>
</body>
</html>
""".strip()
    try:
        return _send_email(
            to_email=to_email,
            subject="Nebula SMTP test",
            text_body=text_body,
            html_body=html_body,
        )
    except Exception as exc:
        logger.warning("Failed to send test email: %s", exc)
        return False
