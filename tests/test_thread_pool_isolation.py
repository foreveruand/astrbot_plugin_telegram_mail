import asyncio
import threading

from astrbot_plugin_telegram_mail.main import TelegramMailPlugin


def test_mail_io_uses_plugin_executor_thread():
    plugin = TelegramMailPlugin.__new__(TelegramMailPlugin)
    plugin.config = {"max_workers": 1}

    async def run():
        return await plugin._run_mail_io(lambda: threading.current_thread().name)

    try:
        thread_name = asyncio.run(run())
    finally:
        plugin.executor.shutdown(wait=False, cancel_futures=True)

    assert thread_name.startswith("telegram-mail")
