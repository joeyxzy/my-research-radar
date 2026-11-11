"""Send complete multipart HTML/plain-text email using a provider's SMTP credential."""
import os
import smtplib
from email.message import EmailMessage


def send_email(subject: str, html_body: str, text_body: str):
    missing = [x for x in ("SMTP_USER", "SMTP_PASSWORD", "TO_EMAIL") if not os.getenv(x)]
    if missing:
        raise RuntimeError("Missing Actions Secrets: " + ", ".join(missing))
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["TO_EMAIL"]
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    host = os.getenv("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.getenv("SMTP_PORT") or "465")
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as session:
            session.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            session.send_message(msg)
    else:
        # STARTTLS is required for alternate SMTP ports (e.g. 587).
        with smtplib.SMTP(host, port, timeout=30) as session:
            session.starttls()
            session.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            session.send_message(msg)
