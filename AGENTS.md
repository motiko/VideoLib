# 🤖 Agent Guidelines & Codebase Conventions

Welcome! If you are an AI developer agent working on **CornBot**, please adhere to the rules, architectural patterns, and security practices documented below.

---

## 🎯 Architecture Goals

* **Modular Platform Adapters**: Keep platform-specific code (Telegram, Discord, etc.) strictly isolated in `src/platforms/`. Platform classes must inherit from `BasePlatform` and expose uniform methods.
* **Strict Non-Blocking Loop**: Video downloading can take seconds or minutes. Never block the main asyncio loop. Always use async subprocesses or execute blocking tasks in an executor (`asyncio.to_thread`).
* **Guaranteed Disk Cleanup**: Ensure that every downloaded file is cleaned up, regardless of whether the transfer succeeded or failed.

---

## 🔒 Security & Command Execution

Since this bot receives URLs from untrusted users and passes them to shell commands, we must guard against **Shell Injection Vulnerabilities**.

### 1. Prefer Subprocess Arrays (`exec`)
Whenever possible, execute shell commands as list arguments using `asyncio.create_subprocess_exec` instead of `asyncio.create_subprocess_shell` with a string template.

*❌ **Incorrect / Vulnerable**:*
```python
# Vulnerable to URL shell injections like 'https://example.com; rm -rf /'
cmd = f"yt-dlp -o {out} {url}"
process = await asyncio.create_subprocess_shell(cmd)
```

*✅ **Correct**:*
```python
# Safe: arguments are passed as a list, preventing shell injection
args = ["yt-dlp", "-f", "mp4", "-o", out, url]
process = await asyncio.create_subprocess_exec(*args)
```

### 2. Sanitizing Custom Command Strings
If the user demands a configurable shell command string (e.g., via the `.env` file structure `yt-dlp -o "{output_path}" "{url}"`), you must:
1. Parse and validate that the URL is a syntactically correct HTTP/HTTPS link before inserting it.
2. Use Python's `shlex.quote()` on both the URL and the output path.
3. Reject inputs that contain illegal characters or command separators.

---

## 🛠️ Code Conventions

### Python Style
* **Type Hints**: All functions and methods must have type annotations (e.g., `async def send_video(self, chat_id: str, file_path: str) -> None`).
* **Async/Await**: Use `asyncio` for asynchronous programming. Do not use sync wrappers like `time.sleep()`; use `await asyncio.sleep()`.

### Error Handling & Cleanup
Wrap shell command execution and file deliveries in `try...finally` blocks to guarantee file deletion.

Example pattern:
```python
from src.storage.manager import storage_manager

async def process_request(url: str, chat_id: str):
    file_path = None
    try:
        file_path = await downloader.download(url)
        await platform.send_video(chat_id, file_path)
    except Exception as e:
        logger.error(f"Failed to process video: {e}")
        await platform.send_message(chat_id, "Sorry, download failed.")
    finally:
        if file_path:
            await storage_manager.cleanup(file_path)
```

---

## 🧪 Testing Strategies

* **Mock Subprocesses**: When writing tests, mock `asyncio.create_subprocess_exec` to avoid making real network requests and running external binaries.
* **Mock Platform APIs**: Mock the Telegram Bot API and Discord Client to isolate internal business logic from real API roundtrips.
* **Test Runner**: Use `pytest` for running unit and integration tests.

To run tests:
```bash
pytest tests/
```

---

## 🚦 Feature Implementation Flow

When tasked with implementing a feature (e.g., "Add Discord platform"):
1. Check [README.md](file:///Users/k/cornbot/README.md) for architecture.
2. Create your file under the correct namespace.
3. Hook into the orchestrator inside `src/core/orchestrator.py` without modifying the core download logic.
4. Run `ruff check .` (or equivalent linter) to verify style.
5. Create a corresponding test file under `tests/` and run `pytest`.
