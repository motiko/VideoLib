import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock, ANY
from src.core.orchestrator import Orchestrator
from src.platforms.base import BasePlatform
from src.downloader.shell_runner import DownloadError

class MockPlatform(BasePlatform):
    """Platform implementation mock for unit tests."""
    def __init__(self, name):
        super().__init__(name)
        self.sent_messages = []
        self.edited_messages = []
        self.deleted_messages = []
        self.reactions = []
        self.sent_videos = []
        self._msg_id_counter = 0

    async def start(self) -> None: pass
    async def stop(self) -> None: pass
    async def send_message(self, chat_id: str, text: str, reply_to_message_id: str | None = None) -> str | None:
        self.sent_messages.append((chat_id, text, reply_to_message_id))
        self._msg_id_counter += 1
        return str(self._msg_id_counter)
        
    async def edit_message(self, chat_id: str, message_id: str, text: str) -> None:
        self.edited_messages.append((chat_id, message_id, text))

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        self.deleted_messages.append((chat_id, message_id))
        return True

    async def react_to_message(self, chat_id: str, message_id: str, emoji: str = "✅") -> bool:
        self.reactions.append((chat_id, message_id, emoji))
        return True

    async def send_video(self, chat_id: str, file_path: Path, caption: str | None = None, reply_to_message_id: str | None = None, **kwargs) -> None:
        self.sent_videos.append((chat_id, file_path, caption, reply_to_message_id))

@pytest.fixture
def orchestrator():
    return Orchestrator()

@pytest.fixture
def mock_platform(orchestrator):
    platform = MockPlatform("mock_platform")
    orchestrator.register_platform(platform)
    return platform

@pytest.mark.asyncio
@patch("src.core.orchestrator.shell_runner")
@patch("src.core.orchestrator.storage_manager")
async def test_orchestrator_success(mock_storage, mock_runner, orchestrator, mock_platform):
    """Verify that a valid URL triggers title extraction, download, upload, and cleanup successfully."""
    # Setup mocks
    mock_runner.validate_url.return_value = True
    mock_runner.extract_title = AsyncMock(return_value="My Test Title")
    temp_path = Path("/tmp/downloads/video.mp4")
    mock_storage.generate_path.return_value = temp_path
    
    # Use AsyncMock for async methods
    mock_runner.download = AsyncMock(return_value=temp_path)
    mock_storage.cleanup = AsyncMock(return_value=True)
    
    url = "https://youtube.com/watch?v=123"
    await orchestrator.handle_request("mock_platform", "12345", "101", url)
    
    # Assert lifecycle progression reactions were added to the incoming message
    assert ("12345", "101", "👀") in mock_platform.reactions
    assert ("12345", "101", "⚡") in mock_platform.reactions
    assert ("12345", "101", "🚀") in mock_platform.reactions
    assert ("12345", "101", "💯") in mock_platform.reactions

    # Assert title extraction was called
    mock_runner.extract_title.assert_called_once_with(url)

    # Assert download was called
    mock_runner.download.assert_called_once_with(url, temp_path, progress_callback=ANY)
    
    # Assert video was sent with plain text title caption
    assert len(mock_platform.sent_videos) == 1
    assert mock_platform.sent_videos[0][0] == "12345"
    assert mock_platform.sent_videos[0][1] == temp_path
    assert mock_platform.sent_videos[0][2] == "My Test Title"
    assert mock_platform.sent_videos[0][3] == "101"

    # Assert status message was deleted
    assert len(mock_platform.deleted_messages) == 1
    assert mock_platform.deleted_messages[0] == ("12345", "1")
    
    # Assert cleanup was called
    mock_storage.cleanup.assert_called_once_with(temp_path)

@pytest.mark.asyncio
@patch("src.core.orchestrator.shell_runner")
@patch("src.core.orchestrator.storage_manager")
async def test_orchestrator_download_error(mock_storage, mock_runner, orchestrator, mock_platform):
    """Verify that downloader errors are reported and cleanup is still executed."""
    mock_runner.validate_url.return_value = True
    temp_path = Path("/tmp/downloads/video.mp4")
    mock_storage.generate_path.return_value = temp_path
    
    # Mock download to raise DownloadError
    mock_runner.download = AsyncMock(side_effect=DownloadError("File too large"))
    mock_storage.cleanup = AsyncMock(return_value=True)
    
    url = "https://youtube.com/watch?v=123"
    await orchestrator.handle_request("mock_platform", "12345", "101", url)
    
    # Verify error message sent contains user-friendly message
    assert any("too large" in msg[2].lower() or "File too large" in msg[2] for msg in mock_platform.edited_messages)
    # Verify failure reaction was set
    assert ("12345", "101", "👎") in mock_platform.reactions
    
    # Verify cleanup still ran
    mock_storage.cleanup.assert_called_once_with(temp_path)
    assert len(mock_platform.sent_videos) == 0

