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
    account = _plugin()._parse_account(_account_config(), "u1")

    assert account.owner_id == "u1"
    assert account.realtime_enabled is True
    assert account.idle_timeout == DEFAULT_IDLE_TIMEOUT


def test_realtime_can_be_disabled_per_account():
    account = _plugin()._parse_account(_account_config(realtime_enabled=False), "u1")

    assert account.realtime_enabled is False


def test_idle_timeout_can_be_overridden_per_account():
    account = _plugin({"idle_timeout": 1200})._parse_account(
        _account_config(idle_timeout=600),
        "u1",
    )

    assert account.idle_timeout == 600


def test_realtime_uses_global_default_when_account_omits_value():
    account = _plugin({"realtime_enabled": False, "idle_timeout": 900})._parse_account(
        _account_config(),
        "u1",
    )

    assert account.realtime_enabled is False
    assert account.idle_timeout == 900


def test_outlook_provider_uses_oauth2_defaults():
    account = _plugin()._parse_account(
        _account_config(
            provider="outlook",
            imap_host="",
            imap_password="",
            oauth2_access_token="token",
        ),
        "u1",
    )

    assert account.imap_host == "outlook.office365.com"
    assert account.smtp_host == "smtp-mail.outlook.com"
    assert account.smtp_port == 587
    assert account.smtp_tls == "starttls"
    assert account.imap_auth_type == "oauth2"
    assert account.smtp_auth_type == "oauth2"


def test_outlook_provider_uses_plugin_oauth_defaults():
    account = _plugin(
        {
            "oauth2_client_id": "plugin-client-id",
            "oauth2_client_secret": "plugin-client-secret",
        }
    )._parse_account(
        _account_config(
            provider="outlook",
            imap_host="",
            imap_password="",
        ),
        "u1",
    )

    assert account.oauth2_client_id == "plugin-client-id"
    assert account.oauth2_client_secret == "plugin-client-secret"


def test_oauth2_account_accepts_refresh_token_without_password():
    account = _plugin()._parse_account(
        _account_config(
            auth_type="oauth2",
            imap_password="",
            oauth2_refresh_token="refresh",
            oauth2_client_id="client-id",
        ),
        "u1",
    )

    assert account.imap_auth_type == "oauth2"
    assert account.oauth2_refresh_token == "refresh"


def test_oauth2_account_accepts_client_id_before_authorization():
    account = _plugin()._parse_account(
        _account_config(
            provider="outlook",
            imap_host="",
            imap_password="",
            oauth2_client_id="client-id",
        ),
        "u1",
    )

    assert account.oauth2_access_token == ""
    assert account.oauth2_refresh_token == ""
    assert account.oauth2_client_id == "client-id"


def test_oauth2_account_falls_back_to_plugin_client_values():
    account = _plugin(
        {
            "oauth2_client_id": "plugin-client-id",
            "oauth2_client_secret": "plugin-client-secret",
        }
    )._parse_account(
        _account_config(
            provider="outlook",
            imap_host="",
            imap_password="",
        ),
        "u1",
    )

    assert account.oauth2_client_id == "plugin-client-id"
    assert account.oauth2_client_secret == "plugin-client-secret"


def test_oauth2_account_reads_saved_token_state():
    class Store:
        def get_oauth2_state(self, owner_id, account_id):
            assert owner_id == "u1"
            assert account_id == "a1"
            return {
                "access_token": "stored-access",
                "refresh_token": "stored-refresh",
                "expires_at": 123.0,
            }

    plugin = _plugin()
    plugin.store = Store()
    account = plugin._parse_account(
        _account_config(
            auth_type="oauth2",
            imap_password="",
            oauth2_client_id="client-id",
        ),
        "u1",
    )

    assert account.oauth2_access_token == "stored-access"
    assert account.oauth2_refresh_token == "stored-refresh"
    assert account.oauth2_expires_at == 123.0


def test_oauth2_token_save_keeps_current_stored_refresh_token():
    states = {("u1", "a1"): {"refresh_token": "stored-refresh"}}

    class Store:
        def get_oauth2_state(self, owner_id, account_id):
            return states.get((owner_id, account_id), {})

        def set_oauth2_state(self, owner_id, account_id, payload):
            states[(owner_id, account_id)] = payload

        def save(self):
            return None

    plugin = _plugin()
    plugin.store = Store()
    account = plugin._parse_account(
        _account_config(
            auth_type="oauth2",
            imap_password="",
            oauth2_client_id="client-id",
            oauth2_refresh_token="old-config-refresh",
        ),
        "u1",
    )

    plugin._save_oauth2_token_response(
        account,
        {"access_token": "new-access", "expires_in": 3600},
    )

    assert states[("u1", "a1")]["refresh_token"] == "stored-refresh"
