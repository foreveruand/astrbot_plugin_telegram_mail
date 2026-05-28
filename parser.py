from __future__ import annotations

import html
import re
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from .models import MailAttachment, ParsedMail

HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
UNSUBSCRIBE_ANCHOR_RE = re.compile(
    r"<a\b[^>]+href=[\"']([^\"']+)[\"'][^>]*>[^<]*(?:unsubscribe|退订|取消订阅)[^<]*</a>",
    re.IGNORECASE,
)


def parse_message(raw: bytes, *, account_id: str, folder: str, uid: str) -> ParsedMail:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    subject = decode_mime_header(message.get("Subject", "")) or "(No subject)"
    sender = decode_mime_header(message.get("From", "")) or "(Unknown sender)"
    _, sender_email = parseaddr(sender)
    recipients = [addr for _, addr in getaddresses(message.get_all("To", []))]
    date = _format_date(message.get("Date", ""))
    body_text, body_html = _extract_bodies(message)
    attachments = _extract_attachments(message)
    unsubscribe_urls, unsubscribe_mailtos = _extract_unsubscribe(message, body_html)

    return ParsedMail(
        account_id=account_id,
        folder=folder,
        uid=uid,
        message_id=str(message.get("Message-ID", "")),
        subject=subject,
        sender=sender,
        sender_email=sender_email.lower(),
        recipients=recipients,
        date=date,
        body_text=body_text or html_to_text(body_html),
        body_html=body_html,
        attachments=attachments,
        unsubscribe_urls=unsubscribe_urls,
        unsubscribe_mailtos=unsubscribe_mailtos,
    )


def decode_mime_header(value: str) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return value.strip()


def html_to_text(value: str) -> str:
    if not value:
        return ""
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</p\s*>", "\n\n", value)
    value = HTML_TAG_RE.sub(" ", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = [WHITESPACE_RE.sub(" ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def extract_attachment_payload(raw: bytes, index: int) -> tuple[str, bytes, str]:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    for current, attachment in enumerate(message.iter_attachments()):
        filename = decode_mime_header(
            attachment.get_filename() or f"attachment-{index}"
        )
        if not filename:
            filename = f"attachment-{index}"
        if current == index:
            payload = attachment.get_payload(decode=True) or b""
            return filename, payload, attachment.get_content_type()
    raise IndexError(f"Attachment not found: {index}")


def _format_date(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
        return parsed.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return decode_mime_header(value)


def _extract_bodies(message: EmailMessage | Message) -> tuple[str, str]:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        disposition = (part.get_content_disposition() or "").lower()
        if disposition == "attachment":
            continue
        content_type = part.get_content_type()
        content = _get_part_text(part)
        if not content:
            continue
        if content_type == "text/plain":
            plain_parts.append(content)
        elif content_type == "text/html":
            html_parts.append(content)
    return "\n\n".join(plain_parts).strip(), "\n\n".join(html_parts).strip()


def _get_part_text(part: Message) -> str:
    try:
        payload = part.get_content()
        return str(payload).strip()
    except Exception:
        payload = part.get_payload(decode=True)
        if not payload:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace").strip()


def _extract_attachments(message: EmailMessage | Message) -> list[MailAttachment]:
    attachments: list[MailAttachment] = []
    for index, part in enumerate(message.iter_attachments()):
        payload = part.get_payload(decode=True) or b""
        filename = decode_mime_header(part.get_filename() or f"attachment-{index + 1}")
        attachments.append(
            MailAttachment(
                index=index,
                filename=filename,
                content_type=part.get_content_type(),
                size=len(payload),
                content_id=str(part.get("Content-ID", "")).strip("<>"),
            )
        )
    return attachments


def _extract_unsubscribe(
    message: Message, body_html: str
) -> tuple[list[str], list[str]]:
    urls: list[str] = []
    mailtos: list[str] = []
    for header in message.get_all("List-Unsubscribe", []):
        for item in re.findall(r"<([^>]+)>", header):
            _append_unsubscribe_target(item, urls, mailtos)
        for item in header.split(","):
            _append_unsubscribe_target(item.strip(), urls, mailtos)

    for url in UNSUBSCRIBE_ANCHOR_RE.findall(body_html or ""):
        _append_unsubscribe_target(html.unescape(url), urls, mailtos)

    return _dedupe(urls)[:5], _dedupe(mailtos)[:5]


def _append_unsubscribe_target(value: str, urls: list[str], mailtos: list[str]) -> None:
    value = value.strip().strip("<>")
    if not value:
        return
    if value.lower().startswith("mailto:"):
        mailtos.append(value)
        return
    if value.lower().startswith(("http://", "https://")):
        urls.append(value)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
