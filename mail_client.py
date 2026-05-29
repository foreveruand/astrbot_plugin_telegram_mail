from __future__ import annotations

import email.utils
import imaplib
import json
import select
import smtplib
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from email.message import EmailMessage

from .models import MailAccount


class IdleNotSupported(RuntimeError):
    pass


class MailClient:
    def __init__(
        self,
        oauth2_token_updater: Callable[[MailAccount, dict], None] | None = None,
        oauth2_token_loader: Callable[[MailAccount], dict] | None = None,
    ) -> None:
        self._oauth2_cache: dict[str, tuple[str, float]] = {}
        self._oauth2_token_updater = oauth2_token_updater
        self._oauth2_token_loader = oauth2_token_loader

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
        if account.imap_auth_type == "oauth2":
            client.authenticate(
                "XOAUTH2",
                lambda _: self._xoauth2_string(
                    account.imap_user,
                    self._oauth2_access_token(account),
                ),
            )
        else:
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

    def _smtp_login(self, client: smtplib.SMTP, account: MailAccount) -> None:
        user = account.smtp_user or account.imap_user
        if not user:
            return
        if account.smtp_auth_type == "oauth2":
            client.auth(
                "XOAUTH2",
                lambda _: self._xoauth2_string(
                    user,
                    self._oauth2_access_token(account),
                ),
            )
            return
        password = account.smtp_password or account.imap_password
        client.login(user, password)

    @staticmethod
    def _xoauth2_string(user: str, access_token: str) -> str:
        return f"user={user}\x01auth=Bearer {access_token}\x01\x01"

    def _oauth2_access_token(self, account: MailAccount) -> str:
        cache_key = account.account_id
        now = time.time()
        cached = self._oauth2_cache.get(cache_key)
        if cached and cached[1] > time.time() + 60:
            return cached[0]

        stored = self._oauth2_token_loader(account) if self._oauth2_token_loader else {}
        access_token = str(
            stored.get("access_token") or account.oauth2_access_token or ""
        )
        refresh_token = str(
            stored.get("refresh_token") or account.oauth2_refresh_token or ""
        )
        expires_at = float(stored.get("expires_at") or account.oauth2_expires_at or 0)

        if access_token and expires_at > now + 60:
            self._oauth2_cache[cache_key] = (access_token, expires_at)
            return access_token
        if access_token and not refresh_token:
            return access_token
        if not refresh_token:
            raise RuntimeError(
                f"OAuth2 account {account.account_id} is not authorized; run /mail oauth {account.account_id}"
            )
        if not account.oauth2_client_id:
            raise RuntimeError(
                f"OAuth2 account {account.account_id} is missing oauth2_client_id"
            )

        form = {
            "client_id": account.oauth2_client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        if account.oauth2_client_secret:
            form["client_secret"] = account.oauth2_client_secret
        if account.oauth2_scope:
            form["scope"] = account.oauth2_scope

        data = urllib.parse.urlencode(form).encode("utf-8")
        request = urllib.request.Request(
            account.oauth2_token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                error_payload = json.loads(body)
            except json.JSONDecodeError:
                raise RuntimeError(
                    f"OAuth2 token refresh failed for {account.account_id}: HTTP {exc.code}"
                ) from exc
            error = str(error_payload.get("error") or "")
            description = str(error_payload.get("error_description") or error)
            raise RuntimeError(
                f"OAuth2 token refresh failed for {account.account_id}: "
                f"HTTP {exc.code} {description}"
            ) from exc
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise RuntimeError(
                f"OAuth2 token response for {account.account_id} has no access_token"
            )
        expires_in = int(payload.get("expires_in") or 3600)
        expires_at = time.time() + expires_in
        self._oauth2_cache[cache_key] = (access_token, expires_at)
        if self._oauth2_token_updater:
            self._oauth2_token_updater(account, {**payload, "expires_at": expires_at})
        return access_token

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
