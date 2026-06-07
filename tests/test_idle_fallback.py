from astrbot_plugin_telegram_mail.main import TelegramMailPlugin


def _plugin(config=None):
    plugin = TelegramMailPlugin.__new__(TelegramMailPlugin)
    plugin.config = config or {}
    plugin.folder_modes = {}
    plugin.resolved_folders = {}
    plugin.idle_disabled_accounts = set()
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


def test_disable_idle_on_error_records_account_until_restart():
    plugin = _plugin({"disable_idle_on_error": True})
    account = _account(plugin)
    plugin.resolved_folders[plugin._account_state_key(account)] = ["INBOX", "Alerts"]

    plugin._disable_idle_for_account(account, "read timeout")

    assert plugin._idle_disabled_for_account(account) is True
    assert plugin._account_mode(account) == "polling fallback"


def test_disable_idle_on_error_defaults_to_disabled():
    plugin = _plugin()
    account = _account(plugin)

    plugin._disable_idle_for_account(account, "read timeout")

    assert plugin._idle_disabled_for_account(account) is False
