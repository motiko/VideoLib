import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from src.config import config
from src.downloader.shell_runner import ShellRunner, DownloadError

def test_validate_url():
    """Verify that only syntactically valid and shell-safe URLs are accepted."""
    runner = ShellRunner("yt-dlp {url}", 50)
    
    # Valid URLs
    assert runner.validate_url("https://youtube.com/watch?v=123") is True
    assert runner.validate_url("https://youtube.com/watch\\?v\\=123") is True  # Escaped URL
    assert runner.validate_url("http://example.com/video.mp4?size=large&format=hd") is True
    assert runner.validate_url("https://vimeo.com/123456") is True
    
    # Invalid or unsafe URLs
    assert runner.validate_url("ftp://example.com/video.mp4") is False  # Non http/https
    assert runner.validate_url("https://example.com; rm -rf /") is False  # Semi-colon injection
    assert runner.validate_url("https://example.com|cat /etc/passwd") is False  # Pipe injection
    assert runner.validate_url("https://example.com/foo bar") is False  # Whitespace
    assert runner.validate_url("") is False
    assert runner.validate_url(None) is False

def test_build_command_args():
    """Verify that templates are tokenized and placeholders replaced correctly."""
    runner = ShellRunner('yt-dlp -f mp4 -o "{output_path}" "{url}"', 50)
    out_path = Path("/tmp/downloads/video.mp4")
    url = "https://youtube.com/watch?v=123"
    
    args = runner.build_command_args(url, out_path)
    
    # Verify quotes are stripped/normalized by shlex and build_command_args
    assert args == ["yt-dlp", "-f", "mp4", "-o", str(out_path), url]

@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_download_success(mock_create_subprocess, tmp_path):
    """Verify successful download process when binary returns 0."""
    runner = ShellRunner('yt-dlp -o "{output_path}" "{url}"', 50)
    dest_file = tmp_path / "video.mp4"
    
    # Create the output file mock since the runner checks for path existence
    dest_file.write_text("fake video data")
    
    # Setup mock subprocess
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"Download successful", b"")
    mock_proc.returncode = 0
    mock_create_subprocess.return_value = mock_proc
    
    result = await runner.download("https://youtube.com/watch\\?v\\=123", dest_file)
    
    assert result == dest_file
    assert mock_create_subprocess.call_count == 1
    call_args, call_kwargs = mock_create_subprocess.call_args
    assert call_args == ("yt-dlp", "-o", str(dest_file), "https://youtube.com/watch?v=123")
    assert call_kwargs["stdout"] == asyncio.subprocess.PIPE
    assert call_kwargs["stderr"] == asyncio.subprocess.PIPE
    assert call_kwargs["env"]["PYTHONWARNINGS"] == "ignore"

@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_download_failure(mock_create_subprocess, tmp_path):
    """Verify download error is raised and failure log is generated when subprocess fails."""
    config.LOG_DIR = tmp_path / "logs"
    runner = ShellRunner("yt-dlp -o {output_path} {url}", 50)
    dest_file = tmp_path / "video.mp4"
    
    # Setup mock subprocess showing failure
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"Standard Out Data", b"ERROR: Private video")
    mock_proc.returncode = 1
    mock_create_subprocess.return_value = mock_proc
    
    with pytest.raises(DownloadError) as exc_info:
        await runner.download("https://youtube.com/watch?v=123", dest_file)
        
    assert "Download command exited with code 1" in str(exc_info.value)
    
    # Verify failure diagnostic report was created in logs/failures
    failures_dir = config.LOG_DIR / "failures"
    assert failures_dir.exists()
    reports = list(failures_dir.glob("failed_*.log"))
    assert len(reports) == 1
    report_content = reports[0].read_text()
    assert "ERROR: Private video" in report_content
    assert "Standard Out Data" in report_content
    assert "https://youtube.com/watch?v=123" in report_content

@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_download_timeout(mock_create_subprocess, tmp_path):
    """Verify download process gets killed and raises error upon timeout."""
    runner = ShellRunner("yt-dlp -o {output_path} {url}", 50)
    dest_file = tmp_path / "video.mp4"
    
    # Setup mock subprocess that triggers TimeoutError on communicate
    mock_proc = AsyncMock()
    mock_proc.kill = MagicMock()
    mock_proc.communicate.side_effect = asyncio.TimeoutError()
    mock_create_subprocess.return_value = mock_proc
    
    with pytest.raises(DownloadError) as exc_info:
        await runner.download("https://youtube.com/watch?v=123", dest_file, timeout_seconds=0.1)
        
    assert "timed out" in str(exc_info.value)
    mock_proc.kill.assert_called_once()
