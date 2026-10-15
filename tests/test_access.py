import pytest
from src.core.access import AccessManager

@pytest.fixture
def temp_access_manager(tmp_path):
    db_file = tmp_path / "test_videolib.db"
    mgr = AccessManager(db_path=db_file)
    return mgr

def test_user_recording_and_admin(temp_access_manager):
    mgr = temp_access_manager
    mgr.record_user(1001, "alice", "Alice Cooper")
    info = mgr.get_user_info(1001)
    assert info is not None
    assert info["username"] == "alice"
    assert info["first_name"] == "Alice Cooper"
    assert mgr.is_admin(1001) is False

def test_default_restricted_mode_and_public_toggle(temp_access_manager):
    mgr = temp_access_manager
    # By default, public access must be disabled
    assert mgr.is_public_access_enabled() is False

    mgr.record_user(2001, "bob", "Bob")
    # Bob is not allowlisted and public access is disabled -> rejected
    allowed, reason = mgr.check_can_download(2001)
    assert allowed is False
    assert "Access restricted" in reason

    # Allow bob into allowlist
    mgr.allow_user(2001, True)
    assert mgr.is_user_allowed(2001) is True
    allowed, reason = mgr.check_can_download(2001)
    assert allowed is True
    assert reason is None

    # Revoke bob from allowlist
    mgr.allow_user(2001, False)
    allowed, reason = mgr.check_can_download(2001)
    assert allowed is False

    # Turn on public access -> Bob is now allowed via public tier
    mgr.set_public_access_enabled(True)
    assert mgr.is_public_access_enabled() is True
    allowed, reason = mgr.check_can_download(2001)
    assert allowed is True

    # Turn off public access -> Bob restricted again
    mgr.set_public_access_enabled(False)
    allowed, reason = mgr.check_can_download(2001)
    assert allowed is False

def test_tiered_limits_differentiation(temp_access_manager):
    mgr = temp_access_manager
    mgr.set_tier_limits("allowlist", daily_mb=1500, daily_downloads=30)
    mgr.set_tier_limits("public", daily_mb=100, daily_downloads=2)

    # 1. Allowlisted user
    user_al = 3001
    mgr.record_user(user_al, "alice_vip", "Alice")
    mgr.allow_user(user_al, True)
    mb, dl, tier = mgr.get_user_limits(user_al)
    assert mb == 1500
    assert dl == 30
    assert "Allowlist" in tier

    # 2. Public user
    user_pub = 3002
    mgr.record_user(user_pub, "charlie_guest", "Charlie")
    mb_pub, dl_pub, tier_pub = mgr.get_user_limits(user_pub)
    assert mb_pub == 100
    assert dl_pub == 2
    assert "Public" in tier_pub

def test_daily_download_count_limit(temp_access_manager):
    mgr = temp_access_manager
    mgr.set_public_access_enabled(True)
    mgr.set_tier_limits("public", daily_mb=500, daily_downloads=2)

    user_id = 4001
    mgr.record_user(user_id, "dave", "Dave")

    # 1st download allowed
    can_dl, _ = mgr.check_can_download(user_id)
    assert can_dl is True
    mgr.record_download(user_id, size_bytes=10 * 1024 * 1024)

    # 2nd download allowed
    can_dl, _ = mgr.check_can_download(user_id)
    assert can_dl is True
    mgr.record_download(user_id, size_bytes=10 * 1024 * 1024)

    # 3rd download blocked
    can_dl, reason = mgr.check_can_download(user_id)
    assert can_dl is False
    assert "Daily download limit reached (2/2 downloads)" in reason

def test_daily_bandwidth_limit(temp_access_manager):
    mgr = temp_access_manager
    mgr.set_public_access_enabled(True)
    mgr.set_tier_limits("public", daily_mb=50, daily_downloads=10)

    user_id = 5001
    mgr.record_user(user_id, "david", "David")

    # Record 55MB (exceeds 50MB limit)
    mgr.record_download(user_id, size_bytes=55 * 1024 * 1024)
    can_dl, reason = mgr.check_can_download(user_id)
    assert can_dl is False
    assert "Daily traffic limit reached" in reason

