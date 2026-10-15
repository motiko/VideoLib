"""Host/system introspection helpers for admin status commands (uptime, disk, yt-dlp)."""

import asyncio
import os
import shlex
import shutil
import sys
from pathlib import Path
from src.config import config
from src.utils.logger import logger

_PKG_MANAGED_HINTS = ("package manager", "please use that to update", "pip or setup.py")


def format_uptime(seconds: float) -> str:
    """Formats a duration as '1d 2h 5m' / '2h 0m' / '1m 1s' / '59s'."""
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_bytes(num: int | float) -> str:
    """Human-readable byte size."""
    num = float(num or 0)
    if num >= 1024 ** 3:
        return f"{num / 1024 ** 3:.2f} GB"
    if num >= 1024 ** 2:
        return f"{num / 1024 ** 2:.1f} MB"
    if num >= 1024:
        return f"{num / 1024:.1f} KB"
    return f"{int(num)} B"


def dir_stats(path: Path) -> tuple[int, int]:
    """Returns (file_count, total_bytes) for regular files directly inside `path`."""
    if not path.is_dir():
        return 0, 0
    count = 0
    size = 0
    for p in path.iterdir():
        try:
            if p.is_file():
                count += 1
                size += p.stat().st_size
        except OSError:
            continue
    return count, size


def disk_free(path: Path) -> int:
    """Free bytes on the filesystem containing `path` (walks up to the nearest existing parent)."""
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return 0


def ytdlp_binary() -> str:
    """The downloader binary as configured in DOWNLOAD_COMMAND (first token), defaulting to yt-dlp."""
    try:
        tokens = shlex.split(config.DOWNLOAD_COMMAND)
    except ValueError:
        tokens = []
    return tokens[0] if tokens else "yt-dlp"


async def run_command(args: list[str], timeout: float, env: dict[str, str] | None = None) -> tuple[int, str]:
    """Runs a command without a shell; returns (exit_code, combined output). `env` adds to the inherited environment."""
    full_env = {**os.environ, **env} if env else None
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=full_env,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, f"Timed out after {int(timeout)}s"
    code = proc.returncode if proc.returncode is not None else -1
    return code, (stdout or b"").decode(errors="replace")


async def get_ytdlp_version() -> str:
    """Returns the downloader's reported version, or 'unknown'."""
    try:
        code, out = await run_command([ytdlp_binary(), "--version"], timeout=20.0)
    except (FileNotFoundError, OSError) as e:
        logger.debug(f"System: yt-dlp version check failed: {e}")
        return "unknown"
    out = out.strip()
    return out.splitlines()[-1].strip() if code == 0 and out else "unknown"


def _brew_path_for(binary: str) -> str | None:
    """Returns the brew executable if `binary` appears to be Homebrew-managed."""
    resolved = shutil.which(binary) or binary
    brew = shutil.which("brew")
    if not brew:
        return None
    if "/homebrew/" in resolved or "/Cellar/" in resolved or resolved.startswith("/usr/local/bin/"):
        return brew
    return None


async def update_ytdlp() -> tuple[bool, str]:
    """Updates yt-dlp via its self-updater, falling back to brew or pip when it is package-managed."""
    binary = ytdlp_binary()
    try:
        code, out = await run_command([binary, "-U"], timeout=300.0)
    except (FileNotFoundError, OSError) as e:
        return False, f"Could not run {binary}: {e}"

    lowered = out.lower()
    if code == 0 and not any(h in lowered for h in _PKG_MANAGED_HINTS):
        return True, out.strip()

    brew = _brew_path_for(binary)
    if brew:
        args = [brew, "upgrade", "yt-dlp"]
    else:
        args = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"]
    logger.info(f"System: yt-dlp self-update unavailable, falling back to: {' '.join(args)}")
    try:
        code2, out2 = await run_command(args, timeout=300.0)
    except (FileNotFoundError, OSError) as e:
        return False, f"{out.strip()}\n\nFallback failed: {e}"
    combined = (out.strip() + "\n\n" + out2.strip()).strip()
    return code2 == 0, combined
