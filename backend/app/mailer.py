"""
Low-level Gmail SMTP send helper, shared by the bulk-send route and the
background scheduler. One function, one responsibility: deliver a single message.
"""

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def verify_credentials(address: str, app_password: str) -> None:
    """Raise on bad credentials / connection; return None on success."""
    smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
    try:
        smtp.starttls()
        smtp.login(address, app_password)
    finally:
        try:
            smtp.quit()
        except Exception:
            pass


def send_email(address: str, app_password: str, to: str, subject: str, body: str) -> None:
    """Send one plain-text email. Raises on failure."""
    smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
    try:
        smtp.starttls()
        smtp.login(address, app_password)

        msg = MIMEMultipart("alternative")
        msg["From"]    = address
        msg["To"]      = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        smtp.sendmail(address, to, msg.as_string())
        log.info(f"Sent to {to}: {subject!r}")
    finally:
        try:
            smtp.quit()
        except Exception:
            pass
