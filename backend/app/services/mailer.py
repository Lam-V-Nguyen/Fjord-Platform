"""
mailer.py - Sends e-mail (invitations, password resets, access requests) via SMTP.
Without SMTP_HOST nothing is sent: it logs and returns False -> the admin page shows the link to send manually.
"""
import logging, smtplib, ssl
from email.message import EmailMessage
from config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_TLS, SMTP_SSL, PUBLIC_URL

log = logging.getLogger("fjord.mail")
APP_NAME = "Fjord Platform"

def enabled() -> bool:
    return bool(SMTP_HOST)

def send(to: str | list[str], subject: str, text: str) -> bool:
    recipients = [to] if isinstance(to, str) else list(to)
    recipients = [r for r in recipients if r]
    if not recipients: return False
    if not enabled():
        log.warning("SMTP not configured – e-mail to %s not sent: %s", recipients, subject)
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = SMTP_FROM, ", ".join(recipients), f"[{APP_NAME}] {subject}"
    msg.set_content(text)
    try:
        ctx = ssl.create_default_context()
        if SMTP_SSL:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=20) as s:
                if SMTP_USER: s.login(SMTP_USER, SMTP_PASSWORD or "")
                s.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
                if SMTP_TLS: s.starttls(context=ctx)
                if SMTP_USER: s.login(SMTP_USER, SMTP_PASSWORD or "")
                s.send_message(msg)
        log.info("E-mail sent to %s: %s", recipients, subject)
        return True
    except Exception as e:
        log.error("E-mail to %s failed: %s", recipients, e)
        return False

def invite_text(name: str, username: str, link: str, hours: int, inviter: str) -> str:
    return (f"Hello {name or username},\n\n{inviter} has invited you to {APP_NAME} ({PUBLIC_URL}).\n\n"
        f"Your username: {username}\nSet your password here (valid for {hours} hours, single use):\n{link}\n\n"
        "If you did not expect this invitation, you can ignore this e-mail.\n"
    )

def reset_text(name: str, username: str, link: str, hours: int) -> str:
    return (f"Hello {name or username},\n\nA password reset was requested for your {APP_NAME} account '{username}'.\n"
        f"Choose a new password here (valid for {hours} hours, single use):\n{link}\n\n"
        "If you did not request this, ignore this e-mail - your password stays unchanged.\n"
    )

def access_request_text(username: str, email: str, full_name: str, organisation: str, note: str) -> str:
    return (f"New access request for {APP_NAME}:\n\nUsername: {username}\nName: {full_name}\nE-mail: {email}\n"
        f"Organisation: {organisation}\nMessage: {note}\n\nApprove or reject it in {PUBLIC_URL}/admin\n"
    )
