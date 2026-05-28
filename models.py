from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class MailAccount:
    account_id: str
    display_name: str
    enabled: bool
    target_chat_id: str
    platform_id: str
    message_type: str
    imap_host: str
    imap_port: int
    imap_user: str
    imap_password: str
    imap_tls: bool
    imap_folders: list[str]
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_tls: str
    from_address: str
    archive_folder: str
    trash_folder: str
    poll_interval: int


@dataclass(slots=True)
class MailAttachment:
    index: int
    filename: str
    content_type: str
    size: int
    content_id: str = ""


@dataclass(slots=True)
class ParsedMail:
    account_id: str
    folder: str
    uid: str
    message_id: str
    subject: str
    sender: str
    sender_email: str
    recipients: list[str]
    date: str
    body_text: str
    body_html: str
    attachments: list[MailAttachment] = field(default_factory=list)
    unsubscribe_urls: list[str] = field(default_factory=list)
    unsubscribe_mailtos: list[str] = field(default_factory=list)
    received_at: datetime = field(default_factory=datetime.now)
