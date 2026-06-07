import imaplib
import urllib.error
import urllib.parse

import pytest
from astrbot_plugin_telegram_mail.mail_client import MailClient, MailConnectionError
from astrbot_plugin_telegram_mail.models import MailAccount


def _account(**overrides):
    values = {
        "owner_id": "u1",
        "account_id": "outlook",
        "display_name": "Outlook",
        "enabled": True,
        "target_chat_id": "123",
        "platform_id": "telegram",
        "message_type": "friend",
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "imap_user": "user@outlook.com",
        "imap_password": "",
        "imap_auth_type": "oauth2",
        "imap_tls": True,
        "imap_folders": ["INBOX"],
        "imap_folder_mode": "configured",
        "smtp_host": "smtp-mail.outlook.com",
        "smtp_port": 587,
        "smtp_user": "user@outlook.com",
        "smtp_password": "",
        "smtp_auth_type": "oauth2",
        "smtp_tls": "starttls",
        "from_address": "user@outlook.com",
        "oauth2_access_token": "access-token",
        "oauth2_refresh_token": "",
        "oauth2_client_id": "",
        "oauth2_client_secret": "",
        "oauth2_token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "oauth2_device_code_url": "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode",
        "oauth2_scope": "scope",
        "oauth2_expires_at": 0.0,
        "archive_folder": "Archive",
        "trash_folder": "Trash",
        "poll_interval": 300,
        "realtime_enabled": True,
        "idle_timeout": 1740,
    }
    values.update(overrides)
    return MailAccount(**values)


def test_xoauth2_string_contains_user_and_bearer_token():
    assert (
        MailClient._xoauth2_string("user@outlook.com", "token")
        == b"user=user@outlook.com\x01auth=Bearer token\x01\x01"
    )


def test_smtp_oauth2_uses_auth_instead_of_login():
    calls = []

    class Client:
        def auth(self, mechanism, authobject):
            response = authobject()
            calls.append((mechanism, response, type(response)))

        def login(self, user, password):
            raise AssertionError("login should not be used for oauth2")

    MailClient()._smtp_login(Client(), _account())

    assert calls == [
        (
            "XOAUTH2",
            "user=user@outlook.com\x01auth=Bearer access-token\x01\x01",
            str,
        )
    ]


def test_smtp_password_login_still_supported():
    calls = []

    class Client:
        def login(self, user, password):
            calls.append((user, password))

    account = _account(
        imap_password="imap-secret",
        imap_auth_type="password",
        smtp_password="smtp-secret",
        smtp_auth_type="password",
    )

    MailClient()._smtp_login(Client(), account)

    assert calls == [("user@outlook.com", "smtp-secret")]


def test_oauth2_access_token_uses_loader_state():
    client = MailClient(
        oauth2_token_loader=lambda account: {
            "access_token": "stored-access",
            "refresh_token": "stored-refresh",
            "expires_at": 9999999999.0,
        }
    )

    assert (
        client._oauth2_access_token(_account(oauth2_access_token="")) == "stored-access"
    )


def test_oauth2_access_token_cache_is_scoped_by_owner_and_mailbox():
    client = MailClient(
        oauth2_token_loader=lambda account: {
            "access_token": f"stored-{account.owner_id}-{account.imap_user}",
            "refresh_token": "",
            "expires_at": 9999999999.0,
        }
    )

    first = _account(owner_id="u1", account_id="outlook", imap_user="a@example.com")
    second = _account(owner_id="u2", account_id="outlook", imap_user="b@example.com")

    assert client._oauth2_access_token(first) == "stored-u1-a@example.com"
    assert client._oauth2_access_token(second) == "stored-u2-b@example.com"
    assert len(client._oauth2_cache) == 2
    assert len(client._oauth2_refresh_locks) == 2


def test_oauth2_cache_invalidation_clears_only_matching_account():
    tokens = {
        ("u1", "a@example.com"): "old-a",
        ("u2", "b@example.com"): "old-b",
    }

    def loader(account):
        return {
            "access_token": tokens[(account.owner_id, account.imap_user)],
            "refresh_token": "",
            "expires_at": 9999999999.0,
        }

    client = MailClient(oauth2_token_loader=loader)
    first = _account(owner_id="u1", account_id="outlook", imap_user="a@example.com")
    second = _account(owner_id="u2", account_id="outlook", imap_user="b@example.com")
    assert client._oauth2_access_token(first) == "old-a"
    assert client._oauth2_access_token(second) == "old-b"

    tokens[("u1", "a@example.com")] = "new-a"
    tokens[("u2", "b@example.com")] = "new-b"
    client.invalidate_oauth2_cache(first)

    assert client._oauth2_access_token(first) == "new-a"
    assert client._oauth2_access_token(second) == "old-b"


def test_oauth2_refresh_updates_persistent_state(monkeypatch):
    updates = []
    timeouts = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self):
            return b'{"access_token":"new-access","refresh_token":"new-refresh","expires_in":3600}'

    def urlopen(request, timeout):
        timeouts.append(timeout)
        return Response()

    monkeypatch.setattr(
        "astrbot_plugin_telegram_mail.mail_client.urllib.request.urlopen",
        urlopen,
    )
    client = MailClient(
        oauth2_token_updater=lambda account, payload: updates.append(payload),
        network_timeout=7,
    )
    account = _account(
        oauth2_access_token="",
        oauth2_refresh_token="refresh-token",
        oauth2_client_id="client-id",
    )

    assert client._oauth2_access_token(account) == "new-access"
    assert updates[0]["refresh_token"] == "new-refresh"
    assert updates[0]["expires_at"] > 0
    assert timeouts == [7]


