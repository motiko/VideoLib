import asyncio
import contextlib
import inspect
import time
from pathlib import Path
from src.config import config
from src.core.errors import classify_download_error, classify_upload_error, format_error_message
from src.downloader.shell_runner import shell_runner, DownloadError
from src.storage.manager import storage_manager
from src.platforms.base import BasePlatform
from src.utils.logger import logger
from src.utils.progress import (
    MessageProgressTracker,
    format_progress_bar,
    run_estimated_upload_ticker,
    upload_speed_estimator,
    format_speed,
    format_eta,
    parse_eta_seconds,
    parse_size_bytes,
    DownloadProgress,
)

class Orchestrator:
    """Coordinates downloading tasks and routes messages between the platforms, downloader, and storage manager."""

    def __init__(self):
        self.platforms: dict[str, BasePlatform] = {}
        # Concurrency semaphore to throttle concurrent download tasks
        self.semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_DOWNLOADS)

    def register_platform(self, platform: BasePlatform) -> None:
        """Registers a platform adapter (e.g. TelegramPlatform) and binds the orchestrator callback."""
        self.platforms[platform.name] = platform
        platform.register_callback(self.handle_request)
        logger.info(f"Orchestrator: Registered platform adapter '{platform.name}'")

    async def handle_request(self, platform_name: str, chat_id: str, message_id: str, url: str) -> None:
        """Callback invoked by platform adapters when a download request is received."""
        platform = self.platforms.get(platform_name)
        if not platform:
            logger.error(f"Orchestrator: Platform '{platform_name}' requested but not registered.")
            return

        # Safe URL check beforehand
        if not shell_runner.validate_url(url):
            await platform.send_message(
                chat_id,
                "❌ Error: Invalid or unsafe URL. Make sure it starts with http:// or https:// and contains no illegal characters.",
                reply_to_message_id=message_id
            )
            return

        # 1. Received / Queued: Eyes reaction
        await platform.react_to_message(chat_id, message_id, "👀")

        file_path: Path | None = None
        status_msg_id: str | None = None

        try:
            # Let the user know the bot is waiting for a slot in the concurrency queue if locked
            if self.semaphore.locked():
                status_msg_id = await platform.send_message(chat_id, "⏳ System busy. Your request is queued...", reply_to_message_id=message_id)

            async with self.semaphore:
                # 1. Extract video title before starting the status reply message
                title = None
                try:
                    res = shell_runner.extract_title(url)
                    title = await res if inspect.isawaitable(res) else res
                except Exception as e:
                    logger.debug(f"Orchestrator: Could not extract title: {e}")

                # 2. Starting actual download: Lightning reaction
                await platform.react_to_message(chat_id, message_id, "⚡")

                initial_status = format_progress_bar("Downloading", 0.0, title=title)
                if status_msg_id:
                    await platform.edit_message(chat_id, status_msg_id, initial_status)
                else:
                    status_msg_id = await platform.send_message(chat_id, initial_status, reply_to_message_id=message_id)

                tracker = MessageProgressTracker(platform, chat_id, status_msg_id)
                tracker.last_text = initial_status
                if title:
                    tracker.set_title(title, url)

                async def on_download_progress(percent: float) -> None:
                    # Calculate combined ETA (download ETA + estimated upload ETA)
                    dl_sec = parse_eta_seconds(getattr(percent, "eta", None))
                    total_bytes = parse_size_bytes(getattr(percent, "size", None))
                    upload_bps = upload_speed_estimator.get_speed_bps()
                    upload_est_sec = int(total_bytes / upload_bps) if (total_bytes and upload_bps > 0) else 0

                    if dl_sec is not None:
                        combined_eta = format_eta(dl_sec + upload_est_sec)
                    else:
                        combined_eta = getattr(percent, "eta", None)

                    text = format_progress_bar(
                        "Downloading",
                        percent,
                        eta=combined_eta,
                        speed=getattr(percent, "speed", None),
                        size=getattr(percent, "size", None),
                        title=tracker.title,
                    )
                    await tracker.update(text)

                # Generate a secure temporary path
                file_path = storage_manager.generate_path(suffix=".mp4")

                # Run the download command
                downloaded_file = await shell_runner.download(
                    url,
                    file_path,
                    progress_callback=on_download_progress
                )

                # 3. Download finished, starting upload: Rocket reaction
                await platform.react_to_message(chat_id, message_id, "🚀")

                # Deliver the file with background estimated upload ticker
                file_size_bytes = downloaded_file.stat().st_size if downloaded_file.exists() else 0
                upload_start = time.monotonic()
                ticker_task = None
                if status_msg_id and file_size_bytes > 0:
                    ticker_task = asyncio.create_task(
                        run_estimated_upload_ticker(tracker, file_size_bytes, upload_speed_estimator)
                    )

                caption = tracker.title if tracker.title else url
                try:
                    await platform.send_video(
                        chat_id=chat_id,
                        file_path=downloaded_file,
                        caption=caption,
                        reply_to_message_id=message_id,
                    )
                    # 4. Successfully delivered: 100 reaction!
                    await platform.react_to_message(chat_id, message_id, "💯")

                    # Remove status message so final result is one URL, one reply
                    if status_msg_id:
                        with contextlib.suppress(Exception):
                            await platform.delete_message(chat_id, status_msg_id)
                        status_msg_id = None
                finally:
                    if ticker_task:
                        ticker_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await ticker_task

                upload_duration = time.monotonic() - upload_start
                if file_size_bytes > 0 and upload_duration > 0.5:
                    upload_speed_estimator.record_upload(file_size_bytes, upload_duration)
                    actual_speed = file_size_bytes / upload_duration
                else:
                    actual_speed = upload_speed_estimator.get_speed_bps()

                # Complete status message at 100% if status message still exists
                if status_msg_id and file_size_bytes > 0:
                    size_mb = file_size_bytes / (1024 * 1024)
                    final_prog = DownloadProgress(
                        100.0,
                        eta="00:00",
                        speed=format_speed(actual_speed),
                        size=f"{size_mb:.2f}MiB",
                    )
                    await tracker.update(
                        format_progress_bar("📤 Uploading to chat", final_prog, title=tracker.title, url=tracker.url),
                        force=True
                    )

        except DownloadError as de:
            logger.warning(f"Orchestrator: Download failed for {url} in chat {chat_id}: {de}")
            user_error = classify_download_error(str(de))
            error_text = format_error_message(user_error)
            if status_msg_id:
                await platform.edit_message(chat_id, status_msg_id, error_text)
            else:
                await platform.send_message(chat_id, error_text, reply_to_message_id=message_id)
            await platform.react_to_message(chat_id, message_id, "👎")
        except Exception as e:
            logger.exception(f"Orchestrator: Unexpected exception occurred handling URL {url} in chat {chat_id}: {e}")
            user_error = classify_upload_error(str(e))
            error_text = format_error_message(user_error)
            if status_msg_id:
                await platform.edit_message(chat_id, status_msg_id, error_text)
            else:
                await platform.send_message(chat_id, error_text, reply_to_message_id=message_id)
            await platform.react_to_message(chat_id, message_id, "👎")
        finally:
            # Guarantee cleanup of files to prevent filling up the disk
            if file_path:
                await storage_manager.cleanup(file_path)
