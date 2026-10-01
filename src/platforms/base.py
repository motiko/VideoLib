from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Coroutine, Any

# Type alias for message handler callbacks
# Callback receives: (platform_name, chat_id, message_id, url)
MessageCallback = Callable[[str, str, str, str], Coroutine[Any, Any, None]]

class BasePlatform(ABC):
    """Abstract base class that all platform bot adapters (Telegram) must implement."""

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
    async def send_message(self, chat_id: str, text: str, reply_to_message_id: str | None = None) -> str | None:
        """Sends a text message to a specific chat/user. Returns message ID if supported."""
        pass

    @abstractmethod
    async def edit_message(self, chat_id: str, message_id: str, text: str) -> None:
        """Edits an existing message in a specific chat."""
        pass

    @abstractmethod
    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """Deletes a message in a specific chat. Returns True if successful."""
        pass

    @abstractmethod
    async def react_to_message(self, chat_id: str, message_id: str, emoji: str = "✅") -> bool:
        """Reacts to a message with an emoji."""
        pass

    @abstractmethod
    async def send_video(
        self,
        chat_id: str,
        file_path: Path,
        caption: str | None = None,
        reply_to_message_id: str | None = None,
        progress_callback: Callable[[float], Coroutine[Any, Any, None]] | Callable[[float], None] | None = None,
    ) -> None:
        """Sends a video file to a specific chat/user."""
        pass

