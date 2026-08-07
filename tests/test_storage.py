import json
import sqlite3

from astrbot_plugin_telegram_mail.storage import (
    DEFAULT_OWNER_ID,
    STATE_MIGRATION_MARKER,
    JsonStore,
)


def test_sqlite_state_round_trip(tmp_path):
    store = JsonStore(tmp_path, max_tokens=10)
    store.load()

    store.set_account_config("u1", "a1", {"imap_user": "u1@example.com"})
    store.set_seen("u1", "a1", "INBOX", {"2", "10"})
    store.add_seen("u1", "a1", "INBOX", "11")
    assert store.claim_delivery("u1", "a1", "<message-1@example.com>")
    assert not store.claim_delivery("u1", "a1", "<message-1@example.com>")
    store.set_initialized("u1", "a1", "INBOX")
    store.set_oauth2_state(
        "u1",
        "a1",
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_at": 123.0,
        },
    )
    store.block_sender("u1", "a1", "sender@example.com")
    store.block_sender("u1", "a1", "example.org")
    store.set_last_error("u1", "a1", "x" * 600)
    store.set_last_check("u1", "a1", "2026-06-08 10:00:00")
    token = store.put_token("u1", {"account_id": "a1", "uid": "42"})
    store.save()

    reloaded = JsonStore(tmp_path, max_tokens=10)
    reloaded.load()

    assert (tmp_path / "mail_state.db").exists()
    assert reloaded.account_configs("u1") == [
        {"account_id": "a1", "imap_user": "u1@example.com"}
    ]
    assert reloaded.get_seen("u1", "a1", "INBOX") == {"2", "10", "11"}
    assert reloaded.is_initialized("u1", "a1", "INBOX")
    assert not reloaded.is_initialized("u2", "a1", "INBOX")
    assert reloaded.get_oauth2_state("u1", "a1") == {
        "access_token": "access",
        "refresh_token": "refresh",
        "expires_at": 123.0,
    }
    assert reloaded.is_blocked("u1", "a1", "sender@example.com")
    assert reloaded.is_blocked("u1", "a1", "news@example.org")
    assert not reloaded.is_blocked("u2", "a1", "sender@example.com")
    assert reloaded.last_error("u1", "a1") == "x" * 500
    assert reloaded.last_check("u1", "a1") == "2026-06-08 10:00:00"
    assert reloaded.get_token("u1", token)["uid"] == "42"


def test_migrates_users_state_json_to_sqlite(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "users": {
                    "u1": {
                        "accounts": {
                            "a1": {
                                "account_id": "a1",
                                "imap_user": "u1@example.com",
                            }
                        },
                        "seen": {"a1": {"INBOX": ["1", "2"]}},
                        "initialized": {"a1": {"INBOX": True}},
                        "tokens": {
                            "tok1": {
                                "created_at": 1.0,
                                "account_id": "a1",
                                "uid": "2",
                            }
                        },
                        "oauth2": {
                            "a1": {
                                "access_token": "access",
                                "refresh_token": "refresh",
                            }
                        },
                        "blocked": {"a1": ["sender@example.com"]},
                        "last_errors": {"a1": "error"},
                        "last_checks": {"a1": "2026-06-08 10:00:00"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    store = JsonStore(tmp_path)
    store.load()

    assert store.account_configs("u1") == [
        {"account_id": "a1", "imap_user": "u1@example.com"}
    ]
    assert store.get_seen("u1", "a1", "INBOX") == {"1", "2"}
    assert store.is_initialized("u1", "a1", "INBOX")
    assert store.get_token("u1", "tok1")["uid"] == "2"
    assert store.get_oauth2_state("u1", "a1")["refresh_token"] == "refresh"
    assert store.blocked_senders("u1", "a1") == ["sender@example.com"]
    assert store.last_error("u1", "a1") == "error"
    assert store.last_check("u1", "a1") == "2026-06-08 10:00:00"

    migrated_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert migrated_state[STATE_MIGRATION_MARKER]["completed"] is True


def test_migrates_legacy_top_level_state_json_to_default_owner(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "seen": {"a1": {"INBOX": ["1"]}},
                "initialized": {"a1": {"INBOX": True}},
                "tokens": {"tok1": {"created_at": 1.0, "uid": "1"}},
                "oauth2": {"a1": {"access_token": "access"}},
                "blocked": {"a1": ["example.org"]},
                "last_errors": {"a1": "error"},
                "last_checks": {"a1": "2026-06-08 10:00:00"},
            }
        ),
        encoding="utf-8",
    )

    store = JsonStore(tmp_path)
    store.load()

    assert store.get_seen(DEFAULT_OWNER_ID, "a1", "INBOX") == {"1"}
    assert store.is_initialized(DEFAULT_OWNER_ID, "a1", "INBOX")
    assert store.get_token(DEFAULT_OWNER_ID, "tok1")["uid"] == "1"
    assert store.get_oauth2_state(DEFAULT_OWNER_ID, "a1") == {"access_token": "access"}
    assert store.is_blocked(DEFAULT_OWNER_ID, "a1", "news@example.org")
    assert store.last_error(DEFAULT_OWNER_ID, "a1") == "error"
    assert store.last_check(DEFAULT_OWNER_ID, "a1") == "2026-06-08 10:00:00"


def test_state_json_migration_is_not_repeated_after_marker(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "users": {
                    "u1": {
                        "accounts": {
                            "a1": {
                                "account_id": "a1",
                                "imap_user": "u1@example.com",
                            }
                        },
                        "tokens": {"tok1": {"created_at": 1.0, "uid": "1"}},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    store = JsonStore(tmp_path)
    store.load()
    store.close()

    reloaded = JsonStore(tmp_path)
    reloaded.load()

    with sqlite3.connect(tmp_path / "mail_state.db") as conn:
        account_count = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        token_count = conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]

    assert account_count == 1
    assert token_count == 1
    assert reloaded.account_configs("u1") == [
        {"account_id": "a1", "imap_user": "u1@example.com"}
    ]


def test_state_json_migration_preserves_existing_database_conflicts(tmp_path):
    store = JsonStore(tmp_path)
    store.load()
    store.set_account_config("u1", "a1", {"imap_user": "db@example.com"})
    store.set_oauth2_state("u1", "a1", {"access_token": "db-access"})
    store.set_last_error("u1", "a1", "db-error")
    store.set_last_check("u1", "a1", "db-check")
    store.close()

    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "users": {
                    "u1": {
                        "accounts": {
                            "a1": {
                                "account_id": "a1",
                                "imap_user": "json@example.com",
                            }
                        },
                        "seen": {"a1": {"INBOX": ["1"]}},
                        "oauth2": {"a1": {"access_token": "json-access"}},
                        "last_errors": {"a1": "json-error"},
                        "last_checks": {"a1": "json-check"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    reloaded = JsonStore(tmp_path)
    reloaded.load()

    assert reloaded.account_configs("u1") == [
        {"account_id": "a1", "imap_user": "db@example.com"}
    ]
    assert reloaded.get_oauth2_state("u1", "a1") == {"access_token": "db-access"}
    assert reloaded.last_error("u1", "a1") == "db-error"
    assert reloaded.last_check("u1", "a1") == "db-check"
    assert reloaded.get_seen("u1", "a1", "INBOX") == {"1"}
