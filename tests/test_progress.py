import asyncio
import io
import pytest
from pathlib import Path
from src.utils.progress import ProgressFileReader, MessageProgressTracker, format_progress_bar, DownloadProgress
from src.platforms.base import BasePlatform

class DummyPlatform(BasePlatform):
    def __init__(self):
        super().__init__("dummy")
        self.edits = []

    async def start(self) -> None: pass
    async def stop(self) -> None: pass
    async def send_message(self, chat_id: str, text: str, reply_to_message_id: str | None = None) -> str | None:
        return "1"
    async def edit_message(self, chat_id: str, message_id: str, text: str) -> None:
        self.edits.append((chat_id, message_id, text))
    async def send_video(self, chat_id: str, file_path: Path, caption: str | None = None, reply_to_message_id: str | None = None, **kwargs) -> None:
        pass

def test_format_progress_bar():
    assert "0.0%" in format_progress_bar("Downloading", 0.0)
    assert "50.0%" in format_progress_bar("Downloading", 50.0)
    assert "100%" in format_progress_bar("Downloading", 100.0)
    assert "█" in format_progress_bar("Downloading", 50.0)
    assert "░" in format_progress_bar("Downloading", 50.0)

    # Test with DownloadProgress object containing ETA, speed, size
    prog = DownloadProgress(45.5, eta="00:12", speed="3.45MiB/s", size="25.00MiB")
    formatted = format_progress_bar("📥 Downloading video", prog)
    assert "45.5%" in formatted
    assert "ETA: 00:12" in formatted
    assert "Speed: 3.45MiB/s" in formatted
    assert "Size: 25.00MiB" in formatted

@pytest.mark.asyncio
async def test_progress_file_reader():
    data = b"0123456789" * 100  # 1000 bytes
    bio = io.BytesIO(data)
    recorded = []

    def on_progress(pct: float):
        recorded.append(pct)

    reader = ProgressFileReader(bio, len(data), on_progress)
    read_data = reader.read(250)
    assert len(read_data) == 250
    assert 25.0 in recorded

    read_remainder = reader.read()
    assert len(read_remainder) == 750
    assert 100.0 in recorded

@pytest.mark.asyncio
async def test_message_progress_tracker_throttling():
    platform = DummyPlatform()
    tracker = MessageProgressTracker(platform, "chat123", "msg456", min_interval=0.1)

    # First update should execute immediately
    await tracker.update("Step 1: 10%")
    assert len(platform.edits) == 1
    assert platform.edits[-1][2] == "Step 1: 10%"

    # Immediate second update without force should be throttled
    await tracker.update("Step 1: 20%")
    assert len(platform.edits) == 1

    # Forced update should bypass throttling
    await tracker.update("Step 1: 30%", force=True)
    assert len(platform.edits) == 2
    assert platform.edits[-1][2] == "Step 1: 30%"

    # After waiting longer than min_interval, next update should pass
    await asyncio.sleep(0.12)
    await tracker.update("Step 1: 50%")
    assert len(platform.edits) == 3
    assert platform.edits[-1][2] == "Step 1: 50%"