@pytest.mark.asyncio
@patch("src.core.orchestrator.shell_runner")
@patch("src.core.orchestrator.storage_manager")
async def test_orchestrator_invalid_url(mock_storage, mock_runner, orchestrator, mock_platform):
    """Verify that invalid URLs are rejected without calling downloader or storage manager."""
    mock_runner.validate_url.return_value = False
    mock_runner.download = AsyncMock()
    mock_storage.cleanup = AsyncMock()
    
    await orchestrator.handle_request("mock_platform", "12345", "101", "ftp://unsafe-link")
    
    mock_runner.download.assert_not_called()
    mock_storage.generate_path.assert_not_called()
    assert any("Invalid or unsafe URL" in msg[1] for msg in mock_platform.sent_messages)

@pytest.mark.asyncio
@patch("src.core.orchestrator.shell_runner")
@patch("src.core.orchestrator.storage_manager")
async def test_orchestrator_progress_updates(mock_storage, mock_runner, orchestrator, mock_platform, tmp_path):
    """Verify that progress callbacks update the reply message with percentage, progress bar, ETA, and speed."""
    from src.utils.progress import DownloadProgress
    mock_runner.validate_url.return_value = True
    mock_runner.extract_title = AsyncMock(return_value="Progress Title")
    temp_path = tmp_path / "video.mp4"
    temp_path.write_bytes(b"A" * 10000)
    mock_storage.generate_path.return_value = temp_path

    async def fake_download(url, output_path, progress_callback=None):
        if progress_callback:
            prog = DownloadProgress(42.5, eta="00:05", speed="2.50MiB/s", size="15.00MiB")
            await progress_callback(prog)
        return temp_path

    async def fake_send_video(chat_id, file_path, caption=None, reply_to_message_id=None, **kwargs):
        await asyncio.sleep(0.01)
        mock_platform.sent_videos.append((chat_id, file_path, caption, reply_to_message_id))

    mock_runner.download = AsyncMock(side_effect=fake_download)
    mock_storage.cleanup = AsyncMock(return_value=True)
    mock_platform.send_video = fake_send_video

    url = "https://youtube.com/watch?v=123"
    await orchestrator.handle_request("mock_platform", "12345", "101", url)

    # Check that edited messages contain progress info and title
    edited_texts = [msg[2] for msg in mock_platform.edited_messages]
    assert any("Progress Title" in t for t in edited_texts)
    assert any("Downloading" in t and "42.5%" in t for t in edited_texts)
    assert any("ETA" in t for t in edited_texts)
    assert any("2.50MiB/s" in t for t in edited_texts)
    assert any("Uploading" in t for t in edited_texts)

    # Check video caption has plain text title
    assert mock_platform.sent_videos[0][2] == "Progress Title"
    # Check status message was deleted
    assert len(mock_platform.deleted_messages) == 1


@pytest.mark.asyncio
@patch("src.core.orchestrator.shell_runner")
@patch("src.core.orchestrator.storage_manager")
async def test_orchestrator_upload_error(mock_storage, mock_runner, orchestrator, mock_platform, tmp_path):
    """Verify that upload errors are classified and reported with friendly messages."""
    mock_runner.validate_url.return_value = True
    mock_runner.extract_title = AsyncMock(return_value="Test Video")
    temp_path = tmp_path / "video.mp4"
    temp_path.write_bytes(b"A" * 1000)
    mock_storage.generate_path.return_value = temp_path
    mock_runner.download = AsyncMock(return_value=temp_path)
    mock_storage.cleanup = AsyncMock(return_value=True)

    # Mock send_video to raise a Telegram file-too-big error
    async def failing_send_video(**kwargs):
        raise Exception("telegram.error.BadRequest: File is too big")
    mock_platform.send_video = failing_send_video

    await orchestrator.handle_request("mock_platform", "12345", "101", "https://example.com/video")

    # Should show user-friendly message, not raw exception
    assert any("too large" in msg[2].lower() for msg in mock_platform.edited_messages)
    # Should have failure reaction
    assert ("12345", "101", "👎") in mock_platform.reactions
    # Cleanup should still run
    mock_storage.cleanup.assert_called_once_with(temp_path)

