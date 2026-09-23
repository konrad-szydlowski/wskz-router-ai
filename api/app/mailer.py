"""SMTP delivery. The only place in the code base that talks to the mail server."""

import smtplib
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from typing import Protocol

from email_validator import EmailNotValidError, validate_email


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


def ascii_address(address: str) -> str | None:
    """Header-safe form of an address: IDN domain -> punycode. None = the local part itself is non-ASCII."""
    try:
        return validate_email(address, check_deliverability=False).ascii_email
    except EmailNotValidError:
        return None


class SmtpMailer:
    def __init__(self, host: str, port: int, sender: str, timeout: float = 10.0):
        self.host, self.port, self.sender, self.timeout = host, port, sender, timeout

    def build(self, mail: OutgoingMail) -> EmailMessage:
        # RFC 2047 encoded-words are not allowed inside an address (RFC 2047 §5): a domain like żółw.pl goes out as
        # punycode, a local part like józef@ needs a raw UTF-8 header (RFC 6532), i.e. SMTPUTF8.
        ascii_reply_to = ascii_address(mail.reply_to)
        msg = EmailMessage(policy=policy.default if ascii_reply_to else policy.SMTPUTF8)
        msg["From"] = self.sender
        msg["To"] = mail.to
        msg["Reply-To"] = ascii_reply_to or mail.reply_to
        msg["Subject"] = clean_subject(mail.subject)
        msg.set_content(mail.body)
        return msg

    def send(self, mail: OutgoingMail) -> None:
        try:
            msg = self.build(mail)
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                if not msg.policy.utf8:
                    smtp.send_message(msg)
                    return
                smtp.ehlo()
                if not smtp.has_extn("smtputf8"):
                    raise MailerError(f"SMTP {self.host}:{self.port} lacks SMTPUTF8, needed for a non-ASCII sender")
                smtp.send_message(msg, mail_options=["SMTPUTF8", "BODY=8BITMIME"])
        except (OSError, smtplib.SMTPException) as exc:
            raise MailerError(f"SMTP {self.host}:{self.port}: {exc}") from exc

    def ping(self) -> bool:
        try:
            with smtplib.SMTP(self.host, self.port, timeout=3) as smtp:
                return smtp.noop()[0] == 250
        except (OSError, smtplib.SMTPException):
            return False
