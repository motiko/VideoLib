import argparse
import asyncio
import logging
import signal
import sys
from src.config import config
from src.utils.logger import logger, setup_logger
from src.storage.manager import storage_manager
from src.core.orchestrator import Orchestrator
from src.platforms.telegram import TelegramPlatform
from src.platforms.cli import CliPlatform

def parse_args():
    parser = argparse.ArgumentParser(description="CornBot — Multi-Platform Video Downloader Bot")
    parser.add_argument("--url", type=str, help="Download a single video URL directly via CLI")
    parser.add_argument("--cli", action="store_true", help="Run interactive CLI prompt mode in terminal")
    parser.add_argument("--debug", action="store_true", help="Enable verbose DEBUG logging")
    parser.add_argument("--keep-files", action="store_true", help="Preserve temporary downloaded files (don't delete)")
    return parser.parse_args()

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
            await orchestrator.handle_request("cli", "cli_user", args.url)

        if args.cli:
            print("\n" + "=" * 60)
            print("🤖 Welcome to CornBot Interactive CLI")
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
                    await orchestrator.handle_request("cli", "cli_user", user_input)

        await cli_platform.stop()
        return

    # 3. Standard Server Mode (Telegram / Discord)
    logger.info("Starting CornBot Platform Daemon...")
    orchestrator = Orchestrator()
    platforms_started = 0

    if config.TELEGRAM_BOT_TOKEN:
        logger.info("Initializing Telegram Platform...")
        telegram_platform = TelegramPlatform(config.TELEGRAM_BOT_TOKEN)
        orchestrator.register_platform(telegram_platform)
        platforms_started += 1
    else:
        logger.warning("TELEGRAM_BOT_TOKEN is not set in environment. Telegram platform will not start.")

    if config.DISCORD_BOT_TOKEN:
        logger.info("Discord token detected. (Discord integration code can be loaded here in the future.)")

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

    logger.info("CornBot is online. Press Ctrl+C to stop.")

    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Initiating graceful shutdown...")
        stop_tasks = [platform.stop() for platform in orchestrator.platforms.values()]
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
        logger.info("CornBot has been successfully stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Exiting.")
        sys.exit(0)
