"""SMTP delivery. The only place in the code base that talks to the mail server."""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol


class MailerError(Exception):
    """The mail server could not accept the message."""


@dataclass(frozen=True)
class OutgoingMail:
    to: str
    reply_to: str
    subject: str
    body: str


class Mailer(Protocol):
    def send(self, mail: OutgoingMail) -> None: ...


def clean_subject(subject: str, limit: int = 120) -> str:
    """Model-written subject -> single safe header line (no CR/LF header injection)."""
    one_line = " ".join(subject.split())
    return one_line[:limit].rstrip() or "Zgłoszenie"


class SmtpMailer:
    def __init__(self, host: str, port: int, sender: str, timeout: float = 10.0):
        self.host, self.port, self.sender, self.timeout = host, port, sender, timeout

    def build(self, mail: OutgoingMail) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = mail.to
        msg["Reply-To"] = mail.reply_to
        msg["Subject"] = clean_subject(mail.subject)
        msg.set_content(mail.body)
        return msg

    def send(self, mail: OutgoingMail) -> None:
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                smtp.send_message(self.build(mail))
        except (OSError, smtplib.SMTPException) as exc:
            raise MailerError(f"SMTP {self.host}:{self.port}: {exc}") from exc

    def ping(self) -> bool:
        try:
            with smtplib.SMTP(self.host, self.port, timeout=3) as smtp:
                return smtp.noop()[0] == 250
        except (OSError, smtplib.SMTPException):
            return False
