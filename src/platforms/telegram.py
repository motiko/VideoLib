import asyncio
import re
from typing import Callable, Coroutine, Any
from pathlib import Path
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from src.platforms.base import BasePlatform
from src.utils.logger import logger
from src.utils.progress import ProgressFileReader
from src.config import config

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
        
        # Add basic commands
        self.application.add_handler(CommandHandler("start", self._handle_start))
        self.application.add_handler(CommandHandler("help", self._handle_help))
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
        """Sends a text message back to the Telegram chat."""
        if not self.application:
            raise RuntimeError("Telegram application is not running.")
        try:
            kwargs = {"chat_id": chat_id, "text": text}
            if reply_to_message_id:
                kwargs["reply_to_message_id"] = int(reply_to_message_id)
            msg = await self.application.bot.send_message(**kwargs)
            return str(msg.message_id)
        except Exception as e:
            logger.error(f"TelegramPlatform: Failed to send message to {chat_id}: {e}")
            return None

    async def edit_message(self, chat_id: str, message_id: str, text: str) -> None:
        """Edits an existing message in the Telegram chat."""
        if not self.application:
            raise RuntimeError("Telegram application is not running.")
        try:
            await self.application.bot.edit_message_text(chat_id=chat_id, message_id=int(message_id), text=text)
        except Exception as e:
            err_str = str(e).lower()
            if "not modified" in err_str:
                return
            if "retry_after" in err_str or "flood" in err_str:
                logger.warning(f"TelegramPlatform: Rate limited editing message {message_id} in {chat_id}: {e}")
                return
            logger.error(f"TelegramPlatform: Failed to edit message {message_id} in {chat_id}: {e}")

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
        welcome_text = (
            "🤖 **Welcome to VideoLib!**\n\n"
            "Send me a message containing a video URL (e.g. YouTube, TikTok, Twitter/X), "
            "and I will download and send you the video file directly."
        )
        await self.send_message(chat_id, welcome_text)

    async def _handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Replies to the /help command."""
        chat_id = str(update.effective_chat.id)
        help_text = (
            "💡 **How to use VideoLib:**\n\n"
            "1. Copy the URL of any video.\n"
            "2. Paste it here in the chat.\n"
            "3. Wait for the download to complete and receive your file.\n\n"
            "Note: Files are subject to limits (typically under 50MB) for upload compatibility."
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
        logger.info(f"TelegramPlatform: Found URL {url} from chat {chat_id}")
        
        message_id = str(update.message.message_id)

        if self.message_callback:
            # Delegate handling to orchestrator asynchronously
            # We schedule it as a separate background task so we don't block the platform's event listener thread
            asyncio.create_task(self.message_callback("telegram", chat_id, message_id, url))
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

