"""
Integration tests for Discord gateway security controls.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from twitter_intel.application.use_cases.approve_tweet import ApprovalResult
from twitter_intel.config import Config
from twitter_intel.infrastructure.database import SqliteTweetRepository
from twitter_intel.infrastructure.notifications.discord_gateway import DiscordGateway


def _build_interaction(
    custom_id: str,
    user_id: str = "111",
    channel_id: str = "222",
    message_id: str = "333",
):
    interaction = MagicMock()
    interaction.data = {"custom_id": custom_id}
    interaction.user = MagicMock()
    interaction.user.id = int(user_id)
    interaction.user.roles = []
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
def repository(tmp_path: Path) -> SqliteTweetRepository:
    repo = SqliteTweetRepository(str(tmp_path / "gateway-security.db"))
    yield repo
    repo.close()


@pytest.fixture
def gateway(repository: SqliteTweetRepository) -> DiscordGateway:
    config = Config(
        discord_bot_token="test-token",
        discord_command_auth_mode="enforce",
        discord_allowed_user_ids=["111"],
        discord_allowed_role_ids=[],
        discord_allowed_channel_ids=["222"],
        discord_require_pending_channel_match=True,
    )

    approve = MagicMock()
    approve.execute = AsyncMock(
        return_value=ApprovalResult(success=True, message="ok", reply_text="reply")
    )
    approve.execute_custom_reply = AsyncMock()

    reject = MagicMock()
    reject.execute = AsyncMock()

    smoke = MagicMock()
    smoke.execute = AsyncMock()

    ingest = MagicMock()
    ingest.execute = AsyncMock()

    return DiscordGateway(
        config=config,
        repository=repository,
        approve_use_case=approve,
        reject_use_case=reject,
        smoke_use_case=smoke,
        ingest_use_case=ingest,
    )


def _seed_pending(repository: SqliteTweetRepository) -> None:
    repository.mark_processed(
        tweet_id="123456",
        url="https://x.com/test/status/123456",
        text="hello",
        author="test",
        category="brand-mentions",
        sentiment="neutral",
        search_query="test",
    )
    repository.save_pending_approval(
        tweet_id="123456",
        reply_options=["reply one", "reply two"],
        discord_message_id="333",
        discord_channel_id="222",
        category="brand-mentions",
    )


class TestPendingContextBinding:
    async def test_rejects_cross_channel_approval(self, gateway: DiscordGateway, repository: SqliteTweetRepository):
        _seed_pending(repository)
        interaction = _build_interaction("approve:123456:0", channel_id="222", message_id="999")

        await gateway._handle_component(interaction)

        gateway._approve_use_case.execute.assert_not_called()
        interaction.response.send_message.assert_called_once()
        assert "not valid from this message" in interaction.response.send_message.call_args[0][0]

    async def test_allows_matching_pending_context(self, gateway: DiscordGateway, repository: SqliteTweetRepository):
        _seed_pending(repository)
        interaction = _build_interaction("approve:123456:0", channel_id="222", message_id="333")

        await gateway._handle_component(interaction)

        gateway._approve_use_case.execute.assert_called_once_with("123456", 0)
