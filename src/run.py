import argparse
import asyncio
import logging
import signal
import sys
import os
import subprocess
from pathlib import Path
from src.config import config
from src.utils.logger import logger, setup_logger
from src.storage.manager import storage_manager
from src.core.orchestrator import Orchestrator
from src.platforms.telegram import TelegramPlatform
from src.platforms.cli import CliPlatform

def parse_args():
    parser = argparse.ArgumentParser(description="VideoLib — Multi-Platform Video Downloader Bot")
    parser.add_argument("--url", type=str, help="Download a single video URL directly via CLI")
    parser.add_argument("--cli", action="store_true", help="Run interactive CLI prompt mode in terminal")
    parser.add_argument("--debug", action="store_true", help="Enable verbose DEBUG logging")
    parser.add_argument("--keep-files", action="store_true", help="Preserve temporary downloaded files (don't delete)")
    parser.add_argument("--status", action="store_true", help="Check if the VideoLib launchd service is running")
    return parser.parse_args()

# launchd labels: the project's own plist (scripts/install-service.sh) and the ones `brew services` uses
SERVICE_LABELS: tuple[str, ...] = ("com.videolib.agent", "sh.brew.videolib", "homebrew.mxcl.videolib")


def _homebrew_prefix() -> Path | None:
    """Best-effort Homebrew prefix without shelling out: env var, then the usual locations."""
    env = os.getenv("HOMEBREW_PREFIX")
    candidates = [Path(env)] if env else []
    candidates += [Path("/opt/homebrew"), Path("/usr/local")]
    for c in candidates:
        if (c / "bin" / "brew").exists():
            return c
    return None


def _find_launchd_job() -> tuple[str | None, str | None, str | None]:
    """Returns (label, pid, exit_status) for the first known VideoLib launchd job, or (None, None, None)."""
    try:
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
    except FileNotFoundError:
        return None, None, "launchctl not found"
    except subprocess.TimeoutExpired:
        return None, None, "launchctl timed out"
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] in SERVICE_LABELS:
            pid = parts[0] if parts[0] != "-" else None
            return parts[2], pid, parts[1]
    return None, None, None


def _print_log_dir(label: str, log_dir: Path) -> None:
    if not log_dir.exists():
        print(f"  {label}📁 {log_dir} (not created yet)")
        return
    log_files = sorted(log_dir.glob("*.log"))
    print(f"  {label}📁 {log_dir} ({len(log_files)} file(s))")
    for lf in log_files[:5]:
        size_kb = lf.stat().st_size / 1024
        print(f"           - {lf.name} ({size_kb:.1f} KB)")


async def check_service_status() -> None:
    """Check and display the status of the VideoLib launchd service (custom plist or brew services)."""
    from src import __version__

    print(f"\n{'=' * 50}")
    print(f"VideoLib Service Status (v{__version__})")
    print(f"{'=' * 50}")

    label, pid, status_code = _find_launchd_job()
    if label is None and status_code:
        print(f"  Status:  ❓ {status_code}")
    elif label is None:
        print("  Status:  ❌ Not loaded")
        print("  Hint:    Run 'brew services start videolib' or ./scripts/install-service.sh")
    elif pid:
        print(f"  Status:  ✅ Running (PID {pid}, launchd job {label})")
    else:
        print(f"  Status:  ⚠️  Loaded but not running (launchd job {label}, exit code: {status_code})")

    # Check config file
    env_file = os.getenv("VIDEOLIB_ENV_FILE", os.path.expanduser("~/.config/videolib/.env"))
    if os.path.exists(env_file):
        print(f"  Config:  ✅ {env_file}")
    else:
        print(f"  Config:  ❌ {env_file} (not found)")

    # Log locations differ by install method: custom plist vs brew services
    brew_prefix = _homebrew_prefix()
    is_brew = label is not None and label != "com.videolib.agent"
    if is_brew and brew_prefix:
        _print_log_dir("Logs:    ", brew_prefix / "var" / "log" / "videolib")
        _print_log_dir("App log: ", brew_prefix / "var" / "lib" / "videolib" / "logs")
    else:
        _print_log_dir("Logs:    ", Path(os.path.expanduser("~/Library/Logs/VideoLib")))
        if brew_prefix and (brew_prefix / "var" / "log" / "videolib").exists():
            _print_log_dir("Brew logs:", brew_prefix / "var" / "log" / "videolib")

    print(f"{'=' * 50}\n")

