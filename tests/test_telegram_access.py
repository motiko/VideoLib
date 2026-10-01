import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.platforms.telegram import TelegramPlatform
from src.core.access import AccessManager

@pytest.fixture
def mock_access_manager(tmp_path):
    db_file = tmp_path / "tg_test_access.db"
    mgr = AccessManager(db_path=db_file)
    return mgr

@pytest.fixture
def telegram_platform(mock_access_manager):
    with patch("src.platforms.telegram.access_manager", mock_access_manager):
        platform = TelegramPlatform(token="1234:MOCK_TOKEN")
        platform.application = MagicMock()
        platform.application.bot = MagicMock()
        platform.application.bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))
        platform.send_message = AsyncMock(return_value="999")
        yield platform, mock_access_manager

def make_update(user_id=12345, username="testuser", text="/mylimits", reply_to_user=None):
    update = MagicMock()
    update.effective_chat.id = user_id
    update.effective_chat.type = "private"
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.effective_user.first_name = "Test"
    update.message.text = text
    update.message.message_id = 101

    if reply_to_user:
        reply_msg = MagicMock()
        reply_msg.from_user.id = reply_to_user.get("id")
        reply_msg.from_user.username = reply_to_user.get("username")
        reply_msg.from_user.first_name = reply_to_user.get("first_name", "Target")
        update.message.reply_to_message = reply_msg
    else:
        update.message.reply_to_message = None

    context = MagicMock()
    context.args = text.split()[1:]
    return update, context

@pytest.mark.asyncio
async def test_id_command(telegram_platform):
    platform, mgr = telegram_platform
    update, context = make_update(user_id=8888, username="myuser", text="/id")

    await platform._handle_id(update, context)

    platform.send_message.assert_called_once()
    msg = platform.send_message.call_args[0][1]
    assert "8888" in msg
    assert "@myuser" in msg

@pytest.mark.asyncio
async def test_mylimits_command(telegram_platform):
    platform, mgr = telegram_platform
    update, context = make_update(user_id=5555, username="joe", text="/mylimits")

    await platform._handle_mylimits(update, context)

    platform.send_message.assert_called_once()
    msg_sent = platform.send_message.call_args[0][1]
    assert "5555" in msg_sent
    assert "Your Limits & Usage" in msg_sent
    assert "Downloads:" in msg_sent

@pytest.mark.asyncio
async def test_admin_commands_require_admin(telegram_platform):
    platform, mgr = telegram_platform
    # Non-admin user
    update, context = make_update(user_id=7777, username="not_admin", text="/allow @someone")

    await platform._handle_allow(update, context)
    platform.send_message.assert_called_with("7777", "⛔ This command is restricted to bot administrators.")

@pytest.mark.asyncio
async def test_admin_allow_and_disallow(telegram_platform):
    platform, mgr = telegram_platform
    admin_id = 9999
    with patch("src.config.config.ADMIN_USERS", {admin_id}):
        # Pre-record target user
        mgr.record_user(1111, "target_user", "Target")

        # Allow user
        update, context = make_update(user_id=admin_id, username="admin", text="/allow @target_user")
        await platform._handle_allow(update, context)

        assert mgr.get_user_info(1111)["is_allowed"] == 1
        msg = platform.send_message.call_args[0][1]
        assert "authorized to download" in msg

        # Disallow user
        update2, context2 = make_update(user_id=admin_id, username="admin", text="/disallow @target_user")
        await platform._handle_disallow(update2, context2)

        assert mgr.get_user_info(1111)["is_allowed"] == 0
        msg2 = platform.send_message.call_args[0][1]
        assert "Revoked authorization" in msg2

@pytest.mark.asyncio
async def test_admin_adjustlimits_and_showusage(telegram_platform):
    platform, mgr = telegram_platform
    admin_id = 9999
    with patch("src.config.config.ADMIN_USERS", {admin_id}):
        mgr.record_user(2222, "bob", "Bob")

        # Adjust limits
        update, context = make_update(user_id=admin_id, text="/adjustlimits @bob mb=1500 dl=30")
        await platform._handle_adjustlimits(update, context)

        mb, dl, tier = mgr.get_user_limits(2222)
        assert mb == 1500
        assert dl == 30
        assert "Custom" in tier

        # Show usage for bob
        update2, context2 = make_update(user_id=admin_id, text="/showusage @bob")
        await platform._handle_showusage(update2, context2)

        msg = platform.send_message.call_args[0][1]
        assert "Usage Details for @bob" in msg
        assert "1500" in msg

        # Show overall usage
        update3, context3 = make_update(user_id=admin_id, text="/showusage")
        await platform._handle_showusage(update3, context3)
        msg_all = platform.send_message.call_args[0][1]
        assert "Bot Traffic Summary" in msg_all

