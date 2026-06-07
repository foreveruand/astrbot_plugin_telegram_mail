import asyncio
import threading
import time

from astrbot_plugin_telegram_mail.main import TelegramMailPlugin
from astrbot_plugin_telegram_mail.storage import JsonStore


def _plugin(tmp_path, mail_client):
    plugin = TelegramMailPlugin.__new__(TelegramMailPlugin)
    plugin.config = {"max_workers": 2}
    plugin.mail_client = mail_client
    plugin.store = JsonStore(tmp_path)
    plugin.store.load()
    return plugin


def _account(plugin):
    return plugin._parse_account(
        {
            "account_id": "outlook",
            "provider": "outlook",
            "target_chat_id": "123",
            "imap_user": "user@example.com",
            "oauth2_client_id": "client-id",
            "imap_folders": ["INBOX"],
        },
        "u1",
    )


def test_oauth2_account_poll_folder_io_is_serialized(tmp_path):
    class MailClient:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def list_uids(self, account, folder):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.05)
                return ["1"]
            finally:
                with self.lock:
                    self.active -= 1

        def fetch_message(self, account, folder, uid):
            raise AssertionError("first poll should initialize seen state only")

    mail_client = MailClient()
    plugin = _plugin(tmp_path, mail_client)
    account = _account(plugin)

    async def run():
        await asyncio.gather(
            plugin._poll_folder(account, "INBOX", push=True),
            plugin._poll_folder(account, "INBOX", push=True),
        )

    try:
        asyncio.run(run())
    finally:
        plugin.executor.shutdown(wait=False, cancel_futures=True)

    assert mail_client.max_active == 1


def test_oauth2_account_poll_account_folders_are_serialized(tmp_path):
    class MailClient:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.calls = []
            self.lock = threading.Lock()

        def list_uids(self, account, folder):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                self.calls.append(("list", folder))
            try:
                time.sleep(0.02)
                return ["1"]
            finally:
                with self.lock:
                    self.active -= 1

        def fetch_message(self, account, folder, uid):
            raise AssertionError("first poll should initialize seen state only")

    mail_client = MailClient()
    plugin = _plugin(tmp_path, mail_client)
    account = plugin._parse_account(
        {
            "account_id": "outlook",
            "provider": "outlook",
            "target_chat_id": "123",
            "imap_user": "user@example.com",
            "oauth2_client_id": "client-id",
            "imap_folders": ["INBOX", "Alerts"],
            "imap_folder_mode": "configured",
        },
        "u1",
    )

    try:
        count = asyncio.run(plugin._poll_account(account, push=True))
    finally:
        plugin.executor.shutdown(wait=False, cancel_futures=True)

    assert count == 0
    assert mail_client.max_active == 1
    assert mail_client.calls == [("list", "INBOX"), ("list", "Alerts")]


def test_oauth2_manual_check_waits_for_background_poll_lock(tmp_path):
    class MailClient:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def list_uids(self, account, folder):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.05)
                return ["1"]
            finally:
                with self.lock:
                    self.active -= 1

        def fetch_message(self, account, folder, uid):
            raise AssertionError("first poll should initialize seen state only")

    mail_client = MailClient()
    plugin = _plugin(tmp_path, mail_client)
    account = _account(plugin)

    async def run():
        await asyncio.gather(
            plugin._poll_account(account, push=True),
            plugin._poll_account(account, push=True),
        )

    try:
        asyncio.run(run())
    finally:
        plugin.executor.shutdown(wait=False, cancel_futures=True)

    assert mail_client.max_active == 1


def test_password_account_poll_folder_keeps_existing_concurrency(tmp_path):
    class MailClient:
        def list_uids(self, account, folder):
            return []

    plugin = _plugin(tmp_path, MailClient())
    account = plugin._parse_account(
        {
            "account_id": "imap",
            "target_chat_id": "123",
            "imap_host": "imap.example.com",
            "imap_user": "user@example.com",
            "imap_password": "secret",
        },
        "u1",
    )

    assert plugin._serializes_account_mail_io(account) is False
