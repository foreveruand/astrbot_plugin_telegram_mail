# Changelog

## 0.1.15

- Migrated Telegram mail state storage from `state.json` to SQLite in the plugin data directory.
- Automatically imported legacy `state.json` data and old `accounts` / `accounts_json` config into the new database.
- Kept existing database records authoritative during migration to avoid overwriting newer account or token data.

## 0.1.14

- Changed Outlook/OAuth2 realtime mail checks to account-level polling instead of per-folder IMAP IDLE watchers, avoiding long IDLE waits blocking the same OAuth2 account.
- Kept OAuth2 IMAP access serialized per account while continuing to scan later folders when one folder fails.
- Allowed `/mail check` across multiple accounts to continue after one account fails, recording the failed account in `/mail status`.
- Updated repeated Outlook `User is authenticated but not connected` errors to recommend retrying or reducing realtime folder pressure before reauthorization; `/mail oauth` is reserved for explicit token expiry or `invalid_grant` cases.

## 0.1.13

- Added readable recoverable error handling for IMAP, SMTP, and OAuth network/authentication failures.
- Downgraded recoverable background mail connection failures from traceback logs to warning logs while keeping `/mail status` error details.
- Improved repeated Outlook OAuth2 IMAP `User is authenticated but not connected` failures with a clear reauthorization hint.
- Scoped OAuth2 access-token caches and refresh locks by owner, account, and mailbox address to avoid cross-user/account token reuse.
- Serialized Outlook/OAuth2 IMAP polling and IDLE waits per account to reduce concurrent login failures across auto-discovered folders.

## 0.1.12

- Added `disable_idle_on_error` to stop retrying IMAP IDLE for an account until bot restart after realtime listener errors.
- Separated realtime resync/follow-up poll failures from IDLE failures in logs, while still falling back to scheduled polling.
- Updated the registered plugin version to match plugin metadata.

## 0.1.11

- Retried Outlook OAuth2 IMAP authentication once after transient `AUTHENTICATE failed` errors, clearing the cached access token before retrying.

## 0.1.10

- Serialized OAuth2 token refresh per account to avoid concurrent refresh attempts corrupting Microsoft single-use refresh tokens.
- Retried Outlook IMAP authentication once after the `User is authenticated but not connected` error, clearing the cached access token before retrying.

## 0.1.9

- Fixed Outlook OAuth2 IMAP authentication by returning bytes from the XOAUTH2 IMAP callback payload.
- Isolated blocking IMAP/SMTP/OAuth calls in a plugin-owned thread pool so mail connection stalls do not consume AstrBot's default executor.
- Split long IMAP IDLE waits into 60-second executor slices so IDLE folders do not monopolize the plugin worker pool.
- Added explicit network timeouts for IMAP, SMTP, and OAuth requests to fail unhealthy mail connections quickly.
- Deferred realtime folder discovery to background watcher tasks so plugin hot-loading does not block AstrBot services.
- Added `network_timeout` and `max_workers` plugin settings for tuning mailbox network isolation.

## 0.1.8

- Improved Telegram mail cards with Markdown subject/from/date formatting and escaped dynamic mail content.
- Moved attachment callbacks onto the main mail card as direct attachment buttons, while keeping the old attachment-list callback compatible.
- Added `imap_folder_mode` with Outlook/Microsoft auto folder discovery and fallback to configured folders when discovery fails.
- Updated `/mail status` to show folder mode and resolved folder summary.

## 0.1.7

- Treated IMAP IDLE socket EOF/BrokenPipe as recoverable disconnects to avoid repeated traceback logs.
- Added a Date-based historical mail guard so unexpectedly unseen old UIDs are marked processed without being pushed.
- Added `historical_mail_grace_seconds` to configure the Date guard grace window.

## 0.1.6

- Disabled Telegram web page previews when editing mail callback messages to avoid URL preview truncation issues.

## 0.1.5

- Filtered common HTML/CSS boilerplate from mail body previews and full-text view so the actual message content appears first.

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
