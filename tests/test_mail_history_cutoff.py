import asyncio

from astrbot_plugin_telegram_mail.main import TelegramMailPlugin
from astrbot_plugin_telegram_mail.storage import JsonStore


def _raw_message(date: str) -> bytes:
    return (
        "From: Sender <sender@example.com>\r\n"
        "To: User <user@example.com>\r\n"
        "Subject: Test mail\r\n"
        f"Date: {date}\r\n"
        "\r\n"
        "Hello body\r\n"
    ).encode()


def _plugin(tmp_path, raw: bytes):
    class MailClient:
        def list_uids(self, account, folder):
            return ["1"]

        def fetch_message(self, account, folder, uid):
            return raw

    plugin = TelegramMailPlugin.__new__(TelegramMailPlugin)
    plugin.config = {}
    plugin.mail_client = MailClient()
    plugin.store = JsonStore(tmp_path)
    plugin.store.load()
    return plugin


def _account(plugin):
    return plugin._parse_account(
        {
            "account_id": "a1",
            "target_chat_id": "123",
            "imap_host": "imap.example.com",
            "imap_user": "user@example.com",
            "imap_password": "secret",
        },
        "u1",
    )


def test_poll_folder_skips_historical_mail_by_date(tmp_path):
    plugin = _plugin(tmp_path, _raw_message("Tue, 01 Jan 2019 10:00:00 +0800"))
    account = _account(plugin)
    pushes = []

    async def push_mail_card(account, parsed, raw):
        pushes.append(parsed.uid)

    plugin._push_mail_card = push_mail_card
    plugin.store.set_initialized("u1", "a1", "INBOX")
    plugin.store.set_last_check("u1", "a1", "2026-06-02 12:00:00")

    count = asyncio.run(plugin._poll_folder(account, "INBOX", push=True))

    assert count == 0
    assert pushes == []
    assert plugin.store.get_seen("u1", "a1", "INBOX") == {"1"}


def test_poll_folder_allows_recent_mail_within_date_grace(tmp_path):
    plugin = _plugin(tmp_path, _raw_message("Tue, 02 Jun 2026 11:30:00 +0800"))
    account = _account(plugin)
    pushes = []

    async def push_mail_card(account, parsed, raw):
        pushes.append(parsed.uid)

    plugin._push_mail_card = push_mail_card
    plugin.store.set_initialized("u1", "a1", "INBOX")
    plugin.store.set_last_check("u1", "a1", "2026-06-02 12:00:00")

    count = asyncio.run(plugin._poll_folder(account, "INBOX", push=True))

    assert count == 1
    assert pushes == ["1"]
