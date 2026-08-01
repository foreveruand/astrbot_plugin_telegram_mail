import asyncio

from astrbot_plugin_telegram_mail.mail_client import MailClient
from astrbot_plugin_telegram_mail.main import TelegramMailPlugin


def _plugin(mail_client):
    plugin = TelegramMailPlugin.__new__(TelegramMailPlugin)
    plugin.config = {}
    plugin.mail_client = mail_client
    return plugin


def _account(plugin, **overrides):
    config = {
        "account_id": "a1",
        "provider": "outlook",
        "target_chat_id": "123",
        "imap_user": "user@example.com",
        "oauth2_client_id": "client-id",
        "imap_folders": ["INBOX"],
    }
    config.update(overrides)
    return plugin._parse_account(config, "u1")


def test_auto_folder_filter_keeps_custom_and_junk_folders():
    folders = TelegramMailPlugin._filter_auto_folders(
        [
            "INBOX",
            "验证码",
            "Sent",
            "Junk",
            "Trash",
            "Drafts",
            "Projects/Inbox",
            "[Gmail]/All Mail",
        ]
    )

    assert folders == ["INBOX", "验证码", "Junk", "Projects/Inbox"]


def test_auto_folder_resolution_uses_listed_receive_folders():
    class MailClient:
        def list_folders(self, account):
            return ["INBOX", "验证码", "Sent Items", "Junk Email"]

    plugin = _plugin(MailClient())
    account = _account(plugin)

    folders = asyncio.run(plugin._resolve_account_folders(account))

    assert folders == ["INBOX", "验证码", "Junk Email"]


def test_auto_folder_resolution_falls_back_to_configured_folders():
    class MailClient:
        def list_folders(self, account):
            raise RuntimeError("LIST failed")

    plugin = _plugin(MailClient())
    account = _account(plugin, imap_folders=["INBOX", "验证码"])

    folders = asyncio.run(plugin._resolve_account_folders(account))

    assert folders == ["INBOX", "验证码"]


def test_auto_folder_resolution_uses_cached_result():
    class MailClient:
        def __init__(self):
            self.calls = 0

        def list_folders(self, account):
            self.calls += 1
            return ["INBOX", "Junk Email"]

    mail_client = MailClient()
    plugin = _plugin(mail_client)
    account = _account(plugin)

    assert asyncio.run(plugin._resolve_account_folders(account)) == [
        "INBOX",
        "Junk Email",
    ]
    assert asyncio.run(plugin._resolve_account_folders(account)) == [
        "INBOX",
        "Junk Email",
    ]
    assert mail_client.calls == 1


def test_select_folder_quotes_mailbox_names_with_spaces():
    class ImapClient:
        def __init__(self):
            self.mailbox = ""

        def select(self, mailbox):
            self.mailbox = mailbox
            return "OK", []

    client = ImapClient()

    MailClient()._select_folder(client, "Sent Messages")

    assert client.mailbox == '"Sent Messages"'
