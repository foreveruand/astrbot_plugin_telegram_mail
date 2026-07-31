from astrbot_plugin_telegram_mail.parser import (
    extract_attachment_payload,
    html_to_text,
    parse_message,
)


def test_html_to_markdown_text_renders_anchors_as_inline_links():
    from astrbot_plugin_telegram_mail.parser import html_to_markdown_text

    html_body = (
        "<p>请点击 "
        '<a href="https://example.com/path?x=1">查看详情</a>，'
        '或访问 <a href="https://example.com">https://example.com</a>。</p>'
    )
    text = html_to_markdown_text(html_body)
    assert text == (
        "请点击 [查看详情](https://example.com/path?x=1)，"
        "或访问 [https://example\\.com](https://example.com)。"
    )


def test_parse_message_html_body_markdown_links():
    raw = (
        b"From: Sender <sender@example.com>\r\n"
        b"To: User <user@example.com>\r\n"
        b"Subject: Link mail\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b'<p>Hi <a href="https://example.com">click</a> here</p>\r\n'
    )
    parsed = parse_message(raw, account_id="a1", folder="INBOX", uid="9")
    assert parsed.body_markdown is True
    assert parsed.body_text == "Hi [click](https://example.com) here"


def test_parse_plain_message_with_attachment():
    raw = (
        b"From: Sender <sender@example.com>\r\n"
        b"To: User <user@example.com>\r\n"
        b"Subject: Test mail\r\n"
        b"Date: Tue, 02 Jun 2026 11:30:00 +0800\r\n"
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
    assert parsed.date == "2026-06-02 11:30"
    assert parsed.date_datetime is not None
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


def test_html_to_text_drops_style_noise_and_boilerplate():
    text = html_to_text(
        """
        <html>
          <head>
            <style>
              #outlook a { padding:0; }
              body { margin:0;padding:0; }
            </style>
          </head>
          <body>
            Subject: gojo8在Domain Patreaction 中提及了您
            Date: 2026-05-30 00:23
            Attachments: 0

            <p>有效内容</p>
          </body>
        </html>
        """
    )

    assert text == "有效内容"


def test_parse_message_uses_cleaned_html_body_text():
    raw = (
        "From: Sender <sender@example.com>\r\n"
        "To: User <user@example.com>\r\n"
        "Subject: Test mail\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        "<html><head><style>#outlook a { padding:0; }</style></head><body>"
        "Subject: gojo8在Domain Patreaction 中提及了您\r\n"
        "Date: 2026-05-30 00:23\r\n"
        "Attachments: 0\r\n"
        "<div>有效内容</div>"
        "</body></html>\r\n"
    ).encode()

    parsed = parse_message(raw, account_id="a1", folder="INBOX", uid="8")

    assert parsed.body_text == "有效内容"
