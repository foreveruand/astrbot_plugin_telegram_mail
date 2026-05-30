from __future__ import annotations

import asyncio
import json
import re
import shlex
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, MessageEventResult, filter
from astrbot.api.message_components import File, Plain
from astrbot.api.star import Context, Star, register
from astrbot.api.util import SessionController, session_waiter
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.telegram.tg_event import (
    TelegramCallbackQueryEvent,
    TelegramPlatformEvent,
)
from astrbot.core.utils.astrbot_path import (
    get_astrbot_plugin_data_path,
    get_astrbot_temp_path,
)

from .mail_client import IdleNotSupported, MailClient
from .models import MailAccount, ParsedMail
from .parser import extract_attachment_payload, parse_message
from .storage import JsonStore

PLUGIN_NAME = "astrbot_plugin_telegram_mail"
CALLBACK_PREFIX = "tmail"
DEFAULT_PREVIEW_LENGTH = 600
DEFAULT_PAGE_SIZE = 2500
DEFAULT_MAX_FETCH = 10
DEFAULT_IDLE_TIMEOUT = 1740
IDLE_WAIT_SLICE = 60
MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
MICROSOFT_DEVICE_CODE_URL = (
    "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode"
)
MICROSOFT_OUTLOOK_SCOPE = (
    "https://outlook.office.com/IMAP.AccessAsUser.All "
    "https://outlook.office.com/SMTP.Send offline_access"
)
OWNER_ID_FALLBACK = "default"
MAIL_COMMAND_RE = re.compile(r"^/?mail(?:@\S+)?$", re.IGNORECASE)
ADD_SESSION_TIMEOUT = 300
SUPPORTED_ADD_PROVIDERS = {"gmail", "outlook", "qq"}
SKIP_VALUES = {"-", "skip", "default", "默认", "跳过", "否", "no", "n"}
CANCEL_VALUES = {"cancel", "取消", "退出", "exit", "quit"}


