from __future__ import annotations

import email.utils
import imaplib
import select
import smtplib
import ssl
import threading
import time
from collections.abc import Iterable
from email.message import EmailMessage

from .models import MailAccount


class IdleNotSupported(RuntimeError):
    pass


class MailClient:
    def list_uids(self, account: MailAccount, folder: str) -> list[str]:
        with self._imap(account) as client:
            self._select_folder(client, folder)
            status, data = client.uid("search", None, "ALL")
            if status != "OK" or not data:
                return []
            return [uid.decode("ascii", errors="ignore") for uid in data[0].split()]

    def fetch_message(self, account: MailAccount, folder: str, uid: str) -> bytes:
        with self._imap(account) as client:
            self._select_folder(client, folder)
            status, data = client.uid("fetch", uid, "(BODY.PEEK[])")
            if status != "OK":
                raise RuntimeError(f"Failed to fetch message UID {uid}: {status}")
            for item in data:
                if isinstance(item, tuple) and item[1]:
                    return item[1]
            raise RuntimeError(f"Message body not found for UID {uid}")

    def move_message(
        self,
        account: MailAccount,
        folder: str,
        uid: str,
        target_folder: str,
    ) -> None:
        with self._imap(account) as client:
            self._select_folder(client, folder)
            self._ensure_folder(client, target_folder)
            self._select_folder(client, folder)
            status, _ = client.uid("COPY", uid, target_folder)
            if status != "OK":
                raise RuntimeError(f"Failed to copy UID {uid} to {target_folder}")
            self._delete_selected_uid(client, uid)

    def delete_message(self, account: MailAccount, folder: str, uid: str) -> None:
        with self._imap(account) as client:
            self._select_folder(client, folder)
            if account.trash_folder:
                try:
                    self._ensure_folder(client, account.trash_folder)
                    status, _ = client.uid("COPY", uid, account.trash_folder)
                    if status == "OK":
                        self._delete_selected_uid(client, uid)
                        return
                except Exception:
                    pass
                self._select_folder(client, folder)
            self._delete_selected_uid(client, uid)

    def mark_seen(
        self, account: MailAccount, folder: str, uid: str, seen: bool
    ) -> None:
        with self._imap(account) as client:
            self._select_folder(client, folder)
            op = "+FLAGS" if seen else "-FLAGS"
            status, _ = client.uid("STORE", uid, op, r"(\Seen)")
            if status != "OK":
                raise RuntimeError(f"Failed to update seen flag for UID {uid}")

    def wait_for_new_mail(
        self,
        account: MailAccount,
        folder: str,
        timeout: int,
        check_interval: int = 60,
        stop_event: threading.Event | None = None,
    ) -> bool:
        with self._imap(account) as client:
            if not self._supports_idle(client):
                raise IdleNotSupported(
                    f"IMAP server does not support IDLE: {account.account_id}"
                )
            self._select_folder(client, folder)
            return self._idle_wait(
                client,
                max(timeout, 1),
                max(check_interval, 1),
                stop_event,
            )

    def send_mail(
        self,
        account: MailAccount,
        to_addrs: Iterable[str],
        subject: str,
        body: str,
        *,
        in_reply_to: str = "",
        references: str = "",
    ) -> None:
        sender = account.from_address or account.smtp_user or account.imap_user
        recipients = [addr.strip() for addr in to_addrs if addr.strip()]
        if not recipients:
            raise ValueError("No recipients provided")

        message = EmailMessage()
        message["From"] = sender
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message["Date"] = email.utils.formatdate(localtime=True)
        message["Message-ID"] = email.utils.make_msgid()
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
        if references:
            message["References"] = references
        message.set_content(body)

        if account.smtp_tls == "ssl":
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                account.smtp_host, account.smtp_port, context=context
            ) as client:
                self._smtp_login(client, account)
                client.send_message(message)
            return

        with smtplib.SMTP(account.smtp_host, account.smtp_port) as client:
            if account.smtp_tls == "starttls":
                client.starttls(context=ssl.create_default_context())
            self._smtp_login(client, account)
            client.send_message(message)

    def _imap(self, account: MailAccount):
        if account.imap_tls:
            client = imaplib.IMAP4_SSL(account.imap_host, account.imap_port)
        else:
            client = imaplib.IMAP4(account.imap_host, account.imap_port)
        client.login(account.imap_user, account.imap_password)
        return _ImapContext(client)

    @staticmethod
    def _select_folder(client: imaplib.IMAP4, folder: str) -> None:
        status, _ = client.select(folder)
        if status != "OK":
            raise RuntimeError(f"Failed to select IMAP folder: {folder}")

    @staticmethod
    def _ensure_folder(client: imaplib.IMAP4, folder: str) -> None:
        status, _ = client.status(folder, "(MESSAGES)")
        if status == "OK":
            return
        create_status, _ = client.create(folder)
        if create_status != "OK":
            raise RuntimeError(f"Failed to create IMAP folder: {folder}")

    @staticmethod
    def _delete_selected_uid(client: imaplib.IMAP4, uid: str) -> None:
        status, _ = client.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
        if status != "OK":
            raise RuntimeError(f"Failed to mark UID {uid} as deleted")
        client.expunge()

    @staticmethod
    def _smtp_login(client: smtplib.SMTP, account: MailAccount) -> None:
        user = account.smtp_user or account.imap_user
        password = account.smtp_password or account.imap_password
        if user:
            client.login(user, password)

    @staticmethod
    def _supports_idle(client: imaplib.IMAP4) -> bool:
        status, data = client.capability()
        if status != "OK" or not data:
            return False
        caps = b" ".join(part for part in data if isinstance(part, bytes)).upper()
        return b"IDLE" in caps.split()

    @staticmethod
    def _idle_wait(
        client: imaplib.IMAP4,
        timeout: int,
        check_interval: int = 60,
        stop_event: threading.Event | None = None,
    ) -> bool:
        sock = getattr(client, "sock", None)
        if sock is None:
            raise RuntimeError("IMAP connection does not expose a socket")

        tag = client._new_tag()
        old_timeout = sock.gettimeout()
        idle_started = False
        try:
            client.send(tag + b" IDLE\r\n")
            line = client._get_line()
            if not line.startswith(b"+"):
                raise RuntimeError(f"Failed to enter IMAP IDLE: {line!r}")
            idle_started = True

            deadline = time.monotonic() + timeout
            while True:
                if stop_event and stop_event.is_set():
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                ready, _, _ = select.select(
                    [sock], [], [], min(remaining, check_interval)
                )
                if not ready:
                    continue
                line = client._get_line()
                upper = line.upper()
                if b" EXISTS" in upper or b" RECENT" in upper:
                    return True
                if line.startswith(tag + b" "):
                    return False
        finally:
            if idle_started:
                try:
                    sock.settimeout(10)
                    client.send(b"DONE\r\n")
                    MailClient._drain_idle_done(client, tag)
                finally:
                    sock.settimeout(old_timeout)
            client.tagged_commands.pop(tag, None)

    @staticmethod
    def _drain_idle_done(client: imaplib.IMAP4, tag: bytes) -> None:
        tag_prefix = tag + b" "
        while True:
            line = client._get_line()
            if line.startswith(tag_prefix):
                upper = line.upper()
                if b" OK" not in upper:
                    raise RuntimeError(f"Failed to end IMAP IDLE: {line!r}")
                return


class _ImapContext:
    def __init__(self, client: imaplib.IMAP4) -> None:
        self.client = client

    def __enter__(self) -> imaplib.IMAP4:
        return self.client

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.client.close()
        except Exception:
            pass
        try:
            self.client.logout()
        except Exception:
            pass