def test_oauth2_access_token_prefers_stored_refresh_token(monkeypatch):
    client = MailClient(
        oauth2_token_loader=lambda account: {
            "access_token": "",
            "refresh_token": "stored-refresh",
            "expires_at": 0,
        }
    )

    called = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self):
            return b'{"access_token":"new-access","expires_in":3600}'

    monkeypatch.setattr(
        "astrbot_plugin_telegram_mail.mail_client.urllib.request.urlopen",
        lambda request, timeout: Response(),
    )

    def updater(account, payload):
        called.append(payload)

    client._oauth2_token_updater = updater
    account = _account(
        oauth2_access_token="", oauth2_refresh_token="", oauth2_client_id="client-id"
    )

    assert client._oauth2_access_token(account) == "new-access"
    assert called[0]["access_token"] == "new-access"


def test_oauth2_refresh_does_not_send_client_secret_for_public_client(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self):
            return b'{"access_token":"new-access","expires_in":3600}'

    def urlopen(request, timeout):
        requests.append(request)
        return Response()

    monkeypatch.setattr(
        "astrbot_plugin_telegram_mail.mail_client.urllib.request.urlopen",
        urlopen,
    )

    account = _account(
        oauth2_access_token="",
        oauth2_refresh_token="refresh-token",
        oauth2_client_id="client-id",
        oauth2_client_secret="should-not-be-sent",
    )

    assert MailClient()._oauth2_access_token(account) == "new-access"
    body = urllib.parse.parse_qs(requests[0].data.decode("utf-8"))
    assert body["client_id"] == ["client-id"]
    assert "client_secret" not in body


def test_oauth2_refresh_error_includes_microsoft_description(monkeypatch):
    class ErrorResponse:
        def read(self):
            return b'{"error":"invalid_grant","error_description":"AADSTS700082: refresh token expired"}'

        def close(self):
            return None

    def raise_error(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            {},
            ErrorResponse(),
        )

    monkeypatch.setattr(
        "astrbot_plugin_telegram_mail.mail_client.urllib.request.urlopen",
        raise_error,
    )

    account = _account(
        oauth2_access_token="",
        oauth2_refresh_token="refresh-token",
        oauth2_client_id="client-id",
    )
    with pytest.raises(RuntimeError, match="AADSTS700082: refresh token expired"):
        MailClient()._oauth2_access_token(account)


def test_imap_connection_uses_configured_timeout(monkeypatch):
    calls = []

    class Client:
        def __init__(self, host, port, timeout):
            calls.append((host, port, timeout))

        def login(self, user, password):
            return "OK", []

        def close(self):
            return None

        def logout(self):
            return None

    monkeypatch.setattr(
        "astrbot_plugin_telegram_mail.mail_client.imaplib.IMAP4_SSL",
        Client,
    )
    account = _account(
        imap_auth_type="password",
        imap_password="secret",
        imap_tls=True,
    )

    with MailClient(network_timeout=9)._imap(account):
        pass

    assert calls == [("outlook.office365.com", 993, 9)]


def test_imap_oauth2_authenticate_failed_retries_with_fresh_token(monkeypatch):
    calls = []
    sleeps = []

    class Client:
        def __init__(self, host, port, timeout):
            pass

        def authenticate(self, mechanism, authobject):
            calls.append(authobject(None))
            if len(calls) == 1:
                raise imaplib.IMAP4.error("AUTHENTICATE failed")
            return "OK", []

        def close(self):
            return None

        def logout(self):
            return None

    monkeypatch.setattr(
        "astrbot_plugin_telegram_mail.mail_client.imaplib.IMAP4_SSL",
        Client,
    )
    monkeypatch.setattr(
        "astrbot_plugin_telegram_mail.mail_client.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )
    client = MailClient(
        oauth2_token_loader=lambda account: {
            "access_token": f"fresh-{len(calls)}",
            "refresh_token": "",
            "expires_at": 9999999999.0,
        }
    )

    with client._imap(_account(oauth2_access_token="")):
        pass

    assert calls == [
        b"user=user@outlook.com\x01auth=Bearer fresh-0\x01\x01",
        b"user=user@outlook.com\x01auth=Bearer fresh-1\x01\x01",
    ]
    assert sleeps == [2]


def test_imap_oauth2_repeated_not_connected_error_is_readable(monkeypatch):
    class Client:
        def __init__(self, host, port, timeout):
            pass

        def authenticate(self, mechanism, authobject):
            raise imaplib.IMAP4.error("User is authenticated but not connected.")

        def logout(self):
            return None

    monkeypatch.setattr(
        "astrbot_plugin_telegram_mail.mail_client.imaplib.IMAP4_SSL",
        Client,
    )
    monkeypatch.setattr(
        "astrbot_plugin_telegram_mail.mail_client.time.sleep",
        lambda seconds: None,
    )

    with pytest.raises(MailConnectionError) as exc_info:
        MailClient()._imap(_account())

    message = str(exc_info.value)
    assert "IMAP OAuth2 登录暂时失败" in message
    assert "/mail oauth outlook" in message
    assert "User is authenticated but not connected" in message
