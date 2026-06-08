from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

DEFAULT_OWNER_ID = "default"
SCHEMA_VERSION = 1
STATE_MIGRATION_MARKER = "_telegram_mail_db_migration"
STATE_MIGRATION_META_KEY = "state_json_migration"


class JsonStore:
    def __init__(self, data_dir: Path, *, max_tokens: int = 500) -> None:
        self.data_dir = data_dir
        self.cache_dir = data_dir / "messages"
        self.state_path = data_dir / "state.json"
        self.db_path = data_dir / "mail_state.db"
        self.max_tokens = max_tokens
        self._conn: sqlite3.Connection | None = None
        self._schema_ready = False
        self._lock = threading.RLock()

    def load(self) -> None:
        with self._lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            conn = self._db()
            self._migrate_state_json(conn)
            conn.commit()

    def save(self) -> None:
        with self._lock:
            if self._conn is None:
                self.load()
                return
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is None:
                return
            self._conn.commit()
            self._conn.close()
            self._conn = None
            self._schema_ready = False

    def get_seen(self, owner_id: str, account_id: str, folder: str) -> set[str]:
        with self._lock:
            rows = self._db().execute(
                """
                SELECT uid
                FROM seen
                WHERE owner_id = ? AND account_id = ? AND folder = ?
                """,
                (_owner_id(owner_id), str(account_id), str(folder)),
            )
            return {str(row["uid"]) for row in rows}

    def set_seen(
        self, owner_id: str, account_id: str, folder: str, uids: set[str]
    ) -> None:
        owner_id = _owner_id(owner_id)
        account_id = str(account_id)
        folder = str(folder)
        with self._lock:
            conn = self._db()
            conn.execute(
                """
                DELETE FROM seen
                WHERE owner_id = ? AND account_id = ? AND folder = ?
                """,
                (owner_id, account_id, folder),
            )
            conn.executemany(
                """
                INSERT OR IGNORE INTO seen(owner_id, account_id, folder, uid)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (owner_id, account_id, folder, str(uid))
                    for uid in sorted(uids, key=_uid_sort_key)
                ],
            )
            conn.commit()

    def add_seen(self, owner_id: str, account_id: str, folder: str, uid: str) -> None:
        with self._lock:
            self._db().execute(
                """
                INSERT OR IGNORE INTO seen(owner_id, account_id, folder, uid)
                VALUES (?, ?, ?, ?)
                """,
                (_owner_id(owner_id), str(account_id), str(folder), str(uid)),
            )
            self._db().commit()

    def is_initialized(self, owner_id: str, account_id: str, folder: str) -> bool:
        with self._lock:
            row = (
                self._db()
                .execute(
                    """
                SELECT 1
                FROM initialized
                WHERE owner_id = ? AND account_id = ? AND folder = ?
                LIMIT 1
                """,
                    (_owner_id(owner_id), str(account_id), str(folder)),
                )
                .fetchone()
            )
            return row is not None

    def set_initialized(self, owner_id: str, account_id: str, folder: str) -> None:
        with self._lock:
            conn = self._db()
            conn.execute(
                """
                INSERT OR IGNORE INTO initialized(owner_id, account_id, folder)
                VALUES (?, ?, ?)
                """,
                (_owner_id(owner_id), str(account_id), str(folder)),
            )
            conn.commit()

    def save_raw_message(self, owner_id: str, raw: bytes) -> str:
        name = f"{uuid.uuid4().hex}.eml"
        owner_dir = self.cache_dir / _safe_owner_path(owner_id)
        owner_dir.mkdir(parents=True, exist_ok=True)
        path = owner_dir / name
        path.write_bytes(raw)
        return str(path)

    def put_token(self, owner_id: str, payload: dict[str, Any]) -> str:
        owner_id = _owner_id(owner_id)
        token_payload = {"created_at": time.time(), **payload}
        created_at = _float_value(token_payload.get("created_at"), time.time())
        payload_json = _dump_json(token_payload)
        with self._lock:
            conn = self._db()
            for _ in range(20):
                token = uuid.uuid4().hex[:12]
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO tokens(
                        owner_id, token, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (owner_id, token, payload_json, created_at),
                )
                if cursor.rowcount:
                    self._trim_tokens(owner_id)
                    conn.commit()
                    return token
            raise RuntimeError("failed to allocate unique mail token")

    def get_token(self, owner_id: str, token: str) -> dict[str, Any] | None:
        with self._lock:
            row = (
                self._db()
                .execute(
                    """
                SELECT payload_json
                FROM tokens
                WHERE owner_id = ? AND token = ?
                """,
                    (_owner_id(owner_id), str(token)),
                )
                .fetchone()
            )
        if row is None:
            return None
        payload = _load_json(row["payload_json"], {})
        return payload if isinstance(payload, dict) else None

    def block_sender(self, owner_id: str, account_id: str, sender: str) -> None:
        sender = sender.lower().strip()
        if not sender:
            return
        with self._lock:
            conn = self._db()
            conn.execute(
                """
                INSERT OR IGNORE INTO blocked(owner_id, account_id, sender)
                VALUES (?, ?, ?)
                """,
                (_owner_id(owner_id), str(account_id), sender),
            )
            conn.commit()

    def unblock_sender(self, owner_id: str, account_id: str, sender: str) -> bool:
        sender = sender.lower().strip()
        with self._lock:
            conn = self._db()
            cursor = conn.execute(
                """
                DELETE FROM blocked
                WHERE owner_id = ? AND account_id = ? AND sender = ?
                """,
                (_owner_id(owner_id), str(account_id), sender),
            )
            conn.commit()
            return cursor.rowcount > 0

    def blocked_senders(self, owner_id: str, account_id: str) -> list[str]:
        with self._lock:
            rows = self._db().execute(
                """
                SELECT sender
                FROM blocked
                WHERE owner_id = ? AND account_id = ?
                ORDER BY sender
                """,
                (_owner_id(owner_id), str(account_id)),
            )
            return [str(row["sender"]) for row in rows]

    def is_blocked(self, owner_id: str, account_id: str, sender_email: str) -> bool:
        sender_email = sender_email.lower().strip()
        if not sender_email:
            return False
        domain = sender_email.split("@", 1)[1] if "@" in sender_email else sender_email
        blocked = set(self.blocked_senders(owner_id, account_id))
        return sender_email in blocked or domain in blocked or f"@{domain}" in blocked

    def set_last_error(self, owner_id: str, account_id: str, error: str) -> None:
        with self._lock:
            conn = self._db()
            self._upsert_account_state(
                conn,
                _owner_id(owner_id),
                str(account_id),
                last_error=str(error)[:500],
            )
            conn.commit()

    def clear_last_error(self, owner_id: str, account_id: str) -> None:
        self.set_last_error(owner_id, account_id, "")

    def set_last_check(self, owner_id: str, account_id: str, value: str) -> None:
        with self._lock:
            conn = self._db()
            self._upsert_account_state(
                conn,
                _owner_id(owner_id),
                str(account_id),
                last_check=str(value),
            )
            conn.commit()

    def last_error(self, owner_id: str, account_id: str) -> str:
        with self._lock:
            row = (
                self._db()
                .execute(
                    """
                SELECT last_error
                FROM account_state
                WHERE owner_id = ? AND account_id = ?
                """,
                    (_owner_id(owner_id), str(account_id)),
                )
                .fetchone()
            )
            return str(row["last_error"]) if row else ""

    def last_check(self, owner_id: str, account_id: str) -> str:
        with self._lock:
            row = (
                self._db()
                .execute(
                    """
                SELECT last_check
                FROM account_state
                WHERE owner_id = ? AND account_id = ?
                """,
                    (_owner_id(owner_id), str(account_id)),
                )
                .fetchone()
            )
            return str(row["last_check"]) if row else ""

    def get_oauth2_state(self, owner_id: str, account_id: str) -> dict[str, Any]:
        with self._lock:
            row = (
                self._db()
                .execute(
                    """
                SELECT payload_json
                FROM oauth2
                WHERE owner_id = ? AND account_id = ?
                """,
                    (_owner_id(owner_id), str(account_id)),
                )
                .fetchone()
            )
        if row is None:
            return {}
        payload = _load_json(row["payload_json"], {})
        return payload if isinstance(payload, dict) else {}

    def set_oauth2_state(
        self, owner_id: str, account_id: str, payload: dict[str, Any]
    ) -> None:
        with self._lock:
            conn = self._db()
            conn.execute(
                """
                INSERT INTO oauth2(owner_id, account_id, payload_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(owner_id, account_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    _owner_id(owner_id),
                    str(account_id),
                    _dump_json(payload),
                    time.time(),
                ),
            )
            conn.commit()

    def clear_oauth2_state(self, owner_id: str, account_id: str) -> None:
        with self._lock:
            conn = self._db()
            conn.execute(
                """
                DELETE FROM oauth2
                WHERE owner_id = ? AND account_id = ?
                """,
                (_owner_id(owner_id), str(account_id)),
            )
            conn.commit()

    def owner_ids(self) -> list[str]:
        with self._lock:
            rows = self._db().execute(
                """
                SELECT owner_id FROM accounts
                UNION SELECT owner_id FROM seen
                UNION SELECT owner_id FROM initialized
                UNION SELECT owner_id FROM tokens
                UNION SELECT owner_id FROM oauth2
                UNION SELECT owner_id FROM blocked
                UNION SELECT owner_id FROM account_state
                ORDER BY owner_id
                """
            )
            return [str(row["owner_id"]) for row in rows]

    def account_configs(self, owner_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db().execute(
                """
                SELECT account_id, payload_json
                FROM accounts
                WHERE owner_id = ?
                ORDER BY account_id
                """,
                (_owner_id(owner_id),),
            )
            return [
                payload
                for payload in (
                    self._account_payload(row["account_id"], row["payload_json"])
                    for row in rows
                )
                if payload is not None
            ]

    def all_account_configs(self) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            rows = self._db().execute(
                """
                SELECT owner_id, account_id, payload_json
                FROM accounts
                ORDER BY owner_id, account_id
                """
            )
            result = []
            for row in rows:
                payload = self._account_payload(row["account_id"], row["payload_json"])
                if payload is not None:
                    result.append((str(row["owner_id"]), payload))
            return result

    def has_account_config(self, owner_id: str, account_id: str) -> bool:
        with self._lock:
            row = (
                self._db()
                .execute(
                    """
                SELECT 1
                FROM accounts
                WHERE owner_id = ? AND account_id = ?
                LIMIT 1
                """,
                    (_owner_id(owner_id), str(account_id)),
                )
                .fetchone()
            )
            return row is not None

    def set_account_config(
        self, owner_id: str, account_id: str, payload: dict[str, Any]
    ) -> None:
        account = dict(payload)
        account["account_id"] = account_id
        with self._lock:
            conn = self._db()
            self._insert_account_config(
                conn,
                _owner_id(owner_id),
                str(account_id),
                account,
                replace=True,
            )
            conn.commit()

    def set_account_config_if_absent(
        self, owner_id: str, account_id: str, payload: dict[str, Any]
    ) -> bool:
        account = dict(payload)
        account["account_id"] = account_id
        with self._lock:
            conn = self._db()
            inserted = self._insert_account_config(
                conn,
                _owner_id(owner_id),
                str(account_id),
                account,
                replace=False,
            )
            conn.commit()
            return inserted

    def remove_account_config(self, owner_id: str, account_id: str) -> bool:
        owner_id = _owner_id(owner_id)
        account_id = str(account_id)
        with self._lock:
            conn = self._db()
            cursor = conn.execute(
                """
                DELETE FROM accounts
                WHERE owner_id = ? AND account_id = ?
                """,
                (owner_id, account_id),
            )
            for table in ("seen", "initialized", "oauth2", "blocked", "account_state"):
                conn.execute(
                    f"DELETE FROM {table} WHERE owner_id = ? AND account_id = ?",
                    (owner_id, account_id),
                )
            conn.commit()
            return cursor.rowcount > 0

    def get_meta(self, key: str) -> Any:
        with self._lock:
            return self._get_meta(self._db(), key)

    def set_meta(self, key: str, value: Any) -> None:
        with self._lock:
            conn = self._db()
            self._set_meta(conn, key, value)
            conn.commit()

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.execute("PRAGMA journal_mode = WAL")
        if not self._schema_ready:
            self._init_schema(self._conn)
            self._schema_ready = True
        return self._conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                owner_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(owner_id, account_id)
            );
            CREATE TABLE IF NOT EXISTS seen (
                owner_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                folder TEXT NOT NULL,
                uid TEXT NOT NULL,
                PRIMARY KEY(owner_id, account_id, folder, uid)
            );
            CREATE TABLE IF NOT EXISTS initialized (
                owner_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                folder TEXT NOT NULL,
                PRIMARY KEY(owner_id, account_id, folder)
            );
            CREATE TABLE IF NOT EXISTS tokens (
                owner_id TEXT NOT NULL,
                token TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(owner_id, token)
            );
            CREATE TABLE IF NOT EXISTS oauth2 (
                owner_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(owner_id, account_id)
            );
            CREATE TABLE IF NOT EXISTS blocked (
                owner_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                sender TEXT NOT NULL,
                PRIMARY KEY(owner_id, account_id, sender)
            );
            CREATE TABLE IF NOT EXISTS account_state (
                owner_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                last_error TEXT NOT NULL DEFAULT '',
                last_check TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(owner_id, account_id)
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            );
            """
        )
        self._set_meta(conn, "schema_version", SCHEMA_VERSION)
        conn.commit()

    def _migrate_state_json(self, conn: sqlite3.Connection) -> None:
        state = self._read_state_json()
        if state is None:
            return

        marker = state.get(STATE_MIGRATION_MARKER)
        if _migration_completed(marker):
            if self._get_meta(conn, STATE_MIGRATION_META_KEY) is None:
                self._set_meta(conn, STATE_MIGRATION_META_KEY, marker)
            return

        meta = self._get_meta(conn, STATE_MIGRATION_META_KEY)
        if _migration_completed(meta):
            self._write_state_migration_marker(state, meta)
            return

        self._import_state_json(conn, state)
        marker = {
            "completed": True,
            "schema_version": SCHEMA_VERSION,
            "db_file": self.db_path.name,
            "completed_at": time.time(),
        }
        self._set_meta(conn, STATE_MIGRATION_META_KEY, marker)
        conn.commit()
        self._write_state_migration_marker(state, marker)

    def _read_state_json(self) -> dict[str, Any] | None:
        if not self.state_path.exists():
            return None
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return loaded if isinstance(loaded, dict) else None

    def _write_state_migration_marker(
        self, state: dict[str, Any], marker: dict[str, Any]
    ) -> None:
        state = dict(state)
        state[STATE_MIGRATION_MARKER] = marker
        tmp_path = self.state_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self.state_path)

    def _import_state_json(
        self, conn: sqlite3.Connection, state: dict[str, Any]
    ) -> None:
        imported_owners: set[str] = set()
        users = state.get("users")
        if isinstance(users, dict):
            for owner_id, bucket in users.items():
                if isinstance(bucket, dict):
                    self._import_user_bucket(conn, str(owner_id), bucket)
                    imported_owners.add(_owner_id(str(owner_id)))

        legacy_bucket = {
            key: state.get(key) for key in _LEGACY_KEYS if state.get(key) is not None
        }
        if legacy_bucket:
            self._import_user_bucket(conn, DEFAULT_OWNER_ID, legacy_bucket)
            imported_owners.add(DEFAULT_OWNER_ID)

        for owner_id in imported_owners:
            self._trim_tokens(owner_id)

    def _import_user_bucket(
        self,
        conn: sqlite3.Connection,
        owner_id: str,
        bucket: dict[str, Any],
    ) -> None:
        owner_id = _owner_id(owner_id)
        self._import_accounts(conn, owner_id, bucket.get("accounts"))
        self._import_seen(conn, owner_id, bucket.get("seen"))
        self._import_initialized(conn, owner_id, bucket.get("initialized"))
        self._import_tokens(conn, owner_id, bucket.get("tokens"))
        self._import_oauth2(conn, owner_id, bucket.get("oauth2"))
        self._import_blocked(conn, owner_id, bucket.get("blocked"))
        self._import_account_states(
            conn,
            owner_id,
            bucket.get("last_errors"),
            bucket.get("last_checks"),
        )

    def _import_accounts(
        self, conn: sqlite3.Connection, owner_id: str, accounts: Any
    ) -> None:
        for account_id, payload in _iter_account_payloads(accounts):
            self._insert_account_config(
                conn, owner_id, account_id, payload, replace=False
            )

    def _import_seen(self, conn: sqlite3.Connection, owner_id: str, seen: Any) -> None:
        if not isinstance(seen, dict):
            return
        for account_id, folders in seen.items():
            if not isinstance(folders, dict):
                continue
            for folder, uids in folders.items():
                rows = [
                    (owner_id, str(account_id), str(folder), uid)
                    for uid in _iter_string_values(uids)
                ]
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO seen(owner_id, account_id, folder, uid)
                    VALUES (?, ?, ?, ?)
                    """,
                    rows,
                )

    def _import_initialized(
        self, conn: sqlite3.Connection, owner_id: str, initialized: Any
    ) -> None:
        if not isinstance(initialized, dict):
            return
        rows: list[tuple[str, str, str]] = []
        for account_id, folders in initialized.items():
            if isinstance(folders, dict):
                rows.extend(
                    (owner_id, str(account_id), str(folder))
                    for folder, value in folders.items()
                    if value
                )
            else:
                rows.extend(
                    (owner_id, str(account_id), folder)
                    for folder in _iter_string_values(folders)
                )
        conn.executemany(
            """
            INSERT OR IGNORE INTO initialized(owner_id, account_id, folder)
            VALUES (?, ?, ?)
            """,
            rows,
        )

    def _import_tokens(
        self, conn: sqlite3.Connection, owner_id: str, tokens: Any
    ) -> None:
        if not isinstance(tokens, dict):
            return
        rows = []
        now = time.time()
        for token, payload in tokens.items():
            if not isinstance(payload, dict):
                continue
            rows.append(
                (
                    owner_id,
                    str(token),
                    _dump_json(payload),
                    _float_value(payload.get("created_at"), now),
                )
            )
        conn.executemany(
            """
            INSERT OR IGNORE INTO tokens(owner_id, token, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )

    def _import_oauth2(
        self, conn: sqlite3.Connection, owner_id: str, oauth2: Any
    ) -> None:
        if not isinstance(oauth2, dict):
            return
        rows = []
        now = time.time()
        for account_id, payload in oauth2.items():
            if isinstance(payload, dict):
                rows.append((owner_id, str(account_id), _dump_json(payload), now))
        conn.executemany(
            """
            INSERT OR IGNORE INTO oauth2(
                owner_id, account_id, payload_json, updated_at
            )
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )

    def _import_blocked(
        self, conn: sqlite3.Connection, owner_id: str, blocked: Any
    ) -> None:
        if not isinstance(blocked, dict):
            return
        rows = []
        for account_id, senders in blocked.items():
            rows.extend(
                (owner_id, str(account_id), sender.lower().strip())
                for sender in _iter_string_values(senders)
                if sender.lower().strip()
            )
        conn.executemany(
            """
            INSERT OR IGNORE INTO blocked(owner_id, account_id, sender)
            VALUES (?, ?, ?)
            """,
            rows,
        )

    def _import_account_states(
        self,
        conn: sqlite3.Connection,
        owner_id: str,
        last_errors: Any,
        last_checks: Any,
    ) -> None:
        error_by_account = {}
        check_by_account = {}
        if isinstance(last_errors, dict):
            error_by_account = {
                str(account_id): value for account_id, value in last_errors.items()
            }
        if isinstance(last_checks, dict):
            check_by_account = {
                str(account_id): value for account_id, value in last_checks.items()
            }

        for account_id in set(error_by_account) | set(check_by_account):
            last_error = str(error_by_account.get(account_id) or "")[:500]
            last_check = str(check_by_account.get(account_id) or "")
            self._upsert_account_state(
                conn,
                owner_id,
                account_id,
                last_error=last_error,
                last_check=last_check,
                preserve_existing=True,
            )

    def _insert_account_config(
        self,
        conn: sqlite3.Connection,
        owner_id: str,
        account_id: str,
        payload: dict[str, Any],
        *,
        replace: bool,
    ) -> bool:
        sql = (
            """
            INSERT INTO accounts(owner_id, account_id, payload_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(owner_id, account_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """
            if replace
            else """
            INSERT OR IGNORE INTO accounts(
                owner_id, account_id, payload_json, updated_at
            )
            VALUES (?, ?, ?, ?)
            """
        )
        cursor = conn.execute(
            sql,
            (owner_id, account_id, _dump_json(payload), time.time()),
        )
        return cursor.rowcount > 0

    def _upsert_account_state(
        self,
        conn: sqlite3.Connection,
        owner_id: str,
        account_id: str,
        *,
        last_error: str | None = None,
        last_check: str | None = None,
        preserve_existing: bool = False,
    ) -> None:
        row = conn.execute(
            """
            SELECT last_error, last_check
            FROM account_state
            WHERE owner_id = ? AND account_id = ?
            """,
            (owner_id, account_id),
        ).fetchone()
        current_error = str(row["last_error"]) if row else ""
        current_check = str(row["last_check"]) if row else ""
        next_error = current_error
        next_check = current_check
        if last_error is not None and (not preserve_existing or not current_error):
            next_error = last_error
        if last_check is not None and (not preserve_existing or not current_check):
            next_check = last_check
        conn.execute(
            """
            INSERT INTO account_state(owner_id, account_id, last_error, last_check)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(owner_id, account_id) DO UPDATE SET
                last_error = excluded.last_error,
                last_check = excluded.last_check
            """,
            (owner_id, account_id, next_error, next_check),
        )

    def _trim_tokens(self, owner_id: str) -> None:
        max_tokens = max(int(self.max_tokens), 0)
        conn = self._db()
        count = int(
            conn.execute(
                "SELECT COUNT(*) AS total FROM tokens WHERE owner_id = ?",
                (owner_id,),
            ).fetchone()["total"]
        )
        if count <= max_tokens:
            return
        conn.execute(
            """
            DELETE FROM tokens
            WHERE owner_id = ?
              AND token IN (
                  SELECT token
                  FROM tokens
                  WHERE owner_id = ?
                  ORDER BY created_at, token
                  LIMIT ?
              )
            """,
            (owner_id, owner_id, count - max_tokens),
        )

    def _account_payload(
        self, account_id: str, payload_json: str
    ) -> dict[str, Any] | None:
        payload = _load_json(payload_json, {})
        if not isinstance(payload, dict):
            return None
        account = dict(payload)
        account["account_id"] = str(account.get("account_id") or account_id)
        return account

    def _get_meta(self, conn: sqlite3.Connection, key: str) -> Any:
        row = conn.execute(
            "SELECT value_json FROM meta WHERE key = ?",
            (str(key),),
        ).fetchone()
        if row is None:
            return None
        return _load_json(row["value_json"], None)

    def _set_meta(self, conn: sqlite3.Connection, key: str, value: Any) -> None:
        conn.execute(
            """
            INSERT INTO meta(key, value_json)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
            """,
            (str(key), _dump_json(value)),
        )


_LEGACY_KEYS = (
    "seen",
    "initialized",
    "tokens",
    "oauth2",
    "blocked",
    "last_errors",
    "last_checks",
)


def _owner_id(value: str) -> str:
    return str(value or DEFAULT_OWNER_ID)


def _safe_owner_path(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or DEFAULT_OWNER_ID)).strip("._")
    return value or DEFAULT_OWNER_ID


def _uid_sort_key(value: str) -> tuple[int, str]:
    try:
        return int(value), value
    except ValueError:
        return 0, value


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json(value: str, default: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _migration_completed(value: Any) -> bool:
    return isinstance(value, dict) and bool(value.get("completed"))


def _iter_string_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value)]


def _iter_account_payloads(accounts: Any) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    if isinstance(accounts, dict):
        items = accounts.items()
    elif isinstance(accounts, list):
        items = ((None, item) for item in accounts)
    else:
        return result

    for key, payload in items:
        if not isinstance(payload, dict):
            continue
        account_id = str(payload.get("account_id") or payload.get("id") or key or "")
        account_id = account_id.strip()
        if not account_id:
            continue
        account = dict(payload)
        account["account_id"] = account_id
        result.append((account_id, account))
    return result
