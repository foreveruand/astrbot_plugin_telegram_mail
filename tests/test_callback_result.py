from astrbot_plugin_telegram_mail.main import _message_chain_result

from astrbot.api.event import MessageChain, MessageEventResult
from astrbot.api.message_components import Plain


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
