# Changelog

## 0.1.2

- Updated `/mail add` to use an interactive account setup flow for Gmail, Outlook, and QQ Mail.
- Added plugin-level Microsoft OAuth client defaults for Outlook accounts, with optional per-account override during setup.
- Fixed legacy `/mail add {json}` parsing so JSON payloads are preserved when passed through command parsing.
- Updated help text and documentation for the command-based account setup flow.

## 0.1.1

- Fixed Telegram inline mail callbacks failing after button actions by returning a `MessageEventResult` instead of a bare `MessageChain`.

## 0.1.0

- Added multi-account Telegram mail assistant plugin.
- Added IMAP polling, SMTP send/reply, inline attachment browsing, full-text pagination, and mail actions.
- Added local sender/domain blocklist and unsubscribe link display.
