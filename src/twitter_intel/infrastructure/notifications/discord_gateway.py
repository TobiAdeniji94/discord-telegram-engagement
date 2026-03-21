"""
Discord Gateway for receiving interactions.

Connects to Discord to receive button clicks and commands,
routing them to the appropriate use cases.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from twitter_intel.application.use_cases import (
        ApproveTweetUseCase,
        ManualIngestUseCase,
        RejectTweetUseCase,
        SmokeTestUseCase,
    )
    from twitter_intel.config import Config, SearchRuntime
    from twitter_intel.domain.interfaces import TweetRepository

from twitter_intel.domain.entities.category import parse_smoke_category

log = logging.getLogger(__name__)


class DiscordGateway:
    """
    Discord Gateway for handling interactions.

    Connects to Discord Gateway (via discord.py) to receive real-time
    button clicks and message commands. Routes interactions to
    appropriate use cases for processing.
    """

    def __init__(
        self,
        config: "Config",
        repository: "TweetRepository",
        approve_use_case: "ApproveTweetUseCase",
        reject_use_case: "RejectTweetUseCase",
        smoke_use_case: "SmokeTestUseCase",
        ingest_use_case: "ManualIngestUseCase",
        runtime: "SearchRuntime | None" = None,
    ):
        """
        Initialize the Discord Gateway.

        Args:
            config: Application configuration
            repository: Tweet repository for stats
            approve_use_case: Use case for handling approvals
            reject_use_case: Use case for handling rejections
            smoke_use_case: Use case for smoke tests
            ingest_use_case: Use case for manual ingestion
            runtime: Optional runtime telemetry store
        """
        self._config = config
        self._repository = repository
        self._approve_use_case = approve_use_case
        self._reject_use_case = reject_use_case
        self._smoke_use_case = smoke_use_case
        self._ingest_use_case = ingest_use_case
        self._runtime = runtime
        self._running = False

    @property
    def _auth_mode(self) -> str:
        raw_mode = str(getattr(self._config, "discord_command_auth_mode", "enforce")).strip().lower()
        return raw_mode if raw_mode in {"audit", "enforce"} else "enforce"

    def _bump_runtime_counter(self, attr_name: str) -> None:
        if not self._runtime or not hasattr(self._runtime, attr_name):
            return
        current = getattr(self._runtime, attr_name, 0)
        setattr(self._runtime, attr_name, int(current) + 1)

    @staticmethod
    def _safe_id(raw_value: Any) -> str:
        if raw_value is None:
            return ""
        return str(raw_value).strip()

    @staticmethod
    def _extract_role_ids(actor: Any) -> set[str]:
        roles = getattr(actor, "roles", None)
        if not isinstance(roles, list):
            return set()

        role_ids: set[str] = set()
        for role in roles:
            role_id = getattr(role, "id", role)
            text = str(role_id).strip()
            if text:
                role_ids.add(text)
        return role_ids

    @staticmethod
    def _coerce_id_values(raw_values: Any) -> set[str]:
        if not isinstance(raw_values, (list, tuple, set)):
            return set()
        return {str(value).strip() for value in raw_values if str(value).strip()}

    def _is_authorized(self, user_id: str, role_ids: set[str], channel_id: str) -> bool:
        allowed_users = self._coerce_id_values(
            getattr(self._config, "discord_allowed_user_ids", [])
        )
        allowed_roles = self._coerce_id_values(
            getattr(self._config, "discord_allowed_role_ids", [])
        )
        allowed_channels = self._coerce_id_values(
            getattr(self._config, "discord_allowed_channel_ids", [])
        )

        actor_allowed = user_id in allowed_users or bool(role_ids & allowed_roles)
        channel_allowed = channel_id in allowed_channels
        return actor_allowed and channel_allowed

    async def _respond_interaction(self, interaction: Any, text: str) -> None:
        response = getattr(interaction, "response", None)
        is_done = False
        if response is not None:
            is_done_fn = getattr(response, "is_done", None)
            if callable(is_done_fn):
                try:
                    is_done = bool(is_done_fn())
                except Exception:
                    is_done = False

        if not is_done and response is not None and hasattr(response, "send_message"):
            await response.send_message(text, ephemeral=True)
            return

        followup = getattr(interaction, "followup", None)
        if followup is not None and hasattr(followup, "send"):
            await followup.send(text, ephemeral=True)

    async def _authorize_message(self, message: Any) -> bool:
        user_id = self._safe_id(getattr(getattr(message, "author", None), "id", None))
        role_ids = self._extract_role_ids(getattr(message, "author", None))
        channel_id = self._safe_id(
            getattr(message, "channel_id", None) or getattr(getattr(message, "channel", None), "id", None)
        )
        authorized = self._is_authorized(user_id, role_ids, channel_id)
        if authorized:
            return True

        self._bump_runtime_counter("auth_denied_commands")
        log.warning(
            "Discord command auth denied: mode=%s user=%s channel=%s roles=%s",
            self._auth_mode,
            user_id or "unknown",
            channel_id or "unknown",
            ",".join(sorted(role_ids)) if role_ids else "none",
        )
        if self._auth_mode == "audit":
            return True

        await message.reply("You are not authorized to run this command in this channel.")
        return False

    async def _authorize_interaction(self, interaction: Any) -> bool:
        user_id = self._safe_id(getattr(getattr(interaction, "user", None), "id", None))
        role_ids = self._extract_role_ids(getattr(interaction, "user", None))
        channel_id = self._safe_id(
            getattr(interaction, "channel_id", None)
            or getattr(getattr(interaction, "channel", None), "id", None)
        )
        authorized = self._is_authorized(user_id, role_ids, channel_id)
        if authorized:
            return True

        self._bump_runtime_counter("auth_denied_interactions")
        log.warning(
            "Discord interaction auth denied: mode=%s user=%s channel=%s roles=%s",
            self._auth_mode,
            user_id or "unknown",
            channel_id or "unknown",
            ",".join(sorted(role_ids)) if role_ids else "none",
        )
        if self._auth_mode == "audit":
            return True

        await self._respond_interaction(
            interaction,
            "You are not authorized to perform this action in this channel.",
        )
        return False

    def _get_pending_record(
        self, tweet_id: str
    ) -> tuple[list[str], str | None, str | None, str | None] | None:
        pending = self._repository.get_pending(tweet_id)
        if not pending or pending[0] is None:
            return None

        replies = pending[0]
        if not isinstance(replies, list):
            return None

        return pending

    def _is_pending_context_valid(
        self,
        pending_message_id: str | None,
        pending_channel_id: str | None,
        interaction: Any,
    ) -> bool:
        if not getattr(self._config, "discord_require_pending_channel_match", True):
            return True

        interaction_channel_id = self._safe_id(
            getattr(interaction, "channel_id", None)
            or getattr(getattr(interaction, "channel", None), "id", None)
        )
        interaction_message_id = self._safe_id(getattr(getattr(interaction, "message", None), "id", None))

        pending_channel = self._safe_id(pending_channel_id)
        pending_message = self._safe_id(pending_message_id)
        if pending_channel and interaction_channel_id and pending_channel != interaction_channel_id:
            self._bump_runtime_counter("pending_channel_mismatch_denied")
            return False
        if pending_message and interaction_message_id and pending_message != interaction_message_id:
            self._bump_runtime_counter("pending_channel_mismatch_denied")
            return False
        return True

    def _is_pending_channel_valid_for_message(
        self,
        pending_channel_id: str | None,
        message: Any,
    ) -> bool:
        if not getattr(self._config, "discord_require_pending_channel_match", True):
            return True

        message_channel_id = self._safe_id(
            getattr(message, "channel_id", None) or getattr(getattr(message, "channel", None), "id", None)
        )
        pending_channel = self._safe_id(pending_channel_id)
        if pending_channel and message_channel_id and pending_channel != message_channel_id:
            self._bump_runtime_counter("pending_channel_mismatch_denied")
            return False
        return True

    async def run(self) -> None:
        """
        Run the Discord gateway connection.

        Connects to Discord via discord.py and registers event handlers.
        Falls back to HTTP polling if discord.py is not available.
        """
        self._running = True
        try:
            import discord

            intents = discord.Intents.default()
            intents.message_content = True

            client = discord.Client(intents=intents)

            @client.event
            async def on_ready() -> None:
                log.info(f"Discord bot connected as {client.user}")

            @client.event
            async def on_interaction(interaction: discord.Interaction) -> None:
                if interaction.type.value == 3:  # MESSAGE_COMPONENT
                    await self._handle_component(interaction)

            @client.event
            async def on_message(message: discord.Message) -> None:
                if message.author.bot:
                    return
                await self._handle_message(message)

            await client.start(self._config.discord_bot_token)

        except ImportError:
            log.warning("discord.py not installed, falling back to HTTP polling")
            await self._poll_interactions()

    async def stop(self) -> None:
        """Stop the gateway."""
        self._running = False

    async def _handle_component(self, interaction: "discord.Interaction") -> None:
        """
        Handle button press interactions.

        Parses the custom_id to determine action and routes to
        appropriate use case.
        """
        if not await self._authorize_interaction(interaction):
            return

        custom_id = str(getattr(interaction, "data", {}).get("custom_id", "")).strip()
        parts = custom_id.split(":")
        action = parts[0] if parts else ""
        if not action:
            await self._respond_interaction(interaction, "Malformed action payload.")
            return

        if action == "approve":
            if len(parts) != 3:
                await self._respond_interaction(interaction, "Malformed approval payload.")
                return

            tweet_id = parts[1]
            try:
                reply_idx = int(parts[2])
            except ValueError:
                await self._respond_interaction(interaction, "Invalid reply option selected.")
                return

            pending = self._get_pending_record(tweet_id)
            if not pending:
                await self._respond_interaction(interaction, "Tweet is no longer pending approval.")
                return

            replies, pending_msg_id, pending_ch_id, _ = pending
            if not self._is_pending_context_valid(pending_msg_id, pending_ch_id, interaction):
                await self._respond_interaction(interaction, "This action is not valid from this message.")
                return
            if not (0 <= reply_idx < len(replies)):
                await self._respond_interaction(interaction, "Invalid reply option selected.")
                return

            # Acknowledge immediately
            await interaction.response.send_message("Posting reply...", ephemeral=True)
            result = await self._approve_use_case.execute(tweet_id, reply_idx)
            if result.success:
                await interaction.message.edit(
                    content=f"**Reply posted!**\n> {result.reply_text}",
                    embeds=interaction.message.embeds,
                    view=None,
                )
                await interaction.followup.send("Reply posted successfully!", ephemeral=True)
            else:
                await interaction.followup.send(f"Failed: {result.message}", ephemeral=True)
            return

        if action == "reject":
            if len(parts) != 2:
                await self._respond_interaction(interaction, "Malformed rejection payload.")
                return

            tweet_id = parts[1]
            pending = self._get_pending_record(tweet_id)
            if not pending:
                await self._respond_interaction(interaction, "Tweet is no longer pending approval.")
                return

            _, pending_msg_id, pending_ch_id, _ = pending
            if not self._is_pending_context_valid(pending_msg_id, pending_ch_id, interaction):
                await self._respond_interaction(interaction, "This action is not valid from this message.")
                return

            await self._reject_use_case.execute(tweet_id)
            await interaction.response.send_message("Skipped.", ephemeral=True)
            await interaction.message.edit(
                content="**Skipped**",
                embeds=interaction.message.embeds,
                view=None,
            )
            return

        if action == "custom":
            if len(parts) != 2:
                await self._respond_interaction(interaction, "Malformed custom-reply payload.")
                return

            tweet_id = parts[1]
            pending = self._get_pending_record(tweet_id)
            if not pending:
                await self._respond_interaction(interaction, "Tweet is no longer pending approval.")
                return

            _, pending_msg_id, pending_ch_id, _ = pending
            if not self._is_pending_context_valid(pending_msg_id, pending_ch_id, interaction):
                await self._respond_interaction(interaction, "This action is not valid from this message.")
                return

            await interaction.response.send_message(
                f"Send a custom reply with:\n`!reply {tweet_id} Your reply text`",
                ephemeral=True,
            )
            return

        await self._respond_interaction(interaction, "Unknown action.")

    async def _handle_message(self, message: "discord.Message") -> None:
        """
        Handle message commands.

        Routes commands to appropriate handlers.
        """
        content = message.content
        is_command = (
            content.startswith("!reply ")
            or content.startswith("!ingest ")
            or content.startswith("!smoke")
            or content == "!status"
            or content == "!stats"
        )
        if not is_command:
            return

        if not await self._authorize_message(message):
            return

        if content.startswith("!reply "):
            await self._handle_custom_reply(message)
        elif content.startswith("!ingest "):
            await self._handle_manual_ingest(message)
        elif content.startswith("!smoke"):
            await self._handle_smoke_test(message)
        elif content == "!status":
            await self._handle_status(message)
        elif content == "!stats":
            await self._handle_stats(message)

    async def _handle_custom_reply(self, message: "discord.Message") -> None:
        """Handle !reply command."""
        parts = message.content.split(" ", 2)
        if len(parts) < 3:
            await message.reply("Usage: `!reply <tweet_id> <your reply text>`")
            return

        tweet_id = parts[1]
        reply_text = parts[2]

        pending = self._get_pending_record(tweet_id)
        if not pending:
            self._bump_runtime_counter("custom_reply_missing_pending")
            await message.reply("Failed: No pending approval found for this tweet.")
            return

        _, _, pending_ch_id, _ = pending
        if not self._is_pending_channel_valid_for_message(pending_ch_id, message):
            await message.reply("Failed: This custom reply must be sent in the original review channel.")
            return

        result = await self._approve_use_case.execute_custom_reply(tweet_id, reply_text)
        if result.success:
            await message.reply("Custom reply posted!")
        else:
            await message.reply(f"Failed: {result.message}")

    async def _handle_smoke_test(self, message: "discord.Message") -> None:
        """Handle !smoke command."""
        parts = message.content.split(" ", 1)
        category = parse_smoke_category(parts[1] if len(parts) > 1 else None)

        if category is None:
            await message.reply("Usage: `!smoke [brand|competitor|seekers]`")
            return

        success, response_msg = await self._smoke_use_case.execute(category)
        await message.reply(response_msg)

    async def _handle_manual_ingest(self, message: "discord.Message") -> None:
        """Handle !ingest command."""
        parts = message.content.split(" ", 2)
        if len(parts) < 3:
            await message.reply("Usage: `!ingest <brand|competitor|seekers> <tweet text>`")
            return

        category = parse_smoke_category(parts[1])
        if category is None:
            await message.reply("Usage: `!ingest <brand|competitor|seekers> <tweet text>`")
            return

        success, response_msg = await self._ingest_use_case.execute(category, parts[2])
        await message.reply(response_msg)

    async def _handle_status(self, message: "discord.Message") -> None:
        """Handle !status command."""
        stats = self._repository.get_stats()
        cat_lines = "\n".join(f"  {k}: {v}" for k, v in stats["by_category"].items())

        await message.reply(
            f"**Bot Status**\n"
            f"Total: {stats['total_processed']} | Replied: {stats['replied']} | "
            f"Rejected: {stats['rejected']} | Pending: {stats['pending']}\n"
            f"By category:\n{cat_lines}"
        )

    async def _handle_stats(self, message: "discord.Message") -> None:
        """Handle !stats command - detailed stats."""
        stats = self._repository.get_stats()
        cat_lines = "\n".join(f"  {k}: {v}" for k, v in stats["by_category"].items())

        # Calculate percentages
        total = stats["total_processed"] or 1  # Avoid division by zero
        replied_pct = (stats["replied"] / total) * 100
        rejected_pct = (stats["rejected"] / total) * 100
        pending_pct = (stats["pending"] / total) * 100

        await message.reply(
            f"**Detailed Stats**\n"
            f"Total Processed: {stats['total_processed']}\n"
            f"Replied: {stats['replied']} ({replied_pct:.1f}%)\n"
            f"Rejected: {stats['rejected']} ({rejected_pct:.1f}%)\n"
            f"Pending: {stats['pending']} ({pending_pct:.1f}%)\n\n"
            f"By Category:\n{cat_lines}"
        )

    async def _poll_interactions(self) -> None:
        """
        Fallback: poll for interactions if discord.py isn't available.

        This is a placeholder - in practice, HTTP polling for interactions
        is not practical. This just keeps the gateway running.
        """
        log.info("Running in HTTP poll mode (install discord.py for real-time)")
        while self._running:
            await asyncio.sleep(5)
