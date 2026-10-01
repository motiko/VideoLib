from pathlib import Path
from src.platforms.base import BasePlatform
from src.utils.logger import logger

class CliPlatform(BasePlatform):
    """Platform adapter for command-line interface execution."""
    
    def __init__(self):
        super().__init__("cli")

    async def start(self) -> None:
        logger.info("CliPlatform: Started CLI platform interface.")

    async def stop(self) -> None:
        logger.info("CliPlatform: Stopped CLI platform interface.")

    async def send_message(self, chat_id: str, text: str, reply_to_message_id: str | None = None) -> str | None:
        """Outputs text status messages directly to the terminal stdout."""
        print(f"\n💬 [CLI Status] {text}")
        return None

    async def edit_message(self, chat_id: str, message_id: str, text: str) -> None:
        """Outputs text status messages directly to the terminal stdout."""
        print(f"\n💬 [CLI Status Update] {text}")

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """Simulates deleting a message in CLI platform."""
        print(f"\n🗑️ [CLI Status] Deleted message {message_id}")
        return True

    async def react_to_message(self, chat_id: str, message_id: str, emoji: str = "✅") -> bool:
        """Simulates reacting to a message with an emoji."""
        print(f"\n✨ [CLI Reaction] {emoji} on message {message_id}")
        return True

    async def send_video(
        self,
        chat_id: str,
        file_path: Path,
        caption: str | None = None,
        reply_to_message_id: str | None = None,
        progress_callback: Any = None,
    ) -> None:
        """Reports video completion and output file location directly to the terminal."""
        size_mb = file_path.stat().st_size / (1024 * 1024) if file_path.exists() else 0.0
        print(f"\n🎉 [CLI Deliverable] Video successfully created!")
        print(f"   - File Path: {file_path}")
        print(f"   - File Size: {size_mb:.2f} MB")
        if caption:
            print(f"   - Details:   {caption}")