def test_custom_user_limits_override(temp_access_manager):
    mgr = temp_access_manager
    mgr.set_public_access_enabled(True)
    mgr.set_tier_limits("public", daily_mb=50, daily_downloads=2)

    user_id = 6001
    mgr.record_user(user_id, "eve", "Eve")

    # Give Eve VIP limits
    mgr.set_user_limits(user_id, daily_mb=1000, daily_downloads=50)
    mb, dl, tier = mgr.get_user_limits(user_id)
    assert mb == 1000
    assert dl == 50
    assert "Custom" in tier

    # Record 3 downloads (more than public limit of 2)
    for _ in range(3):
        mgr.record_download(user_id, size_bytes=10 * 1024 * 1024)

    can_dl, _ = mgr.check_can_download(user_id)
    assert can_dl is True

    # Reset custom limits back to tier defaults
    mgr.set_user_limits(user_id, reset=True)
    mb, dl, tier = mgr.get_user_limits(user_id)
    assert mb == 50
    assert dl == 2
    assert "Public" in tier

def test_resolve_user_identifier(temp_access_manager):
    mgr = temp_access_manager
    mgr.record_user(7001, "frank_underwood", "Frank")

    uid, name = mgr.resolve_user_identifier("@frank_underwood")
    assert uid == 7001
    assert name == "@frank_underwood"

    uid, name = mgr.resolve_user_identifier("7001")
    assert uid == 7001

    uid, name = mgr.resolve_user_identifier("@nonexistent")
    assert uid is None
    assert name == "@nonexistent"

def test_reset_user_usage_and_stats(temp_access_manager):
    mgr = temp_access_manager
    user_id = 8001
    mgr.record_user(user_id, "grace", "Grace")
    mgr.record_download(user_id, size_bytes=20 * 1024 * 1024)

    dls, bytes_used = mgr.get_user_usage_today(user_id)
    assert dls == 1
    assert bytes_used == 20 * 1024 * 1024

    stats = mgr.get_system_stats_today()
    assert stats["active_users"] == 1
    assert stats["total_downloads"] == 1

    mgr.reset_user_usage(user_id)
    dls, bytes_used = mgr.get_user_usage_today(user_id)
    assert dls == 0
    assert bytes_used == 0

def test_group_access_control(temp_access_manager):
    mgr = temp_access_manager
    group_id = -1001234567890
    user_id = 9001

    # Initially group is not allowed
    assert mgr.is_group_allowed(group_id) is False
    assert mgr.is_public_access_enabled() is False

    # User in group cannot download initially
    can_dl, reason = mgr.check_can_download(user_id, chat_id=group_id)
    assert can_dl is False
    assert "Access restricted" in reason

    # Allow group
    mgr.allow_group(group_id, title="Cool Video Chat", allowed=True)
    assert mgr.is_group_allowed(group_id) is True

    # User in group can now download under group allowlist tier
    can_dl, reason = mgr.check_can_download(user_id, chat_id=group_id)
    assert can_dl is True
    assert reason is None

    mb, dl, tier = mgr.get_user_limits(user_id, chat_id=group_id)
    assert "Group Allowlist Tier" in tier

    # Groups list returns the group
    groups = mgr.get_all_allowed_groups()
    assert len(groups) == 1
    assert groups[0]["group_id"] == group_id
    assert groups[0]["title"] == "Cool Video Chat"

    # Revoke group
    mgr.allow_group(group_id, allowed=False)
    assert mgr.is_group_allowed(group_id) is False
    can_dl, _ = mgr.check_can_download(user_id, chat_id=group_id)
    assert can_dl is False



# ---------------------------------------------------------------------------
# Download history & stats
# ---------------------------------------------------------------------------

