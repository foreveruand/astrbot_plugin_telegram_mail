from astrbot_plugin_telegram_mail.storage import JsonStore


def test_block_sender_matches_email_and_domain(tmp_path):
    store = JsonStore(tmp_path)
    store.load()
    store.block_sender("u1", "a1", "sender@example.com")
    store.block_sender("u1", "a1", "example.org")

    assert store.is_blocked("u1", "a1", "sender@example.com")
    assert store.is_blocked("u1", "a1", "news@example.org")
    assert not store.is_blocked("u1", "a1", "other@example.net")
    assert not store.is_blocked("u2", "a1", "sender@example.com")


def test_initialized_state_round_trip(tmp_path):
    store = JsonStore(tmp_path)
    store.load()

    assert not store.is_initialized("u1", "a1", "INBOX")
    store.set_initialized("u1", "a1", "INBOX")
    store.save()

    reloaded = JsonStore(tmp_path)
    reloaded.load()
    assert reloaded.is_initialized("u1", "a1", "INBOX")
    assert not reloaded.is_initialized("u2", "a1", "INBOX")


def test_oauth2_state_round_trip(tmp_path):
    store = JsonStore(tmp_path)
    store.load()
    store.set_oauth2_state(
        "u1",
        "outlook",
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_at": 123.0,
        },
    )
    store.save()

    reloaded = JsonStore(tmp_path)
    reloaded.load()

    assert reloaded.get_oauth2_state("u1", "outlook") == {
        "access_token": "access",
        "refresh_token": "refresh",
        "expires_at": 123.0,
    }
    assert reloaded.get_oauth2_state("u2", "outlook") == {}


def test_account_configs_are_user_scoped(tmp_path):
    store = JsonStore(tmp_path)
    store.load()

    store.set_account_config("u1", "a1", {"imap_user": "u1@example.com"})
    store.set_account_config("u2", "a1", {"imap_user": "u2@example.com"})
    store.save()

    reloaded = JsonStore(tmp_path)
    reloaded.load()

    assert reloaded.account_configs("u1") == [
        {"account_id": "a1", "imap_user": "u1@example.com"}
    ]
    assert reloaded.account_configs("u2") == [
        {"account_id": "a1", "imap_user": "u2@example.com"}
    ]
