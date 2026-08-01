import asyncio

from astrbot_plugin_telegram_mail.mail_client import MailConnectionError
from astrbot_plugin_telegram_mail.main import TelegramMailPlugin
from astrbot_plugin_telegram_mail.storage import JsonStore


def _plugin(tmp_path, mail_client=None):
    plugin = TelegramMailPlugin.__new__(TelegramMailPlugin)
    plugin.config = {}
    plugin.mail_client = mail_client
    plugin.store = JsonStore(tmp_path)
    plugin.store.load()
    plugin.tasks = []
    plugin.folder_modes = {}
    plugin.resolved_folders = {}
    plugin.idle_disabled_accounts = set()
    plugin._account_mail_locks = {}
    return plugin


def _oauth2_account(plugin, account_id="outlook", **overrides):
    config = {
        "account_id": account_id,
        "provider": "outlook",
        "target_chat_id": "123",
        "imap_user": f"{account_id}@example.com",
        "oauth2_client_id": "client-id",
        "imap_folders": ["INBOX", "Alerts"],
        "imap_folder_mode": "configured",
        "realtime_enabled": True,
    }
    config.update(overrides)
    return plugin._parse_account(config, "u1")


def test_oauth2_realtime_account_uses_account_polling_task(tmp_path):
    class MailClient:
        pass

    plugin = _plugin(tmp_path, MailClient())
    account = _oauth2_account(plugin)
    plugin._set_account_mode(account, "oauth2 polling")

    assert plugin._uses_oauth2_realtime_polling(account) is True
    assert plugin._account_mode(account) == "oauth2 polling"


def test_oauth2_realtime_loop_polls_account_without_folder_idle_watchers(tmp_path):
    class MailClient:
        pass

    plugin = _plugin(tmp_path, MailClient())
    plugin._stop_event = asyncio.Event()
    account = _oauth2_account(plugin)
    calls = []

    async def poll_account(current, *, push):
        calls.append(("poll_account", current.account_id, push))
        plugin._stop_event.set()
        return 0

    async def watch_folder(current, folder):
        raise AssertionError("OAuth2 realtime should not create folder IDLE watchers")

    async def sleep_poll_interval(current):
        return None

    plugin._poll_account = poll_account
    plugin._watch_folder_loop = watch_folder
    plugin._sleep_poll_interval = sleep_poll_interval

    asyncio.run(plugin._watch_oauth2_account_loop(account))

    assert calls == [("poll_account", "outlook", True)]
    assert plugin._account_mode(account) == "oauth2 polling"


def test_initialize_creates_one_oauth2_account_watcher(tmp_path, monkeypatch):
    class MailClient:
        pass

    plugin = _plugin(tmp_path, MailClient())
    plugin._stop_event = asyncio.Event()
    account = _oauth2_account(plugin)
    created = []

    async def watch_oauth2_account_loop(current):
        return None

    async def watch_account_loop(current):
        raise AssertionError("OAuth2 realtime should bypass folder IDLE watcher setup")

    def create_task(coro):
        created.append(coro.cr_code.co_name)
        coro.close()

        class Task:
            def cancel(self):
                return None

        return Task()

    plugin._accounts = lambda: [account]
    plugin._watch_oauth2_account_loop = watch_oauth2_account_loop
    plugin._watch_account_loop = watch_account_loop
    monkeypatch.setattr(
        "astrbot_plugin_telegram_mail.main.asyncio.create_task",
        create_task,
    )

    asyncio.run(plugin.initialize())

    assert created == ["watch_oauth2_account_loop"]
    assert len(plugin.tasks) == 1


def test_check_now_continues_when_one_account_fails(tmp_path):
    class MailClient:
        def list_uids(self, account, folder):
            if account.account_id == "bad":
                raise RuntimeError("LIST failed")
            return ["1"]

        def fetch_message(self, account, folder, uid):
            raise AssertionError("first poll should initialize seen state only")

    plugin = _plugin(tmp_path, MailClient())
    bad = _oauth2_account(plugin, "bad")
    good = _oauth2_account(plugin, "good")

    plugin._accounts = lambda: [bad, good]

    try:
        count = asyncio.run(plugin._check_now(owner_id="u1"))
    finally:
        plugin.executor.shutdown(wait=False, cancel_futures=True)

    assert count == 0
    assert plugin.store.last_error("u1", "bad")
    assert not plugin.store.last_error("u1", "good")
    assert plugin.store.is_initialized("u1", "good", "INBOX")
    assert plugin.store.is_initialized("u1", "good", "Alerts")


def test_oauth2_backoff_grows_and_resets_after_success(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    account = _oauth2_account(plugin, poll_interval=60)
    now = [100.0]
    monkeypatch.setattr("astrbot_plugin_telegram_mail.main.time.monotonic", lambda: now[0])

    plugin._record_oauth2_poll_failure(account, MailConnectionError("temporary"))
    assert plugin._oauth2_backoff_remaining(account) == 60

    now[0] = 160.0
    plugin._record_oauth2_poll_failure(account, MailConnectionError("temporary"))
    assert plugin._oauth2_backoff_remaining(account) == 120

    plugin._reset_oauth2_backoff(account)
    assert plugin._oauth2_backoff_remaining(account) == 0
