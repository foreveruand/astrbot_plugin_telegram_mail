# Changelog

## 0.1.1

- Fixed Telegram inline mail callbacks failing after button actions by returning a `MessageEventResult` instead of a bare `MessageChain`.

## 0.1.0

- Added multi-account Telegram mail assistant plugin.
- Added IMAP polling, SMTP send/reply, inline attachment browsing, full-text pagination, and mail actions.
- Added local sender/domain blocklist and unsubscribe link display.
