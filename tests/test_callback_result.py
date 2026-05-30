import asyncio

from astrbot_plugin_telegram_mail.main import (
    _message_chain_result,
    _patch_telegram_callback_edit_message_preview,
)

from astrbot.api.event import MessageChain, MessageEventResult
from astrbot.api.message_components import Plain
from astrbot.core.platform.sources.telegram.tg_event import TelegramCallbackQueryEvent


def test_message_chain_result_preserves_inline_keyboard_and_can_stop():
    chain = MessageChain([Plain("Hello")]).inline_keyboard(
        [[{"text": "Open", "callback_data": "tmail:token:action"}]]
    )

    result = _message_chain_result(chain)
    result.stop_event()

    assert isinstance(result, MessageEventResult)
    assert result.chain == chain.chain
    assert result.reply_markup == chain.reply_markup
    assert result.is_stopped()


def test_telegram_callback_edit_message_disables_web_preview():
    class FakeClient:
        def __init__(self):
            self.calls = []

        async def edit_message_text(self, **kwargs):
            self.calls.append(kwargs)

    class FakeChat:
        id = 123

    class FakeMessage:
        chat = FakeChat()
        message_id = 456

    class FakeEvent:
        client = FakeClient()
        inline_message_id = None
        message = FakeMessage()

    _patch_telegram_callback_edit_message_preview()

    asyncio.run(
        TelegramCallbackQueryEvent._edit_message(
            FakeEvent(),
            "https://example.com/path?query=value",
            parse_mode="MarkdownV2",
        )
    )

    assert FakeEvent.client.calls[0]["disable_web_page_preview"] is True
    assert FakeEvent.client.calls[0]["chat_id"] == 123
    assert FakeEvent.client.calls[0]["message_id"] == 456