def parse_mail_command_args(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    json_add = re.match(
        r"^(?:(?:/?mail(?:@\S+)?)\s+)?add\s+(\{.*)$", text, re.IGNORECASE | re.DOTALL
    )
    if json_add:
        return ["add", json_add.group(1).strip()]
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    if parts and MAIL_COMMAND_RE.match(parts[0]):
        return parts[1:]
    return parts


def _normalize_mail_provider(value: str) -> str:
    provider = str(value or "").strip().lower()
    aliases = {
        "google": "gmail",
        "googlemail": "gmail",
        "outlook.com": "outlook",
        "hotmail": "outlook",
        "microsoft": "outlook",
        "ms": "outlook",
        "腾讯": "qq",
        "qqmail": "qq",
    }
    provider = aliases.get(provider, provider)
    return provider if provider in SUPPORTED_ADD_PROVIDERS else ""


def _is_cancel_text(value: str) -> bool:
    return str(value or "").strip().lower() in CANCEL_VALUES


def _is_skip_text(value: str) -> bool:
    return str(value or "").strip().lower() in SKIP_VALUES


def _default_account_id(provider: str, email: str) -> str:
    local = email.split("@", 1)[0] if "@" in email else email
    suffix = re.sub(r"[^A-Za-z0-9_.-]+", "-", local).strip(".-_")
    return f"{provider}-{suffix or 'mail'}"


def _build_provider_account_config(
    *,
    provider: str,
    email: str,
    password: str = "",
    account_id: str = "",
    display_name: str = "",
    target_chat_id: str = "",
    platform_id: str = "telegram",
    message_type: str = "friend",
    oauth2_client_id: str = "",
    oauth2_client_secret: str = "",
) -> dict[str, Any]:
    provider = _normalize_mail_provider(provider)
    if not provider:
        raise ValueError("不支持的邮箱类型")
    email = email.strip()
    account_id = (account_id or _default_account_id(provider, email)).strip()
    base: dict[str, Any] = {
        "account_id": account_id,
        "display_name": display_name.strip() or account_id,
        "provider": provider,
        "enabled": True,
        "target_chat_id": target_chat_id.strip(),
        "platform_id": platform_id.strip() or "telegram",
        "message_type": message_type.strip() or "friend",
        "imap_user": email,
        "imap_folders": ["INBOX"],
        "smtp_user": email,
        "from_address": email,
    }
    if provider == "gmail":
        base.update(
            {
                "imap_host": "imap.gmail.com",
                "imap_port": 993,
                "imap_tls": True,
                "imap_password": password,
                "smtp_host": "smtp.gmail.com",
                "smtp_port": 465,
                "smtp_tls": "ssl",
                "smtp_password": password,
                "archive_folder": "[Gmail]/All Mail",
                "trash_folder": "[Gmail]/Trash",
            }
        )
    elif provider == "qq":
        base.update(
            {
                "imap_host": "imap.qq.com",
                "imap_port": 993,
                "imap_tls": True,
                "imap_password": password,
                "smtp_host": "smtp.qq.com",
                "smtp_port": 465,
                "smtp_tls": "ssl",
                "smtp_password": password,
            }
        )
    else:
        base["auth_type"] = "oauth2"
        if oauth2_client_id.strip():
            base["oauth2_client_id"] = oauth2_client_id.strip()
        if oauth2_client_secret.strip():
            base["oauth2_client_secret"] = oauth2_client_secret.strip()
    return base


def _message_chain_result(chain: MessageChain) -> MessageEventResult:
    result = MessageEventResult(chain=list(chain.chain))
    result.use_t2i_ = chain.use_t2i_
    result.use_markdown_ = chain.use_markdown_
    result.type = chain.type
    result.reply_markup = chain.reply_markup
    return result


def _patch_telegram_callback_edit_message_preview() -> None:
    if getattr(
        TelegramCallbackQueryEvent, "_mail_disable_web_page_preview_patched", False
    ):
        return

    async def _edit_message(
        self: TelegramCallbackQueryEvent,
        text: str,
        parse_mode: str | None = None,
        reply_markup=None,
    ) -> None:
        if self.inline_message_id:
            try:
                await self.client.edit_message_text(
                    text=text[: TelegramPlatformEvent.MAX_MESSAGE_LENGTH],
                    inline_message_id=self.inline_message_id,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.warning(f"编辑内联消息失败: {e!s}")
        elif self.message:
            try:
                chat_id = self.message.chat.id
                message_id = self.message.message_id
                await self.client.edit_message_text(
                    text=text[: TelegramPlatformEvent.MAX_MESSAGE_LENGTH],
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.warning(f"编辑消息失败: {e!s}")
        else:
            logger.debug("TelegramCallbackQueryEvent 无可用消息，跳过编辑。")

    TelegramCallbackQueryEvent._edit_message = _edit_message  # type: ignore[assignment]
    TelegramCallbackQueryEvent._mail_disable_web_page_preview_patched = True  # type: ignore[attr-defined]


@register(
    PLUGIN_NAME,
    "foreveruand",
    "Telegram-only IMAP/SMTP mail assistant with inline actions.",
    "0.1.6",
)
class TelegramMailPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context, config)
        self.context = context
        self.config = config or {}
        self.mail_client = MailClient(
            self._save_oauth2_token_response,
            self._load_oauth2_token_state,
        )
        data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.store = JsonStore(data_dir, max_tokens=self._config_int("max_tokens", 500))
        self.tasks: list[asyncio.Task] = []
        self._stop_event = asyncio.Event()
        self._thread_stop_event = threading.Event()
        self.folder_modes: dict[tuple[str, str], str] = {}

    async def initialize(self) -> None:
        _patch_telegram_callback_edit_message_preview()
        self.store.load()
        accounts = self._accounts()
        for account in accounts:
            if not account.enabled:
                continue
            if account.realtime_enabled:
                for folder in account.imap_folders:
                    task = asyncio.create_task(self._watch_folder_loop(account, folder))
                    self.tasks.append(task)
            else:
                task = asyncio.create_task(self._poll_loop(account))
                self.tasks.append(task)
        logger.info("Telegram mail plugin initialized with %s accounts", len(accounts))

    async def terminate(self) -> None:
        self._stop_event.set()
        self._thread_stop_event.set()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.store.save()

    @filter.command("mail")
    async def mail_command(self, event: AstrMessageEvent):
        raw = (event.message_str or "").strip()
        args = self._parse_command_args(raw)
        if not args:
            yield event.plain_result(self._help_text())
            return

        command = args[0].lower()
        owner_id = self._event_owner_id(event)
        try:
            if command == "status":
                yield event.plain_result(self._render_status(owner_id))
            elif command == "check":
                account_id = args[1] if len(args) > 1 else ""
                count = await self._check_now(account_id, owner_id)
                yield event.plain_result(f"检查完成，新增推送 {count} 封邮件。")
            elif command == "send":
                yield event.plain_result(await self._cmd_send(args[1:], owner_id))
            elif command == "reply":
                yield event.plain_result(await self._cmd_reply(args[1:], owner_id))
            elif command == "oauth":
                yield event.plain_result(await self._cmd_oauth(args[1:], owner_id))
            elif command == "add":
                async for result in self._cmd_add(event, args[1:], owner_id, raw):
                    yield result
            elif command == "remove":
                yield event.plain_result(self._cmd_remove(args[1:], owner_id))
            elif command == "blocklist":
                account_id = args[1] if len(args) > 1 else ""
                yield event.plain_result(self._render_blocklist(account_id, owner_id))
            elif command == "unblock":
                yield event.plain_result(self._cmd_unblock(args[1:], owner_id))
            else:
                yield event.plain_result(self._help_text())
        except Exception as exc:
            logger.exception("Telegram mail command failed")
            yield event.plain_result(f"执行失败: {exc}")

    @filter.callback_query()
    async def handle_callback(self, event: TelegramCallbackQueryEvent) -> None:
        data = (event.data or "").strip()
        if not data.startswith(f"{CALLBACK_PREFIX}:"):
            event.continue_event()
            return

        parts = data.split(":")
        if len(parts) < 3:
            await event.answer_callback_query("按钮数据无效")
            event.stop_event()
            return

        token = parts[1]
        op = parts[2]
        owner_id = self._event_owner_id(event)
        payload = self.store.get_token(owner_id, token)
        if not payload:
            await event.answer_callback_query("邮件上下文已过期，请重新检查")
            event.stop_event()
            return

        try:
            await self._handle_mail_callback(event, token, op, parts[3:], payload)
        except Exception as exc:
            logger.exception("Telegram mail callback failed")
            await event.answer_callback_query(f"操作失败: {exc}", show_alert=True)
        finally:
            event.stop_event()

    async def _poll_loop(self, account: MailAccount) -> None:
        while not self._stop_event.is_set():
            try:
                self._set_account_mode(account, "polling")
                await self._poll_account(account, push=True)
                self.store.clear_last_error(account.owner_id, account.account_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Mail poll failed for account %s", account.account_id)
                self.store.set_last_error(
                    account.owner_id, account.account_id, str(exc)
                )
                self.store.save()

            await self._sleep_poll_interval(account)

    async def _watch_folder_loop(self, account: MailAccount, folder: str) -> None:
        last_resync = 0.0
        while not self._stop_event.is_set():
            try:
                if time.monotonic() - last_resync >= max(account.poll_interval, 30):
                    await self._poll_folder(account, folder, push=True)
                    self.store.clear_last_error(account.owner_id, account.account_id)
                    last_resync = time.monotonic()

                self._set_folder_mode(account, folder, "idle")
                changed = await asyncio.to_thread(
                    self.mail_client.wait_for_new_mail,
                    account,
                    folder,
                    account.idle_timeout,
                    IDLE_WAIT_SLICE,
                    self._thread_stop_event,
                )
                if changed:
                    await self._poll_folder(account, folder, push=True)
                    self.store.clear_last_error(account.owner_id, account.account_id)
                    last_resync = time.monotonic()
            except asyncio.CancelledError:
                raise
            except IdleNotSupported:
                self._set_folder_mode(account, folder, "polling fallback")
                try:
                    await self._poll_folder(account, folder, push=True)
                    self.store.clear_last_error(account.owner_id, account.account_id)
                    last_resync = time.monotonic()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception(
                        "Mail fallback poll failed for account %s folder %s",
                        account.account_id,
                        folder,
                    )
                    self.store.set_last_error(
                        account.owner_id, account.account_id, str(exc)
                    )
                    self.store.save()
                await self._sleep_poll_interval(account)
            except Exception as exc:
                logger.exception(
                    "Mail IDLE failed for account %s folder %s",
                    account.account_id,
                    folder,
                )
                self._set_folder_mode(account, folder, "polling fallback")
                self.store.set_last_error(
                    account.owner_id, account.account_id, str(exc)
                )
                self.store.save()
                await self._sleep_poll_interval(account)

    async def _sleep_poll_interval(self, account: MailAccount) -> None:
        try:
            await asyncio.wait_for(
                self._stop_event.wait(),
                timeout=max(account.poll_interval, 30),
            )
        except asyncio.TimeoutError:
            pass

    async def _check_now(
        self, account_id: str = "", owner_id: str | None = None
    ) -> int:
        accounts = self._accounts()
        if owner_id is not None:
            accounts = [account for account in accounts if account.owner_id == owner_id]
        if account_id:
            accounts = [
                account for account in accounts if account.account_id == account_id
            ]
            if not accounts:
                raise ValueError(f"未知账号: {account_id}")
        total = 0
        for account in accounts:
            if account.enabled:
                total += await self._poll_account(account, push=True)
        return total

    async def _poll_account(self, account: MailAccount, *, push: bool) -> int:
        total = 0
        for folder in account.imap_folders:
            total += await self._poll_folder(account, folder, push=push)
        return total

    async def _poll_folder(
        self,
        account: MailAccount,
        folder: str,
        *,
        push: bool,
    ) -> int:
        total = 0
        uids = await asyncio.to_thread(self.mail_client.list_uids, account, folder)
        current = set(uids)
        seen = self.store.get_seen(account.owner_id, account.account_id, folder)
        if not self.store.is_initialized(account.owner_id, account.account_id, folder):
            self.store.set_initialized(account.owner_id, account.account_id, folder)
            if not self._config_bool("notify_existing_on_first_run", False):
                self.store.set_seen(
                    account.owner_id, account.account_id, folder, current
                )
                self._mark_account_checked(account)
                return 0

        new_uids = [uid for uid in uids if uid not in seen]
        max_fetch = self._config_int("max_fetch_per_poll", DEFAULT_MAX_FETCH)
        for uid in new_uids[-max_fetch:]:
            raw = await asyncio.to_thread(
                self.mail_client.fetch_message,
                account,
                folder,
                uid,
            )
            parsed = parse_message(
                raw,
                account_id=account.account_id,
                folder=folder,
                uid=uid,
            )
            self.store.add_seen(account.owner_id, account.account_id, folder, uid)
            if self.store.is_blocked(
                account.owner_id,
                account.account_id,
                parsed.sender_email,
            ):
                continue
            if push:
                await self._push_mail_card(account, parsed, raw)
            total += 1

        self._mark_account_checked(account)
        return total

    def _mark_account_checked(self, account: MailAccount) -> None:
        self.store.set_last_check(
            account.owner_id,
            account.account_id,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        self.store.save()

    async def _push_mail_card(
        self,
        account: MailAccount,
        parsed: ParsedMail,
        raw: bytes,
    ) -> None:
        raw_path = self.store.save_raw_message(account.owner_id, raw)
        token = self.store.put_token(
            account.owner_id,
            {
                "owner_id": account.owner_id,
                "account_id": account.account_id,
                "folder": parsed.folder,
                "uid": parsed.uid,
                "raw_path": raw_path,
                "sender_email": parsed.sender_email,
                "sender": parsed.sender,
                "subject": parsed.subject,
                "message_id": parsed.message_id,
                "recipients": parsed.recipients,
            },
        )
        self.store.save()
        chain = self._mail_card(account, parsed, token)
        session = MessageSession(
            platform_name=account.platform_id,
            message_type=self._message_type(account.message_type),
            session_id=account.target_chat_id,
        )
        sent = await self.context.send_message(session, chain)
        if not sent:
            logger.warning("Failed to push mail card to session %s", session)

    async def _handle_mail_callback(
        self,
        event: TelegramCallbackQueryEvent,
        token: str,
        op: str,
        args: list[str],
        payload: dict[str, Any],
    ) -> None:
        owner_id = self._event_owner_id(event)
        payload_owner_id = str(payload.get("owner_id") or owner_id)
        if payload_owner_id != owner_id:
            await event.answer_callback_query("无权操作此邮件", show_alert=True)
            return
        account = self._account(payload["account_id"], owner_id)
        raw = Path(payload["raw_path"]).read_bytes()
        parsed = parse_message(
            raw,
            account_id=account.account_id,
            folder=payload["folder"],
            uid=payload["uid"],
        )

        if op == "more":
            page = int(args[0]) if args else 0
            event.set_result(
                _message_chain_result(
                    self._full_text_card(account, parsed, token, page)
                )
            )
            await event.answer_callback_query()
            return
        if op == "attachments":
            event.set_result(
                _message_chain_result(self._attachments_card(parsed, token))
            )
            await event.answer_callback_query()
            return
        if op == "att":
            index = int(args[0]) if args else 0
            await self._send_attachment(event, raw, index)
            await event.answer_callback_query("附件已发送")
            return
        if op == "action":
            event.set_result(_message_chain_result(self._action_card(parsed, token)))
            await event.answer_callback_query()
            return
        if op == "back":
            event.set_result(
                _message_chain_result(self._mail_card(account, parsed, token))
            )
            await event.answer_callback_query()
            return
        if op == "block":
            self.store.block_sender(
                account.owner_id, account.account_id, parsed.sender_email
            )
            self.store.save()
            event.set_result(
                _message_chain_result(
                    MessageChain([Plain(f"已屏蔽发件人: {parsed.sender_email}")])
                )
            )
            await event.answer_callback_query("已屏蔽")
            return
        if op in {"archive", "delete", "read", "unread"}:
            await self._run_mail_action(account, parsed, op)
            event.set_result(
                _message_chain_result(
                    MessageChain([Plain(self._action_done_text(parsed, op))])
                )
            )
            await event.answer_callback_query("操作完成")
            return
        if op == "unsubscribe":
            event.set_result(
                _message_chain_result(self._unsubscribe_card(parsed, token))
            )
            await event.answer_callback_query()
            return
        if op == "reply":
            event.set_result(
                _message_chain_result(
                    MessageChain(
                        [Plain(f"使用命令回复此邮件:\n/mail reply {token} <回复内容>")]
                    )
                )
            )
            await event.answer_callback_query()
            return

        await event.answer_callback_query("未知操作")

    async def _run_mail_action(
        self,
        account: MailAccount,
        parsed: ParsedMail,
        op: str,
    ) -> None:
        if op == "archive":
            await asyncio.to_thread(
                self.mail_client.move_message,
                account,
                parsed.folder,
                parsed.uid,
                account.archive_folder,
            )
        elif op == "delete":
            await asyncio.to_thread(
                self.mail_client.delete_message,
                account,
                parsed.folder,
                parsed.uid,
            )
        elif op == "read":
            await asyncio.to_thread(
                self.mail_client.mark_seen,
                account,
                parsed.folder,
                parsed.uid,
                True,
            )
        elif op == "unread":
            await asyncio.to_thread(
                self.mail_client.mark_seen,
                account,
                parsed.folder,
                parsed.uid,
                False,
            )

    async def _send_attachment(
        self,
        event: TelegramCallbackQueryEvent,
        raw: bytes,
        index: int,
    ) -> None:
        filename, content, _ = extract_attachment_payload(raw, index)
        max_size_mb = self._config_int("max_attachment_mb", 20)
        if len(content) > max_size_mb * 1024 * 1024:
            raise ValueError(f"附件超过限制: {max_size_mb} MB")
        temp_dir = Path(get_astrbot_temp_path()) / PLUGIN_NAME
        temp_dir.mkdir(parents=True, exist_ok=True)
        path = temp_dir / self._safe_filename(filename)
        path.write_bytes(content)
        chain = MessageChain(
            [Plain(f"附件: {filename}"), File(name=filename, file=str(path))]
        )
        chat_id = self._callback_chat_id(event)
        await TelegramPlatformEvent.send_with_client(event.client, chain, chat_id)

    async def _cmd_send(
        self, args: list[str], owner_id: str = OWNER_ID_FALLBACK
    ) -> str:
        if len(args) < 2:
            return "用法: /mail send <account_id> <to> | <subject> | <body>"
        account = self._account(args[0], owner_id)
        text = " ".join(args[1:])
        fields = [part.strip() for part in text.split("|", 2)]
        if len(fields) != 3:
            return "用法: /mail send <account_id> <to> | <subject> | <body>"
        to_text, subject, body = fields
        recipients = [item.strip() for item in to_text.split(",") if item.strip()]
        await asyncio.to_thread(
            self.mail_client.send_mail,
            account,
            recipients,
            subject,
            body,
        )
        return f"已发送至 {', '.join(recipients)}"

    async def _cmd_reply(
        self, args: list[str], owner_id: str = OWNER_ID_FALLBACK
    ) -> str:
        if len(args) < 2:
            return "用法: /mail reply <token> <回复内容>"
        token = args[0]
        payload = self.store.get_token(owner_id, token)
        if not payload:
            return "邮件上下文已过期，请重新检查"
        payload_owner_id = str(payload.get("owner_id") or owner_id)
        if payload_owner_id != owner_id:
            return "无权操作此邮件"
        account = self._account(payload["account_id"], owner_id)
        body = " ".join(args[1:]).strip()
        if not body:
            return "回复内容不能为空"
        raw = Path(payload["raw_path"]).read_bytes()
        parsed = parse_message(
            raw,
            account_id=account.account_id,
            folder=payload["folder"],
            uid=payload["uid"],
        )
        subject = parsed.subject
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        await asyncio.to_thread(
            self.mail_client.send_mail,
            account,
            [parsed.sender_email],
            subject,
            body,
            in_reply_to=parsed.message_id,
            references=parsed.message_id,
        )
        return f"已回复 {parsed.sender_email}"

    async def _cmd_oauth(
        self, args: list[str], owner_id: str = OWNER_ID_FALLBACK
    ) -> str:
        if len(args) != 1:
            return "用法: /mail oauth <account_id>"
        account = self._account(args[0], owner_id)
        if "oauth2" not in {account.imap_auth_type, account.smtp_auth_type}:
            return f"账号 {account.account_id} 未启用 OAuth2。"
        if not account.oauth2_client_id:
            return f"账号 {account.account_id} 缺少 oauth2_client_id。"

        device = await asyncio.to_thread(self._request_device_code, account)
        task = asyncio.create_task(self._complete_oauth_device_flow(account, device))
        self.tasks.append(task)

        verification_uri = device.get("verification_uri_complete") or device.get(
            "verification_uri"
        )
        user_code = device.get("user_code", "")
        expires_in = int(device.get("expires_in") or 0)
        lines = [
            f"请打开以下链接完成 {account.display_name} OAuth2 授权:",
            str(verification_uri),
        ]
        if user_code:
            lines.append(f"授权代码: {user_code}")
        if expires_in:
            lines.append(f"有效期: {expires_in // 60} 分钟")
        lines.append("授权完成后插件会自动保存 token，并向目标会话发送结果。")
        return "\n".join(lines)

    def _request_device_code(self, account: MailAccount) -> dict[str, Any]:
        payload = self._post_oauth2_form(
            account.oauth2_device_code_url,
            {
                "client_id": account.oauth2_client_id,
                "scope": account.oauth2_scope,
            },
        )
        if "device_code" not in payload:
            raise RuntimeError("OAuth2 device code response has no device_code")
        return payload

    async def _complete_oauth_device_flow(
        self,
        account: MailAccount,
        device: dict[str, Any],
    ) -> None:
        interval = max(int(device.get("interval") or 5), 1)
        expires_at = time.time() + int(device.get("expires_in") or 900)
        while time.time() < expires_at and not self._stop_event.is_set():
            await asyncio.sleep(interval)
            try:
                payload = await asyncio.to_thread(
                    self._poll_device_token,
                    account,
                    str(device["device_code"]),
                )
            except OAuth2AuthorizationPending:
                continue
            except OAuth2SlowDown:
                interval += 5
                continue
            except Exception as exc:
                logger.exception("OAuth2 device flow failed for %s", account.account_id)
                await self._send_account_notice(account, f"OAuth2 授权失败: {exc}")
                return

            self._save_oauth2_token_response(account, payload)
            await self._send_account_notice(
                account,
                f"OAuth2 授权完成: {account.display_name}",
            )
            return

        await self._send_account_notice(
            account, f"OAuth2 授权超时: {account.display_name}"
        )

    def _poll_device_token(
        self, account: MailAccount, device_code: str
    ) -> dict[str, Any]:
        return self._post_oauth2_form(
            account.oauth2_token_url,
            {
                "client_id": account.oauth2_client_id,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            },
        )

    @staticmethod
    def _post_oauth2_form(
        url: str,
        form: dict[str, str],
        *,
        client_secret: str = "",
    ) -> dict[str, Any]:
        values = {key: value for key, value in form.items() if value}
        if client_secret:
            values["client_secret"] = client_secret
        data = urllib.parse.urlencode(values).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                raise RuntimeError(f"OAuth2 request failed: HTTP {exc.code}") from exc
            error = str(payload.get("error") or "")
            if error == "authorization_pending":
                raise OAuth2AuthorizationPending from exc
            if error == "slow_down":
                raise OAuth2SlowDown from exc
            description = str(payload.get("error_description") or error)
            raise RuntimeError(description) from exc

    def _save_oauth2_token_response(
        self,
        account: MailAccount,
        payload: dict[str, Any],
    ) -> None:
        current_state = self.store.get_oauth2_state(
            account.owner_id, account.account_id
        )
        access_token = str(payload.get("access_token") or "")
        refresh_token = str(
            payload.get("refresh_token")
            or current_state.get("refresh_token")
            or account.oauth2_refresh_token
        )
        expires_at = float(
            payload.get("expires_at")
            or time.time() + int(payload.get("expires_in") or 3600)
        )
        self.store.set_oauth2_state(
            account.owner_id,
            account.account_id,
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": expires_at,
                "updated_at": time.time(),
            },
        )
        self.store.save()

    def _load_oauth2_token_state(self, account: MailAccount) -> dict[str, Any]:
        return self.store.get_oauth2_state(account.owner_id, account.account_id)

    async def _send_account_notice(self, account: MailAccount, text: str) -> None:
        session = MessageSession(
            platform_name=account.platform_id,
            message_type=self._message_type(account.message_type),
            session_id=account.target_chat_id,
        )
        await self.context.send_message(session, MessageChain([Plain(text)]))

    async def _cmd_add(
        self,
        event: AstrMessageEvent,
        args: list[str],
        owner_id: str,
        raw: str,
    ):
        json_text = args[0].strip() if args and args[0].strip().startswith("{") else ""
        if not json_text and raw:
            match = re.match(
                r"^(?:(?:/?mail(?:@\S+)?)\s+)?add\s+(\{.*)$",
                raw.strip(),
                re.IGNORECASE | re.DOTALL,
            )
            json_text = match.group(1).strip() if match else ""
        if json_text:
            try:
                account_config = json.loads(json_text)
            except json.JSONDecodeError as exc:
                yield event.plain_result(f"账号 JSON 无效: {exc}")
                return
            if not isinstance(account_config, dict):
                yield event.plain_result("账号 JSON 必须是对象。")
                return
            account = self._parse_account(account_config, owner_id)
            self.store.set_account_config(owner_id, account.account_id, account_config)
            self.store.save()
            yield event.plain_result(
                f"已保存账号 {account.account_id}。重启插件后会开始后台监听；也可以先执行 /mail check {account.account_id}。"
            )
            return

        yield event.plain_result(
            "请按顺序回复邮箱类型、账号、密码及可选 client 信息。\n"
            "支持类型: gmail / outlook / qq\n"
            "回复「取消」可退出。"
        )

        @session_waiter(timeout=ADD_SESSION_TIMEOUT)
        async def wait_for_add(
            controller: SessionController,
            reply_event: AstrMessageEvent,
        ) -> None:
            reply_text = (reply_event.message_str or "").strip()
            normalized = reply_text.lower()

            if _is_cancel_text(normalized):
                await reply_event.send(reply_event.plain_result("已取消添加账号。"))
                controller.stop()
                return

            if not controller.current_event:
                return

            state = getattr(controller, "mail_add_state", None)
            if state is None:
                state = {
                    "step": "provider",
                    "provider": "",
                    "email": "",
                    "password": "",
                    "account_id": "",
                    "display_name": "",
                    "target_chat_id": owner_id,
                    "platform_id": "telegram",
                    "message_type": "friend",
                    "oauth2_client_id": "",
                }
                setattr(controller, "mail_add_state", state)

            async def ask_next(prompt: str) -> None:
                await reply_event.send(reply_event.plain_result(prompt))

            step = str(state.get("step") or "provider")
            provider = _normalize_mail_provider(state.get("provider", ""))

            if step == "provider":
                provider = _normalize_mail_provider(reply_text)
                if not provider:
                    await ask_next("请选择邮箱类型: gmail / outlook / qq")
                    return
                state["provider"] = provider
                state["step"] = "email"
                prompt = {
                    "gmail": "请输入 Gmail 账号邮箱地址",
                    "qq": "请输入 QQ 邮箱地址",
                    "outlook": "请输入 Microsoft 账号邮箱地址",
                }[provider]
                await ask_next(prompt)
                return

            if step == "email":
                if "@" not in reply_text:
                    await ask_next("请输入有效的邮箱地址。")
                    return
                state["email"] = reply_text
                if provider == "outlook":
                    state["step"] = "outlook_client_choice"
                    await ask_next(
                        "是否自定义 Microsoft client_id？回复 yes/no。默认使用插件设置中的 client_id。"
                    )
                else:
                    state["step"] = "password"
                    await ask_next("请输入邮箱密码或应用专用密码")
                return

            if step == "outlook_client_choice":
                if normalized in {"yes", "y", "是", "需要", "自定义"}:
                    state["step"] = "outlook_client_id"
                    await ask_next("请输入 oauth2_client_id")
                    return
                if normalized in {"no", "n", "否", "默认", "不自定义"}:
                    state["step"] = "account_id"
                    await ask_next("请输入账号 ID，回复 - 则自动生成")
                    return
                await ask_next("请回复 yes 或 no。")
                return

            if step == "outlook_client_id":
                state["oauth2_client_id"] = reply_text
                state["step"] = "account_id"
                await ask_next("请输入账号 ID，回复 - 则自动生成")
                return

            if step == "password":
                state["password"] = "" if _is_skip_text(reply_text) else reply_text
                state["step"] = "target_chat_id"
                await ask_next(
                    "请输入目标会话 ID（Telegram chat_id；群聊可用负数，话题群可用 chat_id#thread_id）"
                )
                return

            if step == "account_id":
                state["account_id"] = "" if _is_skip_text(reply_text) else reply_text
                state["step"] = "target_chat_id"
                await ask_next(
                    "请输入目标会话 ID（Telegram chat_id；群聊可用负数，话题群可用 chat_id#thread_id）"
                )
                return

            if step == "target_chat_id":
                state["target_chat_id"] = reply_text
                state["step"] = "confirm"
                provider_account = _build_provider_account_config(
                    provider=str(state.get("provider") or ""),
                    email=str(state.get("email") or ""),
                    password=str(state.get("password") or ""),
                    account_id=str(state.get("account_id") or ""),
                    display_name=str(state.get("display_name") or ""),
                    target_chat_id=str(state.get("target_chat_id") or ""),
                    platform_id=str(state.get("platform_id") or "telegram"),
                    message_type=str(state.get("message_type") or "friend"),
                    oauth2_client_id=str(state.get("oauth2_client_id") or ""),
                )
                state["pending_config"] = provider_account
                summary_lines = [
                    "请确认账号配置:",
                    f"- 类型: {provider_account.get('provider')}",
                    f"- 账号: {provider_account.get('imap_user')}",
                    f"- 账号ID: {provider_account.get('account_id')}",
                    f"- 目标会话: {provider_account.get('target_chat_id')}",
                    f"- 认证方式: {provider_account.get('auth_type') or 'password'}",
                    "回复 yes 保存，回复 no 取消。",
                ]
                await ask_next("\n".join(summary_lines))
                return

            if step == "confirm":
                if normalized in {"yes", "y", "是", "确认", "ok"}:
                    pending = state.get("pending_config")
                    if not isinstance(pending, dict):
                        await reply_event.send(
                            reply_event.plain_result("账号配置丢失，请重新添加。")
                        )
                        controller.stop()
                        return
                    try:
                        account = self._parse_account(pending, owner_id)
                    except Exception as exc:
                        await reply_event.send(
                            reply_event.plain_result(f"账号配置无效: {exc}")
                        )
                        controller.stop()
                        return
                    self.store.set_account_config(owner_id, account.account_id, pending)
                    self.store.save()
                    await reply_event.send(
                        reply_event.plain_result(
                            f"已保存账号 {account.account_id}。重启插件后会开始后台监听；也可以先执行 /mail check {account.account_id}。"
                        )
                    )
                    controller.stop()
                    return
                if normalized in {"no", "n", "否", "取消", "exit"}:
                    await reply_event.send(reply_event.plain_result("已取消添加账号。"))
                    controller.stop()
                    return
                await ask_next("请回复 yes 保存，或 no 取消。")
                return

            await ask_next("当前状态已失效，请重新执行 /mail add。")

        try:
            await wait_for_add(event)
        except TimeoutError:
            yield event.plain_result("⏰ 等待超时，添加账号已取消。")

    def _cmd_remove(self, args: list[str], owner_id: str) -> str:
        if len(args) != 1:
            return "用法: /mail remove <account_id>"
        removed = self.store.remove_account_config(owner_id, args[0])
        self.store.save()
        return "已删除账号。" if removed else "未找到该账号。"

    def _cmd_unblock(self, args: list[str], owner_id: str = OWNER_ID_FALLBACK) -> str:
        if len(args) != 2:
            return "用法: /mail unblock <account_id> <sender-or-domain>"
        account_id, sender = args
        self._account(account_id, owner_id)
        removed = self.store.unblock_sender(owner_id, account_id, sender)
        self.store.save()
        return "已解除屏蔽。" if removed else "未找到该屏蔽项。"

    def _mail_card(
        self,
        account: MailAccount,
        parsed: ParsedMail,
        token: str,
    ) -> MessageChain:
        preview = self._truncate(parsed.body_text, self._preview_length())
        text = (
            f"📬 {account.display_name}\n"
            f"From: {parsed.sender}\n"
            f"Subject: {parsed.subject}\n"
            f"Date: {parsed.date or '-'}\n"
            f"Attachments: {len(parsed.attachments)}\n\n"
            f"{preview or '(No text content)'}"
        )
        buttons = [
            [
                {"text": "More", "callback_data": f"{CALLBACK_PREFIX}:{token}:more:0"},
                {
                    "text": "Action",
                    "callback_data": f"{CALLBACK_PREFIX}:{token}:action",
                },
            ]
        ]
        if parsed.attachments:
            buttons.insert(
                0,
                [
                    {
                        "text": f"Attachments ({len(parsed.attachments)})",
                        "callback_data": f"{CALLBACK_PREFIX}:{token}:attachments",
                    }
                ],
            )
        return MessageChain([Plain(text)]).inline_keyboard(buttons)

    def _full_text_card(
        self,
        account: MailAccount,
        parsed: ParsedMail,
        token: str,
        page: int,
    ) -> MessageChain:
        body = parsed.body_text or "(No text content)"
        pages = self._paginate(body, self._page_size())
        page = max(0, min(page, len(pages) - 1))
        text = (
            f"📖 {account.display_name} · {parsed.subject}\n"
            f"Page {page + 1}/{len(pages)}\n\n"
            f"{pages[page]}"
        )
        nav = []
        if page > 0:
            nav.append(
                {
                    "text": "Prev",
                    "callback_data": f"{CALLBACK_PREFIX}:{token}:more:{page - 1}",
                }
            )
        if page < len(pages) - 1:
            nav.append(
                {
                    "text": "Next",
                    "callback_data": f"{CALLBACK_PREFIX}:{token}:more:{page + 1}",
                }
            )
        buttons = []
        if nav:
            buttons.append(nav)
        buttons.append(
            [{"text": "Back", "callback_data": f"{CALLBACK_PREFIX}:{token}:back"}]
        )
        return MessageChain([Plain(text)]).inline_keyboard(buttons)

    def _attachments_card(self, parsed: ParsedMail, token: str) -> MessageChain:
        if not parsed.attachments:
            return MessageChain([Plain("这封邮件没有附件。")])
        lines = ["📎 附件列表"]
        buttons = []
        for attachment in parsed.attachments:
            size = self._format_size(attachment.size)
            lines.append(f"{attachment.index + 1}. {attachment.filename} ({size})")
            buttons.append(
                [
                    {
                        "text": f"发送 {attachment.index + 1}",
                        "callback_data": f"{CALLBACK_PREFIX}:{token}:att:{attachment.index}",
                    }
                ]
            )
        buttons.append(
            [{"text": "Back", "callback_data": f"{CALLBACK_PREFIX}:{token}:back"}]
        )
        return MessageChain([Plain("\n".join(lines))]).inline_keyboard(buttons)

    def _action_card(self, parsed: ParsedMail, token: str) -> MessageChain:
        text = f"⚙️ 邮件操作\n{parsed.subject}\nFrom: {parsed.sender}"
        buttons = [
            [
                {"text": "Reply", "callback_data": f"{CALLBACK_PREFIX}:{token}:reply"},
                {
                    "text": "Unsubscribe",
                    "callback_data": f"{CALLBACK_PREFIX}:{token}:unsubscribe",
                },
            ],
            [
                {
                    "text": "Block Sender",
                    "callback_data": f"{CALLBACK_PREFIX}:{token}:block",
                },
                {
                    "text": "Archive",
                    "callback_data": f"{CALLBACK_PREFIX}:{token}:archive",
                },
            ],
            [
                {
                    "text": "Delete",
                    "callback_data": f"{CALLBACK_PREFIX}:{token}:delete",
                },
                {
                    "text": "Mark Read",
                    "callback_data": f"{CALLBACK_PREFIX}:{token}:read",
                },
                {
                    "text": "Mark Unread",
                    "callback_data": f"{CALLBACK_PREFIX}:{token}:unread",
                },
            ],
            [{"text": "Back", "callback_data": f"{CALLBACK_PREFIX}:{token}:back"}],
        ]
        return MessageChain([Plain(text)]).inline_keyboard(buttons)

    def _unsubscribe_card(self, parsed: ParsedMail, token: str) -> MessageChain:
        lines = [f"退订信息\n{parsed.subject}"]
        buttons = []
        for idx, url in enumerate(parsed.unsubscribe_urls, start=1):
            buttons.append([{"text": f"Open unsubscribe link {idx}", "url": url}])
        if parsed.unsubscribe_mailtos:
            lines.append("\nMailto:")
            lines.extend(parsed.unsubscribe_mailtos)
        if not buttons and not parsed.unsubscribe_mailtos:
            lines.append("\n未找到 List-Unsubscribe 或正文退订链接。")
        buttons.append(
            [{"text": "Back", "callback_data": f"{CALLBACK_PREFIX}:{token}:action"}]
        )
        return MessageChain([Plain("\n".join(lines))]).inline_keyboard(buttons)

    def _render_status(self, owner_id: str = OWNER_ID_FALLBACK) -> str:
        accounts = [
            account for account in self._accounts() if account.owner_id == owner_id
        ]
        if not accounts:
            return "未配置邮箱账号。"
        lines = ["Telegram Mail 状态"]
        for account in accounts:
            status = "enabled" if account.enabled else "disabled"
            mode = self._account_mode(account)
            last_check = self.store.last_check(owner_id, account.account_id) or "-"
            last_error = self.store.last_error(owner_id, account.account_id)
            oauth2_state = self.store.get_oauth2_state(owner_id, account.account_id)
            oauth2_status = (
                "authorized" if oauth2_state.get("access_token") else "unauthorized"
            )
            lines.append(
                f"- {account.account_id} ({account.display_name}): {status}, "
                f"mode={mode}, oauth2={oauth2_status}, last_check={last_check}, target={account.target_chat_id}"
            )
            if last_error:
                lines.append(f"  error={last_error}")
        return "\n".join(lines)

    def _render_blocklist(
        self, account_id: str, owner_id: str = OWNER_ID_FALLBACK
    ) -> str:
        account_ids = (
            [account_id]
            if account_id
            else [a.account_id for a in self._accounts() if a.owner_id == owner_id]
        )
        lines = ["本地屏蔽列表"]
        for current in account_ids:
            blocked = self.store.blocked_senders(owner_id, current)
            values = ", ".join(blocked) if blocked else "(empty)"
            lines.append(f"- {current}: {values}")
        return "\n".join(lines)

    def _action_done_text(self, parsed: ParsedMail, op: str) -> str:
        labels = {
            "archive": "已归档",
            "delete": "已删除",
            "read": "已标记已读",
            "unread": "已标记未读",
        }
        return f"{labels.get(op, '已完成')}: {parsed.subject}"

    def _accounts(self) -> list[MailAccount]:
        accounts: list[MailAccount] = []

        raw_accounts = self.config.get("accounts")
        if raw_accounts:
            if not isinstance(raw_accounts, list):
                raise ValueError("accounts/accounts_json 必须是账号数组")
            for item in raw_accounts:
                accounts.append(self._parse_account(item, OWNER_ID_FALLBACK))
        else:
            raw_json = self.config.get("accounts_json", "[]")
            try:
                raw_json_accounts = json.loads(raw_json or "[]")
            except json.JSONDecodeError as exc:
                raise ValueError(f"accounts_json 不是合法 JSON: {exc}") from exc
            if not isinstance(raw_json_accounts, list):
                raise ValueError("accounts/accounts_json 必须是账号数组")
            for item in raw_json_accounts:
                accounts.append(self._parse_account(item, OWNER_ID_FALLBACK))

        for owner_id, item in self.store.all_account_configs():
            accounts.append(self._parse_account(item, owner_id))

        return accounts

    def _account(self, account_id: str, owner_id: str | None = None) -> MailAccount:
        accounts = self._accounts()
        if owner_id is not None:
            for account in accounts:
                if account.account_id == account_id and account.owner_id == owner_id:
                    return account
            raise ValueError(f"未知账号: {account_id}")

        matches = [account for account in accounts if account.account_id == account_id]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"账号 {account_id} 存在多个所有者，请补充用户上下文")
        raise ValueError(f"未知账号: {account_id}")

    def _parse_account(self, item: dict[str, Any], owner_id: str) -> MailAccount:
        if not isinstance(item, dict):
            raise ValueError("账号配置必须是对象")
        account_id = str(item.get("account_id") or item.get("id") or "").strip()
        if not account_id:
            raise ValueError("账号缺少 account_id")
        imap_user = str(item.get("imap_user") or item.get("username") or "").strip()
        smtp_user = str(item.get("smtp_user") or imap_user).strip()
        provider = str(item.get("provider") or "").strip().lower()
        is_outlook = provider in {"outlook", "outlook.com", "hotmail", "microsoft"}
        default_auth_type = "oauth2" if is_outlook else "password"
        auth_type = str(item.get("auth_type") or default_auth_type)
        auth_type = auth_type.lower().replace("xoauth2", "oauth2")
        imap_auth_type = str(item.get("imap_auth_type") or auth_type).lower()
        smtp_auth_type = str(item.get("smtp_auth_type") or auth_type).lower()
        imap_folders = item.get("imap_folders") or ["INBOX"]
        if isinstance(imap_folders, str):
            imap_folders = [imap_folders]
        oauth2_state = {}
        store = getattr(self, "store", None)
        if store is not None:
            oauth2_state = store.get_oauth2_state(owner_id, account_id)
        account = MailAccount(
            owner_id=owner_id,
            account_id=account_id,
            display_name=str(item.get("display_name") or account_id),
            enabled=bool(item.get("enabled", True)),
            target_chat_id=str(item.get("target_chat_id") or "").strip(),
            platform_id=str(item.get("platform_id") or "telegram"),
            message_type=str(item.get("message_type") or "friend"),
            imap_host=str(
                item.get("imap_host") or ("outlook.office365.com" if is_outlook else "")
            ).strip(),
            imap_port=int(item.get("imap_port") or 993),
            imap_user=imap_user,
            imap_password=str(item.get("imap_password") or item.get("password") or ""),
            imap_auth_type=imap_auth_type,
            imap_tls=bool(item.get("imap_tls", True)),
            imap_folders=[str(folder) for folder in imap_folders],
            smtp_host=str(
                item.get("smtp_host") or ("smtp-mail.outlook.com" if is_outlook else "")
            ).strip(),
            smtp_port=int(item.get("smtp_port") or (587 if is_outlook else 465)),
            smtp_user=smtp_user,
            smtp_password=str(item.get("smtp_password") or item.get("password") or ""),
            smtp_auth_type=smtp_auth_type,
            smtp_tls=str(
                item.get("smtp_tls") or ("starttls" if is_outlook else "ssl")
            ).lower(),
            from_address=str(item.get("from_address") or smtp_user or imap_user),
            oauth2_access_token=str(
                item.get("oauth2_access_token")
                or oauth2_state.get("access_token")
                or ""
            ),
            oauth2_refresh_token=str(
                item.get("oauth2_refresh_token")
                or oauth2_state.get("refresh_token")
                or ""
            ),
            oauth2_client_id=str(
                item.get("oauth2_client_id") or self._config_str("oauth2_client_id", "")
            ),
            oauth2_client_secret=str(
                item.get("oauth2_client_secret")
                or self._config_str("oauth2_client_secret", "")
            ),
            oauth2_token_url=str(item.get("oauth2_token_url") or MICROSOFT_TOKEN_URL),
            oauth2_device_code_url=str(
                item.get("oauth2_device_code_url") or MICROSOFT_DEVICE_CODE_URL
            ),
            oauth2_scope=str(item.get("oauth2_scope") or MICROSOFT_OUTLOOK_SCOPE),
            oauth2_expires_at=float(
                item.get("oauth2_expires_at") or oauth2_state.get("expires_at") or 0
            ),
            archive_folder=str(item.get("archive_folder") or "Archive"),
            trash_folder=str(item.get("trash_folder") or "Trash"),
            poll_interval=int(
                item.get("poll_interval") or self._config_int("poll_interval", 300)
            ),
            realtime_enabled=self._item_bool(
                item,
                "realtime_enabled",
                self._config_bool("realtime_enabled", True),
            ),
            idle_timeout=int(
                item.get("idle_timeout")
                or self._config_int("idle_timeout", DEFAULT_IDLE_TIMEOUT)
            ),
        )
        self._validate_account(account)
        return account

    @staticmethod
    def _validate_account(account: MailAccount) -> None:
        if not account.enabled:
            return
        required = {
            "target_chat_id": account.target_chat_id,
            "imap_host": account.imap_host,
            "imap_user": account.imap_user,
        }
        if account.imap_auth_type not in {"password", "oauth2"}:
            required["imap_auth_type"] = ""
        if account.smtp_auth_type not in {"password", "oauth2"}:
            required["smtp_auth_type"] = ""
        if account.imap_auth_type == "password":
            required["imap_password"] = account.imap_password
        if account.smtp_host and account.smtp_auth_type == "password":
            required["smtp_password"] = account.smtp_password or account.imap_password
        if "oauth2" in {account.imap_auth_type, account.smtp_auth_type}:
            token = account.oauth2_access_token or account.oauth2_refresh_token
            client_id = account.oauth2_client_id
            required["oauth2 token 或 oauth2_client_id"] = token or client_id
            if account.oauth2_refresh_token:
                required["oauth2_client_id"] = account.oauth2_client_id
                required["oauth2_token_url"] = account.oauth2_token_url
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise ValueError(
                f"账号 {account.account_id} 缺少必填字段: {', '.join(missing)}"
            )

    def _parse_command_args(self, raw: str) -> list[str]:
        return parse_mail_command_args(raw)

    def _callback_chat_id(self, event: TelegramCallbackQueryEvent) -> str:
        if not event.message:
            return event.get_sender_id()
        chat_id = str(event.message.chat.id)
        thread_id = getattr(event.message, "message_thread_id", None)
        return f"{chat_id}#{thread_id}" if thread_id else chat_id

    @staticmethod
    def _message_type(value: str) -> MessageType:
        if value.lower() in {"group", "group_message"}:
            return MessageType.GROUP_MESSAGE
        return MessageType.FRIEND_MESSAGE

    def _set_account_mode(self, account: MailAccount, mode: str) -> None:
        for folder in account.imap_folders:
            self._set_folder_mode(account, folder, mode)

    def _set_folder_mode(self, account: MailAccount, folder: str, mode: str) -> None:
        self.folder_modes[(self._account_state_key(account), folder)] = mode

    def _account_mode(self, account: MailAccount) -> str:
        if not account.enabled:
            return "-"
        if not account.realtime_enabled:
            return "polling"
        modes = {
            self.folder_modes.get(
                (self._account_state_key(account), folder), "starting"
            )
            for folder in account.imap_folders
        }
        if "polling fallback" in modes:
            return "polling fallback"
        if modes == {"idle"}:
            return "idle"
        return ", ".join(sorted(modes))

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        value = value.strip()
        if len(value) <= limit:
            return value
        return value[: limit - 1].rstrip() + "…"

    @staticmethod
    def _paginate(value: str, size: int) -> list[str]:
        value = value.strip() or "(No text content)"
        return [value[i : i + size] for i in range(0, len(value), size)] or [value]

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / 1024 / 1024:.1f} MB"

    @staticmethod
    def _safe_filename(value: str) -> str:
        value = re.sub(r"[\\/:*?\"<>|]+", "_", value).strip()
        return value or "attachment.bin"

    @staticmethod
    def _account_state_key(account: MailAccount) -> str:
        return f"{account.owner_id}:{account.account_id}"

    def _event_owner_id(self, event: AstrMessageEvent) -> str:
        return _normalize_owner_id(event.get_sender_id() or event.session.session_id)

    def _preview_length(self) -> int:
        return self._config_int("preview_length", DEFAULT_PREVIEW_LENGTH)

    def _page_size(self) -> int:
        return self._config_int("page_size", DEFAULT_PAGE_SIZE)

    def _config_int(self, key: str, default: int) -> int:
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return default

    def _config_bool(self, key: str, default: bool) -> bool:
        value = self.config.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _config_str(self, key: str, default: str) -> str:
        value = self.config.get(key, default)
        return default if value is None else str(value)

    @staticmethod
    def _item_bool(item: dict[str, Any], key: str, default: bool) -> bool:
        if key not in item:
            return default
        value = item.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _help_text() -> str:
        return (
            "Telegram Mail 命令:\n"
            "/mail status\n"
            "/mail check [account_id]\n"
            "/mail add\n"
            "/mail send <account_id> <to> | <subject> | <body>\n"
            "/mail reply <token> <body>\n"
            "/mail oauth <account_id>\n"
            "/mail remove <account_id>\n"
            "/mail blocklist [account_id]\n"
            "/mail unblock <account_id> <sender-or-domain>"
        )


class OAuth2AuthorizationPending(RuntimeError):
    pass


class OAuth2SlowDown(RuntimeError):
    pass


def _normalize_owner_id(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return OWNER_ID_FALLBACK
    if ":" in value:
        return value.rsplit(":", 1)[-1].strip() or OWNER_ID_FALLBACK
    return value
