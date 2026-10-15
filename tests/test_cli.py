import pytest
from unittest.mock import patch
from src.platforms.cli import CliPlatform
from src.run import parse_args

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
    # Test delete_message
    res = await cli.delete_message("cli_user", "msg123")
    assert res is True
    captured = capsys.readouterr()
    assert "[CLI Status] Deleted message msg123" in captured.out

    # Test react_to_message
    r_res = await cli.react_to_message("cli_user", "msg123", "✅")
    assert r_res is True
    captured = capsys.readouterr()
    assert "[CLI Reaction] ✅ on message msg123" in captured.out

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


def test_reexec_uses_same_interpreter_and_argv(monkeypatch):
    import sys
    from src import run as run_mod
    calls = []
    monkeypatch.setattr(run_mod.os, "execv", lambda exe, argv: calls.append((exe, argv)))
    run_mod._reexec()
    assert calls == [(sys.executable, [sys.executable, *sys.argv])]


def _launchctl(stdout: str):
    from unittest.mock import MagicMock
    return MagicMock(return_value=MagicMock(stdout=stdout))


@pytest.mark.asyncio
async def test_status_detects_brew_service(capsys, monkeypatch, tmp_path):
    from src import run as run_mod
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "brew").write_text("")
    logs = tmp_path / "var" / "log" / "videolib"
    logs.mkdir(parents=True)
    (logs / "videolib.log").write_text("x")
    monkeypatch.setenv("HOMEBREW_PREFIX", str(tmp_path))
    monkeypatch.setattr(run_mod.subprocess, "run", _launchctl("80343\t0\tsh.brew.videolib\n123\t0\tcom.apple.foo\n"))

    await run_mod.check_service_status()
    out = capsys.readouterr().out
    assert "Running (PID 80343, launchd job sh.brew.videolib)" in out
    assert str(logs) in out and "1 file(s)" in out


@pytest.mark.asyncio
async def test_status_detects_custom_plist_and_not_loaded(capsys, monkeypatch, tmp_path):
    from src import run as run_mod
    monkeypatch.setenv("HOMEBREW_PREFIX", str(tmp_path))  # no brew binary here
    monkeypatch.setattr(run_mod.subprocess, "run", _launchctl("-\t1\tcom.videolib.agent\n"))
    await run_mod.check_service_status()
    out = capsys.readouterr().out
    assert "Loaded but not running (launchd job com.videolib.agent, exit code: 1)" in out

    monkeypatch.setattr(run_mod.subprocess, "run", _launchctl("123\t0\tcom.apple.foo\n"))
    await run_mod.check_service_status()
    out = capsys.readouterr().out
    assert "Not loaded" in out
