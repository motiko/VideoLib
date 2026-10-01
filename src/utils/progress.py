import asyncio
import io
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

def format_progress_bar(
    action: str,
    percent: float,
    eta: str | None = None,
    speed: str | None = None,
    size: str | None = None,
) -> str:
    """Formats a user-friendly progress status message with a progress bar, percentage, ETA, speed, and size."""
    pct_val = float(percent)
    pct = max(0.0, min(100.0, pct_val))
    filled = int(pct / 10)
    bar = "█" * filled + "░" * (10 - filled)
    pct_str = f"{pct:.1f}%" if pct < 99.95 else "100%"

    header = f"{action}... {pct_str} [{bar}]"

    if eta is None:
        eta = getattr(percent, "eta", None)
    if speed is None:
        speed = getattr(percent, "speed", None)
    if size is None:
        size = getattr(percent, "size", None)

    details = []
    if eta:
        details.append(f"ETA: {eta}")
    if speed:
        details.append(f"Speed: {speed}")
    if size:
        details.append(f"Size: {size}")

    if details:
        return f"{header}\n{' | '.join(details)}"
    return header

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
    await tracker.update(format_progress_bar("📤 Uploading to chat", initial_prog), force=True)

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
        await tracker.update(format_progress_bar("📤 Uploading to chat", prog))

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
    ):
        self.platform = platform
        self.chat_id = chat_id
        self.message_id = message_id
        self.min_interval = min_interval
        self.last_update_time: float = 0.0
        self.last_text: str = ""
        self.lock = asyncio.Lock()

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
