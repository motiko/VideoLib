"""Classifies raw download/upload errors into user-friendly messages."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class UserError:
    """A user-facing error with a short message, optional suggestion, and raw details."""

    short: str  # e.g. "Video is not available in your region"
    suggestion: str | None = None  # e.g. "Try using a VPN or proxy"
    details: str | None = None  # raw stderr snippet for collapsed log


# Pattern → (short_message, suggestion)
# Patterns are checked in order; first match wins.
_DOWNLOAD_PATTERNS: list[tuple[re.Pattern[str], str, str | None]] = [
    # --- Executable not found (system configuration error) ---
    (
        re.compile(r"(executable not found|command not found|not found on host)", re.I),
        "⚙️ Downloader tool is not installed on the server",
        "The bot operator needs to install yt-dlp",
    ),
    # --- Download timeout (our own check) ---
    (
        re.compile(r"timed? ?out after \d+ seconds", re.I),
        "⏰ Download timed out",
        "The file might be very large, or the server is slow",
    ),
    # --- File too large (our own check) ---
    (
        re.compile(
            r"Downloaded file \((.+?)\) exceeds the maximum limit of (\d+)MB", re.I
        ),
        "📦 File too large ({match1} — limit is {match2} MB)",
        "Try a lower quality, or ask the bot operator to raise the limit",
    ),
    # --- Private / sign-in required ---
    (
        re.compile(r"(Private video|Sign in|login required|members.only)", re.I),
        "🔒 This video is private or requires sign-in",
        None,
    ),
    # --- Geo-blocked ---
    (
        re.compile(
            r"(not available in your country|geo.?restrict|geo.?block|country)", re.I
        ),
        "🌍 Video is not available in this region",
        None,
    ),
    # --- Age-restricted ---
    (
        re.compile(r"(age.?restrict|age.?gate|confirm your age|mature)", re.I),
        "🔞 Age-restricted video",
        "Cookies may be required to access this content",
    ),
    # --- Live stream ---
    (
        re.compile(r"(is live|live stream|live event|premiering)", re.I),
        "📡 Live streams cannot be downloaded",
        "Wait until the stream ends and try again",
    ),
    # --- Video removed / deleted / not found ---
    (
        re.compile(
            r"(video.*unavailable|removed|deleted|not found|does not exist|no video)",
            re.I,
        ),
        "🗑️ Video not found or has been removed",
        "Double-check the URL — the video may have been deleted",
    ),
    # --- Copyright / DMCA ---
    (
        re.compile(r"(copyright|dmca|taken down|blocked.*claim)", re.I),
        "⚖️ Video removed due to a copyright claim",
        None,
    ),
    # --- Unsupported site ---
    (
        re.compile(r"(unsupported url|no suitable extractor|not supported)", re.I),
        "🚫 This website is not supported",
        "Check supported sites at github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md",
    ),
    # --- Rate limited by source site ---
    (
        re.compile(r"(too many requests|rate.?limit|429|throttl)", re.I),
        "⏳ Rate limited by the source website",
        "Wait a few minutes and try again",
    ),
    # --- Network / connection errors ---
    (
        re.compile(
            r"(connection refused|timed? ?out|unreachable|network|dns|resolve|connect)",
            re.I,
        ),
        "🌐 Network error — could not reach the source",
        "Check your internet connection or try again later",
    ),
    # --- DRM protected ---
    (
        re.compile(r"(drm|widevine|protected content|encrypted)", re.I),
        "🔐 This content is DRM-protected and cannot be downloaded",
        None,
    ),
    # --- Format not available ---
    (
        re.compile(r"(requested format|format.*not available|no video formats)", re.I),
        "🎞️ Requested video format is not available",
        "The video may only be available in unsupported formats",
    ),
]

# Upload / Telegram API error patterns
_UPLOAD_PATTERNS: list[tuple[re.Pattern[str], str, str | None]] = [
    # --- File too large for Telegram ---
    (
        re.compile(
            r"(too big|too large|413|request entity too large|file is too big)", re.I
        ),
        "📦 File is too large to send via Telegram",
        "The bot operator can set up a Local Bot API server to allow files up to 2 GB",
    ),
    # --- Bot blocked by user ---
    (
        re.compile(r"(bot was blocked|forbidden|user.*deactivated)", re.I),
        "🚷 Bot was blocked by the user",
        None,
    ),
    # --- Chat write permission revoked ---
    (
        re.compile(
            r"(not enough rights|have no write access|no write permission|chat not found)",
            re.I,
        ),
        "🔇 Bot doesn't have permission to send in this chat",
        "An admin needs to grant the bot send-message permissions",
    ),
    # --- Rate limited by Telegram ---
    (
        re.compile(r"(retry.?after|flood|too many requests|429)", re.I),
        "⏳ Telegram rate limit — too many requests",
        "Wait a moment and try again",
    ),
    # --- Timeout uploading ---
    (
        re.compile(r"(timed? ?out|timeout|read timeout|write timeout)", re.I),
        "⏰ Upload to Telegram timed out",
        "The file might be too large for the current connection speed",
    ),
]


def classify_download_error(raw_message: str) -> UserError:
    """Classifies a DownloadError message into a user-friendly UserError."""
    for pattern, short_template, suggestion in _DOWNLOAD_PATTERNS:
        m = pattern.search(raw_message)
        if m:
            # Support {match1}, {match2} placeholders from regex groups
            short = short_template
            for i, group in enumerate(m.groups(), start=1):
                short = short.replace(f"{{match{i}}}", group or "")
            return UserError(
                short=short, suggestion=suggestion, details=raw_message
            )

    # Fallback: unknown download error
    return UserError(
        short="❌ Download failed",
        suggestion="Check the URL and try again",
        details=raw_message,
    )


def classify_upload_error(raw_message: str) -> UserError:
    """Classifies a Telegram upload error into a user-friendly UserError."""
    for pattern, short_template, suggestion in _UPLOAD_PATTERNS:
        m = pattern.search(raw_message)
        if m:
            short = short_template
            for i, group in enumerate(m.groups(), start=1):
                short = short.replace(f"{{match{i}}}", group or "")
            return UserError(
                short=short, suggestion=suggestion, details=raw_message
            )

    return UserError(
        short="❌ Failed to send video",
        suggestion="Try again later",
        details=raw_message,
    )


def format_error_message(error: UserError, include_details: bool = True) -> str:
    """Formats a UserError into a Telegram-ready message string.

    Uses expandable blockquote for the raw details so the message stays compact.
    """
    lines = [error.short]
    if error.suggestion:
        lines.append(f"\n💡 {error.suggestion}")
    if include_details and error.details:
        # Truncate raw details to avoid Telegram's 4096 char message limit
        truncated = error.details[:500]
        if len(error.details) > 500:
            truncated += "…"
        lines.append(f"\n<blockquote expandable>{truncated}</blockquote>")
    return "\n".join(lines)
