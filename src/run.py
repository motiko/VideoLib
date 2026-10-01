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

async def check_service_status() -> None:
    """Check and display the status of the VideoLib launchd service."""
    import subprocess
    label = "com.videolib.agent"
    print(f"\n{'=' * 50}")
    print("VideoLib Service Status")
    print(f"{'=' * 50}")
    
    # Check if launchd job is loaded
    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=5
        )
        lines = [l for l in result.stdout.splitlines() if label in l]
        if lines:
            parts = lines[0].split()
            pid = parts[0] if parts[0] != "-" else None
            status_code = parts[1] if len(parts) > 1 else "unknown"
            if pid:
                print(f"  Status:  ✅ Running (PID {pid})")
            else:
                print(f"  Status:  ⚠️  Loaded but not running (exit code: {status_code})")
        else:
            print("  Status:  ❌ Not loaded")
            print(f"  Hint:    Run 'brew services start videolib' or load the plist manually.")
    except FileNotFoundError:
        print("  Status:  ❓ launchctl not found (not macOS?)")
    except subprocess.TimeoutExpired:
        print("  Status:  ❓ launchctl timed out")
    
    # Check config file
    env_file = os.getenv("VIDEOLIB_ENV_FILE", os.path.expanduser("~/.config/videolib/.env"))
    if os.path.exists(env_file):
        print(f"  Config:  ✅ {env_file}")
    else:
        print(f"  Config:  ❌ {env_file} (not found)")
    
    # Check log files
    log_dir = Path(os.path.expanduser("~/Library/Logs/VideoLib"))
    if log_dir.exists():
        log_files = list(log_dir.glob("*.log"))
        print(f"  Logs:    📁 {log_dir} ({len(log_files)} file(s))")
        for lf in sorted(log_files)[:5]:
            size_kb = lf.stat().st_size / 1024
            print(f"           - {lf.name} ({size_kb:.1f} KB)")
    else:
        print(f"  Logs:    📁 {log_dir} (not created yet)")
    
    print(f"{'=' * 50}\n")

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

    # Handle shutdown signals gracefully
    shutdown_event = asyncio.Event()
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

def main_sync() -> None:
    """Synchronous entry point for console_scripts."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Exiting.")
        sys.exit(0)

if __name__ == "__main__":
    main_sync()
