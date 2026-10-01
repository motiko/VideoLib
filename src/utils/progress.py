import asyncio
import html
import io
import re
import time
from typing import Callable, Coroutine, Any
from src.platforms.base import BasePlatform
from src.utils.logger import logger

class DownloadProgress(float):
    """Float representing percentage that also carries ETA, speed, and total size metadata."""

    def __new__(
        cls,
        percent: float,
        eta: str | None = None,
        speed: str | None = None,
        size: str | None = None,
    ):
        obj = super().__new__(cls, percent)
        obj.percent = float(percent)
        obj.eta = eta
        obj.speed = speed
        obj.size = size
        return obj


def parse_eta_seconds(eta_str: str | None) -> int | None:
    """Parses MM:SS or HH:MM:SS string into seconds."""
    if not eta_str:
        return None
    try:
        clean = eta_str.replace("\u200b", "").strip()
        parts = [int(p) for p in clean.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        elif len(parts) == 1:
            return parts[0]
    except Exception:
        return None
    return None

def parse_size_bytes(size_str: str | None) -> int | None:
    """Parses human-readable size strings (e.g. 122.05MiB, 15MB) into bytes."""
    if not size_str:
        return None
    try:
        clean = size_str.lstrip("~").strip()
        m = re.match(r"^([0-9.]+)\s*([KMGT]?i?B)$", clean, re.IGNORECASE)
        if not m:
            return None
        val = float(m.group(1))
        unit = m.group(2).upper()
        multipliers = {
            "B": 1,
            "KB": 1000, "KIB": 1024,
            "MB": 1000**2, "MIB": 1024**2,
            "GB": 1000**3, "GIB": 1024**3,
            "TB": 1000**4, "TIB": 1024**4,
        }
        return int(val * multipliers.get(unit, 1024**2))
    except Exception:
        return None

def format_progress_bar(
    action: str,
    percent: float,
    eta: str | None = None,
    speed: str | None = None,
    size: str | None = None,
    title: str | None = None,
    url: str | None = None,
) -> str:
    """Formats a user-friendly progress status message using compact emoji layout."""
    pct_val = float(percent)
    pct = max(0.0, min(100.0, pct_val))
    filled = int(pct / 10)
    bar = "█" * filled + "░" * (10 - filled)
    pct_str = f"{pct:5.1f}%"

    if eta is None:
        eta = getattr(percent, "eta", None)
    if speed is None:
        speed = getattr(percent, "speed", None)
    if size is None:
        size = getattr(percent, "size", None)

    is_upload = "upload" in action.lower()
    icon = "📤" if is_upload else "📥"
    action_label = "Uploading" if is_upload else "Downloading"

    main_line = f"{icon} {action_label}: <code>[{bar}] {pct_str}</code>"

    details = []
    if eta:
        # Break colons with zero-width space (e.g. 00:\u200b25) so Telegram does not turn ETA into a clickable link
        safe_eta = eta.replace(":", ":\u200b")
        details.append(f"⏱️ ETA: {safe_eta}")
    if speed:
        details.append(f"⚡ {speed}")
    if size:
        details.append(f"📦 {size}")

    body = f"{main_line}\n{'  •  '.join(details)}" if details else main_line
    if title:
        safe_title = html.escape(title, quote=False)
        return f"{safe_title}\n\n{body}"
    return body

def format_eta(seconds: int) -> str:
    """Formats seconds into MM:SS or HH:MM:SS."""
    seconds = max(0, seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def format_speed(bytes_per_sec: float) -> str:
    """Formats bytes per second into human-readable speed (MiB/s or KiB/s)."""
    mb_per_sec = bytes_per_sec / (1024 * 1024)
    if mb_per_sec >= 1.0:
        return f"{mb_per_sec:.2f}MiB/s"
    kb_per_sec = bytes_per_sec / 1024
    return f"{kb_per_sec:.1f}KiB/s"

class UploadSpeedEstimator:
    """Tracks and updates an exponentially weighted moving average of upload speed."""

    def __init__(self, default_speed_mb: float = 3.8):
        self.current_speed_bps = default_speed_mb * 1024 * 1024

    def record_upload(self, file_size_bytes: int, duration_seconds: float) -> None:
        if duration_seconds > 0.5 and file_size_bytes > 0:
            measured = file_size_bytes / duration_seconds
            # 70% weight on recent upload, 30% historical average
            self.current_speed_bps = 0.7 * measured + 0.3 * self.current_speed_bps

    def get_speed_bps(self) -> float:
        return max(1024 * 100.0, self.current_speed_bps)

upload_speed_estimator = UploadSpeedEstimator()

async def run_estimated_upload_ticker(
    tracker: "MessageProgressTracker",
    file_size_bytes: int,
    speed_estimator: UploadSpeedEstimator = upload_speed_estimator,
    update_interval: float = 1.5,
) -> None:
    """Continuously updates status message with estimated upload percentage, speed, and ETA."""
    start_time = time.monotonic()
    size_mb = file_size_bytes / (1024 * 1024)
    size_str = f"{size_mb:.2f}MiB"
    speed_bps = speed_estimator.get_speed_bps()

    initial_eta = int(file_size_bytes / speed_bps) if speed_bps > 0 else 0
    initial_prog = DownloadProgress(
        0.0,
        eta=format_eta(initial_eta),
        speed=format_speed(speed_bps),
        size=size_str
    )
    await tracker.update(
        format_progress_bar("Uploading", initial_prog, title=tracker.title),
        force=True
    )

    while True:
        await asyncio.sleep(update_interval)
        elapsed = time.monotonic() - start_time
        speed_bps = speed_estimator.get_speed_bps()

        est_bytes = elapsed * speed_bps
        est_pct = min(98.0, (est_bytes / file_size_bytes) * 100.0) if file_size_bytes > 0 else 98.0
        remaining_bytes = max(0, file_size_bytes - (est_pct / 100.0 * file_size_bytes))
        eta_sec = int(remaining_bytes / speed_bps) if speed_bps > 0 else 0

        prog = DownloadProgress(
            est_pct,
            eta=format_eta(eta_sec),
            speed=format_speed(speed_bps),
            size=size_str
        )
        await tracker.update(
            format_progress_bar("Uploading", prog, title=tracker.title)
        )

class ProgressFileReader(io.IOBase):
    """File wrapper that tracks bytes read and invokes a progress callback."""

    def __init__(
        self,
        file_obj: Any,
        total_size: int,
        progress_callback: Callable[[float], Coroutine[Any, Any, None]] | Callable[[float], None] | None = None,
    ):
        self._file = file_obj
        self.total_size = total_size
        self.progress_callback = progress_callback
        self.bytes_read = 0

    @property
    def mode(self) -> str:
        return getattr(self._file, "mode", "rb")

    @property
    def name(self) -> str:
        return getattr(self._file, "name", "")

    @property
    def closed(self) -> bool:
        return getattr(self._file, "closed", False)

    def read(self, size: int = -1) -> bytes:
        chunk = self._file.read(size)
        if chunk:
            self.bytes_read += len(chunk)
            if self.progress_callback and self.total_size > 0:
                pct = min(100.0, (self.bytes_read / self.total_size) * 100.0)
                try:
                    res = self.progress_callback(pct)
                    if asyncio.iscoroutine(res):
                        asyncio.create_task(res)
                except Exception:
                    pass
        return chunk

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        res = self._file.seek(offset, whence)
        self.bytes_read = self._file.tell()
        return res

    def tell(self) -> int:
        return self._file.tell()

    def fileno(self) -> int:
        return self._file.fileno()

    def close(self) -> None:
        self._file.close()

class MessageProgressTracker:
    """Throttles status message edits to avoid rate limits."""

    def __init__(
        self,
        platform: BasePlatform,
        chat_id: str,
        message_id: str | None,
        min_interval: float = 1.5,
        title: str | None = None,
        url: str | None = None,
    ):
        self.platform = platform
        self.chat_id = chat_id
        self.message_id = message_id
        self.min_interval = min_interval
        self.title = title
        self.url = url
        self.last_update_time: float = 0.0
        self.last_text: str = ""
        self.lock = asyncio.Lock()

    def set_title(self, title: str, url: str) -> None:
        """Sets or updates the video title and URL to prepend to progress messages."""
        self.title = title
        self.url = url

    async def update(self, text: str, force: bool = False) -> None:
        if not self.message_id:
            return
        now = time.monotonic()
        if not force:
            if now - self.last_update_time < self.min_interval:
                return
            if text == self.last_text:
                return
            if self.lock.locked():
                return
        elif text == self.last_text:
            return

        async with self.lock:
            now = time.monotonic()
            if not force and (now - self.last_update_time < self.min_interval):
                return
            self.last_update_time = now
            self.last_text = text
            try:
                await self.platform.edit_message(self.chat_id, self.message_id, text)
            except Exception as e:
                logger.debug(f"ProgressTracker: Failed to edit message: {e}")
