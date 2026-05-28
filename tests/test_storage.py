from astrbot_plugin_telegram_mail.storage import JsonStore


def test_block_sender_matches_email_and_domain(tmp_path):
    store = JsonStore(tmp_path)
    store.load()
    store.block_sender("a1", "sender@example.com")
    store.block_sender("a1", "example.org")

    assert store.is_blocked("a1", "sender@example.com")
    assert store.is_blocked("a1", "news@example.org")
    assert not store.is_blocked("a1", "other@example.net")


def test_initialized_state_round_trip(tmp_path):
    store = JsonStore(tmp_path)
    store.load()

    assert not store.is_initialized("a1", "INBOX")
    store.set_initialized("a1", "INBOX")
    store.save()

    reloaded = JsonStore(tmp_path)
    reloaded.load()
    assert reloaded.is_initialized("a1", "INBOX")