def _reexec() -> None:
    """Replaces the current process with a fresh copy of itself (used by the admin /restart command)."""
    logger.info("Restart requested: re-executing process...")
    for handler in logging.getLogger("videolib").handlers:
        try:
            handler.flush()
        except Exception:
            pass
    os.execv(sys.executable, [sys.executable, *sys.argv])

async def main() -> None:
    args = parse_args()

    # Override configuration flags if specified via CLI
    if args.debug:
        config.DEBUG = True
        setup_logger(level=logging.DEBUG)
        logger.debug("Debug logging enabled via CLI flag.")

    if args.keep_files:
        config.KEEP_TMP_FILES = True
        logger.info("Temporary file preservation enabled (KEEP_TMP_FILES=True) via CLI flag.")

    if args.status:
        await check_service_status()
        return

    # 1. Initialize storage directory
    storage_manager.init_dir()

    # 2. Handle CLI Mode (single URL or interactive prompt)
    if args.url or args.cli:
        logger.info("Running in CLI Mode...")
        orchestrator = Orchestrator()
        cli_platform = CliPlatform()
        orchestrator.register_platform(cli_platform)
        await cli_platform.start()

        if args.url:
            logger.info(f"Processing CLI URL: {args.url}")
            await orchestrator.handle_request("cli", "cli_user", "cli_msg", args.url)

        if args.cli:
            print("\n" + "=" * 60)
            print("🤖 Welcome to VideoLib Interactive CLI")
            print("Type 'exit' or 'quit' to exit.")
            print("=" * 60)
            while True:
                try:
                    user_input = input("\nEnter video URL > ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nExiting CLI mode.")
                    break

                if user_input.lower() in ("exit", "quit", "q"):
                    print("Exiting CLI mode.")
                    break

                if user_input:
                    await orchestrator.handle_request("cli", "cli_user", "cli_msg", user_input)

        await cli_platform.stop()
        return

    # 3. Standard Server Mode (Telegram)
    logger.info("Starting VideoLib Platform Daemon...")
    orchestrator = Orchestrator()
    platforms_started = 0

    if config.TELEGRAM_BOT_TOKEN:
        logger.info("Initializing Telegram Platform...")
        telegram_platform = TelegramPlatform(config.TELEGRAM_BOT_TOKEN)
        orchestrator.register_platform(telegram_platform)
        platforms_started += 1
    else:
        logger.warning("TELEGRAM_BOT_TOKEN is not set in environment. Telegram platform will not start.")

    if platforms_started == 0:
        logger.error("No active platforms configured. Set TELEGRAM_BOT_TOKEN or run with --url / --cli.")
        sys.exit(1)

    # Start all registered platforms
    for platform in orchestrator.platforms.values():
        await platform.start()

    # Handle shutdown signals gracefully (admin /restart sets the same event via Orchestrator.request_shutdown)
    shutdown_event = orchestrator.shutdown_event
    loop = asyncio.get_running_loop()

    def signal_handler():
        logger.info("Shutdown signal received.")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass

    logger.info("VideoLib is online. Press Ctrl+C to stop.")

    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Initiating graceful shutdown...")
        stop_tasks = [platform.stop() for platform in orchestrator.platforms.values()]
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
        logger.info("VideoLib has been successfully stopped.")
        if orchestrator.restart_requested:
            _reexec()

def main_sync() -> None:
    """Synchronous entry point for console_scripts."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Exiting.")
        sys.exit(0)

if __name__ == "__main__":
    main_sync()
