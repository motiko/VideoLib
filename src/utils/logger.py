import logging
import logging.handlers
import sys
import uuid
from datetime import datetime
from pathlib import Path
from src.config import config

def setup_logger(name: str = "videolib", level: int | None = None) -> logging.Logger:
    """Configures and returns a standard logger with stdout and file handlers."""
    logger = logging.getLogger(name)
    
    # Determine level
    target_level = level if level is not None else (logging.DEBUG if config.DEBUG else logging.INFO)
    logger.setLevel(target_level)

    # Avoid duplicate handlers if setup is called multiple times
    if not logger.handlers:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        
        # 1. Standard console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(target_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        # 2. File handler under config.LOG_DIR / videolib.log
        try:
            config.LOG_DIR.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                config.LOG_DIR / "videolib.log",
                maxBytes=10 * 1024 * 1024,  # 10 MB per file
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setLevel(target_level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            # Fallback if file logger initialization fails
            sys.stderr.write(f"Failed to set up file logger handler: {e}\n")

    return logger

# Primary app logger
logger = setup_logger()

def save_failure_log(url: str, command: str, exit_code: int, stdout: str, stderr: str) -> Path:
    """Saves a full diagnostic report when a video download fails."""
    failures_dir = config.LOG_DIR / "failures"
    failures_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = f"failed_{timestamp}_{uuid.uuid4().hex[:8]}.log"
    report_path = failures_dir / report_filename
    
    report_content = (
        f"=" * 80 + "\n"
        f"VideoLib Video Download Failure Diagnostic Report\n"
        f"=" * 80 + "\n"
        f"Timestamp:      {datetime.now().isoformat()}\n"
        f"Target URL:     {url}\n"
        f"Exit Code:      {exit_code}\n"
        f"Command:        {command}\n"
        f"-" * 80 + "\n"
        f"SUBPROCESS STDOUT:\n"
        f"-" * 80 + "\n"
        f"{stdout.strip() if stdout.strip() else '(empty stdout)'}\n\n"
        f"-" * 80 + "\n"
        f"SUBPROCESS STDERR:\n"
        f"-" * 80 + "\n"
        f"{stderr.strip() if stderr.strip() else '(empty stderr)'}\n"
        f"=" * 80 + "\n"
    )
    
    try:
        report_path.write_text(report_content, encoding="utf-8")
        logger.info(f"Logger: Saved failure diagnostic report to '{report_path}'")
    except Exception as e:
        logger.error(f"Logger: Failed to write failure diagnostic report to '{report_path}': {e}")

    return report_path
