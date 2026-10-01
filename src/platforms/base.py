from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Coroutine, Any

# Type alias for message handler callbacks
# Callback receives: (platform_name, chat_id, url)
MessageCallback = Callable[[str, str, str], Coroutine[Any, Any, None]]

class BasePlatform(ABC):
    """Abstract base class that all platform bot adapters (Telegram, Discord) must implement."""

    def __init__(self, name: str):
        self.name = name
        self.message_callback: MessageCallback | None = None

    def register_callback(self, callback: MessageCallback) -> None:
        """Registers a callback function to handle incoming video download requests."""
        self.message_callback = callback

    @abstractmethod
    async def start(self) -> None:
        """Starts the platform bot listener loop."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stops the platform bot listener loop."""
        pass

    @abstractmethod
    async def send_message(self, chat_id: str, text: str) -> None:
        """Sends a text message to a specific chat/user."""
        pass

    @abstractmethod
    async def send_video(self, chat_id: str, file_path: Path, caption: str | None = None) -> None:
        """Sends a video file to a specific chat/user."""
        pass
