"""Tests for the error classification and formatting module."""
from src.core.errors import (
    classify_download_error,
    classify_upload_error,
    format_error_message,
    UserError,
)


class TestClassifyDownloadError:
    """Verify that raw yt-dlp stderr strings are classified into user-friendly errors."""

    def test_file_too_large(self) -> None:
        err = classify_download_error(
            "Downloaded file (125.3MB) exceeds the maximum limit of 50MB."
        )
        assert "125.3MB" in err.short
        assert "50" in err.short
        assert err.suggestion is not None
        assert err.details is not None

    def test_private_video(self) -> None:
        err = classify_download_error(
            "Download command exited with code 1: ERROR: [youtube] abc123: "
            "Private video. Sign in if you've been granted access."
        )
        assert "private" in err.short.lower() or "sign-in" in err.short.lower()

    def test_sign_in_required(self) -> None:
        err = classify_download_error(
            "Download command exited with code 1: ERROR: [youtube] abc: "
            "Sign in to confirm your age. This video may be inappropriate for some users."
        )
        assert "private" in err.short.lower() or "sign-in" in err.short.lower()

    def test_geo_blocked(self) -> None:
        err = classify_download_error(
            "Download command exited with code 1: ERROR: Video unavailable. "
            "The uploader has not made this video available in your country."
        )
        assert "region" in err.short.lower()
        # Geo-blocked suggestion should NOT be shown to end users
        assert err.suggestion is None

    def test_age_restricted(self) -> None:
        err = classify_download_error(
            "Download command exited with code 1: ERROR: [youtube] abc: "
            "This video is age-restricted."
        )
        assert "age" in err.short.lower()
        assert "cookies" in (err.suggestion or "").lower()

    def test_live_stream(self) -> None:
        err = classify_download_error(
            "Download command exited with code 1: ERROR: This live event has not started yet."
        )
        assert "live" in err.short.lower()

    def test_premiering(self) -> None:
        err = classify_download_error(
            "Download command exited with code 1: ERROR: This video is premiering soon."
        )
        assert "live" in err.short.lower()

    def test_removed_video(self) -> None:
        err = classify_download_error(
            "Download command exited with code 1: ERROR: Video unavailable. "
            "This video has been removed by the uploader."
        )
        assert "removed" in err.short.lower() or "not found" in err.short.lower()

    def test_deleted_video(self) -> None:
        err = classify_download_error(
            "Download command exited with code 1: ERROR: [youtube] abc: "
            "This video has been deleted."
        )
        assert "removed" in err.short.lower() or "deleted" in err.short.lower()

    def test_copyright_claim(self) -> None:
        err = classify_download_error(
            "Download command exited with code 1: ERROR: This video has been taken down "
            "due to a copyright claim."
        )
        assert "copyright" in err.short.lower()
        assert err.suggestion is None

    def test_unsupported_site(self) -> None:
        err = classify_download_error(
            "Download command exited with code 1: ERROR: Unsupported URL: "
            "https://example.com/video"
        )
        assert "not supported" in err.short.lower()
        assert err.suggestion is not None

    def test_rate_limited_429(self) -> None:
        err = classify_download_error(
            "Download command exited with code 1: ERROR: HTTP Error 429: Too Many Requests"
        )
        assert "rate" in err.short.lower()

    def test_rate_limited_throttle(self) -> None:
        err = classify_download_error(
            "Download command exited with code 1: ERROR: Download throttled by server."
        )
        assert "rate" in err.short.lower()

    def test_network_timeout(self) -> None:
        err = classify_download_error(
            "Download command exited with code 1: ERROR: Unable to download: "
            "Connection timed out"
        )
        assert "network" in err.short.lower() or "timed out" in err.short.lower()

    def test_network_dns(self) -> None:
        err = classify_download_error(
            "Download command exited with code 1: ERROR: Unable to resolve host"
        )
        assert "network" in err.short.lower()

    def test_drm_protected(self) -> None:
        err = classify_download_error(
            "Download command exited with code 1: ERROR: DRM protected content "
            "cannot be downloaded."
        )
        assert "drm" in err.short.lower()

    def test_format_not_available(self) -> None:
        err = classify_download_error(
            "Download command exited with code 1: ERROR: Requested format is not available."
        )
        assert "format" in err.short.lower()

    def test_download_timeout(self) -> None:
        err = classify_download_error(
            "Download request timed out after 300 seconds."
        )
        assert "timed out" in err.short.lower()

    def test_executable_not_found(self) -> None:
        err = classify_download_error(
            "Downloader executable not found on host system: yt-dlp"
        )
        assert "not installed" in err.short.lower()

    def test_unknown_error_fallback(self) -> None:
        err = classify_download_error(
            "Some completely unknown error message xyz 12345"
        )
        assert "failed" in err.short.lower()
        assert err.suggestion is not None
        assert err.details is not None

    def test_details_always_present(self) -> None:
        """Every classified error should preserve the raw message in details."""
        raw = "ERROR: [youtube] abc: Private video."
        err = classify_download_error(raw)
        assert err.details == raw


