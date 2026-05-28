from astrbot_plugin_telegram_mail.main import parse_mail_command_args


def test_parse_mail_command_with_plain_command_name():
    assert parse_mail_command_args("mail status") == ["status"]
    assert parse_mail_command_args("mail check gmail") == ["check", "gmail"]


def test_parse_mail_command_with_slash_and_bot_mention():
    assert parse_mail_command_args("/mail status") == ["status"]
    assert parse_mail_command_args("/mail@my_bot check gmail") == ["check", "gmail"]


def test_parse_mail_command_when_framework_passes_args_only():
    assert parse_mail_command_args("status") == ["status"]
    assert parse_mail_command_args("send acc user@example.com | Hi | Body") == [
        "send",
        "acc",
        "user@example.com",
        "|",
        "Hi",
        "|",
        "Body",
    ]

