from astrbot_plugin_telegram_mail.main import TelegramMailPlugin
from astrbot_plugin_telegram_mail.models import MailAttachment, ParsedMail

from astrbot.api.message_components import Plain


def _plugin():
    plugin = TelegramMailPlugin.__new__(TelegramMailPlugin)
    plugin.config = {}
    return plugin


def _account(plugin):
    return plugin._parse_account(
        {
            "account_id": "a1",
            "display_name": "Work Mail",
            "target_chat_id": "123",
            "imap_host": "imap.example.com",
            "imap_user": "user@example.com",
            "imap_password": "secret",
        },
        "u1",
    )


def _parsed_mail(**overrides):
    parsed = ParsedMail(
        account_id="a1",
        folder="INBOX",
        uid="7",
        message_id="<msg-1@example.com>",
        subject="Invoice_#1 [paid]",
        sender="Alice Example <alice@example.com>",
        sender_email="alice@example.com",
        recipients=["user@example.com"],
        date="2026-06-02 11:30",
        body_text="Hello *team* [link](https://example.com)!",
        body_html="",
    )
    for key, value in overrides.items():
        setattr(parsed, key, value)
    return parsed


def _plain_text(chain):
    return "".join(part.text for part in chain.chain if isinstance(part, Plain))


def test_mail_card_uses_markdown_and_direct_attachment_buttons():
    plugin = _plugin()
    account = _account(plugin)
    parsed = _parsed_mail(
        attachments=[
            MailAttachment(0, "invoice.pdf", "application/pdf", 42),
            MailAttachment(1, "very-long-attachment-name.txt", "text/plain", 12),
        ]
    )

    chain = plugin._mail_card(account, parsed, "tok")
    text = _plain_text(chain)

    assert chain.use_markdown_ is True
    assert "*Subject:* Invoice\\_\\#1 \\[paid\\]" in text
    assert "*From:* [Alice Example](mailto:alice@example.com)" in text
    assert "Attachments:" not in text
    assert "\\*team\\* \\[link\\]\\(https://example\\.com\\)\\!" in text
    assert chain.reply_markup[0][0]["callback_data"] == "tmail:tok:att:0"
    assert chain.reply_markup[0][0]["text"] == "📎 invoice.pdf"
    assert chain.reply_markup[0][1]["callback_data"] == "tmail:tok:att:1"


def test_markdown_cards_enable_markdown():
    plugin = _plugin()
    account = _account(plugin)
    parsed = _parsed_mail()

    assert plugin._full_text_card(account, parsed, "tok", 0).use_markdown_ is True
    assert plugin._action_card(parsed, "tok").use_markdown_ is True
    assert plugin._unsubscribe_card(parsed, "tok").use_markdown_ is True
