# Changelog

## 0.1.4

- Removed `client_secret` from Outlook public-client device code and refresh token requests.
- Simplified Outlook account setup to collect only `oauth2_client_id`.
- Clarified that `AADSTS90023` means the public client flow is still sending a client secret.

## 0.1.3

- Preserved the latest stored Outlook refresh token when Microsoft returns an access-token-only refresh response.
- Added Microsoft OAuth refresh error details to plugin errors so `400 Bad Request` includes the underlying AADSTS reason.

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
