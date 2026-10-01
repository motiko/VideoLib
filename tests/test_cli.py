import pytest
from pathlib import Path
from unittest.mock import patch
from src.platforms.cli import CliPlatform
from run import parse_args

@pytest.mark.asyncio
async def test_cli_platform_messaging(capsys, tmp_path):
    """Verify CliPlatform formats and prints status messages and video output."""
    cli = CliPlatform()
    await cli.start()
    
    # Test send_message
    await cli.send_message("cli_user", "Downloading media...")
    captured = capsys.readouterr()
    assert "[CLI Status] Downloading media..." in captured.out
    
    # Test send_video
    dummy_video = tmp_path / "video.mp4"
    dummy_video.write_text("test video payload")
    
    await cli.send_video("cli_user", dummy_video, caption="Test Caption")
    captured = capsys.readouterr()
    assert "[CLI Deliverable]" in captured.out
    assert str(dummy_video) in captured.out
    assert "Test Caption" in captured.out
    
    await cli.stop()

def test_parse_args():
    """Verify argparse parsing for --url, --cli, --debug, and --keep-files."""
    test_argv = ["run.py", "--url", "https://example.com/video", "--debug", "--keep-files"]
    with patch("sys.argv", test_argv):
        args = parse_args()
        assert args.url == "https://example.com/video"
        assert args.cli is False
        assert args.debug is True
        assert args.keep_files is True
