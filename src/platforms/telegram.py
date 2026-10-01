import asyncio
import contextlib
import re
from typing import Callable, Coroutine, Any
from pathlib import Path
from telegram import Update, InputFile, LinkPreviewOptions
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from src.platforms.base import BasePlatform
from src.utils.logger import logger
from src.utils.progress import ProgressFileReader
from src.config import config
from src.core.access import access_manager

_HTML_TAG_RE = re.compile(r"<[^>]+>")

class TelegramPlatform(BasePlatform):
    """Platform adapter for Telegram using python-telegram-bot."""

    def __init__(self, token: str):
        super().__init__("telegram")
        self.token = token
        self.application: Application | None = None
        self._url_pattern = re.compile(r"(https?://[^\s]+)")

    async def start(self) -> None:
        """Starts the telegram bot application and listener polling."""
        logger.info("TelegramPlatform: Initializing application builder...")
        builder = Application.builder().token(self.token)
        if config.TELEGRAM_API_URL:
            # When using a local API server, we must also specify local_mode=True
            builder = builder.base_url(config.TELEGRAM_API_URL).local_mode(True)
        self.application = builder.build()

        # User commands
        self.application.add_handler(CommandHandler("start", self._handle_start))
        self.application.add_handler(CommandHandler("help", self._handle_help))
        self.application.add_handler(CommandHandler("id", self._handle_id))
        self.application.add_handler(CommandHandler("mylimits", self._handle_mylimits))
        self.application.add_handler(CommandHandler("limits", self._handle_mylimits))
        self.application.add_handler(CommandHandler("usage", self._handle_mylimits))
        self.application.add_handler(CommandHandler("quota", self._handle_mylimits))

        # Admin commands
        self.application.add_handler(CommandHandler("allow", self._handle_allow))
        self.application.add_handler(CommandHandler("disallow", self._handle_disallow))
        self.application.add_handler(CommandHandler("revoke", self._handle_disallow))
        self.application.add_handler(CommandHandler("adjustlimits", self._handle_adjustlimits))
        self.application.add_handler(CommandHandler("setlimit", self._handle_setlimit))
        self.application.add_handler(CommandHandler("setdailylimit", self._handle_setlimit))
        self.application.add_handler(CommandHandler("showusage", self._handle_showusage))
        self.application.add_handler(CommandHandler("publicaccess", self._handle_publicaccess))
        self.application.add_handler(CommandHandler("allowall", self._handle_publicaccess))
        self.application.add_handler(CommandHandler("allowed", self._handle_allowed))
        self.application.add_handler(CommandHandler("resetusage", self._handle_resetusage))
        self.application.add_handler(CommandHandler("adminhelp", self._handle_adminhelp))
        self.application.add_handler(CommandHandler("debug_upload", self._handle_debug_upload))

        # Add handler for general text messages containing URLs
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

        # Start application
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling(drop_pending_updates=True)
        logger.info("TelegramPlatform: Bot polling started successfully.")

    async def stop(self) -> None:
        """Shuts down the polling client cleanly."""
        if self.application:
            logger.info("TelegramPlatform: Stopping polling and shutting down application...")
            if self.application.updater:
                await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
            logger.info("TelegramPlatform: Stopped successfully.")

    async def send_message(self, chat_id: str, text: str, reply_to_message_id: str | None = None) -> str | None:
        """Sends a text message back to the Telegram chat without link preview."""
        if not self.application:
            raise RuntimeError("Telegram application is not running.")
        kwargs = {
            "chat_id": chat_id,
            "text": text,
            "link_preview_options": LinkPreviewOptions(is_disabled=True),
        }
        if any(tag in text for tag in ("<pre>", "<code>", "<b>", "<i>")):
            kwargs["parse_mode"] = "HTML"
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = int(reply_to_message_id)
        try:
            msg = await self.application.bot.send_message(**kwargs)
            return str(msg.message_id)
        except Exception as e:
            if "parse" in str(e).lower() and kwargs.get("parse_mode") == "HTML":
                try:
                    kwargs.pop("parse_mode", None)
                    kwargs["text"] = _HTML_TAG_RE.sub("", text)
                    msg = await self.application.bot.send_message(**kwargs)
                    return str(msg.message_id)
                except Exception as e2:
                    logger.error(f"TelegramPlatform: Fallback send message failed: {e2}")
            logger.error(f"TelegramPlatform: Failed to send message to {chat_id}: {e}")
            return None

    async def edit_message(self, chat_id: str, message_id: str, text: str) -> None:
        """Edits an existing message in the Telegram chat without link preview."""
        if not self.application:
            raise RuntimeError("Telegram application is not running.")
        kwargs = {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "text": text,
            "link_preview_options": LinkPreviewOptions(is_disabled=True),
        }
        if any(tag in text for tag in ("<pre>", "<code>", "<b>", "<i>")):
            kwargs["parse_mode"] = "HTML"
        try:
            await self.application.bot.edit_message_text(**kwargs)
        except Exception as e:
            err_str = str(e).lower()
            if "not modified" in err_str:
                return
            if "retry_after" in err_str or "flood" in err_str:
                logger.warning(f"TelegramPlatform: Rate limited editing message {message_id} in {chat_id}: {e}")
                return
            if "parse" in err_str and kwargs.get("parse_mode") == "HTML":
                try:
                    kwargs.pop("parse_mode", None)
                    kwargs["text"] = _HTML_TAG_RE.sub("", text)
                    await self.application.bot.edit_message_text(**kwargs)
                    return
                except Exception as e2:
                    logger.warning(f"TelegramPlatform: Fallback edit failed: {e2}")
            logger.error(f"TelegramPlatform: Failed to edit message {message_id} in {chat_id}: {e}")

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """Deletes an existing message in the Telegram chat."""
        if not self.application:
            raise RuntimeError("Telegram application is not running.")
        try:
            return await self.application.bot.delete_message(chat_id=chat_id, message_id=int(message_id))
        except Exception as e:
            logger.warning(f"TelegramPlatform: Failed to delete message {message_id} in {chat_id}: {e}")
            return False

    async def react_to_message(self, chat_id: str, message_id: str, emoji: str = "✅") -> bool:
        """Reacts to a message with an emoji."""
        if not self.application:
            raise RuntimeError("Telegram application is not running.")
        try:
            return await self.application.bot.set_message_reaction(
                chat_id=chat_id,
                message_id=int(message_id),
                reaction=emoji
            )
        except Exception:
            try:
                # Fallback to standard thumbs-up if the custom emoji is not enabled in chat
                return await self.application.bot.set_message_reaction(
                    chat_id=chat_id,
                    message_id=int(message_id),
                    reaction="👍"
                )
            except Exception as e2:
                logger.debug(f"TelegramPlatform: Could not set message reaction: {e2}")
                return False

    async def send_video(
        self,
        chat_id: str,
        file_path: Path,
        caption: str | None = None,
        reply_to_message_id: str | None = None,
        progress_callback: Callable[[float], Coroutine[Any, Any, None]] | Callable[[float], None] | None = None,
    ) -> None:
        """Uploads and sends a video file to the Telegram chat."""
        if not self.application:
            raise RuntimeError("Telegram application is not running.")

        # Open the file in binary read mode
        # python-telegram-bot/httpx handles async chunks internally
        try:
            kwargs = {
                "chat_id": chat_id,
                "caption": caption,
                "read_timeout": 180.0,
                "write_timeout": 180.0,
                "connect_timeout": 60.0
            }
            if reply_to_message_id:
                kwargs["reply_to_message_id"] = int(reply_to_message_id)

            total_size = file_path.stat().st_size if file_path.exists() else 0
            with open(file_path, "rb") as video_file:
                if progress_callback and total_size > 0:
                    wrapped_file = ProgressFileReader(video_file, total_size, progress_callback)
                    kwargs["video"] = InputFile(wrapped_file, filename=file_path.name, read_file_handle=False)
                else:
                    kwargs["video"] = video_file
                await self.application.bot.send_video(**kwargs)
            logger.info(f"TelegramPlatform: Successfully sent video {file_path} to {chat_id}")
        except Exception as e:
            logger.error(f"TelegramPlatform: Failed to send video {file_path} to {chat_id}: {e}")
            raise

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Replies to the /start command."""
        chat_id = str(update.effective_chat.id)
        user = update.effective_user
        is_adm = False
        user_id_line = ""
        if user:
            access_manager.record_user(user.id, user.username, user.first_name)
            is_adm = access_manager.is_admin(user.id)
            user_id_line = f"🆔 <b>Your Telegram User ID:</b> <code>{user.id}</code>\n\n"

        welcome_text = (
            "🤖 <b>Welcome to VideoLib!</b>\n\n"
            f"{user_id_line}"
            "Send me a message containing a video URL (e.g. YouTube, TikTok, Twitter/X), "
            "and I will download and send you the video file directly.\n\n"
            "📌 <b>Commands:</b>\n"
            "• /id - Show your Telegram User ID\n"
            "• /mylimits - View your daily traffic quota & usage\n"
            "• /help - Instructions and limitations\n"
        )
        if is_adm:
            welcome_text += "\n👑 <b>Admin:</b> Type /adminhelp for user management & quota controls."

        await self.send_message(chat_id, welcome_text)

    async def _handle_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Replies with the user's numeric Telegram User ID and chat ID."""
        chat_id = str(update.effective_chat.id)
        user = update.effective_user
        if not user:
            return
        access_manager.record_user(user.id, user.username, user.first_name)
        uname = f"@{user.username}" if user.username else "None"
        msg = (
            "🆔 <b>Your Telegram Information:</b>\n\n"
            f"• <b>User ID:</b> <code>{user.id}</code>\n"
            f"• <b>Username:</b> {uname}\n"
            f"• <b>Chat ID:</b> <code>{chat_id}</code>\n"
        )
        await self.send_message(chat_id, msg)

    async def _handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Replies to the /help command."""
        chat_id = str(update.effective_chat.id)
        user = update.effective_user
        is_adm = False
        if user:
            access_manager.record_user(user.id, user.username, user.first_name)
            is_adm = access_manager.is_admin(user.id)

        help_text = (
            "💡 <b>How to use VideoLib:</b>\n\n"
            "1. Copy the URL of any video.\n"
            "2. Paste it here in the chat.\n"
            "3. Wait for the download to complete and receive your file.\n\n"
            "📊 <b>Quota & Limits:</b>\n"
            "• Check your current status with /mylimits\n"
            "• Check your user ID with /id\n"
            "• Individual video files are capped by platform limits (under 50MB on standard Telegram API)."
        )
        if is_adm:
            help_text += "\n\n👑 <b>Admin Commands:</b> Type /adminhelp to manage bot access and limits."

        await self.send_message(chat_id, help_text)

    def _require_admin(self, update: Update) -> bool:
        """Returns True if sender is admin; records user metadata."""
        user = update.effective_user
        if not user:
            return False
        access_manager.record_user(user.id, user.username, user.first_name)
        return access_manager.is_admin(user.id)

    def _resolve_target_and_args(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> tuple[int | None, str, list[str]]:
        """
        Extracts target user (by reply or command argument) and remaining arguments.
        Returns: (user_id, display_name, remaining_args)
        """
        reply_to = update.message.reply_to_message if update.message else None
        if reply_to and reply_to.from_user:
            t_user = reply_to.from_user
            access_manager.record_user(t_user.id, t_user.username, t_user.first_name)
            name = f"@{t_user.username}" if t_user.username else (t_user.first_name or f"ID {t_user.id}")
            return t_user.id, name, list(context.args or [])

        args = list(context.args or [])
        if not args:
            return None, "", []

        first_arg = args[0]
        uid, name = access_manager.resolve_user_identifier(first_arg)
        if uid is not None:
            return uid, name, args[1:]
        elif first_arg.startswith("@"):
            return None, first_arg, args[1:]

        return None, "", args

    async def _handle_mylimits(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Shows user's quota, limits, tier, and today's usage."""
        chat_id = str(update.effective_chat.id)
        user = update.effective_user
        if not user:
            return

        access_manager.record_user(user.id, user.username, user.first_name)
        is_admin = access_manager.is_admin(user.id)
        limit_mb, limit_dl, tier_label = access_manager.get_user_limits(user.id)
        used_dl, used_bytes = access_manager.get_user_usage_today(user.id)
        used_mb = used_bytes / (1024 * 1024)
        can_dl, _ = access_manager.check_can_download(user.id)

        if is_admin:
            status_desc = "👑 Administrator (Unlimited Quota)"
        elif can_dl:
            status_desc = f"✅ Authorized ({tier_label})"
        else:
            status_desc = "⛔ Restricted (Access Approval Required)"

        rem_dl = f"{max(0, limit_dl - used_dl)}" if limit_dl > 0 else "∞"
        rem_mb = f"{max(0.0, limit_mb - used_mb):.1f} MB" if limit_mb > 0 else "∞"

        text = (
            "📊 <b>Your Limits & Usage (Today)</b>\n\n"
            f"• <b>User ID:</b> <code>{user.id}</code>\n"
            f"• <b>Status:</b> {status_desc}\n"
            f"• <b>Tier:</b> {tier_label}\n"
            f"• <b>Downloads:</b> {used_dl} / {limit_dl if limit_dl > 0 else '∞'} used (<b>{rem_dl}</b> remaining)\n"
            f"• <b>Traffic:</b> {used_mb:.1f} MB / {limit_mb if limit_mb > 0 else '∞'} MB used (<b>{rem_mb}</b> remaining)\n"
            "• <b>Quota Reset:</b> Midnight (00:00 UTC)\n"
        )
        await self.send_message(chat_id, text)

    async def _handle_allow(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin command to whitelist a user: /allow <@user | id> or by reply."""
        chat_id = str(update.effective_chat.id)
        if not self._require_admin(update):
            await self.send_message(chat_id, "⛔ This command is restricted to bot administrators.")
            return

        uid, name, _ = self._resolve_target_and_args(update, context)
        if uid is None:
            await self.send_message(
                chat_id,
                f"⚠️ Could not find user {name or 'specified'}.\n"
                "Tip: Ask the user to message the bot first, specify their numeric Telegram ID, or reply directly to their message."
            )
            return

        access_manager.allow_user(uid, True)
        await self.send_message(chat_id, f"✅ User {name} (ID: <code>{uid}</code>) is now authorized to download videos.")

    async def _handle_disallow(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin command to revoke a user: /disallow <@user | id> or by reply."""
        chat_id = str(update.effective_chat.id)
        if not self._require_admin(update):
            await self.send_message(chat_id, "⛔ This command is restricted to bot administrators.")
            return

        uid, name, _ = self._resolve_target_and_args(update, context)
        if uid is None:
            await self.send_message(
                chat_id,
                f"⚠️ Could not find user {name or 'specified'}.\n"
                "Tip: Specify their numeric Telegram ID or reply directly to their message."
            )
            return

        access_manager.allow_user(uid, False)
        await self.send_message(chat_id, f"🚫 Revoked authorization for user {name} (ID: <code>{uid}</code>).")

    async def _handle_adjustlimits(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin command to set custom limits for a user: /adjustlimits <@user | id> [mb=N] [dl=N] [reset]."""
        chat_id = str(update.effective_chat.id)
        if not self._require_admin(update):
            await self.send_message(chat_id, "⛔ This command is restricted to bot administrators.")
            return

        uid, name, args = self._resolve_target_and_args(update, context)
        if uid is None:
            await self.send_message(
                chat_id,
                "⚠️ Usage: <code>/adjustlimits &lt;@user | id&gt; [mb=N] [dl=N] [reset]</code>\n"
                "Or reply directly to a user's message with <code>/adjustlimits mb=N dl=N</code>."
            )
            return

        # Check for reset flag
        if any(a.lower() == "reset" for a in args):
            access_manager.set_user_limits(uid, reset=True)
            def_mb, def_dl = access_manager.get_global_limits()
            await self.send_message(
                chat_id,
                f"🔄 Reset custom limits for {name} (ID: <code>{uid}</code>). They now use global defaults ({def_mb} MB / {def_dl} downloads)."
            )
            return

        mb = None
        dl = None
        for arg in args:
            lower = arg.lower()
            if lower.startswith("mb="):
                with contextlib.suppress(ValueError):
                    mb = int(lower.split("=")[1])
            elif lower.startswith("dl=") or lower.startswith("downloads="):
                with contextlib.suppress(ValueError):
                    dl = int(lower.split("=")[1])
            elif lower.isdigit():
                if mb is None:
                    mb = int(lower)
                elif dl is None:
                    dl = int(lower)

        if mb is None and dl is None:
            cur_mb, cur_dl, is_custom = access_manager.get_user_limits(uid)
            await self.send_message(
                chat_id,
                f"ℹ️ Current limits for {name} (ID: <code>{uid}</code>):\n"
                f"• Bandwidth: {cur_mb} MB\n• Downloads: {cur_dl}\n• Custom: {'Yes' if is_custom else 'No (using defaults)'}\n\n"
                "To adjust, run: <code>/adjustlimits @user mb=1000 dl=25</code> or <code>/adjustlimits @user reset</code>"
            )
            return

        access_manager.set_user_limits(uid, daily_mb=mb, daily_downloads=dl)
        cur_mb, cur_dl, _ = access_manager.get_user_limits(uid)
        await self.send_message(
            chat_id,
            f"✅ Updated custom limits for {name} (ID: <code>{uid}</code>):\n"
            f"• Daily Bandwidth: <b>{cur_mb} MB</b>\n"
            f"• Daily Downloads: <b>{cur_dl} downloads</b>"
        )

    async def _handle_setlimit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin command to set tier limits: /setlimit <allowlist|public> [mb=N] [dl=N]."""
        chat_id = str(update.effective_chat.id)
        if not self._require_admin(update):
            await self.send_message(chat_id, "⛔ This command is restricted to bot administrators.")
            return

        args = list(context.args or [])
        # If no arguments, show current limits for both tiers
        if not args:
            al_mb, al_dl = access_manager.get_tier_limits("allowlist")
            pub_mb, pub_dl = access_manager.get_tier_limits("public")
            pub_status = "ENABLED 🌐" if access_manager.is_public_access_enabled() else "DISABLED 🔒"
            msg = (
                "⚙️ <b>Current Tier Daily Limits:</b>\n\n"
                f"🌟 <b>Allowlist Tier:</b> {al_mb} MB / {al_dl} downloads\n"
                f"🌐 <b>Public Tier:</b> {pub_mb} MB / {pub_dl} downloads (Status: {pub_status})\n\n"
                "<b>To update a tier:</b>\n"
                "• <code>/setlimit allowlist mb=1500 dl=25</code>\n"
                "• <code>/setlimit public mb=300 dl=5</code>"
            )
            await self.send_message(chat_id, msg)
            return

        # Determine target tier
        first = args[0].lower()
        if "allow" in first:
            tier = "allowlist"
            tier_args = args[1:]
        elif "pub" in first or "rest" in first or "guest" in first:
            tier = "public"
            tier_args = args[1:]
        else:
            tier = "allowlist"
            tier_args = args

        mb = None
        dl = None
        for arg in tier_args:
            lower = arg.lower()
            if lower.startswith("mb="):
                with contextlib.suppress(ValueError):
                    mb = int(lower.split("=")[1])
            elif lower.startswith("dl=") or lower.startswith("downloads="):
                with contextlib.suppress(ValueError):
                    dl = int(lower.split("=")[1])
            elif lower.isdigit():
                if mb is None:
                    mb = int(lower)
                elif dl is None:
                    dl = int(lower)

        if mb is None and dl is None:
            cur_mb, cur_dl = access_manager.get_tier_limits(tier)
            await self.send_message(
                chat_id,
                f"ℹ️ Current limits for <b>{tier.capitalize()} Tier</b>: {cur_mb} MB / {cur_dl} downloads.\n"
                f"Example update: <code>/setlimit {tier} mb=1000 dl=20</code>"
            )
            return

        access_manager.set_tier_limits(tier, daily_mb=mb, daily_downloads=dl)
        new_mb, new_dl = access_manager.get_tier_limits(tier)
        await self.send_message(
            chat_id,
            f"✅ Updated <b>{tier.capitalize()} Tier</b> daily limits:\n"
            f"• Daily Bandwidth: <b>{new_mb} MB</b>\n"
            f"• Daily Downloads: <b>{new_dl} downloads</b>"
        )

    async def _handle_showusage(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin command to show stats: /showusage [@user | id] or overall if no target."""
        chat_id = str(update.effective_chat.id)
        if not self._require_admin(update):
            await self.send_message(chat_id, "⛔ This command is restricted to bot administrators.")
            return

        uid, name, _ = self._resolve_target_and_args(update, context)

        if uid is not None:
            is_adm = access_manager.is_admin(uid)
            limit_mb, limit_dl, tier_label = access_manager.get_user_limits(uid)
            used_dl, used_bytes = access_manager.get_user_usage_today(uid)
            used_mb = used_bytes / (1024 * 1024)
            rem_dl = f"{max(0, limit_dl - used_dl)}" if limit_dl > 0 else "∞"
            rem_mb = f"{max(0.0, limit_mb - used_mb):.1f} MB" if limit_mb > 0 else "∞"
            can_dl, _ = access_manager.check_can_download(uid)

            msg = (
                f"👤 <b>Usage Details for {name}</b> (ID: <code>{uid}</code>)\n\n"
                f"• <b>Status:</b> {'👑 Admin' if is_adm else ('✅ Authorized' if can_dl else '⛔ Restricted')}\n"
                f"• <b>Tier:</b> {tier_label}\n"
                f"• <b>Downloads Today:</b> {used_dl} / {limit_dl if limit_dl > 0 else '∞'} (<b>{rem_dl}</b> remaining)\n"
                f"• <b>Traffic Today:</b> {used_mb:.1f} MB / {limit_mb if limit_mb > 0 else '∞'} MB (<b>{rem_mb}</b> remaining)\n"
            )
            await self.send_message(chat_id, msg)
            return
        elif name:
            await self.send_message(
                chat_id,
                f"⚠️ User '{name}' not found. Ask them to /start the bot first, or specify their numeric ID."
            )
            return

        stats = access_manager.get_system_stats_today()
        pub_status = "ENABLED 🌐" if stats["public_access_enabled"] else "DISABLED 🔒"
        msg = (
            f"📈 <b>Bot Traffic Summary (Today, {stats['date']} UTC)</b>\n\n"
            f"• <b>Active Users:</b> {stats['active_users']}\n"
            f"• <b>Total Downloads:</b> {stats['total_downloads']}\n"
            f"• <b>Total Bandwidth:</b> {stats['total_mb']:.1f} MB ({stats['total_bytes'] / (1024**3):.2f} GB)\n\n"
            "<b>Access & Tier Configuration:</b>\n"
            f"• <b>Public Access:</b> {pub_status}\n"
            f"• <b>Allowlist Tier Limits:</b> {stats['allowlist_daily_mb']} MB / {stats['allowlist_daily_downloads']} downloads\n"
            f"• <b>Public Tier Limits:</b> {stats['public_daily_mb']} MB / {stats['public_daily_downloads']} downloads\n\n"
            "Tip: Run <code>/showusage @username</code> to inspect an individual user."
        )
        await self.send_message(chat_id, msg)

    async def _handle_publicaccess(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin command to toggle public access: /publicaccess [on|off]."""
        chat_id = str(update.effective_chat.id)
        if not self._require_admin(update):
            await self.send_message(chat_id, "⛔ This command is restricted to bot administrators.")
            return

        args = list(context.args or [])
        if args and args[0].lower() in ("on", "enable", "1", "true"):
            enabled = True
        elif args and args[0].lower() in ("off", "disable", "0", "false"):
            enabled = False
        else:
            enabled = not access_manager.is_public_access_enabled()

        access_manager.set_public_access_enabled(enabled)
        pub_mb, pub_dl = access_manager.get_tier_limits("public")
        if enabled:
            await self.send_message(
                chat_id,
                f"🌐 <b>Public access is now ENABLED.</b>\n"
                f"All users can now download videos, subject to the Public Tier limits ({pub_mb} MB / {pub_dl} downloads per day)."
            )
        else:
            await self.send_message(
                chat_id,
                "🔒 <b>Public access is now DISABLED.</b>\n"
                "By default, only admins and allowlisted users are permitted to download videos."
            )

    async def _handle_allowed(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin command to list all whitelisted / admin users: /allowed."""
        chat_id = str(update.effective_chat.id)
        if not self._require_admin(update):
            await self.send_message(chat_id, "⛔ This command is restricted to bot administrators.")
            return

        users = access_manager.get_all_allowed_users()
        if not users:
            await self.send_message(chat_id, "📋 No authorized users found in whitelist.")
            return

        lines = [f"📋 <b>Authorized Users ({len(users)}):</b>\n"]
        for u in users:
            role = "👑 Admin" if u["is_admin"] or u["user_id"] in config.ADMIN_USERS else "User"
            uname = f"@{u['username']}" if u["username"] else (u["first_name"] or f"ID {u['user_id']}")
            limits_info = ""
            if u["custom_daily_mb"] is not None or u["custom_daily_downloads"] is not None:
                limits_info = f" (Custom: {u['custom_daily_mb'] or 'def'}MB / {u['custom_daily_downloads'] or 'def'}dl)"
            lines.append(f"• {uname} [<code>{u['user_id']}</code>] - {role}{limits_info}")

        await self.send_message(chat_id, "\n".join(lines))

    async def _handle_resetusage(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin command to reset a user's daily usage counters to 0: /resetusage <@user | id>."""
        chat_id = str(update.effective_chat.id)
        if not self._require_admin(update):
            await self.send_message(chat_id, "⛔ This command is restricted to bot administrators.")
            return

        uid, name, _ = self._resolve_target_and_args(update, context)
        if uid is None:
            await self.send_message(
                chat_id,
                "⚠️ Usage: <code>/resetusage &lt;@user | id&gt;</code> or reply to a message."
            )
            return

        access_manager.reset_user_usage(uid)
        await self.send_message(chat_id, f"🔄 Today's downloads and traffic for {name} (ID: <code>{uid}</code>) have been reset to 0.")

    async def _handle_adminhelp(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin command cheatsheet."""
        chat_id = str(update.effective_chat.id)
        if not self._require_admin(update):
            await self.send_message(chat_id, "⛔ This command is restricted to bot administrators.")
            return

        help_text = (
            "👑 <b>Admin Control Cheatsheet:</b>\n\n"
            "<b>Access Management:</b>\n"
            "• <code>/publicaccess [on|off]</code> - Toggle public access for all users\n"
            "• <code>/allow &lt;@user | id&gt;</code> - Grant allowlist access to a user\n"
            "• <code>/disallow &lt;@user | id&gt;</code> - Revoke allowlist access\n"
            "• <code>/allowed</code> - List all authorized allowlist users\n\n"
            "<b>Tiered Limits & Quotas:</b>\n"
            "• <code>/setlimit allowlist mb=1500 dl=25</code> - Set allowlist tier limits\n"
            "• <code>/setlimit public mb=200 dl=3</code> - Set public tier limits\n"
            "• <code>/adjustlimits &lt;@user | id&gt; mb=2000 dl=50</code> - Custom VIP limits\n"
            "• <code>/adjustlimits &lt;@user | id&gt; reset</code> - Reset to tier defaults\n"
            "• <code>/showusage [@user | id]</code> - Inspect user or overall traffic summary\n"
            "• <code>/resetusage &lt;@user | id&gt;</code> - Reset user's usage for today\n\n"
            "<i>Tip: You can also reply directly to any user's message with these commands!</i>"
        )
        await self.send_message(chat_id, help_text)

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Parses incoming text messages for URLs and triggers the callback orchestrator."""
        if not update.message or not update.message.text:
            return

        text = update.message.text.strip()
        chat = update.effective_chat
        chat_id = str(chat.id)

        from telegram.constants import ChatType

        if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            bot_username = context.bot.username
            if not bot_username:
                return # Can't check mention if username is unknown

            mention = f"@{bot_username}"

            # Use case-insensitive check since Telegram usernames are case-insensitive
            pattern = re.compile(re.escape(mention), re.IGNORECASE)
            if not pattern.search(text):
                return # Ignore messages that don't mention the bot

            # Treat the rest of the message as DM by removing the mention
            text = pattern.sub("", text).strip()

        match = self._url_pattern.search(text)
        if not match:
            # Inform user if they didn't send a link
            await self.send_message(
                chat_id,
                "⚠️ Please send a message containing a valid HTTP/HTTPS link to a video."
            )
            return

        url = match.group(1).replace("\\", "")
        message_id = str(update.message.message_id)

        user = update.effective_user
        user_id = user.id if user else None
        if user:
            access_manager.record_user(user.id, user.username, user.first_name)

        if user_id:
            can_dl, reason = access_manager.check_can_download(user_id)
            if not can_dl:
                await self.send_message(
                    chat_id,
                    reason or "⛔ Access restricted.",
                    reply_to_message_id=message_id,
                )
                return

        logger.info(f"TelegramPlatform: Found URL {url} from chat {chat_id} (user {user_id})")

        if self.message_callback:
            # Delegate handling to orchestrator asynchronously
            # We schedule it as a separate background task so we don't block the platform's event listener thread
            asyncio.create_task(
                self.message_callback("telegram", chat_id, message_id, url, user_id=user_id)
            )
        else:
            logger.warning("TelegramPlatform: Message received, but no callback registered.")

    async def _handle_debug_upload(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Replies to the /debug_upload command."""
        chat_id = str(update.effective_chat.id)
        if not context.args:
            await self.send_message(chat_id, "⚠️ Please provide a file path to retry upload.")
            return

        file_path = Path(" ".join(context.args))
        if not file_path.is_absolute():
            file_path = config.DOWNLOAD_DIR / file_path

        if not file_path.exists():
            await self.send_message(chat_id, f"⚠️ File not found: {file_path}")
            return

        await self.send_message(chat_id, f"📤 Retrying upload for {file_path.name}...")
        try:
            await self.send_video(chat_id, file_path, caption=f"Debug upload: {file_path.name}")
        except Exception as e:
            await self.send_message(chat_id, f"❌ Upload failed: {e}")

