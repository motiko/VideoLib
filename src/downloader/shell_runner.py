import asyncio
import os
import shlex
import urllib.parse
from pathlib import Path
from src.config import config
from src.utils.logger import logger, save_failure_log

class DownloadError(Exception):
    """Exception raised when a download task fails."""
    pass

class ShellRunner:
    """Safely executes download commands in async subprocesses using command-line array tokenization."""
    
    def __init__(self, command_template: str, max_file_size_mb: int):
        self.command_template = command_template
        self.max_file_size_mb = max_file_size_mb

    @staticmethod
    def validate_url(url: str) -> bool:
        """Validates that the input is a valid HTTP/HTTPS URL and contains no shell injection risks."""
        if not url:
            return False
        # Strip escaping backslashes
        url = url.replace("\\", "")
        try:
            parsed = urllib.parse.urlparse(url.strip())
            if parsed.scheme not in ("http", "https"):
                return False
            if not parsed.netloc:
                return False
            
            # Strict safety whitelist: no spaces or shell characters
            illegal_chars = [";", "|", "$", "`", "<", ">", "\n", "\r", " "]
            if any(char in url for char in illegal_chars):
                return False
                
            return True
        except Exception:
            return False

    def build_command_args(self, url: str, output_path: Path) -> list[str]:
        """Tokenizes the command template and safely interpolates url and output path."""
        # Use shlex to safely split the template into arguments list
        raw_tokens = shlex.split(self.command_template)
        
        interpolated_tokens = []
        for token in raw_tokens:
            # Replace placeholder variations (handles quotes if shlex didn't strip them)
            t = token
            for placeholder, val in [
                ("{url}", url),
                ("{output_path}", str(output_path))
            ]:
                t = t.replace(placeholder, val)
                # Also clean up any lingering quote characters wrapped around placeholders
                t = t.replace(f'"{val}"', val).replace(f"'{val}'", val)
            interpolated_tokens.append(t)
            
        return interpolated_tokens

    async def download(self, url: str, output_path: Path, timeout_seconds: float = 300.0) -> Path:
        """Runs the download command asynchronously. Returns the path of the downloaded file."""
        # Strip escaping backslashes
        url = url.replace("\\", "")
        if not self.validate_url(url):
            raise DownloadError("Invalid or unsafe URL format provided.")

        args = self.build_command_args(url, output_path)
        if not args:
            raise DownloadError("Empty download command generated.")

        cmd_str = ' '.join(shlex.quote(arg) for arg in args)
        logger.info(f"ShellRunner: Launching command: {cmd_str}")
        
        env = os.environ.copy()
        env["PYTHONWARNINGS"] = "ignore"
        
        try:
            # Start subprocess using exec (list of arguments) to prevent shell injection
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
        except FileNotFoundError as e:
            err_msg = f"Downloader executable not found on host system: {e}"
            logger.error(f"ShellRunner: Binary not found: {e}")
            save_failure_log(url, cmd_str, -1, "", err_msg)
            raise DownloadError(err_msg)
        except Exception as e:
            err_msg = f"Subprocess initialization failure: {e}"
            logger.error(f"ShellRunner: Failed to start subprocess: {e}")
            save_failure_log(url, cmd_str, -1, "", err_msg)
            raise DownloadError(err_msg)

        try:
            # Wait for execution with timeout
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            try:
                process.kill()
                await process.communicate()
            except Exception:
                pass
            timeout_msg = f"Download request timed out after {timeout_seconds} seconds."
            logger.error(f"ShellRunner: {timeout_msg} for URL: {url}")
            save_failure_log(url, cmd_str, -1, "", timeout_msg)
            raise DownloadError(timeout_msg)

        stdout_str = stdout.decode(errors="replace")
        stderr_str = stderr.decode(errors="replace")
        exit_code = process.returncode

        if exit_code != 0:
            logger.error(f"ShellRunner: Process exited with code {exit_code}. Error: {stderr_str.strip()}")
            save_failure_log(url, cmd_str, exit_code, stdout_str, stderr_str)
            raise DownloadError(f"Download command exited with code {exit_code}: {stderr_str.strip()[:500]}")

        # Check if the output file actually exists
        if not output_path.exists():
            missing_msg = "Downloaded file could not be found on disk."
            logger.error(f"ShellRunner: Command reported success, but output file is missing: {output_path}")
            save_failure_log(url, cmd_str, exit_code, stdout_str, missing_msg)
            raise DownloadError(missing_msg)

        # Check file size limit
        file_size_mb = output_path.stat().st_size / (1024 * 1024)
        if file_size_mb > self.max_file_size_mb:
            size_msg = f"Downloaded file ({file_size_mb:.1f}MB) exceeds the maximum limit of {self.max_file_size_mb}MB."
            logger.warning(f"ShellRunner: {size_msg}")
            save_failure_log(url, cmd_str, exit_code, stdout_str, size_msg)
            raise DownloadError(size_msg)

        logger.info(f"ShellRunner: Successfully downloaded video file to '{output_path}' ({file_size_mb:.2f}MB)")
        return output_path

# Global shell runner instance
shell_runner = ShellRunner(config.DOWNLOAD_COMMAND, config.MAX_FILE_SIZE_MB)
