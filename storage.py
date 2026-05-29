from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any


DEFAULT_OWNER_ID = "default"


class JsonStore:
    def __init__(self, data_dir: Path, *, max_tokens: int = 500) -> None:
        self.data_dir = data_dir
        self.cache_dir = data_dir / "messages"
        self.state_path = data_dir / "state.json"
        self.max_tokens = max_tokens
        self.data: dict[str, Any] = {
            "users": {},
            "seen": {},
            "initialized": {},
            "tokens": {},
            "oauth2": {},
            "blocked": {},
            "last_errors": {},
            "last_checks": {},
        }

    def load(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self.save()
            return
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update(loaded)
                self._migrate_legacy_state()
        except (OSError, json.JSONDecodeError):
            self.save()

    def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self.state_path)

    def get_seen(self, owner_id: str, account_id: str, folder: str) -> set[str]:
        return set(
            self._user_bucket(owner_id)
            .setdefault("seen", {})
            .get(account_id, {})
            .get(folder, [])
        )

    def set_seen(
        self, owner_id: str, account_id: str, folder: str, uids: set[str]
    ) -> None:
        account_seen = (
            self._user_bucket(owner_id).setdefault("seen", {}).setdefault(account_id, {})
        )
        account_seen[folder] = sorted(uids, key=_uid_sort_key)

    def add_seen(self, owner_id: str, account_id: str, folder: str, uid: str) -> None:
        seen = self.get_seen(owner_id, account_id, folder)
        seen.add(str(uid))
        self.set_seen(owner_id, account_id, folder, seen)

    def is_initialized(self, owner_id: str, account_id: str, folder: str) -> bool:
        return bool(
            self._user_bucket(owner_id)
            .setdefault("initialized", {})
            .get(account_id, {})
            .get(folder, False)
        )

    def set_initialized(self, owner_id: str, account_id: str, folder: str) -> None:
        account_state = (
            self._user_bucket(owner_id)
            .setdefault("initialized", {})
            .setdefault(account_id, {})
        )
        account_state[folder] = True

    def save_raw_message(self, owner_id: str, raw: bytes) -> str:
        name = f"{uuid.uuid4().hex}.eml"
        owner_dir = self.cache_dir / _safe_owner_path(owner_id)
        owner_dir.mkdir(parents=True, exist_ok=True)
        path = owner_dir / name
        path.write_bytes(raw)
        return str(path)

    def put_token(self, owner_id: str, payload: dict[str, Any]) -> str:
        token = uuid.uuid4().hex[:12]
        tokens = self._user_bucket(owner_id).setdefault("tokens", {})
        tokens[token] = {"created_at": time.time(), **payload}
        self._trim_tokens(owner_id)
        return token

    def get_token(self, owner_id: str, token: str) -> dict[str, Any] | None:
        payload = self._user_bucket(owner_id).setdefault("tokens", {}).get(token)
        return payload if isinstance(payload, dict) else None

    def block_sender(self, owner_id: str, account_id: str, sender: str) -> None:
        sender = sender.lower().strip()
        if not sender:
            return
        blocked = (
            self._user_bucket(owner_id).setdefault("blocked", {}).setdefault(account_id, [])
        )
        if sender not in blocked:
            blocked.append(sender)
            blocked.sort()

    def unblock_sender(self, owner_id: str, account_id: str, sender: str) -> bool:
        sender = sender.lower().strip()
        blocked = (
            self._user_bucket(owner_id).setdefault("blocked", {}).setdefault(account_id, [])
        )
        if sender not in blocked:
            return False
        blocked.remove(sender)
        return True

    def blocked_senders(self, owner_id: str, account_id: str) -> list[str]:
        return list(
            self._user_bucket(owner_id).setdefault("blocked", {}).get(account_id, [])
        )

    def is_blocked(self, owner_id: str, account_id: str, sender_email: str) -> bool:
        sender_email = sender_email.lower().strip()
        if not sender_email:
            return False
        domain = sender_email.split("@", 1)[1] if "@" in sender_email else sender_email
        blocked = set(self.blocked_senders(owner_id, account_id))
        return sender_email in blocked or domain in blocked or f"@{domain}" in blocked

    def set_last_error(self, owner_id: str, account_id: str, error: str) -> None:
        self._user_bucket(owner_id).setdefault("last_errors", {})[account_id] = error[:500]

    def clear_last_error(self, owner_id: str, account_id: str) -> None:
        self._user_bucket(owner_id).setdefault("last_errors", {}).pop(account_id, None)

    def set_last_check(self, owner_id: str, account_id: str, value: str) -> None:
        self._user_bucket(owner_id).setdefault("last_checks", {})[account_id] = value

    def last_error(self, owner_id: str, account_id: str) -> str:
        return str(
            self._user_bucket(owner_id).setdefault("last_errors", {}).get(account_id, "")
        )

    def last_check(self, owner_id: str, account_id: str) -> str:
        return str(
            self._user_bucket(owner_id).setdefault("last_checks", {}).get(account_id, "")
        )

    def _trim_tokens(self, owner_id: str) -> None:
        tokens = self._user_bucket(owner_id).setdefault("tokens", {})
        if len(tokens) <= self.max_tokens:
            return
        ordered = sorted(tokens.items(), key=lambda item: item[1].get("created_at", 0))
        for token, _ in ordered[: len(tokens) - self.max_tokens]:
            tokens.pop(token, None)

    def get_oauth2_state(self, owner_id: str, account_id: str) -> dict[str, Any]:
        payload = self._user_bucket(owner_id).setdefault("oauth2", {}).get(account_id, {})
        return payload if isinstance(payload, dict) else {}

    def set_oauth2_state(
        self, owner_id: str, account_id: str, payload: dict[str, Any]
    ) -> None:
        self._user_bucket(owner_id).setdefault("oauth2", {})[account_id] = payload

    def clear_oauth2_state(self, owner_id: str, account_id: str) -> None:
        self._user_bucket(owner_id).setdefault("oauth2", {}).pop(account_id, None)

    def owner_ids(self) -> list[str]:
        return sorted(self.data.setdefault("users", {}).keys())

    def account_configs(self, owner_id: str) -> list[dict[str, Any]]:
        accounts = self._user_bucket(owner_id).setdefault("accounts", {})
        return [dict(item) for item in accounts.values() if isinstance(item, dict)]

    def all_account_configs(self) -> list[tuple[str, dict[str, Any]]]:
        result: list[tuple[str, dict[str, Any]]] = []
        for owner_id in self.owner_ids():
            for item in self.account_configs(owner_id):
                result.append((owner_id, item))
        return result

    def set_account_config(
        self, owner_id: str, account_id: str, payload: dict[str, Any]
    ) -> None:
        account = dict(payload)
        account["account_id"] = account_id
        self._user_bucket(owner_id).setdefault("accounts", {})[account_id] = account

    def remove_account_config(self, owner_id: str, account_id: str) -> bool:
        bucket = self._user_bucket(owner_id)
        existed = bucket.setdefault("accounts", {}).pop(account_id, None) is not None
        for key in (
            "seen",
            "initialized",
            "oauth2",
            "blocked",
            "last_errors",
            "last_checks",
        ):
            bucket.setdefault(key, {}).pop(account_id, None)
        return existed

    def _user_bucket(self, owner_id: str) -> dict[str, Any]:
        owner_id = str(owner_id or DEFAULT_OWNER_ID)
        users = self.data.setdefault("users", {})
        bucket = users.setdefault(owner_id, {})
        for key in (
            "accounts",
            "seen",
            "initialized",
            "tokens",
            "oauth2",
            "blocked",
            "last_errors",
            "last_checks",
        ):
            bucket.setdefault(key, {})
        return bucket

    def _migrate_legacy_state(self) -> None:
        if not any(self.data.get(key) for key in _LEGACY_KEYS):
            return
        bucket = self._user_bucket(DEFAULT_OWNER_ID)
        for key in _LEGACY_KEYS:
            value = self.data.get(key)
            if value:
                bucket.setdefault(key, {}).update(value)
            self.data[key] = {}


_LEGACY_KEYS = (
    "seen",
    "initialized",
    "tokens",
    "oauth2",
    "blocked",
    "last_errors",
    "last_checks",
)


def _safe_owner_path(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or DEFAULT_OWNER_ID)).strip("._")
    return value or DEFAULT_OWNER_ID


def _uid_sort_key(value: str) -> tuple[int, str]:
    try:
        return int(value), value
    except ValueError:
        return 0, value
