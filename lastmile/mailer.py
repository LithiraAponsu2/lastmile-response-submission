"""Email delivery behind one small interface. Only publish-side code (api.py, notify.py) uses it; agents cannot.

* FileMailer : writes each message as an .eml file (and keeps the last few in memory). The default: nothing leaves
               the machine, which is right for demos and tests. LASTMILE_DEV_MAILBOX=1 lets the console show them.
* SmtpMailer : any SMTP relay (Google Workspace, SendGrid/Mailgun SMTP, a government relay). Configured by env:
               LASTMILE_SMTP_HOST, LASTMILE_SMTP_PORT (587), LASTMILE_SMTP_USER, LASTMILE_SMTP_PASSWORD,
               LASTMILE_SMTP_STARTTLS (1), LASTMILE_MAIL_FROM.
* off        : LASTMILE_MAIL=off disables email entirely (subscriptions are then refused).
"""

from __future__ import annotations

import os
import smtplib
import ssl
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Optional, Protocol


@dataclass
class Mail:
    to: str
    subject: str
    body: str
    headers: dict[str, str] = field(default_factory=dict)
    lang: str = "en"

    def to_message(self, sender: str) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = self.to
        msg["Subject"] = self.subject
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1].strip(">") or "lastmile.local")
        msg["Content-Language"] = self.lang
        for k, v in self.headers.items():
            msg[k] = v
        msg.set_content(self.body)  # plain text only: readable everywhere, nothing to render or execute
        return msg


class MailError(RuntimeError):
    pass


class Mailer(Protocol):
    name: str
    sender: str

    def send(self, mail: Mail) -> None: ...


class FileMailer:
    name = "file"

    def __init__(self, directory: Optional[os.PathLike | str] = None, sender: str = "alerts@lastmile.local",
                 keep: int = 200) -> None:
        self.dir = Path(directory) if directory else None
        self.sender = sender
        self.sent: deque[Mail] = deque(maxlen=keep)
        self._lock = threading.Lock()
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)

    def send(self, mail: Mail) -> None:
        with self._lock:
            self.sent.append(mail)
            if self.dir:
                (self.dir / f"{uuid.uuid4().hex}.eml").write_bytes(bytes(mail.to_message(self.sender)))


class SmtpMailer:
    name = "smtp"

    def __init__(self, host: str, port: int = 587, user: str = "", password: str = "", starttls: bool = True,
                 sender: str = "alerts@lastmile.local", timeout: float = 20.0) -> None:
        self.host, self.port, self.user, self.password = host, port, user, password
        self.starttls, self.sender, self.timeout = starttls, sender, timeout

    def _open(self):
        s = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        try:
            if self.starttls:
                s.starttls(context=ssl.create_default_context())
            if self.user:
                s.login(self.user, self.password)
        except BaseException:
            _close(s)
            raise
        return s

    def send(self, mail: Mail) -> None:
        err = self.send_batch([mail])[0]
        if err:
            raise MailError(f"SMTP delivery failed: {err}")  # never echo credentials

    def send_batch(self, mails: list[Mail]) -> list[Optional[str]]:
        """Send several messages over ONE connection (one TLS handshake and login, not one per message).
        Returns an error name per message, None when sent. A dropped connection is reopened once."""
        errors: list[Optional[str]] = []
        s = None
        for mail in mails:
            for attempt in (1, 2):
                if s is None:
                    try:
                        s = self._open()
                    except OSError as e:  # cannot connect or log in (SMTPException is an OSError): fail the rest
                        return errors + [type(e).__name__] * (len(mails) - len(errors))
                try:
                    s.send_message(mail.to_message(self.sender))
                    errors.append(None)
                    break
                except smtplib.SMTPServerDisconnected as e:  # connection lost: reconnect, retry once
                    _close(s)
                    s = None
                    if attempt == 2:
                        errors.append(type(e).__name__)
                except smtplib.SMTPException as e:  # this message refused (bad address…): keep the connection
                    errors.append(type(e).__name__)  # (SMTPException is an OSError, so it must come before it)
                    break
                except OSError as e:  # network error: reconnect, retry once
                    _close(s)
                    s = None
                    if attempt == 2:
                        errors.append(type(e).__name__)
        if s is not None:
            try:
                s.quit()
            except Exception:  # noqa: BLE001 — the messages are already accepted
                _close(s)
        return errors


def _close(s) -> None:
    try:
        s.close()
    except Exception:  # noqa: BLE001
        pass


def make_mailer(kind: Optional[str] = None, data_dir: Optional[str] = None) -> Optional[Mailer]:
    kind = (kind or os.environ.get("LASTMILE_MAIL", "file")).strip().lower()
    sender = os.environ.get("LASTMILE_MAIL_FROM", "LastMile Alert demo <alerts@lastmile.local>")
    if kind == "off":
        return None
    if kind == "file":
        return FileMailer(Path(data_dir) / "mail" if data_dir else None, sender=sender)
    if kind == "smtp":
        host = os.environ.get("LASTMILE_SMTP_HOST", "")
        if not host:
            raise ValueError("LASTMILE_MAIL=smtp needs LASTMILE_SMTP_HOST")
        return SmtpMailer(host, int(os.environ.get("LASTMILE_SMTP_PORT", "587")),
                          os.environ.get("LASTMILE_SMTP_USER", ""), os.environ.get("LASTMILE_SMTP_PASSWORD", ""),
                          os.environ.get("LASTMILE_SMTP_STARTTLS", "1").strip().lower() in {"1", "true", "yes", "on"},
                          sender=sender)
    raise ValueError(f"LASTMILE_MAIL must be file, smtp or off, got {kind!r}")
