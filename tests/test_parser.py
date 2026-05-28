from astrbot_plugin_telegram_mail.parser import (
    extract_attachment_payload,
    html_to_text,
    parse_message,
)


def test_parse_plain_message_with_attachment():
    raw = (
        b"From: Sender <sender@example.com>\r\n"
        b"To: User <user@example.com>\r\n"
        b"Subject: Test mail\r\n"
        b"Message-ID: <msg-1@example.com>\r\n"
        b"List-Unsubscribe: <https://example.com/unsub>, <mailto:off@example.com>\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/mixed; boundary=abc\r\n"
        b"\r\n"
        b"--abc\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Hello body\r\n"
        b"--abc\r\n"
        b"Content-Type: text/plain\r\n"
        b"Content-Disposition: attachment; filename=note.txt\r\n"
        b"\r\n"
        b"attachment text\r\n"
        b"--abc--\r\n"
    )

    parsed = parse_message(raw, account_id="a1", folder="INBOX", uid="7")

    assert parsed.sender_email == "sender@example.com"
    assert parsed.subject == "Test mail"
    assert parsed.body_text == "Hello body"
    assert parsed.attachments[0].filename == "note.txt"
    assert parsed.unsubscribe_urls == ["https://example.com/unsub"]
    assert parsed.unsubscribe_mailtos == ["mailto:off@example.com"]

    filename, payload, content_type = extract_attachment_payload(raw, 0)
    assert filename == "note.txt"
    assert payload.strip() == b"attachment text"
    assert content_type == "text/plain"


def test_html_to_text_strips_markup():
    assert html_to_text("<p>Hello<br>World&nbsp;!</p>").splitlines() == [
        "Hello",
        "World !",
    ]

