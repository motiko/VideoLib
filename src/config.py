import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

def _parse_bool(val: str | None, default: bool = False) -> bool:
    if not val:
        return default
    return val.strip().lower() in ("true", "1", "yes", "on")

class Config:
    """Application configuration loaded from environment variables."""
    
    # Platform Tokens
    TELEGRAM_BOT_TOKEN: str | None = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_API_URL: str | None = os.getenv("TELEGRAM_API_URL")
    DISCORD_BOT_TOKEN: str | None = os.getenv("DISCORD_BOT_TOKEN")
    
    # Storage and Log Paths
    DOWNLOAD_DIR: Path = Path(os.getenv("DOWNLOAD_DIR", "tmp")).resolve()
    LOG_DIR: Path = Path(os.getenv("LOG_DIR", "logs")).resolve()
    
    # Debug and File Retention Flags
    DEBUG: bool = _parse_bool(os.getenv("DEBUG"), False)
    KEEP_TMP_FILES: bool = _parse_bool(os.getenv("KEEP_TMP_FILES"), False)

    # Command template for running the downloader
    DOWNLOAD_COMMAND: str = os.getenv(
        "DOWNLOAD_COMMAND",
        'yt-dlp -f "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" --merge-output-format mp4 -o "{output_path}" "{url}"'
    )
    
    # Constraints
    try:
        MAX_CONCURRENT_DOWNLOADS: int = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))
    except ValueError:
        MAX_CONCURRENT_DOWNLOADS = 3
        
    try:
        MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
    except ValueError:
        MAX_FILE_SIZE_MB = 50

# Global configuration instance
config = Config()
