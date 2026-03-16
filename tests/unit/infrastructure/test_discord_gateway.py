"""
Tests for Discord Gateway.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from twitter_intel.application.use_cases.approve_tweet import ApprovalResult
from twitter_intel.application.use_cases.reject_tweet import RejectionResult
from twitter_intel.domain.entities.category import TweetCategory
from twitter_intel.infrastructure.notifications.discord_gateway import DiscordGateway


def _build_message(content: str, user_id: str = "111", channel_id: str = "222", role_ids: list[str] | None = None):
    message = MagicMock()
    message.content = content
    message.reply = AsyncMock()
    message.author = MagicMock()
    message.author.bot = False
    message.author.id = int(user_id)
    message.author.roles = [MagicMock(id=int(role_id)) for role_id in (role_ids or [])]
    message.channel = MagicMock()
    message.channel.id = int(channel_id)
    message.channel_id = int(channel_id)
    return message


def _build_interaction(
    custom_id: str,
    user_id: str = "111",
    channel_id: str = "222",
    message_id: str = "333",
    role_ids: list[str] | None = None,
):
    interaction = MagicMock()
    interaction.data = {"custom_id": custom_id}
    interaction.user = MagicMock()
    interaction.user.id = int(user_id)
    interaction.user.roles = [MagicMock(id=int(role_id)) for role_id in (role_ids or [])]
    interaction.channel_id = int(channel_id)
    interaction.message = MagicMock()
    interaction.message.id = int(message_id)
    interaction.message.embeds = []
    interaction.message.edit = AsyncMock()
    interaction.response = MagicMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.discord_bot_token = "test_token"
    config.discord_command_auth_mode = "enforce"
    config.discord_allowed_user_ids = ["111"]
    config.discord_allowed_role_ids = []
    config.discord_allowed_channel_ids = ["222"]
    config.discord_require_pending_channel_match = True
    return config


@pytest.fixture
def mock_repository():
    repo = MagicMock()
    repo.get_pending = MagicMock(
        return_value=(
            ["Reply 1", "Reply 2"],
            "333",
            "222",
            "brand-mentions",
        )
    )
    repo.get_stats = MagicMock(
        return_value={
            "total_processed": 100,
            "replied": 40,
            "rejected": 30,
            "pending": 30,
            "by_category": {"brand-mentions": 100},
        }
    )
    return repo


@pytest.fixture
def mock_runtime():
    runtime = MagicMock()
    runtime.auth_denied_commands = 0
    runtime.auth_denied_interactions = 0
    runtime.custom_reply_missing_pending = 0
    runtime.pending_channel_mismatch_denied = 0
    return runtime


@pytest.fixture
def gateway(mock_config, mock_repository, mock_runtime):
    mock_approve = MagicMock()
    mock_approve.execute = AsyncMock(
        return_value=ApprovalResult(success=True, message="Success", reply_text="Test reply")
    )
    mock_approve.execute_custom_reply = AsyncMock(
        return_value=ApprovalResult(success=True, message="Success", reply_text="Custom reply")
    )

    mock_reject = MagicMock()
    mock_reject.execute = AsyncMock(return_value=RejectionResult(success=True, message="Skipped"))

    mock_smoke = MagicMock()
    mock_smoke.execute = AsyncMock(return_value=(True, "Smoke test queued"))

    mock_ingest = MagicMock()
    mock_ingest.execute = AsyncMock(return_value=(True, "Manual ingest queued"))

    return DiscordGateway(
        config=mock_config,
        repository=mock_repository,
        approve_use_case=mock_approve,
        reject_use_case=mock_reject,
        smoke_use_case=mock_smoke,
        ingest_use_case=mock_ingest,
        runtime=mock_runtime,
    )


class TestDiscordGatewayAuthorization:
    async def test_unauthorized_command_blocked_in_enforce(self, gateway):
        message = _build_message("!stats", user_id="999")

        await gateway._handle_message(message)

        gateway._repository.get_stats.assert_not_called()
        message.reply.assert_called_once()
        assert "not authorized" in message.reply.call_args[0][0]

    async def test_unauthorized_command_allowed_in_audit(self, gateway):
        gateway._config.discord_command_auth_mode = "audit"
        message = _build_message("!stats", user_id="999")

        await gateway._handle_message(message)

        gateway._repository.get_stats.assert_called_once()
        message.reply.assert_called_once()
        assert "Detailed Stats" in message.reply.call_args[0][0]

    async def test_unauthorized_interaction_blocked_in_enforce(self, gateway):
        interaction = _build_interaction("approve:123456:0", user_id="999")

        await gateway._handle_component(interaction)

        gateway._approve_use_case.execute.assert_not_called()
        interaction.response.send_message.assert_called_once()
        assert "not authorized" in interaction.response.send_message.call_args[0][0]


class TestDiscordGatewayPendingBinding:
    async def test_approve_button_requires_pending_context_match(self, gateway):
        interaction = _build_interaction("approve:123456:0", channel_id="222", message_id="999")

        await gateway._handle_component(interaction)

        gateway._approve_use_case.execute.assert_not_called()
        interaction.response.send_message.assert_called_once()
        assert "not valid from this message" in interaction.response.send_message.call_args[0][0]

    async def test_approve_button_rejects_malformed_reply_index(self, gateway):
        interaction = _build_interaction("approve:123456:not-a-number")

        await gateway._handle_component(interaction)

        gateway._approve_use_case.execute.assert_not_called()
        interaction.response.send_message.assert_called_once()
        assert "Invalid reply option selected" in interaction.response.send_message.call_args[0][0]

    async def test_reject_button_authorized(self, gateway):
        interaction = _build_interaction("reject:123456")

        await gateway._handle_component(interaction)

        gateway._reject_use_case.execute.assert_called_once_with("123456")
        interaction.message.edit.assert_called_once()

    async def test_custom_reply_rejects_non_pending_tweet(self, gateway):
        gateway._repository.get_pending = MagicMock(return_value=(None, None, None, None))
        message = _build_message("!reply 123456 This is my reply")

        await gateway._handle_custom_reply(message)

        gateway._approve_use_case.execute_custom_reply.assert_not_called()
        message.reply.assert_called_once()
        assert "No pending approval" in message.reply.call_args[0][0]


class TestDiscordGatewayCommands:
    async def test_custom_reply_command_authorized(self, gateway):
        message = _build_message("!reply 123456 This is my reply")

        await gateway._handle_message(message)

        gateway._approve_use_case.execute_custom_reply.assert_called_once_with(
            "123456", "This is my reply"
        )
        message.reply.assert_called_once_with("Custom reply posted!")

    async def test_smoke_command_authorized(self, gateway):
        message = _build_message("!smoke brand")

        await gateway._handle_message(message)

        gateway._smoke_use_case.execute.assert_called_once_with(TweetCategory.BRAND_MENTION)
        message.reply.assert_called_once()
