import asyncio

from astrbot_plugin_telegram_mail.main import TelegramMailPlugin
from astrbot_plugin_telegram_mail.storage import JsonStore


def _raw_message() -> bytes:
    return (
        "From: Sender <sender@example.com>\r\n"
        "To: User <user@example.com>\r\n"
        "Message-ID: <same-message@example.com>\r\n"
        "Subject: Same message\r\n"
        "\r\n"
        "Hello body\r\n"
    ).encode()


def test_same_message_in_multiple_folders_is_pushed_once(tmp_path):
    raw = _raw_message()

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
    account = plugin._parse_account(
        {
            "account_id": "a1",
            "target_chat_id": "123",
            "imap_host": "imap.example.com",
            "imap_user": "user@example.com",
            "imap_password": "secret",
            "imap_folders": ["INBOX", "Notes"],
        },
        "u1",
    )
    for folder in ("INBOX", "Notes"):
        plugin.store.set_initialized("u1", "a1", folder)

    pushes = []

    async def push_mail_card(current, parsed, current_raw):
        pushes.append((parsed.folder, parsed.uid))

    plugin._push_mail_card = push_mail_card

    try:
        count = asyncio.run(plugin._poll_account(account, push=True))
    finally:
        plugin.executor.shutdown(wait=False, cancel_futures=True)

    assert count == 1
    assert pushes == [("INBOX", "1")]


def test_duplicate_configured_folders_are_removed(tmp_path):
    plugin = TelegramMailPlugin.__new__(TelegramMailPlugin)
    plugin.config = {}
    plugin.store = JsonStore(tmp_path)
    plugin.store.load()

    account = plugin._parse_account(
        {
            "account_id": "a1",
            "target_chat_id": "123",
            "imap_host": "imap.example.com",
            "imap_user": "user@example.com",
            "imap_password": "secret",
            "imap_folders": ["INBOX", "inbox", "Notes", "Notes"],
        },
        "u1",
    )

    assert account.imap_folders == ["INBOX", "Notes"]
