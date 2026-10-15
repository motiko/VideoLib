# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Read `AGENTS.md` first.** It is the authoritative source for security rules (shell-injection handling), code conventions, git rules (no autonomous commits/pushes to `main`, no force-pushing), and the full release process. This file covers only what AGENTS.md doesn't.

The project is called **VideoLib** (package `videolib`, Python package dir `src/`); the repo directory is named `cornbot`.

## Commands

```bash
# Setup
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# Lint (CI runs this; must be clean)
.venv/bin/python -m ruff check .

# Tests (pytest-asyncio in auto mode — async tests need no decorator)
.venv/bin/pytest
.venv/bin/pytest tests/test_orchestrator.py
.venv/bin/pytest tests/test_orchestrator.py::test_name -v

# Run
.venv/bin/python run.py                  # Telegram daemon (needs TELEGRAM_BOT_TOKEN)
.venv/bin/python run.py --url <URL>      # one-shot download through CliPlatform
.venv/bin/python run.py --cli            # interactive prompt
.venv/bin/python run.py --debug --keep-files   # verbose logs, keep files in DOWNLOAD_DIR
```

CI (`.github/workflows/ci.yml`) runs ruff + pytest on macOS with Python 3.11 and 3.12. The project targets py310, so keep syntax 3.10-compatible.

## Architecture

Request flow: **platform adapter → `Orchestrator.handle_request` → `ShellRunner` → `platform.send_video` → `StorageManager.cleanup`**.

- **Entry point** `src/run.py` (`main_sync` is the `videolib` console script; `run.py` and `src/__main__.py` just delegate). It picks a mode: CLI (`--url`/`--cli` → `CliPlatform`) or daemon (`TelegramPlatform`), registers the platform with an `Orchestrator`, and waits for SIGINT/SIGTERM.
- **Platforms** (`src/platforms/`) subclass `BasePlatform` and implement `send_message`/`edit_message`/`delete_message`/`react_to_message`/`send_video`. `register_platform` binds `orchestrator.handle_request` as the platform's `message_callback`; the platform calls it as `(platform_name, chat_id, message_id, url, user_id=...)`. IDs are passed around as strings.
- **`TelegramPlatform`** (`src/platforms/telegram.py`) handles all chat commands (user + admin) itself, extracts URLs from messages (in groups, only when the bot is @mentioned), and **enforces access control/quota before** dispatching to the orchestrator via `asyncio.create_task`. Access checks don't live in the orchestrator.
- **`Orchestrator`** (`src/core/orchestrator.py`) validates the URL, limits concurrency with a semaphore (`MAX_CONCURRENT_DOWNLOADS`), extracts the title, drives a progress status message (`MessageProgressTracker`, throttled edits) plus emoji reactions (👀 → ⚡ → 🚀 → 💯, or 👎 on failure), uploads, records usage in `access_manager`, and always cleans up in `finally`. Errors are turned into user-facing text by `src/core/errors.py` (`classify_download_error` / `classify_upload_error` → `format_error_message`).
- **`ShellRunner`** (`src/downloader/shell_runner.py`) tokenizes the `DOWNLOAD_COMMAND` template with `shlex.split`, substitutes `{url}`/`{output_path}` per token, and runs it with `create_subprocess_exec` (never a shell). It streams yt-dlp stdout through `parse_yt_dlp_progress`, globs for the actual output file (yt-dlp may change the extension), enforces `MAX_FILE_SIZE_MB`, and writes failure reports to `LOG_DIR` via `save_failure_log`. Failures raise `DownloadError`.
- **`AccessManager`** (`src/core/access.py`) is SQLite-backed (`DB_PATH`, WAL mode, `threading.RLock`), synchronous. Tiers: admin (`ADMIN_USERS` env), allowlist (users or authorized groups), public (only when admin enables public access). Each tier has daily MB/download limits; users can have custom per-user overrides.

### Module-level singletons and config

`config` (`src/config.py`), `storage_manager`, `shell_runner`, `access_manager`, and `upload_speed_estimator` are global instances created at import time. `Config` reads env vars as class attributes at import, loading the file named by `VIDEOLIB_ENV_FILE` (default `./.env`, with `override=True`). Consequences:
- Changing env vars after import has no effect; tests patch the singleton attributes or construct fresh instances instead (e.g. `AccessManager(db_path=tmp_path / "x.db")`).
- Importing `src.core.access` creates the SQLite DB at `config.DB_PATH`.

### Testing patterns

`tests/test_orchestrator.py` defines a `MockPlatform(BasePlatform)` that records sent/edited/deleted messages, reactions, and videos. Reuse it for orchestrator tests and patch `shell_runner`/`storage_manager` methods rather than running yt-dlp.

## Deployment / packaging

- macOS launchd service: `service/com.videolib.agent.plist`, installed via `scripts/install-service.sh`; config at `~/.config/videolib/.env`, logs at `~/Library/Logs/VideoLib/`. `videolib --status` inspects it.
- Homebrew formula lives in `homebrew/videolib.rb` and is published to `motiko/homebrew-videolib` by `.github/workflows/release.yml` when a `vX.Y.Z` tag is pushed (see the release process in AGENTS.md; bump version in both `pyproject.toml` and `src/__init__.py`).

## README discrepancies

The README mentions a `src/queue/` module; it doesn't exist — queueing is just the orchestrator's semaphore.
