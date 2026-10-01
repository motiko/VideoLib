import asyncio
import os
import uuid
from pathlib import Path
from src.config import config
from src.utils.logger import logger

class StorageManager:
    """Manages temporary storage for downloaded media, including directory setup and safe cleanup."""
    
    def __init__(self, download_dir: Path):
        self.download_dir = download_dir

    def init_dir(self) -> None:
        """Synchronously ensures the download directory exists on startup."""
        try:
            self.download_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"StorageManager: Temporary download directory ready at '{self.download_dir}'")
        except Exception as e:
            logger.error(f"StorageManager: Failed to initialize download directory at '{self.download_dir}': {e}")
            raise

    def generate_path(self, suffix: str = ".mp4") -> Path:
        """Generates a secure, unique, and absolute file path for downloads."""
        # Ensure suffix has a leading dot
        if suffix and not suffix.startswith("."):
            suffix = f".{suffix}"
        
        filename = f"{uuid.uuid4().hex}{suffix}"
        return self.download_dir / filename

    async def cleanup(self, path: Path | str) -> bool:
        """Asynchronously and safely removes a file if it exists, unless KEEP_TMP_FILES is enabled."""
        if not path:
            return False
            
        file_path = Path(path).resolve()
        
        # Security sanity check: Ensure path lies inside the configured download directory
        # to prevent directory traversal deletions.
        try:
            file_path.relative_to(self.download_dir)
        except ValueError:
            logger.error(f"StorageManager: Security alert! Attempted deletion outside temp directory rejected: {file_path}")
            return False

        # If KEEP_TMP_FILES is set to True, preserve the file
        if config.KEEP_TMP_FILES:
            logger.info(f"StorageManager: Preserving temporary file at '{file_path}' (KEEP_TMP_FILES=True)")
            return False

        # Perform deletion in a separate thread to prevent blocking the async loop
        try:
            def remove_file():
                deleted_any = False
                # Remove all files that share the same stem (e.g. .mp4, .mp4.webm, .part)
                for f in file_path.parent.glob(f"{file_path.stem}.*"):
                    if f.exists():
                        f.unlink()
                        deleted_any = True
                return deleted_any

            deleted = await asyncio.to_thread(remove_file)
            if deleted:
                logger.info(f"StorageManager: Cleaned up temporary file: {file_path}")
                return True
        except Exception as e:
            logger.error(f"StorageManager: Failed to delete file {file_path}: {e}")
            
        return False

# Global instance of storage manager
storage_manager = StorageManager(config.DOWNLOAD_DIR)
