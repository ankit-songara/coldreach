"""Gmail SMTP helpers shared by the send routes."""

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
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


def auth_error_message(e: smtplib.SMTPAuthenticationError) -> str:
    """Human message for a Gmail login rejection, distinguishing the two causes:
      535-5.7.8          Username and Password not accepted → wrong App Password
      534-5.7.14 / 5.7.9 Please log in via your web browser → IP/security block
                         (common when connecting from a datacenter IP like Vercel)
    """
    code = getattr(e, "smtp_code", "?")
    raw = getattr(e, "smtp_error", b"")
    detail = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    detail = " ".join(detail.split())
    if "5.7.14" in detail or "5.7.9" in detail or "web browser" in detail.lower():
        return (f"Gmail blocked this login from the server's IP [{code}]. Your App "
                f"Password is likely fine — Gmail distrusts logins from cloud/datacenter "
                f"IPs. Gmail said: {detail}")
    return (f"Gmail rejected the credentials [{code}]. Make sure 2-Step Verification "
            f"is ON and you pasted a 16-char App Password (not your normal password). "
            f"Gmail said: {detail}")


def build_message(
    address: str, to: str, subject: str, body: str,
    attachment: tuple[str, bytes] | None = None,
) -> MIMEMultipart:
    """Build the outgoing message. `attachment` is (filename, data) — used to
    attach the candidate's résumé on formal application emails."""
    if attachment is None:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain"))
    else:
        msg = MIMEMultipart("mixed")
        msg.attach(MIMEText(body, "plain"))
        filename, data = attachment
        part = MIMEApplication(data, Name=filename)
        part["Content-Disposition"] = f'attachment; filename="{filename}"'
        msg.attach(part)
    msg["From"]    = address
    msg["To"]      = to
    msg["Subject"] = subject
    return msg


