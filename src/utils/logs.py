"""Read-only helpers for exposing application logs and failure reports to admins."""

import html
import re
from collections import deque
from pathlib import Path
from src.config import config

_FAILURE_NAME_RE = re.compile(r"^failed_[A-Za-z0-9_]+\.log$")
_ERROR_LEVELS = ("[ERROR]", "[WARNING]", "[CRITICAL]")


def app_log_path() -> Path:
    """Path of the rotating application log written by src.utils.logger."""
    return config.LOG_DIR / "videolib.log"


def failures_dir() -> Path:
    """Directory where ShellRunner saves download failure diagnostic reports."""
    return config.LOG_DIR / "failures"


def tail_lines(path: Path, n: int) -> list[str]:
    """Returns the last `n` lines of a text file (without trailing newlines). Missing file -> []."""
    n = max(0, int(n))
    if n == 0 or not path.is_file():
        return []
    buf: deque[str] = deque(maxlen=n)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            buf.append(line.rstrip("\n"))
    return list(buf)


def grep_errors(path: Path, n: int) -> list[str]:
    """Returns the last `n` WARNING/ERROR/CRITICAL lines of a log file."""
    n = max(0, int(n))
    if n == 0 or not path.is_file():
        return []
    buf: deque[str] = deque(maxlen=n)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if any(level in line for level in _ERROR_LEVELS):
                buf.append(line.rstrip("\n"))
    return list(buf)


def list_failure_reports(limit: int = 10) -> list[Path]:
    """Newest-first list of failure report files."""
    d = failures_dir()
    if not d.is_dir():
        return []
    reports = [p for p in d.glob("failed_*.log") if p.is_file()]
    reports.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return reports[: max(0, int(limit))]


def resolve_failure_report(name: str) -> Path | None:
    """Maps 'latest' or an exact report filename to a path inside the failures dir; None if invalid/missing."""
    name = (name or "").strip()
    if name.lower() == "latest":
        reports = list_failure_reports(limit=1)
        return reports[0] if reports else None
    if not _FAILURE_NAME_RE.match(name):
        return None
    candidate = failures_dir() / name
    try:
        candidate.resolve().relative_to(failures_dir().resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def render_pre(text: str, max_chars: int = 3800) -> str:
    """Wraps text in an HTML <pre> block, escaped and truncated from the top to fit Telegram's message limit."""
    lines = text.split("\n")
    omitted = 0
    body = html.escape(text)
    while len(body) > max_chars and len(lines) > 1:
        # Drop lines from the top in chunks proportional to the overflow
        drop = max(1, len(lines) * (len(body) - max_chars) // max(len(body), 1))
        lines = lines[drop:]
        omitted += drop
        body = html.escape("\n".join(lines))
    if len(body) > max_chars:
        body = body[-max_chars:]
    if omitted:
        body = f"… {omitted} lines omitted …\n" + body
    return f"<pre>{body}</pre>"
