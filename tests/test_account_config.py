from astrbot_plugin_telegram_mail.main import (
    DEFAULT_IDLE_TIMEOUT,
    TelegramMailPlugin,
)


def _plugin(config=None):
    plugin = TelegramMailPlugin.__new__(TelegramMailPlugin)
    plugin.config = config or {}
    return plugin


def _account_config(**overrides):
    config = {
        "account_id": "a1",
        "target_chat_id": "123",
        "imap_host": "imap.example.com",
        "imap_user": "user@example.com",
        "imap_password": "secret",
    }
    config.update(overrides)
    return config


def test_realtime_defaults_to_enabled():
    account = _plugin()._parse_account(_account_config())

    assert account.realtime_enabled is True
    assert account.idle_timeout == DEFAULT_IDLE_TIMEOUT


def test_realtime_can_be_disabled_per_account():
    account = _plugin()._parse_account(_account_config(realtime_enabled=False))

    assert account.realtime_enabled is False


def test_idle_timeout_can_be_overridden_per_account():
    account = _plugin({"idle_timeout": 1200})._parse_account(
        _account_config(idle_timeout=600)
    )

    assert account.idle_timeout == 600


def test_realtime_uses_global_default_when_account_omits_value():
    account = _plugin({"realtime_enabled": False, "idle_timeout": 900})._parse_account(
        _account_config()
    )

    assert account.realtime_enabled is False
    assert account.idle_timeout == 900
