"""Tests for the self-update helpers (src/utils/updater.py) and the AUTO_UPDATE background task."""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access import AccessManager
from src.core.orchestrator import ActiveDownload, Orchestrator
from src.core import auto_update as au
from src.utils import updater as upd


# ---------------------------------------------------------------------------
# Version parsing / comparison
# ---------------------------------------------------------------------------

def test_parse_version_handles_prefixes_and_brew_revisions():
    assert upd.parse_version("1.2.0") == (1, 2, 0)
    assert upd.parse_version("v1.3.1") == (1, 3, 1)
    assert upd.parse_version("1.2.0_3") == (1, 2, 0)
    assert upd.parse_version("videolib 2.0") == (2, 0)
    assert upd.parse_version("") == ()
    assert upd.parse_version("garbage") == ()


def test_is_newer():
    assert upd.is_newer("1.3.0", "1.2.0")
    assert upd.is_newer("v2.0.0", "1.9.9")
    assert not upd.is_newer("1.2.0", "1.2.0")
    assert not upd.is_newer("1.2", "1.2.0")
    assert not upd.is_newer("1.1.9", "1.2.0")
    assert not upd.is_newer("", "1.2.0")
    assert not upd.is_newer("1.3.0", "")


# ---------------------------------------------------------------------------
# Release lookup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_latest_version_strips_v_prefix():
    with patch.object(upd, "_http_get_json", return_value={"tag_name": "v1.4.0"}) as get:
        assert await upd.fetch_latest_version(repo="acme/thing") == "1.4.0"
    assert "acme/thing/releases/latest" in get.call_args[0][0]


@pytest.mark.asyncio
async def test_fetch_latest_version_returns_none_on_error():
    with patch.object(upd, "_http_get_json", side_effect=OSError("offline")):
        assert await upd.fetch_latest_version(repo="acme/thing") is None
    with patch.object(upd, "_http_get_json", return_value={}):
        assert await upd.fetch_latest_version(repo="acme/thing") is None


# ---------------------------------------------------------------------------
# Install detection / upgrade commands
# ---------------------------------------------------------------------------

def test_detect_install_brew():
    # Homebrew virtualenv: the interpreter is a symlink to python@3.x, so the keg shows up in sys.prefix
    with patch.object(sys, "executable", "/opt/homebrew/opt/python@3.12/bin/python3.12"), \
         patch.object(sys, "prefix", "/opt/homebrew/Cellar/videolib/1.2.0_3/libexec"), \
         patch.object(upd.shutil, "which", lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None):
        info = upd.detect_install(repo="acme/thing")
    assert info.kind == "brew"
    cmds = info.commands_for("1.3.0")
    assert cmds[0] == ["/opt/homebrew/bin/brew", "update"]
    assert cmds[1] == ["/opt/homebrew/bin/brew", "upgrade", "videolib"]
    # Keep the running interpreter's keg on disk until the restart has happened
    assert info.env.get("HOMEBREW_NO_INSTALL_CLEANUP") == "1"


def test_detect_install_ignores_homebrew_python_outside_the_keg(tmp_path):
    """A project venv built on Homebrew's python@3.x is not a Homebrew install of videolib."""
    with patch.object(sys, "executable", "/opt/homebrew/opt/python@3.14/bin/python3.14"), \
         patch.object(sys, "prefix", "/Users/me/cornbot/.venv"), \
         patch.object(upd, "_project_root", return_value=tmp_path), \
         patch.object(upd.shutil, "which", lambda name: "/opt/homebrew/bin/brew"):
        assert upd.detect_install(repo="acme/thing").kind == "pip"


def test_detect_install_git_checkout(tmp_path):
    with patch.object(sys, "executable", "/some/venv/bin/python"), \
         patch.object(sys, "prefix", "/some/venv"), \
         patch.object(upd, "_project_root", return_value=tmp_path), \
         patch.object(upd.shutil, "which", lambda name: "/usr/bin/git" if name == "git" else None):
        (tmp_path / ".git").mkdir()
        info = upd.detect_install(repo="acme/thing")
    assert info.kind == "git"
    cmds = info.commands_for("1.3.0")
    assert cmds[0] == ["/usr/bin/git", "-C", str(tmp_path), "pull", "--ff-only"]
    assert cmds[1][:4] == ["/some/venv/bin/python", "-m", "pip", "install"] and "-e" in cmds[1]


def test_detect_install_pip_from_release_tarball(tmp_path):
    with patch.object(sys, "executable", "/some/venv/bin/python"), \
         patch.object(sys, "prefix", "/some/venv"), \
         patch.object(upd, "_project_root", return_value=tmp_path), \
         patch.object(upd.shutil, "which", lambda name: None):
        info = upd.detect_install(repo="acme/thing")
    assert info.kind == "pip"
    (cmd,) = info.commands_for("1.3.0")
    assert cmd[:4] == ["/some/venv/bin/python", "-m", "pip", "install"]
    assert cmd[-1] == "https://github.com/acme/thing/archive/refs/tags/v1.3.0.tar.gz"


