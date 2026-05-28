from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


class JsonStore:
    def __init__(self, data_dir: Path, *, max_tokens: int = 500) -> None:
        self.data_dir = data_dir
        self.cache_dir = data_dir / "messages"
        self.state_path = data_dir / "state.json"
        self.max_tokens = max_tokens
        self.data: dict[str, Any] = {
            "seen": {},
            "initialized": {},
            "tokens": {},
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

    def get_seen(self, account_id: str, folder: str) -> set[str]:
        return set(self.data.setdefault("seen", {}).get(account_id, {}).get(folder, []))

    def set_seen(self, account_id: str, folder: str, uids: set[str]) -> None:
        account_seen = self.data.setdefault("seen", {}).setdefault(account_id, {})
        account_seen[folder] = sorted(uids, key=_uid_sort_key)

    def add_seen(self, account_id: str, folder: str, uid: str) -> None:
        seen = self.get_seen(account_id, folder)
        seen.add(str(uid))
        self.set_seen(account_id, folder, seen)

    def is_initialized(self, account_id: str, folder: str) -> bool:
        return bool(
            self.data.setdefault("initialized", {})
            .get(account_id, {})
            .get(folder, False)
        )

    def set_initialized(self, account_id: str, folder: str) -> None:
        account_state = self.data.setdefault("initialized", {}).setdefault(
            account_id, {}
        )
        account_state[folder] = True

    def save_raw_message(self, raw: bytes) -> str:
        name = f"{uuid.uuid4().hex}.eml"
        path = self.cache_dir / name
        path.write_bytes(raw)
        return str(path)

    def put_token(self, payload: dict[str, Any]) -> str:
        token = uuid.uuid4().hex[:12]
        tokens = self.data.setdefault("tokens", {})
        tokens[token] = {"created_at": time.time(), **payload}
        self._trim_tokens()
        return token

    def get_token(self, token: str) -> dict[str, Any] | None:
        payload = self.data.setdefault("tokens", {}).get(token)
        return payload if isinstance(payload, dict) else None

    def block_sender(self, account_id: str, sender: str) -> None:
        sender = sender.lower().strip()
        if not sender:
            return
        blocked = self.data.setdefault("blocked", {}).setdefault(account_id, [])
        if sender not in blocked:
            blocked.append(sender)
            blocked.sort()

    def unblock_sender(self, account_id: str, sender: str) -> bool:
        sender = sender.lower().strip()
        blocked = self.data.setdefault("blocked", {}).setdefault(account_id, [])
        if sender not in blocked:
            return False
        blocked.remove(sender)
        return True

    def blocked_senders(self, account_id: str) -> list[str]:
        return list(self.data.setdefault("blocked", {}).get(account_id, []))

    def is_blocked(self, account_id: str, sender_email: str) -> bool:
        sender_email = sender_email.lower().strip()
        if not sender_email:
            return False
        domain = sender_email.split("@", 1)[1] if "@" in sender_email else sender_email
        blocked = set(self.blocked_senders(account_id))
        return sender_email in blocked or domain in blocked or f"@{domain}" in blocked

    def set_last_error(self, account_id: str, error: str) -> None:
        self.data.setdefault("last_errors", {})[account_id] = error[:500]

    def clear_last_error(self, account_id: str) -> None:
        self.data.setdefault("last_errors", {}).pop(account_id, None)

    def set_last_check(self, account_id: str, value: str) -> None:
        self.data.setdefault("last_checks", {})[account_id] = value

    def last_error(self, account_id: str) -> str:
        return str(self.data.setdefault("last_errors", {}).get(account_id, ""))

    def last_check(self, account_id: str) -> str:
        return str(self.data.setdefault("last_checks", {}).get(account_id, ""))

    def _trim_tokens(self) -> None:
        tokens = self.data.setdefault("tokens", {})
        if len(tokens) <= self.max_tokens:
            return
        ordered = sorted(tokens.items(), key=lambda item: item[1].get("created_at", 0))
        for token, _ in ordered[: len(tokens) - self.max_tokens]:
            tokens.pop(token, None)


def _uid_sort_key(value: str) -> tuple[int, str]:
    try:
        return int(value), value
    except ValueError:
        return 0, value
