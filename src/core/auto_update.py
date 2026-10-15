"""Optional background self-updater (enabled with AUTO_UPDATE=true).

Periodically asks GitHub for the latest release; when a newer one exists and no downloads are
running, it upgrades the package (see src/utils/updater.py), tells the admins, and asks the
Orchestrator for a restart. run.py re-execs the process after the graceful shutdown.
"""

import asyncio
import html
from typing import Iterable

from src import __version__
from src.core.access import PENDING_RESTART_NOTIFY_KEY, access_manager
from src.utils.logger import logger
from src.utils.updater import detect_install, fetch_latest_version, is_newer, run_upgrade

INITIAL_DELAY_S = 60.0
BUSY_RETRY_S = 300.0
DEFAULT_IDLE_WAIT_S = 1800.0
IDLE_POLL_S = 10.0


class AutoUpdater:
    """Checks for new releases every `interval_s` seconds and applies them when the bot is idle."""

    def __init__(
        self,
        orchestrator,
        admin_ids: Iterable[int],
        interval_s: float,
        busy_retry_s: float = BUSY_RETRY_S,
        idle_wait_s: float = DEFAULT_IDLE_WAIT_S,
    ):
        self.orchestrator = orchestrator
        self.admin_ids = [str(a) for a in admin_ids]
        self.interval_s = max(60.0, float(interval_s))
        self.busy_retry_s = busy_retry_s
        self.idle_wait_s = idle_wait_s
        self._task: asyncio.Task | None = None
        self._failed_version: str | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="auto-update")
            logger.info(f"AutoUpdater: Enabled; checking for releases every {self.interval_s / 3600:.1f}h.")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _loop(self) -> None:
        await asyncio.sleep(min(INITIAL_DELAY_S, self.interval_s))
        while True:
            try:
                outcome = await self.check_and_apply()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"AutoUpdater: Unexpected error during update check: {e}")
                outcome = "error"
            await asyncio.sleep(self.busy_retry_s if outcome == "busy" else self.interval_s)

    async def check_and_apply(self) -> str:
        """One check. Returns 'unknown' | 'current' | 'busy' | 'failed' | 'updating'."""
        latest = await fetch_latest_version()
        if not latest:
            return "unknown"
        if not is_newer(latest, __version__):
            logger.debug(f"AutoUpdater: Up to date ({__version__}; latest {latest}).")
            return "current"
        if latest == self._failed_version:
            return "failed"  # already reported; wait for a newer release before trying again
        if self.orchestrator.active:
            logger.info(f"AutoUpdater: Version {latest} available but {len(self.orchestrator.active)} download(s) active; retrying later.")
            return "busy"

        info = detect_install()
        logger.info(f"AutoUpdater: Upgrading {__version__} -> {latest} via {info.label}.")
        await self._notify_admins(f"⬆️ New VideoLib release <b>{latest}</b> found (running {__version__}). Updating via {info.label}…")
        ok, output = await run_upgrade(info, latest)
        if not ok:
            self._failed_version = latest
            tail = "\n".join(output.strip().splitlines()[-10:])
            await self._notify_admins(
                f"❌ Automatic update to {latest} failed. I will not retry this version; run /update now to retry manually.\n<pre>{_esc(tail)}</pre>"
            )
            return "failed"

        await self._wait_until_idle()
        await self._notify_admins(f"♻️ Update to {latest} installed. Restarting now…")
        if self.admin_ids:
            # Reuses the /restart "back online" notice so the first admin hears when the new version is up
            access_manager.set_setting(PENDING_RESTART_NOTIFY_KEY, self.admin_ids[0])
        self.orchestrator.request_shutdown(restart=True)
        return "updating"

    async def _wait_until_idle(self) -> None:
        """A download may have started while the upgrade ran; give it a bounded chance to finish."""
        deadline = asyncio.get_running_loop().time() + self.idle_wait_s
        while self.orchestrator.active and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(min(IDLE_POLL_S, max(0.0, self.idle_wait_s)))

    async def _notify_admins(self, text: str) -> None:
        for platform in list(self.orchestrator.platforms.values()):
            for admin in self.admin_ids:
                try:
                    await platform.send_message(admin, text)
                except Exception as e:
                    logger.warning(f"AutoUpdater: Could not notify admin {admin}: {e}")


def _esc(text: str) -> str:
    return html.escape(text)
