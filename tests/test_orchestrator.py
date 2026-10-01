import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from src.core.orchestrator import Orchestrator
from src.platforms.base import BasePlatform
from src.downloader.shell_runner import DownloadError

class MockPlatform(BasePlatform):
    """Platform implementation mock for unit tests."""
    def __init__(self, name):
        super().__init__(name)
        self.sent_messages = []
        self.sent_videos = []

    async def start(self) -> None: pass
    async def stop(self) -> None: pass
    async def send_message(self, chat_id: str, text: str) -> None:
        self.sent_messages.append((chat_id, text))
    async def send_video(self, chat_id: str, file_path: Path, caption: str | None = None) -> None:
        self.sent_videos.append((chat_id, file_path, caption))

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
    """Verify that a valid URL triggers download, upload, and cleanup successfully."""
    # Setup mocks
    mock_runner.validate_url.return_value = True
    temp_path = Path("/tmp/downloads/video.mp4")
    mock_storage.generate_path.return_value = temp_path
    
    # Use AsyncMock for async methods
    mock_runner.download = AsyncMock(return_value=temp_path)
    mock_storage.cleanup = AsyncMock(return_value=True)
    
    url = "https://youtube.com/watch?v=123"
    await orchestrator.handle_request("mock_platform", "12345", url)
    
    # Assert download was called
    mock_runner.download.assert_called_once_with(url, temp_path)
    
    # Assert video was sent
    assert len(mock_platform.sent_videos) == 1
    assert mock_platform.sent_videos[0][0] == "12345"
    assert mock_platform.sent_videos[0][1] == temp_path
    assert url in mock_platform.sent_videos[0][2]
    
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
    await orchestrator.handle_request("mock_platform", "12345", url)
    
    # Verify error message sent
    assert any("Download error: File too large" in msg[1] for msg in mock_platform.sent_messages)
    
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
    
    await orchestrator.handle_request("mock_platform", "12345", "ftp://unsafe-link")
    
    mock_runner.download.assert_not_called()
    mock_storage.generate_path.assert_not_called()
    assert any("Invalid or unsafe URL" in msg[1] for msg in mock_platform.sent_messages)
