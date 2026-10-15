"""Operational admin commands for the Telegram platform (uptime, logs, stats, restart, ...).

Mixed into TelegramPlatform; relies on its _require_admin / send_message / send_document helpers
and on the Orchestrator reference set by Orchestrator.register_platform.
"""

import asyncio
import html
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes
from src import __version__
from src.config import config
from src.core.access import access_manager
from src.utils.logger import logger
from src.utils.logs import (
    app_log_path,
    grep_errors,
    list_failure_reports,
    render_pre,
    resolve_failure_report,
    tail_lines,
)
from src.utils.system import (
    dir_stats,
    disk_free,
    format_bytes,
    format_uptime,
    get_ytdlp_version,
    update_ytdlp,
)

ADMIN_DENIED = "⛔ This command is restricted to bot administrators."
PRIVATE_ONLY = "🔒 This command exposes logs or user data, so it only works in a private chat with the bot."
RESTART_NOTIFY_KEY = "pending_restart_notify"
BROADCAST_TTL_SEC = 120.0
MAX_LOG_LINES = 200


def _parse_days_limit(args: list[str], default_days: int, default_limit: int) -> tuple[int, int]:
    """Parses '[days] [limit]' positional ints with clamping; non-numeric values keep defaults."""
    days, limit = default_days, default_limit
    nums = [int(a) for a in args[:2] if a.isdigit()]
    if len(args) >= 1 and args[0].isdigit():
        days = nums[0]
        if len(args) >= 2 and args[1].isdigit():
            limit = nums[1]
    days = min(max(days, 1), 365)
    limit = min(max(limit, 1), 50)
    return days, limit


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _user_label(row: dict[str, Any]) -> str:
    if row.get("username"):
        return f"@{_esc(row['username'])}"
    if row.get("first_name"):
        return _esc(row["first_name"])
    return f"ID {row.get('user_id')}"


def _fmt_ts(iso: str | None) -> str:
    if not iso:
        return "?"
    try:
        return datetime.fromisoformat(iso).strftime("%m-%d %H:%M")
    except ValueError:
        return iso[:16]


