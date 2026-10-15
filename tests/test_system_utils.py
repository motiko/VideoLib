from unittest.mock import AsyncMock, patch

import pytest

from src.utils import system as sysu


def test_format_uptime():
    assert sysu.format_uptime(0) == "0s"
    assert sysu.format_uptime(59) == "59s"
    assert sysu.format_uptime(61) == "1m 1s"
    assert sysu.format_uptime(3600 * 26 + 60 * 5) == "1d 2h 5m"
    assert sysu.format_uptime(7200) == "2h 0m"


def test_format_bytes():
    assert sysu.format_bytes(0) == "0 B"
    assert sysu.format_bytes(1536) == "1.5 KB"
    assert sysu.format_bytes(5 * 1024 ** 2) == "5.0 MB"
    assert sysu.format_bytes(3 * 1024 ** 3) == "3.00 GB"


def test_dir_size_and_disk_free(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    (tmp_path / "b.bin").write_bytes(b"y" * 50)
    count, size = sysu.dir_stats(tmp_path)
    assert (count, size) == (2, 150)
    assert sysu.dir_stats(tmp_path / "missing") == (0, 0)
    assert sysu.disk_free(tmp_path) > 0


def _proc(stdout: bytes = b"", stderr: bytes = b"", code: int = 0):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = code
    return proc


@pytest.mark.asyncio
async def test_get_ytdlp_version_runs_binary_from_download_command():
    with patch.object(sysu.config, "DOWNLOAD_COMMAND", '/opt/bin/yt-dlp -o "{output_path}" "{url}"'), \
         patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_proc(b"2026.10.01\n"))) as mock_exec:
        version = await sysu.get_ytdlp_version()
    assert version == "2026.10.01"
    assert mock_exec.call_args[0][:2] == ("/opt/bin/yt-dlp", "--version")


@pytest.mark.asyncio
async def test_get_ytdlp_version_handles_missing_binary():
    with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError())):
        assert await sysu.get_ytdlp_version() == "unknown"


@pytest.mark.asyncio
async def test_update_ytdlp_self_update_success():
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_proc(b"Updated yt-dlp to 2026.10.02\n"))) as mock_exec:
        ok, output = await sysu.update_ytdlp()
    assert ok is True
    assert "Updated" in output
    assert mock_exec.call_args[0][1] == "-U"


@pytest.mark.asyncio
async def test_update_ytdlp_falls_back_to_pip_when_package_managed():
    first = _proc(b"It looks like you installed yt-dlp with a package manager, pip or setup.py; please use that to update\n", code=1)
    second = _proc(b"Successfully installed yt-dlp-2026.10.02\n")
    with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=[first, second])) as mock_exec, \
         patch.object(sysu.shutil, "which", return_value=None):
        ok, output = await sysu.update_ytdlp()
    assert ok is True
    assert "Successfully installed" in output
    pip_call = mock_exec.call_args_list[1][0]
    assert pip_call[1:5] == ("-m", "pip", "install", "-U")


@pytest.mark.asyncio
async def test_update_ytdlp_uses_brew_when_binary_under_brew_prefix():
    first = _proc(b"please use that to update\n", code=1)
    second = _proc(b"==> Upgrading yt-dlp\n")
    with patch.object(sysu.config, "DOWNLOAD_COMMAND", '/opt/homebrew/bin/yt-dlp "{url}"'), \
         patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=[first, second])) as mock_exec, \
         patch.object(sysu.shutil, "which", return_value="/opt/homebrew/bin/brew"):
        ok, output = await sysu.update_ytdlp()
    assert ok is True
    assert mock_exec.call_args_list[1][0][:3] == ("/opt/homebrew/bin/brew", "upgrade", "yt-dlp")
