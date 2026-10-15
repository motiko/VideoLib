"""Self-update helpers: GitHub release lookup, install-method detection, and running the upgrade.

Shared by the admin /update command (src/platforms/telegram_admin.py) and the optional AUTO_UPDATE
background task (src/core/auto_update.py). Nothing here restarts the process; callers do that via
Orchestrator.request_shutdown(restart=True) after a successful upgrade.
"""

import asyncio
import json
import re
import shutil
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from src import __version__
from src.config import config
from src.utils.logger import logger
from src.utils.system import run_command

PACKAGE_NAME = "videolib"
UPGRADE_TIMEOUT_S = 900.0

_VERSION_RE = re.compile(r"(\d+(?:\.\d+)*)")

# Guards against the admin command and the auto-updater upgrading at the same time
_upgrade_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.0' / '1.2.0_3' (Homebrew revision) / 'videolib 2.0' -> (1, 2, 0) / (1, 2, 0) / (2, 0). Unparseable -> ()."""
    m = _VERSION_RE.search(text or "")
    return tuple(int(part) for part in m.group(1).split(".")) if m else ()


def is_newer(candidate: str, current: str) -> bool:
    """True when `candidate` is a strictly newer version than `current` (missing components count as 0)."""
    a, b = parse_version(candidate), parse_version(current)
    if not a or not b:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


# ---------------------------------------------------------------------------
# Release lookup
# ---------------------------------------------------------------------------

def _http_get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"{PACKAGE_NAME}/{__version__}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


async def fetch_latest_version(repo: str | None = None, timeout: float = 15.0) -> str | None:
    """Latest GitHub release version for `repo` (tag without the leading 'v'), or None when unavailable."""
    repo = repo or config.UPDATE_REPO
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        data = await asyncio.to_thread(_http_get_json, url, timeout)
    except Exception as e:
        logger.warning(f"Updater: Release lookup failed ({url}): {e}")
        return None
    tag = str(data.get("tag_name") or "").strip()
    version = tag[1:] if tag.startswith("v") else tag
    return version or None


# ---------------------------------------------------------------------------
# Install detection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InstallInfo:
    """How this copy of VideoLib was installed and how to upgrade it."""

    kind: str  # "brew" | "git" | "pip"
    label: str
    env: dict[str, str] = field(default_factory=dict)
    # Each command may contain "{version}" placeholders; see commands_for()
    _commands: list[list[str]] = field(default_factory=list)

    def commands_for(self, version: str) -> list[list[str]]:
        return [[tok.replace("{version}", version) for tok in cmd] for cmd in self._commands]


def _project_root() -> Path:
    """Directory containing pyproject.toml / .git when running from a source checkout (src/utils/updater.py -> root)."""
    return Path(__file__).resolve().parents[2]


def _is_brew_keg(path: str) -> bool:
    """True when `path` lies inside the Homebrew keg of this package (not merely any Homebrew-installed Python)."""
    return f"/Cellar/{PACKAGE_NAME}/" in path or f"/opt/{PACKAGE_NAME}/" in path


def detect_install(repo: str | None = None) -> InstallInfo:
    """Picks the upgrade strategy: Homebrew keg, git checkout (editable install), or pip from the release tarball."""
    repo = repo or config.UPDATE_REPO
    brew = shutil.which("brew")
    if brew and (_is_brew_keg(sys.prefix) or _is_brew_keg(sys.executable)):
        return InstallInfo(
            kind="brew",
            label="Homebrew",
            # Keep the old keg (the interpreter we are running from) until the restart has happened
            env={"HOMEBREW_NO_INSTALL_CLEANUP": "1", "HOMEBREW_NO_ENV_HINTS": "1"},
            _commands=[[brew, "update"], [brew, "upgrade", PACKAGE_NAME]],
        )

    root = _project_root()
    git = shutil.which("git")
    if git and (root / ".git").is_dir():
        return InstallInfo(
            kind="git",
            label=f"git checkout ({root})",
            _commands=[
                [git, "-C", str(root), "pull", "--ff-only"],
                [sys.executable, "-m", "pip", "install", "-q", "-e", str(root)],
            ],
        )

    tarball = f"https://github.com/{repo}/archive/refs/tags/v{{version}}.tar.gz"
    return InstallInfo(
        kind="pip",
        label="pip (GitHub release tarball)",
        _commands=[[sys.executable, "-m", "pip", "install", "-q", "--upgrade", tarball]],
    )


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------

async def run_upgrade(info: InstallInfo, version: str, timeout: float = UPGRADE_TIMEOUT_S) -> tuple[bool, str]:
    """Runs the install method's upgrade commands in order. Returns (ok, combined output). Refuses to run concurrently."""
    if _upgrade_lock.locked():
        return False, "An update is already in progress."
    async with _upgrade_lock:
        outputs: list[str] = []
        for cmd in info.commands_for(version):
            logger.info(f"Updater: Running: {' '.join(cmd)}")
            try:
                code, out = await run_command(cmd, timeout=timeout, env=info.env or None)
            except (FileNotFoundError, OSError) as e:
                outputs.append(f"Could not run {cmd[0]}: {e}")
                return False, "\n\n".join(outputs)
            outputs.append(f"$ {' '.join(cmd)}\n{out.strip()}".strip())
            if code != 0:
                logger.error(f"Updater: Command failed with exit code {code}: {' '.join(cmd)}")
                return False, "\n\n".join(outputs)
        logger.info(f"Updater: Upgrade to {version} installed via {info.label}.")
        return True, "\n\n".join(outputs)
