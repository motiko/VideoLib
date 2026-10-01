"""Access control, user management, tiered limits, and daily quota tracking for VideoLib."""

import contextlib
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator
from src.config import config


class AccessManager:
    """Manages bot user authorization, whitelist status, tiered limits, and daily quota consumption."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or config.DB_PATH
        self._lock = threading.RLock()
        self._init_db()

    @contextlib.contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager providing an auto-closing SQLite connection with WAL journal mode."""
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initializes database schema and default settings."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    is_allowed INTEGER DEFAULT 0,
                    is_admin INTEGER DEFAULT 0,
                    custom_daily_mb INTEGER DEFAULT NULL,
                    custom_daily_downloads INTEGER DEFAULT NULL,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_users_username ON users(username COLLATE NOCASE)
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_daily (
                    user_id INTEGER,
                    date TEXT,
                    downloads_count INTEGER DEFAULT 0,
                    bytes_downloaded INTEGER DEFAULT 0,
                    last_download_at REAL,
                    PRIMARY KEY (user_id, date)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS allowed_groups (
                    group_id INTEGER PRIMARY KEY,
                    title TEXT,
                    is_allowed INTEGER DEFAULT 1,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )

            # Seed default tiered settings if not present
            defaults = {
                "public_access_enabled": "0",  # Disabled by default: only admins & allowlist can download
                "allowlist_daily_mb": str(config.DEFAULT_ALLOWLIST_DAILY_MB),
                "allowlist_daily_downloads": str(config.DEFAULT_ALLOWLIST_DAILY_DOWNLOADS),
                "public_daily_mb": str(config.DEFAULT_PUBLIC_DAILY_MB),
                "public_daily_downloads": str(config.DEFAULT_PUBLIC_DAILY_DOWNLOADS),
            }
            for k, v in defaults.items():
                cursor.execute(
                    "INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)",
                    (k, v),
                )
            conn.commit()

    @staticmethod
    def _today_utc() -> str:
        """Returns today's date in YYYY-MM-DD UTC format."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def record_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        """Records or updates user profile metadata from incoming messages."""
        clean_user = username.lstrip("@").strip() if username else None
        is_config_admin = 1 if user_id in config.ADMIN_USERS else 0
        now_str = datetime.now(timezone.utc).isoformat()

        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO users (user_id, username, first_name, is_allowed, is_admin, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = coalesce(excluded.username, users.username),
                    first_name = coalesce(excluded.first_name, users.first_name),
                    is_admin = CASE WHEN excluded.is_admin = 1 THEN 1 ELSE users.is_admin END,
                    is_allowed = CASE WHEN excluded.is_admin = 1 THEN 1 ELSE users.is_allowed END,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    clean_user,
                    first_name,
                    is_config_admin,
                    is_config_admin,
                    now_str,
                    now_str,
                ),
            )
            conn.commit()

    def is_admin(self, user_id: int) -> bool:
        """Checks if a given user has admin privileges."""
        if user_id in config.ADMIN_USERS:
            return True
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT is_admin FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return bool(row and row["is_admin"])

    def is_user_allowed(self, user_id: int) -> bool:
        """Checks if a user is explicitly in the allowlist."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT is_allowed FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return bool(row and row["is_allowed"])

    def is_public_access_enabled(self) -> bool:
        """Checks whether public access is enabled for non-allowlisted users."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM bot_settings WHERE key = 'public_access_enabled'")
            row = cursor.fetchone()
            if row:
                return row["value"] in ("1", "true", "yes")
            return False

    def set_public_access_enabled(self, enabled: bool) -> None:
        """Enables or disables public access for all users."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bot_settings (key, value) VALUES ('public_access_enabled', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                ("1" if enabled else "0",),
            )
            conn.commit()

    def allow_user(self, user_id: int, allowed: bool = True) -> None:
        """Grants or revokes allowlist authorization for a user."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO users (user_id, is_allowed, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    is_allowed = excluded.is_allowed,
                    updated_at = excluded.updated_at
                """,
                (user_id, 1 if allowed else 0, now_str, now_str),
            )
            conn.commit()

    def allow_group(self, group_id: int, title: str | None = None, allowed: bool = True) -> None:
        """Grants or revokes allowlist authorization for an entire group chat."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO allowed_groups (group_id, title, is_allowed, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    title = coalesce(excluded.title, allowed_groups.title),
                    is_allowed = excluded.is_allowed,
                    updated_at = excluded.updated_at
                """,
                (group_id, title, 1 if allowed else 0, now_str, now_str),
            )
            conn.commit()

    def is_group_allowed(self, group_id: int) -> bool:
        """Checks if a group chat is authorized."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT is_allowed FROM allowed_groups WHERE group_id = ?",
                (group_id,),
            )
            row = cursor.fetchone()
            return bool(row and row["is_allowed"])

    def get_all_allowed_groups(self) -> list[dict[str, Any]]:
        """Returns list of all authorized group records."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT group_id, title, is_allowed, created_at, updated_at
                FROM allowed_groups WHERE is_allowed = 1
                ORDER BY group_id ASC
                """
            )
            return [dict(r) for r in cursor.fetchall()]

    def set_user_limits(
        self,
        user_id: int,
        daily_mb: int | None = None,
        daily_downloads: int | None = None,
        reset: bool = False,
    ) -> None:
        """Configures custom daily limits for an individual user, or resets them to their tier defaults."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            if reset:
                cursor.execute(
                    """
                    UPDATE users
                    SET custom_daily_mb = NULL, custom_daily_downloads = NULL, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (now_str, user_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO users (user_id, custom_daily_mb, custom_daily_downloads, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        custom_daily_mb = coalesce(excluded.custom_daily_mb, users.custom_daily_mb),
                        custom_daily_downloads = coalesce(excluded.custom_daily_downloads, users.custom_daily_downloads),
                        updated_at = excluded.updated_at
                    """,
                    (user_id, daily_mb, daily_downloads, now_str, now_str),
                )
            conn.commit()

    def get_tier_limits(self, tier: str = "allowlist") -> tuple[int, int]:
        """Returns (daily_mb, daily_downloads) for the given tier ('allowlist' or 'public')."""
        t = "allowlist" if "allow" in tier.lower() else "public"
        mb_key = f"{t}_daily_mb"
        dl_key = f"{t}_daily_downloads"
        def_mb = (
            config.DEFAULT_ALLOWLIST_DAILY_MB
            if t == "allowlist"
            else config.DEFAULT_PUBLIC_DAILY_MB
        )
        def_dl = (
            config.DEFAULT_ALLOWLIST_DAILY_DOWNLOADS
            if t == "allowlist"
            else config.DEFAULT_PUBLIC_DAILY_DOWNLOADS
        )

        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT key, value FROM bot_settings WHERE key IN (?, ?)",
                (mb_key, dl_key),
            )
            rows = {row["key"]: row["value"] for row in cursor.fetchall()}
            try:
                mb = int(rows.get(mb_key, def_mb))
            except (ValueError, TypeError):
                mb = def_mb
            try:
                dl = int(rows.get(dl_key, def_dl))
            except (ValueError, TypeError):
                dl = def_dl
            return mb, dl

    def set_tier_limits(
        self,
        tier: str,
        daily_mb: int | None = None,
        daily_downloads: int | None = None,
    ) -> None:
        """Updates limits for a tier ('allowlist' or 'public')."""
        t = "allowlist" if "allow" in tier.lower() else "public"
        mb_key = f"{t}_daily_mb"
        dl_key = f"{t}_daily_downloads"

        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            if daily_mb is not None:
                cursor.execute(
                    """
                    INSERT INTO bot_settings (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (mb_key, str(daily_mb)),
                )
            if daily_downloads is not None:
                cursor.execute(
                    """
                    INSERT INTO bot_settings (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (dl_key, str(daily_downloads)),
                )
            conn.commit()

    def get_user_limits(self, user_id: int, chat_id: int | None = None) -> tuple[int, int, str]:
        """Returns (daily_mb, daily_downloads, tier_label)."""
        if self.is_admin(user_id):
            return 0, 0, "👑 Administrator (Unlimited)"

        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT custom_daily_mb, custom_daily_downloads, is_allowed FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = cursor.fetchone()

        is_allowed = bool(row and row["is_allowed"])
        is_group_ok = bool(chat_id is not None and self.is_group_allowed(chat_id))

        effective_allowlist = is_allowed or is_group_ok
        tier_name = "allowlist" if effective_allowlist else "public"
        tier_mb, tier_dl = self.get_tier_limits(tier_name)

        if row and (row["custom_daily_mb"] is not None or row["custom_daily_downloads"] is not None):
            mb = row["custom_daily_mb"] if row["custom_daily_mb"] is not None else tier_mb
            dl = row["custom_daily_downloads"] if row["custom_daily_downloads"] is not None else tier_dl
            return mb, dl, "🌟 Custom VIP"

        if is_allowed:
            return tier_mb, tier_dl, "🌟 Allowlist Tier"
        elif is_group_ok:
            return tier_mb, tier_dl, "👥 Group Allowlist Tier"
        else:
            return tier_mb, tier_dl, "🌐 Public Tier"

    def get_user_usage_today(self, user_id: int) -> tuple[int, int]:
        """Returns (downloads_count, bytes_downloaded) for the current UTC day."""
        today = self._today_utc()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT downloads_count, bytes_downloaded FROM usage_daily WHERE user_id = ? AND date = ?",
                (user_id, today),
            )
            row = cursor.fetchone()
            if row:
                return row["downloads_count"], row["bytes_downloaded"]
            return 0, 0

    def check_can_download(self, user_id: int, chat_id: int | None = None) -> tuple[bool, str | None]:
        """Evaluates whether the user is permitted to start a download."""
        # 1. Admins bypass all restrictions
        if self.is_admin(user_id):
            return True, None

        # 2. Check allowlist status: user-specific or group-wide
        is_allowed = self.is_user_allowed(user_id)
        is_group_ok = bool(chat_id is not None and self.is_group_allowed(chat_id))

        # 3. If neither user nor group is allowlisted, check if public access is opened
        if not is_allowed and not is_group_ok and not self.is_public_access_enabled():
            return (
                False,
                "⛔ Access restricted: Only authorized users or allowed groups can download videos with this bot. Please contact the administrator to get access.",
            )

        # 4. Check quota against the user's tier limits
        limit_mb, limit_dl, _ = self.get_user_limits(user_id, chat_id=chat_id)
        used_dl, used_bytes = self.get_user_usage_today(user_id)

        if limit_dl > 0 and used_dl >= limit_dl:
            return (
                False,
                f"⚠️ Daily download limit reached ({used_dl}/{limit_dl} downloads). Your quota resets at 00:00 UTC.",
            )

        used_mb = used_bytes / (1024 * 1024)
        if limit_mb > 0 and used_mb >= limit_mb:
            return (
                False,
                f"⚠️ Daily traffic limit reached ({used_mb:.1f} MB / {limit_mb} MB). Your quota resets at 00:00 UTC.",
            )

        return True, None

    def record_download(self, user_id: int, size_bytes: int) -> None:
        """Increments today's download count and bandwidth usage for a user."""
        today = self._today_utc()
        now_ts = datetime.now(timezone.utc).timestamp()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO usage_daily (user_id, date, downloads_count, bytes_downloaded, last_download_at)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(user_id, date) DO UPDATE SET
                    downloads_count = downloads_count + 1,
                    bytes_downloaded = bytes_downloaded + excluded.bytes_downloaded,
                    last_download_at = excluded.last_download_at
                """,
                (user_id, today, size_bytes, now_ts),
            )
            conn.commit()

    def reset_user_usage(self, user_id: int) -> None:
        """Resets today's usage counters for a user."""
        today = self._today_utc()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM usage_daily WHERE user_id = ? AND date = ?",
                (user_id, today),
            )
            conn.commit()

    def resolve_user_identifier(self, identifier: str) -> tuple[int | None, str]:
        """Resolves a numeric user ID or @username string to (user_id, display_name)."""
        clean = identifier.strip().lstrip("@")
        if clean.isdigit():
            uid = int(clean)
            info = self.get_user_info(uid)
            name = f"@{info['username']}" if info and info["username"] else f"ID {uid}"
            return uid, name

        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id, username, first_name FROM users WHERE username = ? COLLATE NOCASE LIMIT 1",
                (clean,),
            )
            row = cursor.fetchone()
            if row:
                disp = f"@{row['username']}" if row["username"] else (row["first_name"] or str(row["user_id"]))
                return row["user_id"], disp
            return None, f"@{clean}"

    def get_user_info(self, user_id: int) -> dict[str, Any] | None:
        """Returns user row details as a dictionary."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT user_id, username, first_name, is_allowed, is_admin,
                       custom_daily_mb, custom_daily_downloads, created_at, updated_at
                FROM users WHERE user_id = ?
                """,
                (user_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_allowed_users(self) -> list[dict[str, Any]]:
        """Returns list of whitelisted user records."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT user_id, username, first_name, is_allowed, is_admin,
                       custom_daily_mb, custom_daily_downloads
                FROM users WHERE is_allowed = 1 OR is_admin = 1
                ORDER BY user_id ASC
                """
            )
            return [dict(r) for r in cursor.fetchall()]

    def get_system_stats_today(self) -> dict[str, Any]:
        """Returns aggregated bot traffic stats for today."""
        today = self._today_utc()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT count(DISTINCT user_id) as active_users,
                       coalesce(sum(downloads_count), 0) as total_downloads,
                       coalesce(sum(bytes_downloaded), 0) as total_bytes
                FROM usage_daily WHERE date = ?
                """,
                (today,),
            )
            row = cursor.fetchone()
            active_users = row["active_users"] if row else 0
            total_dl = row["total_downloads"] if row else 0
            total_bytes = row["total_bytes"] if row else 0

            al_mb, al_dl = self.get_tier_limits("allowlist")
            pub_mb, pub_dl = self.get_tier_limits("public")
            pub_on = self.is_public_access_enabled()

            return {
                "date": today,
                "active_users": active_users,
                "total_downloads": total_dl,
                "total_bytes": total_bytes,
                "total_mb": total_bytes / (1024 * 1024),
                "public_access_enabled": pub_on,
                "allowlist_daily_mb": al_mb,
                "allowlist_daily_downloads": al_dl,
                "public_daily_mb": pub_mb,
                "public_daily_downloads": pub_dl,
            }


# Global access manager instance
access_manager = AccessManager()