class TelegramAdminOpsMixin:
    """Admin-only operational commands. Expects to be mixed into TelegramPlatform."""

    # Provided by TelegramPlatform / BasePlatform
    orchestrator: Any
    application: Any

    def _require_admin(self, update: Update) -> bool:  # pragma: no cover - overridden by TelegramPlatform
        raise NotImplementedError

    async def send_message(self, chat_id: str, text: str, reply_to_message_id: str | None = None) -> str | None:  # pragma: no cover
        raise NotImplementedError

    async def edit_message(self, chat_id: str, message_id: str, text: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def send_document(self, chat_id: str, file_path: Path, caption: str | None = None) -> None:  # pragma: no cover
        raise NotImplementedError

    async def send_video(self, chat_id: str, file_path: Path, caption: str | None = None, **kwargs: Any) -> None:  # pragma: no cover
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def _pending_broadcasts(self) -> dict[int, tuple[str, float]]:
        store = getattr(self, "_pending_broadcasts_store", None)
        if store is None:
            store = {}
            self._pending_broadcasts_store = store
        return store

    async def _deny_unless_admin(self, update: Update, private_only: bool = False) -> str | None:
        """Returns chat_id when the sender is an admin (and, if private_only, the chat is a DM); else replies and returns None."""
        chat_id = str(update.effective_chat.id)
        if not self._require_admin(update):
            await self.send_message(chat_id, ADMIN_DENIED)
            return None
        if private_only:
            chat = update.effective_chat
            if chat is None or chat.type != ChatType.PRIVATE:
                await self.send_message(chat_id, PRIVATE_ONLY)
                return None
        return chat_id

    def _uptime_line(self) -> str:
        orch = self.orchestrator
        if not orch:
            return "unknown"
        return f"{format_uptime(orch.uptime_seconds())} (since {orch.started_at.strftime('%Y-%m-%d %H:%M')} UTC)"

    async def _send_restart_notice(self) -> None:
        """After a /restart, tells the requesting chat that the bot is back. Called from start()."""
        try:
            chat_id = access_manager.get_setting(RESTART_NOTIFY_KEY)
            if not chat_id:
                return
            access_manager.delete_setting(RESTART_NOTIFY_KEY)
            ytdlp = await get_ytdlp_version()
            await self.send_message(
                chat_id,
                f"✅ <b>Bot restarted and back online.</b>\n• Version: <code>{_esc(__version__)}</code>\n• yt-dlp: <code>{_esc(ytdlp)}</code>",
            )
        except Exception as e:
            logger.warning(f"TelegramPlatform: Failed to send restart notice: {e}")

    # ------------------------------------------------------------------
    # /uptime /status /queue
    # ------------------------------------------------------------------

    async def _handle_uptime(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin: show process uptime, PID, and version."""
        chat_id = await self._deny_unless_admin(update)
        if not chat_id:
            return
        await self.send_message(
            chat_id,
            "⏱ <b>Uptime:</b> " + _esc(self._uptime_line()) + "\n"
            f"• <b>PID:</b> <code>{os.getpid()}</code>\n"
            f"• <b>Version:</b> <code>{_esc(__version__)}</code>",
        )

    async def _handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin: one-shot health overview."""
        chat_id = await self._deny_unless_admin(update)
        if not chat_id:
            return
        orch = self.orchestrator
        active = list(orch.active.values()) if orch else []
        running = sum(1 for a in active if a.stage != "queued")
        queued = len(active) - running
        ytdlp = await get_ytdlp_version()
        tmp_count, tmp_bytes = dir_stats(config.DOWNLOAD_DIR)
        log_path = app_log_path()
        log_size = log_path.stat().st_size if log_path.is_file() else 0
        db_size = config.DB_PATH.stat().st_size if config.DB_PATH.is_file() else 0
        stats = access_manager.get_system_stats_today()
        pub = "ENABLED 🌐" if stats["public_access_enabled"] else "DISABLED 🔒"
        text = (
            "🩺 <b>VideoLib Status</b>\n\n"
            f"• <b>Uptime:</b> {_esc(self._uptime_line())}\n"
            f"• <b>Version:</b> <code>{_esc(__version__)}</code> · <b>yt-dlp:</b> <code>{_esc(ytdlp)}</code>\n"
            f"• <b>Active downloads:</b> {running} running, {queued} queued (max {config.MAX_CONCURRENT_DOWNLOADS})\n"
            f"• <b>Disk free:</b> {format_bytes(disk_free(config.DOWNLOAD_DIR))} · "
            f"<b>Temp files:</b> {tmp_count} ({format_bytes(tmp_bytes)})\n"
            f"• <b>Log size:</b> {format_bytes(log_size)} · <b>DB size:</b> {format_bytes(db_size)}\n"
            f"• <b>Public access:</b> {pub}\n"
            f"• <b>Today:</b> {stats['total_downloads']} downloads, {stats['total_mb']:.1f} MB, "
            f"{stats['active_users']} active users\n"
        )
        await self.send_message(chat_id, text)

    async def _handle_queue(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin: list in-flight downloads."""
        chat_id = await self._deny_unless_admin(update, private_only=True)
        if not chat_id:
            return
        orch = self.orchestrator
        active = list(orch.active.values()) if orch else []
        if not active:
            await self.send_message(chat_id, "📭 Nothing in progress.")
            return
        lines = [f"📥 <b>In progress ({len(active)}):</b>\n"]
        for a in sorted(active, key=lambda x: x.started_at):
            label = a.title or access_manager.extract_domain(a.url)
            who = f"user <code>{a.user_id}</code>" if a.user_id else "unknown user"
            lines.append(f"• [{_esc(a.stage)}] {_esc(label)} — {who}, {format_uptime(a.elapsed)}")
        await self.send_message(chat_id, "\n".join(lines))

    # ------------------------------------------------------------------
    # /logs
    # ------------------------------------------------------------------

    async def _handle_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin: /logs [tail|errors] [n] | upload | failures [n] | failure <name|latest>."""
        chat_id = await self._deny_unless_admin(update, private_only=True)
        if not chat_id:
            return
        args = list(context.args or [])
        sub = args[0].lower() if args else "tail"
        rest = args[1:]
        log_path = app_log_path()
        usage = (
            "⚠️ Usage:\n"
            "• <code>/logs [tail] [n]</code> — last n lines (default 30)\n"
            "• <code>/logs errors [n]</code> — last n warnings/errors\n"
            "• <code>/logs upload</code> — send the log file\n"
            "• <code>/logs failures [n]</code> — list failure reports\n"
            "• <code>/logs failure &lt;name|latest&gt;</code> — send a failure report"
        )

        if sub.isdigit():
            sub, rest = "tail", [sub]

        if sub in ("tail", "errors"):
            n = int(rest[0]) if rest and rest[0].isdigit() else 30
            n = min(max(n, 1), MAX_LOG_LINES)
            if not log_path.is_file():
                await self.send_message(chat_id, f"📄 No log file at <code>{_esc(log_path)}</code>.")
                return
            lines = tail_lines(log_path, n) if sub == "tail" else grep_errors(log_path, n)
            if not lines:
                await self.send_message(chat_id, "📄 No matching log lines.")
                return
            await self.send_message(chat_id, render_pre("\n".join(lines)))
            return

        if sub == "upload":
            if not log_path.is_file():
                await self.send_message(chat_id, f"📄 No log file at <code>{_esc(log_path)}</code>.")
                return
            await self.send_document(chat_id, log_path, caption=f"videolib.log ({format_bytes(log_path.stat().st_size)})")
            return

        if sub == "failures":
            n = int(rest[0]) if rest and rest[0].isdigit() else 10
            reports = list_failure_reports(limit=min(max(n, 1), 50))
            if not reports:
                await self.send_message(chat_id, "📄 No failure reports found.")
                return
            now = time.time()
            lines = [f"🧾 <b>Failure reports ({len(reports)} newest):</b>\n"]
            for r in reports:
                st = r.stat()
                lines.append(f"• <code>{_esc(r.name)}</code> — {format_bytes(st.st_size)}, {format_uptime(now - st.st_mtime)} ago")
            lines.append("\nSend one with <code>/logs failure &lt;name&gt;</code> or <code>/logs failure latest</code>.")
            await self.send_message(chat_id, "\n".join(lines))
            return

        if sub == "failure":
            name = rest[0] if rest else "latest"
            report = resolve_failure_report(name)
            if not report:
                await self.send_message(chat_id, f"⚠️ Failure report not found: <code>{_esc(name)}</code>")
                return
            await self.send_document(chat_id, report, caption=report.name)
            return

        await self.send_message(chat_id, usage)

    # ------------------------------------------------------------------
    # /stats /history
    # ------------------------------------------------------------------

    async def _handle_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin: /stats [days] | top_users|sites|daily|errors [days] [n]."""
        chat_id = await self._deny_unless_admin(update, private_only=True)
        if not chat_id:
            return
        args = list(context.args or [])
        sub = args[0].lower() if args and not args[0].isdigit() else "overview"
        rest = args[1:] if sub != "overview" else args
        usage = (
            "⚠️ Usage:\n"
            "• <code>/stats [days]</code> — overview\n"
            "• <code>/stats top_users [days] [n]</code>\n"
            "• <code>/stats sites [days] [n]</code>\n"
            "• <code>/stats daily [days]</code>\n"
            "• <code>/stats errors [days] [n]</code>"
        )

        if sub == "overview":
            days, _ = _parse_days_limit(rest, 7, 5)
            s = access_manager.get_range_summary(days)
            rate = (100.0 * s["ok"] / s["downloads"]) if s["downloads"] else 0.0
            lines = [
                f"📈 <b>Stats — last {days} days</b>\n",
                f"• <b>Downloads:</b> {s['downloads']} ({s['ok']} ok, {s['failed']} failed)",
                f"• <b>Success rate:</b> {rate:.0f}%",
                f"• <b>Traffic:</b> {format_bytes(s['bytes'])}",
                f"• <b>Active users:</b> {s['active_users']}",
                "\n<b>Top users:</b>",
            ]
            lines += self._fmt_top_users(access_manager.get_top_users(days, 5))
            lines.append("\n<b>Top sites:</b>")
            lines += self._fmt_top_domains(access_manager.get_top_domains(days, 5))
            await self.send_message(chat_id, "\n".join(lines))
            return

        if sub in ("top_users", "users", "top_user"):
            days, limit = _parse_days_limit(rest, 7, 10)
            lines = [f"👥 <b>Top users — last {days} days</b>\n"]
            lines += self._fmt_top_users(access_manager.get_top_users(days, limit))
            await self.send_message(chat_id, "\n".join(lines))
            return

        if sub in ("sites", "websites", "popular_websites", "domains"):
            days, limit = _parse_days_limit(rest, 7, 10)
            lines = [f"🌐 <b>Popular sites — last {days} days</b>\n"]
            lines += self._fmt_top_domains(access_manager.get_top_domains(days, limit))
            await self.send_message(chat_id, "\n".join(lines))
            return

        if sub == "daily":
            days, _ = _parse_days_limit(rest, 14, 10)
            rows = access_manager.get_daily_totals(days)
            lines = [f"📅 <b>Daily totals — last {days} days</b>\n"]
            if not rows:
                lines.append("• (no activity)")
            for r in rows:
                lines.append(f"• {r['date']}: {r['downloads']} downloads, {format_bytes(r['bytes'])}, {r['active_users']} users")
            await self.send_message(chat_id, "\n".join(lines))
            return

        if sub == "errors":
            days, limit = _parse_days_limit(rest, 7, 10)
            rows = access_manager.get_error_stats(days, limit)
            lines = [f"🧨 <b>Top failures — last {days} days</b>\n"]
            if not rows:
                lines.append("• (no failures)")
            for r in rows:
                lines.append(f"• <b>{r['count']}×</b> {_esc(r['domain'])}: <code>{_esc(r['error'] or 'unknown')}</code>")
            await self.send_message(chat_id, "\n".join(lines))
            return

        await self.send_message(chat_id, usage)

    @staticmethod
    def _fmt_top_users(rows: list[dict[str, Any]]) -> list[str]:
        if not rows:
            return ["• (no activity)"]
        return [
            f"• {_user_label(r)} [<code>{r['user_id']}</code>] — {r['downloads']} downloads, {format_bytes(r['bytes'])}"
            for r in rows
        ]

    @staticmethod
    def _fmt_top_domains(rows: list[dict[str, Any]]) -> list[str]:
        if not rows:
            return ["• (no activity)"]
        return [
            f"• {_esc(r['domain'])} — {r['downloads']} ({r['ok']} ok / {r['failed']} failed), {format_bytes(r['bytes'])}"
            for r in rows
        ]

    async def _handle_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin: /history [@user | id] [n] — numeric args below 1000 are treated as the row count."""
        chat_id = await self._deny_unless_admin(update, private_only=True)
        if not chat_id:
            return
        args = list(context.args or [])
        target_uid: int | None = None
        target_name = ""
        limit = 15

        reply_to = update.message.reply_to_message if update.message else None
        if reply_to and reply_to.from_user:
            target_uid = reply_to.from_user.id
            target_name = f"@{reply_to.from_user.username}" if reply_to.from_user.username else f"ID {target_uid}"

        for a in args:
            if a.isdigit() and int(a) < 1000:
                limit = int(a)
            elif target_uid is None:
                uid, name = access_manager.resolve_user_identifier(a)
                if uid is None:
                    await self.send_message(chat_id, f"⚠️ User {_esc(name)} not found.")
                    return
                target_uid, target_name = uid, name
        limit = min(max(limit, 1), 50)

        rows = access_manager.get_recent_downloads(limit=limit, user_id=target_uid)
        header = f"🕓 <b>Recent downloads{' for ' + _esc(target_name) if target_uid is not None else ''}:</b>\n"
        if not rows:
            await self.send_message(chat_id, header + "• (none)")
            return
        lines = [header]
        for r in rows:
            icon = "✅" if r["status"] == "ok" else "❌"
            what = r["title"] or r["url"] or r["domain"] or "?"
            who = "" if target_uid is not None else f" — {_user_label(r)}"
            extra = f" ({format_bytes(r['size_bytes'])}, {format_uptime(r['duration_sec'] or 0)})" if r["status"] == "ok" else (
                f" — <code>{_esc(r['error'] or 'failed')}</code>"
            )
            lines.append(f"{icon} {_fmt_ts(r['created_at'])} {_esc(what)}{who}{extra}")
        await self.send_message(chat_id, "\n".join(lines))

    # ------------------------------------------------------------------
    # /restart /ytdlp /broadcast /debug_upload
    # ------------------------------------------------------------------

    async def _handle_restart(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin: gracefully stop and re-exec the bot. Refuses while downloads are active unless 'force'."""
        chat_id = await self._deny_unless_admin(update)
        if not chat_id:
            return
        orch = self.orchestrator
        if not orch:
            await self.send_message(chat_id, "⚠️ Orchestrator not attached; cannot restart.")
            return
        force = any(a.lower() == "force" for a in (context.args or []))
        n_active = len(orch.active)
        if n_active and not force:
            await self.send_message(
                chat_id,
                f"⚠️ {n_active} download(s) in progress. Use <code>/restart force</code> to restart anyway (they will fail).",
            )
            return
        access_manager.set_setting(RESTART_NOTIFY_KEY, chat_id)
        await self.send_message(chat_id, "♻️ Restarting… I'll message you here when I'm back.")
        logger.info(f"TelegramPlatform: Restart requested by admin in chat {chat_id} (force={force}).")
        orch.request_shutdown(restart=True)

    async def _handle_ytdlp(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin: /ytdlp [version|update]."""
        chat_id = await self._deny_unless_admin(update)
        if not chat_id:
            return
        sub = (context.args or ["version"])[0].lower()
        if sub != "update":
            version = await get_ytdlp_version()
            await self.send_message(chat_id, f"📦 <b>yt-dlp version:</b> <code>{_esc(version)}</code>\nRun <code>/ytdlp update</code> to upgrade.")
            return
        msg_id = await self.send_message(chat_id, "⏳ Updating yt-dlp… this can take a minute.")
        ok, output = await update_ytdlp()
        version = await get_ytdlp_version()
        tail = "\n".join(output.strip().splitlines()[-15:])
        text = (
            ("✅ <b>yt-dlp update finished.</b>" if ok else "❌ <b>yt-dlp update failed.</b>")
            + f"\n• Version now: <code>{_esc(version)}</code>\n"
            + render_pre(tail or "(no output)", max_chars=3000)
        )
        if msg_id:
            await self.edit_message(chat_id, msg_id, text)
        else:
            await self.send_message(chat_id, text)

    async def _handle_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin: /broadcast <text> previews; /broadcast confirm sends to every known user; /broadcast cancel."""
        chat_id = await self._deny_unless_admin(update)
        if not chat_id:
            return
        admin_id = update.effective_user.id
        args = list(context.args or [])
        pending = self._pending_broadcasts
        now = time.monotonic()
        # Expire stale previews
        for uid, (_, ts) in list(pending.items()):
            if now - ts > BROADCAST_TTL_SEC:
                pending.pop(uid, None)

        if not args:
            await self.send_message(
                chat_id,
                "⚠️ Usage: <code>/broadcast &lt;message&gt;</code> to preview, then <code>/broadcast confirm</code> to send "
                "to every user who has messaged the bot, or <code>/broadcast cancel</code>.",
            )
            return

        if args[0].lower() == "cancel":
            pending.pop(admin_id, None)
            await self.send_message(chat_id, "🚫 Broadcast cancelled.")
            return

        if args[0].lower() == "confirm":
            entry = pending.pop(admin_id, None)
            if not entry:
                await self.send_message(chat_id, "⚠️ No pending broadcast (previews expire after 2 minutes).")
                return
            text = entry[0]
            users = access_manager.get_all_users()
            sent = failed = 0
            for u in users:
                target = str(u["user_id"])
                try:
                    result = await self.send_message(target, f"📢 {_esc(text)}")
                    if result:
                        sent += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
                await asyncio.sleep(0.05)
            await self.send_message(chat_id, f"📢 Broadcast done. Sent to {sent} user(s), {failed} failed.")
            return

        raw = update.message.text if update.message and isinstance(update.message.text, str) else ""
        text = raw.split(None, 1)[1].strip() if raw.startswith("/") and len(raw.split(None, 1)) > 1 else " ".join(args)
        pending[admin_id] = (text, now)
        count = len(access_manager.get_all_users())
        await self.send_message(
            chat_id,
            f"📢 <b>Broadcast preview</b> (to {count} user(s)):\n\n{_esc(text)}\n\n"
            "Send <code>/broadcast confirm</code> within 2 minutes to deliver, or <code>/broadcast cancel</code>.",
        )

    async def _handle_debug_upload(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin: re-send a file from DOWNLOAD_DIR as a video (debugging uploads). Paths outside it are refused."""
        chat_id = await self._deny_unless_admin(update)
        if not chat_id:
            return
        if not context.args:
            await self.send_message(chat_id, "⚠️ Please provide a file name inside the download directory to retry upload.")
            return

        file_path = Path(" ".join(context.args))
        if not file_path.is_absolute():
            file_path = config.DOWNLOAD_DIR / file_path
        try:
            file_path.resolve().relative_to(config.DOWNLOAD_DIR.resolve())
        except ValueError:
            await self.send_message(chat_id, f"⛔ Only files inside <code>{_esc(config.DOWNLOAD_DIR)}</code> can be uploaded.")
            return
        if not file_path.is_file():
            await self.send_message(chat_id, f"⚠️ File not found: <code>{_esc(file_path.name)}</code>")
            return

        await self.send_message(chat_id, f"📤 Retrying upload for {_esc(file_path.name)}...")
        try:
            await self.send_video(chat_id, file_path, caption=f"Debug upload: {file_path.name}")
        except Exception as e:
            await self.send_message(chat_id, f"❌ Upload failed: {_esc(e)}")