class TestClassifyUploadError:
    """Verify that Telegram API error strings are classified into user-friendly errors."""

    def test_file_too_big(self) -> None:
        err = classify_upload_error(
            "telegram.error.BadRequest: File is too big"
        )
        assert "too large" in err.short.lower()

    def test_request_entity_too_large(self) -> None:
        err = classify_upload_error(
            "httpx.HTTPStatusError: 413 Request Entity Too Large"
        )
        assert "too large" in err.short.lower()

    def test_bot_blocked(self) -> None:
        err = classify_upload_error(
            "telegram.error.Forbidden: bot was blocked by the user"
        )
        assert "blocked" in err.short.lower()

    def test_user_deactivated(self) -> None:
        err = classify_upload_error(
            "telegram.error.Forbidden: user is deactivated"
        )
        assert "blocked" in err.short.lower()

    def test_no_write_permission(self) -> None:
        err = classify_upload_error(
            "telegram.error.BadRequest: Not enough rights to send messages"
        )
        assert "permission" in err.short.lower()

    def test_rate_limit_flood(self) -> None:
        err = classify_upload_error(
            "RetryAfter: Flood control exceeded. Retry in 30 seconds."
        )
        assert "rate limit" in err.short.lower()

    def test_upload_timeout(self) -> None:
        err = classify_upload_error("httpx.ReadTimeout: timed out")
        assert "timed out" in err.short.lower()

    def test_write_timeout(self) -> None:
        err = classify_upload_error("httpx.WriteTimeout: write timeout")
        assert "timed out" in err.short.lower()

    def test_unknown_upload_error(self) -> None:
        err = classify_upload_error(
            "Something completely unknown went wrong during upload"
        )
        assert "failed" in err.short.lower()
        assert err.suggestion is not None


class TestFormatErrorMessage:
    """Verify that UserError objects are formatted into Telegram-ready messages."""

    def test_with_suggestion_and_details(self) -> None:
        err = UserError(
            short="📦 File too large",
            suggestion="Try a lower quality",
            details="Downloaded file (125MB) exceeds limit of 50MB",
        )
        msg = format_error_message(err)
        assert "📦 File too large" in msg
        assert "💡 Try a lower quality" in msg
        assert "<blockquote expandable>" in msg
        assert "Downloaded file" in msg

    def test_without_suggestion(self) -> None:
        err = UserError(short="🔒 Private video", details="stderr output here")
        msg = format_error_message(err)
        assert "💡" not in msg
        assert "<blockquote expandable>" in msg

    def test_without_details(self) -> None:
        err = UserError(short="❌ Download failed", suggestion="Try again")
        msg = format_error_message(err, include_details=True)
        assert "blockquote" not in msg
        assert "💡 Try again" in msg

    def test_include_details_false(self) -> None:
        err = UserError(
            short="Error",
            suggestion="Hint",
            details="raw log output here",
        )
        msg = format_error_message(err, include_details=False)
        assert "blockquote" not in msg
        assert "raw log" not in msg

    def test_details_truncation(self) -> None:
        long_details = "x" * 600
        err = UserError(short="Error", details=long_details)
        msg = format_error_message(err)
        assert "…" in msg
        # Truncated to 500 chars + ellipsis
        assert "x" * 500 in msg
        assert "x" * 501 not in msg

    def test_short_details_no_truncation(self) -> None:
        short_details = "Short error text"
        err = UserError(short="Error", details=short_details)
        msg = format_error_message(err)
        assert "…" not in msg
        assert short_details in msg

    def test_empty_short_message(self) -> None:
        err = UserError(short="")
        msg = format_error_message(err)
        assert isinstance(msg, str)
