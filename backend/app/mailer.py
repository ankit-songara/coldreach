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


def normalize_app_password(app_password: str) -> str:
    """Strip ALL whitespace from a Gmail App Password.

    Google displays App Passwords grouped for readability ("abcd efgh ijkl mnop"),
    and users routinely paste them with the spaces — but the real 16-char password
    has none, so smtp.login rejects the spaced version. Removing every whitespace
    char (spaces, tabs, stray newlines from copy-paste) makes both forms work.
    """
    return "".join((app_password or "").split())


def verify_credentials(address: str, app_password: str) -> None:
    """Raise on bad credentials / connection; return None on success."""
    smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
    try:
        smtp.starttls()
        smtp.login(address.strip(), normalize_app_password(app_password))
    finally:
        try:
            smtp.quit()
        except Exception:
            pass


def _build_message(address: str, to: str, subject: str, body: str) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["From"]    = address
    msg["To"]      = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    return msg


def send_email(address: str, app_password: str, to: str, subject: str, body: str) -> None:
    """Send one plain-text email over a fresh connection. Raises on failure."""
    smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
    try:
        smtp.starttls()
        smtp.login(address.strip(), normalize_app_password(app_password))
        smtp.sendmail(address, to, _build_message(address, to, subject, body).as_string())
        log.info(f"Sent to {to}: {subject!r}")
    finally:
        try:
            smtp.quit()
        except Exception:
            pass


class GmailSMTP:
    """Reusable authenticated Gmail SMTP session — one login, many sends.

    Re-logging in per message is slow and is itself a pattern Gmail flags. The
    background scheduler uses this to send a batch of due follow-ups over a single
    connection. Use as a context manager:

        with GmailSMTP(addr, pw) as smtp:
            smtp.send(to, subject, body)
    """

    def __init__(self, address: str, app_password: str, timeout: int = 20):
        self.address = address.strip()
        self.app_password = normalize_app_password(app_password)
        self.timeout = timeout
        self._smtp: smtplib.SMTP | None = None

    def __enter__(self) -> "GmailSMTP":
        self._smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=self.timeout)
        self._smtp.starttls()
        self._smtp.login(self.address, self.app_password)
        return self

    def send(self, to: str, subject: str, body: str) -> None:
        assert self._smtp is not None, "GmailSMTP used outside its context manager"
        self._smtp.sendmail(self.address, to, _build_message(self.address, to, subject, body).as_string())
        log.info(f"Sent to {to}: {subject!r}")

    def __exit__(self, *exc) -> None:
        if self._smtp is not None:
            try:
                self._smtp.quit()
            except Exception:
                pass
            self._smtp = None