def test_record_download_event_domain_only_by_default(temp_access_manager, monkeypatch):
    from src.core import access as access_mod
    monkeypatch.setattr(access_mod.config, "STORE_FULL_URLS", False)
    mgr = temp_access_manager
    mgr.record_user(1, "alice", "Alice")
    mgr.record_download_event(
        user_id=1, chat_id=1, url="https://www.youtube.com/watch?v=abc", title="Cat video",
        size_bytes=1024, duration_sec=2.5, status="ok",
    )
    rows = mgr.get_recent_downloads(limit=10)
    assert len(rows) == 1
    assert rows[0]["domain"] == "youtube.com"
    assert rows[0]["url"] is None
    assert rows[0]["title"] is None
    assert rows[0]["status"] == "ok"
    assert rows[0]["username"] == "alice"


def test_record_download_event_stores_full_url_when_enabled(temp_access_manager, monkeypatch):
    from src.core import access as access_mod
    monkeypatch.setattr(access_mod.config, "STORE_FULL_URLS", True)
    mgr = temp_access_manager
    mgr.record_download_event(
        user_id=1, chat_id=1, url="https://m.tiktok.com/v/1", title="T",
        size_bytes=1, duration_sec=1.0, status="failed", error="x" * 500,
    )
    row = mgr.get_recent_downloads(limit=1)[0]
    assert row["domain"] == "tiktok.com"
    assert row["url"] == "https://m.tiktok.com/v/1"
    assert row["title"] == "T"
    assert len(row["error"]) == 200


def test_stats_queries(temp_access_manager):
    mgr = temp_access_manager
    mgr.record_user(1, "alice", "Alice")
    mgr.record_user(2, "bob", "Bob")
    for _ in range(3):
        mgr.record_download(1, 100)
        mgr.record_download_event(1, 1, "https://youtube.com/a", None, 100, 1.0, "ok")
    mgr.record_download(2, 50)
    mgr.record_download_event(2, 2, "https://vimeo.com/b", None, 50, 1.0, "ok")
    mgr.record_download_event(2, 2, "https://vimeo.com/c", None, 0, 1.0, "failed", error="boom")

    top_users = mgr.get_top_users(days=7, limit=10)
    assert top_users[0]["user_id"] == 1
    assert top_users[0]["downloads"] == 3
    assert top_users[0]["bytes"] == 300
    assert top_users[0]["username"] == "alice"

    domains = mgr.get_top_domains(days=7, limit=10)
    assert domains[0]["domain"] == "youtube.com"
    assert domains[0]["downloads"] == 3
    assert domains[1]["domain"] == "vimeo.com"
    assert domains[1]["downloads"] == 2
    assert domains[1]["failed"] == 1

    daily = mgr.get_daily_totals(days=7)
    assert len(daily) == 1
    assert daily[0]["downloads"] == 4
    assert daily[0]["bytes"] == 350

    errors = mgr.get_error_stats(days=7, limit=5)
    assert errors == [{"domain": "vimeo.com", "error": "boom", "count": 1}]

    recent_for_bob = mgr.get_recent_downloads(limit=5, user_id=2)
    assert len(recent_for_bob) == 2
    assert recent_for_bob[0]["status"] == "failed"  # newest first

    summary = mgr.get_range_summary(days=7)
    assert summary == {"days": 7, "downloads": 5, "ok": 4, "failed": 1, "bytes": 350, "active_users": 2}


def test_get_all_users_and_settings(temp_access_manager):
    mgr = temp_access_manager
    mgr.record_user(10, "u1", "U1")
    mgr.record_user(11, None, "U2")
    users = mgr.get_all_users()
    assert {u["user_id"] for u in users} == {10, 11}

    assert mgr.get_setting("pending_restart_notify") is None
    mgr.set_setting("pending_restart_notify", "123")
    assert mgr.get_setting("pending_restart_notify") == "123"
    mgr.delete_setting("pending_restart_notify")
    assert mgr.get_setting("pending_restart_notify") is None
