"""Send research digest email over authenticated, encrypted SMTP.

Providers may use implicit TLS on nonstandard ports such as 994.
This module never falls back to an unencrypted SMTP connection.
"""
import os
import smtplib
import ssl
from email.message import EmailMessage


def smtp_settings():
    """Resolve settings without guessing the SMTP endpoint of ECNU student mail."""
    missing = [x for x in ("SMTP_USER", "SMTP_PASSWORD", "TO_EMAIL") if not os.getenv(x)]
    if missing:
        raise RuntimeError("Missing Actions Secrets: " + ", ".join(missing))
    user = os.environ["SMTP_USER"].strip()
    host = (os.getenv("SMTP_HOST") or "").strip()
    port_raw = (os.getenv("SMTP_PORT") or "").strip()
    # Student-mail server settings are not publicly confirmed. Fail safely.
    if user.lower().endswith("@stu.ecnu.edu.cn") and (not host or not port_raw):
        raise RuntimeError("ECNU student SMTP_HOST and SMTP_PORT must be verified and configured as Repository variables; see README.md")
    host = host or "smtp.gmail.com"
    try:
        port = int(port_raw or "465")
    except ValueError as e:
        raise ValueError("SMTP_PORT must be a valid TCP port number") from e
    if not 1 <= port <= 65535:
        raise ValueError("SMTP_PORT must be between 1 and 65535")
    security = (os.getenv("SMTP_SECURITY") or "auto").strip().lower()
    if security == "auto":
        # 465 is standard implicit TLS; 994 is used by some 163 enterprise systems.
        security = "ssl" if port in (465, 994) else "starttls"
    if security not in ("ssl", "starttls"):
        raise ValueError("SMTP_SECURITY must be 'ssl', 'starttls', or 'auto'")
    return {"user": user, "password": os.environ["SMTP_PASSWORD"],
            "recipient": os.environ["TO_EMAIL"], "host": host,
            "port": port, "security": security}


def _deliver(message: EmailMessage):
    cfg = smtp_settings()
    # SMTP_SSL negotiates TLS before authentication. STARTTLS upgrades before login.
    if cfg["security"] == "ssl":
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30, context=ssl.create_default_context()) as server:
            server.login(cfg["user"], cfg["password"])
            server.send_message(message)
    else:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(cfg["user"], cfg["password"])
            server.send_message(message)


def send_email(subject: str, html_body: str, text_body: str):
    cfg = smtp_settings()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["user"]
    msg["To"] = cfg["recipient"]
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    _deliver(msg)


def send_test_email():
    """Test actual delivery without calling a paid LLM API or editing archives."""
    cfg = smtp_settings()
    msg = EmailMessage()
    msg["Subject"] = "Research Radar · SMTP 连接测试"
    msg["From"] = cfg["user"]
    msg["To"] = cfg["recipient"]
    msg.set_content("如果你收到了这封邮件，GitHub Actions 已成功通过配置的 SMTP 服务器发送测试邮件。\n本次测试没有调用 DeepSeek API，也没有修改 Research Radar 历史归档。")
    _deliver(msg)
    print(f"SMTP test sent using encrypted {cfg['security'].upper()} connection to {cfg['host']}:{cfg['port']}.")


if __name__ == "__main__":
    send_test_email()
