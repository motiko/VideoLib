# VideoLib — Telegram Video Downloader Bot

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Telegram Bot API](https://img.shields.io/badge/Telegram-Bot%20API-blue.svg?logo=telegram)](https://core.telegram.org/bots)
[![yt-dlp](https://img.shields.io/badge/downloader-yt--dlp-red.svg)](https://github.com/yt-dlp/yt-dlp)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**VideoLib** is a modular Telegram bot designed to receive URLs from users, download videos using a configurable shell command (such as `yt-dlp`), and reply directly with the downloaded media. Built for high extensibility, it orchestrates downloads and media processing specifically for Telegram.

---

## 🌟 Key Features

* **Telegram Integration**: Tailored specifically for the Telegram Bot API.
* **Non-Blocking Execution**: Asynchronous task runner ensuring bot responsiveness during long download tasks.
* **Custom Command Wrapper**: Run any shell command (e.g., `yt-dlp`, `ffmpeg`, custom scripts) to fetch and process media.
* **Automatic File Cleanup**: Instantly cleans up temporary files post-delivery or on download failure to preserve disk space.
* **Task Queues**: Rate-limiting and download queues to prevent server overload.

---

## 📐 System Architecture

VideoLib is designed with a decoupled architecture. Below is the system flow for a typical request:

```mermaid
sequenceDiagram
    autonumber
    actor User as Chat User
    participant Adapter as Telegram Adapter
    participant Engine as Core Orchestrator
    participant Queue as Download Queue
    participant Shell as Shell Worker
    participant Storage as File Manager

    User->>Adapter: Send message containing URL
    Adapter->>Engine: Parse & validate URL
    Engine->>Queue: Enqueue download task
    Queue->>Shell: Execute shell command async
    Note over Shell: runs e.g. yt-dlp -f mp4 <url>
    Shell-->>Storage: Save file to disk
    Shell-->>Engine: Task completed (File Path)
    Engine->>Adapter: Deliver video to user
    Adapter->>User: Reply with video file
    Engine->>Storage: Request cleanup
    Storage-->>Storage: Delete temporary file
```

### Component Breakdown

1. **Telegram Adapter** (`src/platforms/`): Listens to Telegram events, normalizes messages into internal events, and handles file uploads.
2. **Core Orchestrator** (`src/core/`): Manages the state machine of the request, routing messages between the adapter, queues, and command runners.
3. **Download Queue** (`src/queue/`): Controls concurrency, retries, and rate limits.
4. **Shell Execution Worker** (`src/downloader/`): Runs subprocesses asynchronously using Python's `asyncio.subprocess` to prevent blocking the event loop.
5. **Storage Manager** (`src/storage/`): Handles temporary directories, naming collisions, and guaranteed deletion hooks.

---

## 📂 Project Structure

```text
videolib/
├── .env.example             # Configuration template
├── README.md                # System documentation
├── AGENTS.md                # AI agent instructions
├── requirements.txt         # Dependencies
├── run.py                   # Entry point
└── src/
    ├── __init__.py
    ├── config.py            # Settings and env loading
    ├── core/                # Core orchestrator and task runners
    │   ├── __init__.py
    │   └── orchestrator.py
    ├── downloader/          # Subprocess wrappers for shell commands
    │   ├── __init__.py
    │   └── shell_runner.py
    ├── platforms/           # Platform abstraction and implementations
    │   ├── __init__.py
    │   ├── base.py          # Platform interface
    │   └── telegram.py      # Telegram implementation
    ├── storage/             # File storage & cleanup utility
    │   ├── __init__.py
    │   └── manager.py
    └── utils/               # Shared helpers (logger, parsing)
        ├── __init__.py
        └── logger.py
```

---

## 🚀 Getting Started

### Prerequisites

* **Python 3.10+**
* **FFmpeg**: Required by many downloader packages (like `yt-dlp`) for merging/transcoding video.
  * *macOS*: `brew install ffmpeg`
  * *Linux (Ubuntu)*: `sudo apt install ffmpeg`


### Installation

#### Option A: Homebrew (Recommended for macOS)

```bash
brew tap motiko/videolib
brew install videolib
```

#### Option B: From Source

1. Clone the repository:
   ```bash
   git clone https://github.com/motiko/VideoLib.git
   cd VideoLib
   ```

2. Create a virtual environment and install:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```

### Configuration

```bash
# For Homebrew installs:
mkdir -p ~/.config/videolib
cp $(brew --prefix videolib)/.env.example ~/.config/videolib/.env
vim ~/.config/videolib/.env

# For source installs:
cp .env.example .env
vim .env
```

#### Environment Variables

| Variable | Description | Default |
| :--- | :--- | :--- |
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) | (Required) |
| `TELEGRAM_API_URL` | Optional custom Local Bot API server URL | `https://api.telegram.org` |
| `DOWNLOAD_DIR` | Path where temporary files are stored | `./tmp` |
| `DOWNLOAD_COMMAND` | Shell command template. Use `{url}` and `{output_path}` as placeholders. | `yt-dlp -f "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" --merge-output-format mp4 -o "{output_path}" "{url}"` |
| `MAX_CONCURRENT_DOWNLOADS` | Max number of videos downloading at the same time | `3` |
| `MAX_FILE_SIZE_MB` | Maximum size in MB to download and send | `50` |

### Running the Local Telegram Bot API (Optional)

For handling large files (up to 2GB via Telegram Bot API) or reducing network overhead, you can run a local Telegram Bot API server using Docker. 

Run the generic container with Docker:

```bash
docker run -d -p 8081:8081 \
  --name=telegram-bot-api \
  --restart=always \
  -v telegram-bot-api-data:/var/lib/telegram-bot-api \
  -e TELEGRAM_API_ID="<YOUR_API_ID>" \
  -e TELEGRAM_API_HASH="<YOUR_API_HASH>" \
  aiogram/telegram-bot-api:latest
```
*(Make sure to replace `<YOUR_API_ID>` and `<YOUR_API_HASH>` with your credentials from my.telegram.org.)*

After starting the container, update your `.env` file to point to the local instance (for example, `TELEGRAM_API_URL=http://localhost:8081`).

### Running the Bot

```bash
# Foreground (source install)
python run.py
# or
videolib

# Single URL download
videolib --url https://example.com/video

# Interactive CLI mode
videolib --cli
```

---

## 🖥️ macOS Background Service

VideoLib can run as a persistent background service via macOS `launchd`, with automatic restart on crashes.

### Quick Start (Homebrew)

```bash
brew services start videolib    # Start & enable auto-start on login
brew services stop videolib     # Stop the service
brew services restart videolib  # Restart after config changes
brew services info videolib     # View service status
```

### Quick Start (Manual)

```bash
# Install the launchd plist
./scripts/install-service.sh

# Or specify a custom binary path
./scripts/install-service.sh /path/to/videolib
```

### Service Behavior

| Feature | Detail |
|---|---|
| **Auto-start on login** | `RunAtLoad = true` |
| **Auto-restart on crash** | `KeepAlive.SuccessfulExit = false` |
| **Throttle rapid crashes** | Waits 10 seconds before restarting |
| **Logs** | `~/Library/Logs/VideoLib/` |
| **Config** | `~/.config/videolib/.env` |

### Service Management

```bash
# Check service status
videolib --status

# View live logs
tail -f ~/Library/Logs/VideoLib/videolib.stdout.log

# Uninstall the service
./scripts/uninstall-service.sh
```

### Updating

```bash
# Homebrew
brew update && brew upgrade videolib
brew services restart videolib
```

---

## 🔒 Access Control & Tiered Daily Limits

VideoLib includes built-in tiered access control and daily bandwidth/download quotas backed by SQLite persistence.

* **Default Policy**: Only bot administrators and explicitly allowlisted users are permitted to download videos.
* **Public Access**: Administrators can open access to everyone using `/publicaccess on`.
* **Separate Limit Tiers**: Allowlisted users and public users ("the rest") have distinct, independently configurable daily quotas.

### Configuration (`.env`)
```ini
# Comma-separated list of admin numeric Telegram User IDs
ADMIN_USERS=12345678,98765432

# Default daily limits for allowlisted users
DEFAULT_ALLOWLIST_DAILY_MB=1000
DEFAULT_ALLOWLIST_DAILY_DOWNLOADS=20

# Default daily limits for all other users (active when public access is opened)
DEFAULT_PUBLIC_DAILY_MB=200
DEFAULT_PUBLIC_DAILY_DOWNLOADS=3

# Path to SQLite database file
DB_PATH=data/videolib.db
```

### Chat Commands

#### For All Users
* `/id`: Check your numeric Telegram User ID, username, and Chat ID.
* `/mylimits` (aliases: `/limits`, `/quota`, `/usage`): Check your current daily downloads, bandwidth used, tier, and remaining quota.

#### For Bot Administrators
* `/publicaccess [on|off]` (alias: `/allowall`): Toggle open public access for all users on or off.
* `/setlimit <allowlist|public> [mb=N] [dl=N]`: Set daily bandwidth or download limits for a tier (e.g. `/setlimit allowlist mb=1500 dl=25` or `/setlimit public mb=150 dl=2`). Run without arguments to view current limits for both tiers.
* `/allow <@user | user_id>`: Grant allowlist access to a user (or reply to their message).
* `/disallow <@user | user_id>`: Revoke a user's allowlist access.
* `/allowed`: List all currently authorized allowlist users and their custom limits.
* `/adjustlimits <@user | user_id> [mb=N] [dl=N] [reset]`: Assign individual custom VIP limits to a specific user (or `reset` to tier defaults).
* `/showusage [@user | user_id]`: View detailed stats for a user, or overall bot traffic summary if no user is specified.
* `/resetusage <@user | user_id>`: Reset a user's daily usage counters to zero.
* `/adminhelp`: View the complete administrator command cheatsheet.

---

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
