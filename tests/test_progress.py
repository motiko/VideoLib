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
        self.deleted = []

    async def start(self) -> None: pass
    async def stop(self) -> None: pass
    async def send_message(self, chat_id: str, text: str, reply_to_message_id: str | None = None) -> str | None:
        return "1"
    async def edit_message(self, chat_id: str, message_id: str, text: str) -> None:
        self.edits.append((chat_id, message_id, text))
    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        self.deleted.append((chat_id, message_id))
        return True
    async def react_to_message(self, chat_id: str, message_id: str, emoji: str = "✅") -> bool:
        return True
    async def send_video(self, chat_id: str, file_path: Path, caption: str | None = None, reply_to_message_id: str | None = None, **kwargs) -> None:
        pass

def test_format_progress_bar():
    assert "0.0%" in format_progress_bar("Downloading", 0.0)
    assert "50.0%" in format_progress_bar("Downloading", 50.0)
    assert "100" in format_progress_bar("Downloading", 100.0)
    assert "█" in format_progress_bar("Downloading", 50.0)
    assert "░" in format_progress_bar("Downloading", 50.0)

    # Test with DownloadProgress object containing ETA, speed, size
    prog = DownloadProgress(45.5, eta="00:12", speed="3.45MiB/s", size="25.00MiB")
    formatted = format_progress_bar("Downloading", prog)
    assert "45.5%" in formatted
    # Check ETA has colons broken with zero-width space so Telegram client does not linkify it
    assert "00:\u200b12" in formatted
    assert "3.45MiB/s" in formatted
    assert "25.00MiB" in formatted
    assert "📥 Downloading:" in formatted

    assert "<code>" in formatted
    assert "</code>" in formatted

    # Test with title (plain text title without link, properly HTML-escaped)
    formatted_with_title = format_progress_bar(
        "Downloading",
        prog,
        title="Sample Video & Clips <HD>",
        url="https://youtube.com/watch?v=abc"
    )
    assert formatted_with_title.startswith("Sample Video &amp; Clips &lt;HD&gt;\n\n📥 Downloading:")

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

def test_format_eta_and_speed():
    from src.utils.progress import format_eta, format_speed
    assert format_eta(15) == "00:15"
    assert format_eta(75) == "01:15"
    assert format_eta(3665) == "01:01:05"

    assert format_speed(3.5 * 1024 * 1024) == "3.50MiB/s"
    assert format_speed(500 * 1024) == "500.0KiB/s"

def test_upload_speed_estimator():
    from src.utils.progress import UploadSpeedEstimator
    est = UploadSpeedEstimator(default_speed_mb=2.0)
    assert est.get_speed_bps() == 2.0 * 1024 * 1024

    # 10MB in 2 seconds = 5MB/s
    # 0.7 * 5 + 0.3 * 2 = 3.5 + 0.6 = 4.1MB/s
    est.record_upload(10 * 1024 * 1024, 2.0)
    expected_mb = 4.1
    actual_mb = est.get_speed_bps() / (1024 * 1024)
    assert abs(actual_mb - expected_mb) < 0.01

@pytest.mark.asyncio
async def test_run_estimated_upload_ticker():
    from src.utils.progress import run_estimated_upload_ticker, UploadSpeedEstimator
    platform = DummyPlatform()
    tracker = MessageProgressTracker(platform, "chat123", "msg456", min_interval=0.01)
    estimator = UploadSpeedEstimator(default_speed_mb=10.0)

    # File size 10MB
    file_size = 10 * 1024 * 1024
    task = asyncio.create_task(
        run_estimated_upload_ticker(tracker, file_size, estimator, update_interval=0.05)
    )

    await asyncio.sleep(0.12)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(platform.edits) >= 1
    assert any("Uploading" in edit[2] for edit in platform.edits)
    assert any("10.00MiB" in edit[2] for edit in platform.edits)

def test_parse_eta_and_size():
    from src.utils.progress import parse_eta_seconds, parse_size_bytes
    assert parse_eta_seconds("00:25") == 25
    assert parse_eta_seconds("01:15") == 75
    assert parse_eta_seconds("01:02:03") == 3723
    assert parse_eta_seconds(None) is None

    assert parse_size_bytes("100B") == 100
    assert parse_size_bytes("10.00MiB") == 10 * 1024 * 1024
    assert parse_size_bytes("~ 5.5MiB") == int(5.5 * 1024 * 1024)
    assert parse_size_bytes(None) is None