@pytest.mark.asyncio
async def test_run_upgrade_runs_commands_in_order_and_stops_on_failure():
    info = upd.InstallInfo(kind="brew", label="Homebrew", env={"X": "1"}, _commands=[["brew", "update"], ["brew", "upgrade", "videolib"]])
    calls = []

    async def fake_run(args, timeout, env=None):
        calls.append((args, env))
        return (0, "ok") if args[1] == "update" else (1, "boom")

    with patch.object(upd, "run_command", fake_run):
        ok, output = await upd.run_upgrade(info, "1.3.0")
    assert not ok
    assert [c[0] for c in calls] == [["brew", "update"], ["brew", "upgrade", "videolib"]]
    assert calls[0][1]["X"] == "1"
    assert "boom" in output and "$ brew upgrade videolib" in output


@pytest.mark.asyncio
async def test_run_upgrade_success_and_missing_binary():
    info = upd.InstallInfo(kind="pip", label="pip", env={}, _commands=[["pip", "install", "x"]])
    with patch.object(upd, "run_command", AsyncMock(return_value=(0, "Successfully installed"))):
        ok, output = await upd.run_upgrade(info, "1.3.0")
    assert ok and "Successfully installed" in output

    with patch.object(upd, "run_command", AsyncMock(side_effect=FileNotFoundError("pip"))):
        ok, output = await upd.run_upgrade(info, "1.3.0")
    assert not ok and "Could not run" in output


@pytest.mark.asyncio
async def test_run_upgrade_refuses_concurrent_runs():
    info = upd.InstallInfo(kind="pip", label="pip", env={}, _commands=[["pip", "install", "x"]])
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_run(args, timeout, env=None):
        started.set()
        await release.wait()
        return 0, "done"

    with patch.object(upd, "run_command", slow_run):
        first = asyncio.create_task(upd.run_upgrade(info, "1.3.0"))
        await started.wait()
        ok, output = await upd.run_upgrade(info, "1.3.0")
        assert not ok and "already in progress" in output.lower()
        release.set()
        assert (await first)[0] is True


# ---------------------------------------------------------------------------
# AutoUpdater
# ---------------------------------------------------------------------------

@pytest.fixture
def orch(tmp_path):
    mgr = AccessManager(db_path=tmp_path / "au.db")
    with patch.object(au, "access_manager", mgr):
        o = Orchestrator()
        platform = MagicMock()
        platform.send_message = AsyncMock()
        o.platforms["telegram"] = platform
        o._test_mgr = mgr
        o._test_platform = platform
        yield o


def _updater(orch, **kw):
    return au.AutoUpdater(orch, admin_ids=[111, 222], interval_s=3600, **kw)


@pytest.mark.asyncio
async def test_auto_update_noop_when_current_or_unreachable(orch):
    u = _updater(orch)
    with patch.object(au, "fetch_latest_version", AsyncMock(return_value="1.0.0")), \
         patch.object(au, "__version__", "1.2.0"):
        assert await u.check_and_apply() == "current"
    with patch.object(au, "fetch_latest_version", AsyncMock(return_value=None)):
        assert await u.check_and_apply() == "unknown"
    orch._test_platform.send_message.assert_not_called()
    assert not orch.shutdown_event.is_set()


@pytest.mark.asyncio
async def test_auto_update_waits_while_busy(orch):
    u = _updater(orch)
    orch.active["k"] = ActiveDownload("telegram", "1", "1", "https://x.com/1")
    with patch.object(au, "fetch_latest_version", AsyncMock(return_value="9.9.9")), \
         patch.object(au, "run_upgrade", AsyncMock()) as run:
        assert await u.check_and_apply() == "busy"
    run.assert_not_called()


@pytest.mark.asyncio
async def test_auto_update_applies_notifies_and_restarts(orch):
    u = _updater(orch, idle_wait_s=0)
    with patch.object(au, "fetch_latest_version", AsyncMock(return_value="9.9.9")), \
         patch.object(au, "run_upgrade", AsyncMock(return_value=(True, "upgraded"))):
        assert await u.check_and_apply() == "updating"
    texts = [c[0][1] for c in orch._test_platform.send_message.call_args_list]
    targets = {c[0][0] for c in orch._test_platform.send_message.call_args_list}
    assert targets == {"111", "222"}
    assert any("9.9.9" in t for t in texts)
    assert orch.shutdown_event.is_set() and orch.restart_requested
    assert orch._test_mgr.get_setting("pending_restart_notify") == "111"


@pytest.mark.asyncio
async def test_auto_update_reports_failure_once_per_version(orch):
    u = _updater(orch, idle_wait_s=0)
    with patch.object(au, "fetch_latest_version", AsyncMock(return_value="9.9.9")), \
         patch.object(au, "run_upgrade", AsyncMock(return_value=(False, "brew exploded"))) as run:
        assert await u.check_and_apply() == "failed"
        assert await u.check_and_apply() == "failed"
    assert run.await_count == 1
    failures = [c[0][1] for c in orch._test_platform.send_message.call_args_list if "failed" in c[0][1].lower()]
    assert len(failures) == 2  # one per admin, not repeated on the second check
    assert not orch.shutdown_event.is_set()


@pytest.mark.asyncio
async def test_auto_update_start_stop(orch):
    u = _updater(orch)
    u.start()
    assert u._task is not None and not u._task.done()
    await u.stop()
    assert u._task is None
