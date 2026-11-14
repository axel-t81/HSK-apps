import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from datetime import datetime

from .errors import ConfigurationError


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    security: str
    username: str
    password: str
    sender: str
    recipient: str

    @classmethod
    def from_environment(cls):
        host = os.environ.get("DIGEST_SMTP_HOST", "").strip()
        sender = os.environ.get("DIGEST_EMAIL_FROM", "").strip()
        recipient = os.environ.get("DIGEST_EMAIL_TO", "").strip()
        if not host or not sender or not recipient:
            raise ConfigurationError(
                "Set DIGEST_SMTP_HOST, DIGEST_EMAIL_FROM, and DIGEST_EMAIL_TO to send email"
            )
        try:
            port = int(os.environ.get("DIGEST_SMTP_PORT", "587"))
        except ValueError as error:
            raise ConfigurationError("DIGEST_SMTP_PORT must be an integer") from error
        if not 1 <= port <= 65535:
            raise ConfigurationError("DIGEST_SMTP_PORT is outside the valid range")
        security = os.environ.get("DIGEST_SMTP_SECURITY", "starttls").lower()
        if security not in ("starttls", "ssl"):
            raise ConfigurationError("DIGEST_SMTP_SECURITY must be starttls or ssl")
        for label, value in (("sender", sender), ("recipient", recipient), ("host", host)):
            if "\r" in value or "\n" in value:
                raise ConfigurationError(f"Invalid newline in SMTP {label}")
        return cls(
            host=host, port=port, security=security,
            username=os.environ.get("DIGEST_SMTP_USERNAME", ""),
            password=os.environ.get("DIGEST_SMTP_PASSWORD", ""),
            sender=sender, recipient=recipient,
        )


def build_message(config, subject, text_body, html_body):
    if "\r" in subject or "\n" in subject:
        raise ConfigurationError("Email subject contains a newline")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = config.recipient
    message["Date"] = format_datetime(datetime.now().astimezone())
    domain = config.sender.rsplit("@", 1)[-1] if "@" in config.sender else None
    message["Message-ID"] = make_msgid("china-business-digest", domain)
    message.set_content(text_body, subtype="plain", charset="utf-8")
    message.add_alternative(html_body, subtype="html", charset="utf-8")
    return message


def send_email(config, subject, text_body, html_body, smtp_factory=None):
    message = build_message(config, subject, text_body, html_body)
    context = ssl.create_default_context()
    if config.security == "ssl":
        factory = smtp_factory or smtplib.SMTP_SSL
        with factory(config.host, config.port, timeout=45, context=context) as smtp:
            if config.username:
                smtp.login(config.username, config.password)
            smtp.send_message(message)
    else:
        factory = smtp_factory or smtplib.SMTP
        with factory(config.host, config.port, timeout=45) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            if config.username:
                smtp.login(config.username, config.password)
            smtp.send_message(message)
