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