@pytest.mark.asyncio
async def test_admin_publicaccess_and_setlimit(telegram_platform):
    platform, mgr = telegram_platform
    admin_id = 9999
    with patch("src.config.config.ADMIN_USERS", {admin_id}):
        # Set allowlist tier limit
        update, context = make_update(user_id=admin_id, text="/setlimit allowlist mb=1800 dl=35")
        await platform._handle_setlimit(update, context)
        al_mb, al_dl = mgr.get_tier_limits("allowlist")
        assert al_mb == 1800
        assert al_dl == 35

        # Set public tier limit
        update2, context2 = make_update(user_id=admin_id, text="/setlimit public mb=150 dl=2")
        await platform._handle_setlimit(update2, context2)
        pub_mb, pub_dl = mgr.get_tier_limits("public")
        assert pub_mb == 150
        assert pub_dl == 2

        # Toggle public access
        update3, context3 = make_update(user_id=admin_id, text="/publicaccess on")
        await platform._handle_publicaccess(update3, context3)
        assert mgr.is_public_access_enabled() is True

        update4, context4 = make_update(user_id=admin_id, text="/publicaccess off")
        await platform._handle_publicaccess(update4, context4)
        assert mgr.is_public_access_enabled() is False

@pytest.mark.asyncio
async def test_handle_message_blocks_unauthorized_by_default(telegram_platform):
    platform, mgr = telegram_platform
    # Default: public access is disabled
    assert mgr.is_public_access_enabled() is False

    callback_mock = AsyncMock()
    platform.register_callback(callback_mock)

    update, context = make_update(user_id=3333, username="random_user", text="https://youtube.com/watch?v=abc")
    await platform._handle_message(update, context)

    # Message callback should NOT have been invoked
    callback_mock.assert_not_called()
    msg = platform.send_message.call_args[0][1]
    assert "Access restricted" in msg

    # Now open public access
    mgr.set_public_access_enabled(True)
    await platform._handle_message(update, context)
    # Callback should now be invoked
    assert callback_mock.call_count == 1

@pytest.mark.asyncio
async def test_admin_allowgroup_in_group_chat(telegram_platform):
    platform, mgr = telegram_platform
    admin_id = 9999
    group_id = -1001122334455

    with patch("src.config.config.ADMIN_USERS", {admin_id}):
        update, context = make_update(user_id=admin_id, text="/allowgroup")
        update.effective_chat.id = group_id
        update.effective_chat.type = "supergroup"
        update.effective_chat.title = "Movie Night Chat"

        await platform._handle_allowgroup(update, context)

        assert mgr.is_group_allowed(group_id) is True
        msg = platform.send_message.call_args[0][1]
        assert "Movie Night Chat" in msg
        assert "now authorized" in msg

        # Check /allowedgroups
        await platform._handle_allowedgroups(update, context)
        msg_list = platform.send_message.call_args[0][1]
        assert "Authorized Groups" in msg_list
        assert "Movie Night Chat" in msg_list

        # Disallow
        await platform._handle_disallowgroup(update, context)
        assert mgr.is_group_allowed(group_id) is False
        msg_dis = platform.send_message.call_args[0][1]
        assert "Revoked authorization" in msg_dis

@pytest.mark.asyncio
async def test_admin_allowgroup_with_explicit_id(telegram_platform):
    platform, mgr = telegram_platform
    admin_id = 9999
    group_id = -1007788990011

    with patch("src.config.config.ADMIN_USERS", {admin_id}):
        # In DM, admin runs: /allowgroup -1007788990011 Custom Title
        update, context = make_update(user_id=admin_id, text=f"/allowgroup {group_id} Custom Title")
        await platform._handle_allowgroup(update, context)

        assert mgr.is_group_allowed(group_id) is True
        msg = platform.send_message.call_args[0][1]
        assert "Custom Title" in msg
        assert str(group_id) in msg

        # Disallow by ID in DM
        update_dis, context_dis = make_update(user_id=admin_id, text=f"/disallowgroup {group_id}")
        await platform._handle_disallowgroup(update_dis, context_dis)
        assert mgr.is_group_allowed(group_id) is False

@pytest.mark.asyncio
async def test_group_member_download_permission(telegram_platform):
    platform, mgr = telegram_platform
    group_id = -1005544332211
    member_id = 4444
    callback_mock = AsyncMock()
    platform.register_callback(callback_mock)

    # 1. Initially, group is NOT allowlisted, user is NOT allowlisted -> blocked
    update, context = make_update(
        user_id=member_id,
        username="regular_member",
        text="@mybot https://youtube.com/watch?v=xyz",
    )
    update.effective_chat.id = group_id
    update.effective_chat.type = "supergroup"
    context.bot.username = "mybot"

    await platform._handle_message(update, context)
    callback_mock.assert_not_called()
    msg = platform.send_message.call_args[0][1]
    assert "Access restricted" in msg

    # 2. Authorize group
    mgr.allow_group(group_id, title="Test Supergroup", allowed=True)

    # 3. Message should now be accepted and callback called
    await platform._handle_message(update, context)
    assert callback_mock.call_count == 1

