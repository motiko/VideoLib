from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access import AccessManager
from src.core.orchestrator import Orchestrator, ActiveDownload
from src.platforms.telegram import TelegramPlatform
from src.platforms import telegram_admin as admin_mod
from tests.test_telegram_access import make_update

ADMIN_ID = 424242
DENIED = "⛔ This command is restricted to bot administrators."


@pytest.fixture
def mgr(tmp_path):
    return AccessManager(db_path=tmp_path / "admin_test.db")


@pytest.fixture
def platform(mgr, tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir()
    with patch("src.platforms.telegram.access_manager", mgr), \
         patch("src.platforms.telegram_admin.access_manager", mgr), \
         patch.object(admin_mod.config, "ADMIN_USERS", {ADMIN_ID}), \
         patch.object(admin_mod.config, "LOG_DIR", log_dir), \
         patch.object(admin_mod.config, "DOWNLOAD_DIR", dl_dir), \
         patch("src.utils.logs.config.LOG_DIR", log_dir):
        p = TelegramPlatform(token="1234:MOCK")
        p.application = MagicMock()
        p.send_message = AsyncMock(return_value="999")
        p.edit_message = AsyncMock()
        p.send_document = AsyncMock()
        p.send_video = AsyncMock()
        orch = Orchestrator()
        orch.register_platform(p)
        yield p


def admin_update(text: str, **kw):
    return make_update(user_id=ADMIN_ID, username="boss", text=text, **kw)


def sent(platform) -> str:
    return platform.send_message.call_args[0][1]


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("handler,text", [
    ("_handle_uptime", "/uptime"),
    ("_handle_status", "/status"),
    ("_handle_queue", "/queue"),
    ("_handle_logs", "/logs"),
    ("_handle_stats", "/stats"),
    ("_handle_history", "/history"),
    ("_handle_restart", "/restart"),
    ("_handle_ytdlp", "/ytdlp"),
    ("_handle_broadcast", "/broadcast hi"),
    ("_handle_debug_upload", "/debug_upload x.mp4"),
])
async def test_ops_commands_require_admin(platform, handler, text):
    update, context = make_update(user_id=1, username="nobody", text=text)
    await getattr(platform, handler)(update, context)
    platform.send_message.assert_called_once_with("1", DENIED)


# ---------------------------------------------------------------------------
# Uptime / status / queue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uptime(platform):
    await platform._handle_uptime(*admin_update("/uptime"))
    msg = sent(platform)
    assert "Uptime" in msg and "PID" in msg and "Version" in msg


@pytest.mark.asyncio
async def test_status_reports_runtime(platform):
    with patch.object(admin_mod, "get_ytdlp_version", AsyncMock(return_value="2026.10.01")):
        await platform._handle_status(*admin_update("/status"))
    msg = sent(platform)
    assert "2026.10.01" in msg
    assert "Active downloads" in msg
    assert "Disk free" in msg
    assert "Public access" in msg


@pytest.mark.asyncio
async def test_queue_empty_and_populated(platform):
    await platform._handle_queue(*admin_update("/queue"))
    assert "Nothing in progress" in sent(platform)

    platform.orchestrator.active["telegram:1:1"] = ActiveDownload(
        "telegram", "1", "1", "https://youtube.com/w?v=1", user_id=7, title="Cat <video>", stage="downloading"
    )
    await platform._handle_queue(*admin_update("/queue"))
    msg = sent(platform)
    assert "downloading" in msg
    assert "Cat &lt;video&gt;" in msg


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_logs_tail_escapes_html(platform):
    log = admin_mod.config.LOG_DIR / "videolib.log"
    log.write_text("\n".join(f"[INFO] line {i} <tag>" for i in range(50)) + "\n")
    await platform._handle_logs(*admin_update("/logs tail 3"))
    msg = sent(platform)
    assert msg.startswith("<pre>")
    assert "line 49 &lt;tag&gt;" in msg
    assert "line 46" not in msg


@pytest.mark.asyncio
async def test_logs_default_is_tail_and_missing_file(platform):
    await platform._handle_logs(*admin_update("/logs"))
    assert "No log file" in sent(platform)


@pytest.mark.asyncio
async def test_logs_errors_upload_failures(platform):
    log = admin_mod.config.LOG_DIR / "videolib.log"
    log.write_text("[INFO] ok\n[ERROR] bad thing\n")
    await platform._handle_logs(*admin_update("/logs errors"))
    msg = sent(platform)
    assert "bad thing" in msg and "ok\n" not in msg

    await platform._handle_logs(*admin_update("/logs upload"))
    platform.send_document.assert_called_once()
    assert platform.send_document.call_args[0][1] == log

    await platform._handle_logs(*admin_update("/logs failures"))
    assert "No failure reports" in sent(platform)

    fdir = admin_mod.config.LOG_DIR / "failures"
    fdir.mkdir()
    report = fdir / "failed_20260101_000000_abcd.log"
    report.write_text("report")
    await platform._handle_logs(*admin_update("/logs failures"))
    assert report.name in sent(platform)

    platform.send_document.reset_mock()
    await platform._handle_logs(*admin_update("/logs failure latest"))
    assert platform.send_document.call_args[0][1] == report

    await platform._handle_logs(*admin_update("/logs failure ../../etc/passwd"))
    assert "not found" in sent(platform).lower()

    await platform._handle_logs(*admin_update("/logs bogus"))
    assert "Usage" in sent(platform)


# ---------------------------------------------------------------------------
# Stats / history
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stats_overview_and_subcommands(platform, mgr):
    mgr.record_user(1, "alice", "Alice")
    mgr.record_download(1, 2048)
    mgr.record_download_event(1, 1, "https://youtube.com/a", None, 2048, 1.0, "ok")
    mgr.record_download_event(1, 1, "https://vimeo.com/b", None, 0, 1.0, "failed", error="nope")

    await platform._handle_stats(*admin_update("/stats"))
    msg = sent(platform)
    assert "youtube.com" in msg and "@alice" in msg and "Success rate" in msg

    await platform._handle_stats(*admin_update("/stats top_users 30 5"))
    assert "Top users" in sent(platform) and "30 days" in sent(platform)

    await platform._handle_stats(*admin_update("/stats popular_websites"))
    assert "youtube.com" in sent(platform)

    await platform._handle_stats(*admin_update("/stats daily"))
    assert "Daily" in sent(platform)

    await platform._handle_stats(*admin_update("/stats errors"))
    assert "nope" in sent(platform)

    await platform._handle_stats(*admin_update("/stats whatever"))
    assert "Usage" in sent(platform)


@pytest.mark.asyncio
async def test_history_all_and_per_user(platform, mgr):
    mgr.record_user(1, "alice", "Alice")
    mgr.record_user(2, "bob", "Bob")
    mgr.record_download_event(1, 1, "https://youtube.com/a", None, 10, 1.0, "ok")
    mgr.record_download_event(2, 2, "https://vimeo.com/b", None, 10, 1.0, "ok")

    await platform._handle_history(*admin_update("/history"))
    msg = sent(platform)
    assert "youtube.com" in msg and "vimeo.com" in msg

    await platform._handle_history(*admin_update("/history @bob 5"))
    msg = sent(platform)
    assert "vimeo.com" in msg and "youtube.com" not in msg

    await platform._handle_history(*admin_update("/history alice 1"))
    msg = sent(platform)
    assert "youtube.com" in msg and "vimeo.com" not in msg

    await platform._handle_history(*admin_update("/history @ghost"))
    assert "not found" in sent(platform).lower()


# ---------------------------------------------------------------------------
# Restart / ytdlp / broadcast / debug_upload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_restart_refuses_when_busy_unless_forced(platform, mgr):
    orch = platform.orchestrator
    orch.active["k"] = ActiveDownload("telegram", "1", "1", "https://x.com/1")
    await platform._handle_restart(*admin_update("/restart"))
    assert "force" in sent(platform)
    assert not orch.shutdown_event.is_set()

    await platform._handle_restart(*admin_update("/restart force"))
    assert orch.shutdown_event.is_set() and orch.restart_requested is True
    assert mgr.get_setting("pending_restart_notify") == str(ADMIN_ID)


@pytest.mark.asyncio
async def test_restart_when_idle(platform):
    await platform._handle_restart(*admin_update("/restart"))
    assert "Restarting" in sent(platform)
    assert platform.orchestrator.restart_requested is True


@pytest.mark.asyncio
async def test_ytdlp_version_and_update(platform):
    with patch.object(admin_mod, "get_ytdlp_version", AsyncMock(return_value="2026.10.01")):
        await platform._handle_ytdlp(*admin_update("/ytdlp"))
        assert "2026.10.01" in sent(platform)

    with patch.object(admin_mod, "update_ytdlp", AsyncMock(return_value=(True, "Updated to 2026.10.02"))), \
         patch.object(admin_mod, "get_ytdlp_version", AsyncMock(return_value="2026.10.02")):
        await platform._handle_ytdlp(*admin_update("/ytdlp update"))
    platform.edit_message.assert_called_once()
    final = platform.edit_message.call_args[0][2]
    assert "2026.10.02" in final and "Updated" in final


@pytest.mark.asyncio
async def test_broadcast_two_step(platform, mgr):
    mgr.record_user(1, "alice", "Alice")
    mgr.record_user(2, "bob", "Bob")

    await platform._handle_broadcast(*admin_update("/broadcast"))
    assert "Usage" in sent(platform)

    await platform._handle_broadcast(*admin_update("/broadcast Hello <everyone>"))
    msg = sent(platform)
    assert "confirm" in msg and "3 user" in msg and "&lt;everyone&gt;" in msg
    assert platform.send_message.call_count == 2

    await platform._handle_broadcast(*admin_update("/broadcast confirm"))
    targets = {c[0][0] for c in platform.send_message.call_args_list}
    assert {"1", "2"} <= targets
    assert "Sent to 3" in sent(platform)

    await platform._handle_broadcast(*admin_update("/broadcast confirm"))
    assert "No pending broadcast" in sent(platform)

    await platform._handle_broadcast(*admin_update("/broadcast later"))
    await platform._handle_broadcast(*admin_update("/broadcast cancel"))
    assert "cancelled" in sent(platform).lower()


@pytest.mark.asyncio
async def test_debug_upload_confined_to_download_dir(platform, tmp_path):
    outside = tmp_path / "secret.env"
    outside.write_text("TOKEN")
    await platform._handle_debug_upload(*admin_update(f"/debug_upload {outside}"))
    assert "inside" in sent(platform).lower()
    platform.send_video.assert_not_called()

    inside = admin_mod.config.DOWNLOAD_DIR / "clip.mp4"
    inside.write_bytes(b"0")
    await platform._handle_debug_upload(*admin_update("/debug_upload clip.mp4"))
    platform.send_video.assert_called_once()
    assert platform.send_video.call_args[0][1] == inside


@pytest.mark.asyncio
async def test_adminhelp_lists_ops(platform):
    await platform._handle_adminhelp(*admin_update("/adminhelp"))
    msg = sent(platform)
    for cmd in ("/uptime", "/status", "/queue", "/logs", "/stats", "/history", "/restart", "/ytdlp", "/broadcast"):
        assert cmd in msg


@pytest.mark.asyncio
async def test_restart_notice_sent_on_start(platform, mgr):
    mgr.set_setting("pending_restart_notify", "555")
    with patch.object(admin_mod, "get_ytdlp_version", AsyncMock(return_value="v")):
        await platform._send_restart_notice()
    assert platform.send_message.call_args[0][0] == "555"
    assert "back online" in sent(platform).lower()
    assert mgr.get_setting("pending_restart_notify") is None


def test_parse_days_and_limit():
    assert admin_mod._parse_days_limit([], 7, 10) == (7, 10)
    assert admin_mod._parse_days_limit(["30"], 7, 10) == (30, 10)
    assert admin_mod._parse_days_limit(["30", "3"], 7, 10) == (30, 3)
    assert admin_mod._parse_days_limit(["x", "0"], 7, 10) == (7, 10)
    assert admin_mod._parse_days_limit(["9999"], 7, 10) == (365, 10)
