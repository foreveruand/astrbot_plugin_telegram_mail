import urllib.error
import urllib.parse

import pytest
from astrbot_plugin_telegram_mail.mail_client import MailClient
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
        == "user=user@outlook.com\x01auth=Bearer token\x01\x01"
    )


def test_smtp_oauth2_uses_auth_instead_of_login():
    calls = []

    class Client:
        def auth(self, mechanism, authobject):
            calls.append((mechanism, authobject(None)))

        def login(self, user, password):
            raise AssertionError("login should not be used for oauth2")

    MailClient()._smtp_login(Client(), _account())

    assert calls == [
        (
            "XOAUTH2",
            "user=user@outlook.com\x01auth=Bearer access-token\x01\x01",
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


def test_oauth2_refresh_updates_persistent_state(monkeypatch):
    updates = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self):
            return b'{"access_token":"new-access","refresh_token":"new-refresh","expires_in":3600}'

    monkeypatch.setattr(
        "astrbot_plugin_telegram_mail.mail_client.urllib.request.urlopen",
        lambda request, timeout: Response(),
    )
    client = MailClient(
        oauth2_token_updater=lambda account, payload: updates.append(payload)
    )
    account = _account(
        oauth2_access_token="",
        oauth2_refresh_token="refresh-token",
        oauth2_client_id="client-id",
    )

    assert client._oauth2_access_token(account) == "new-access"
    assert updates[0]["refresh_token"] == "new-refresh"
    assert updates[0]["expires_at"] > 0


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
