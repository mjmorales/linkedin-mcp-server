"""
LinkedIn messaging tools.

Provides inbox listing, conversation reading, message search, and sending.
"""

import logging
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from pydantic import Field

from linkedin_mcp_server.constants import TOOL_TIMEOUT_SECONDS
from linkedin_mcp_server.core.exceptions import (
    AuthenticationError,
    LinkedInScraperException,
)
from linkedin_mcp_server.dependencies import get_ready_extractor, handle_auth_error
from linkedin_mcp_server.error_handler import raise_tool_error

logger = logging.getLogger(__name__)


def register_messaging_tools(mcp: FastMCP) -> None:
    """Register all messaging-related tools with the MCP server."""

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Inbox",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"messaging", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_inbox(
        ctx: Context,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        List recent conversations from the LinkedIn messaging inbox.

        Args:
            ctx: FastMCP context for progress reporting
            limit: Maximum number of conversations to load (1-50, default 20)

        Returns:
            Dict with url, sections (inbox -> raw text), and optional references.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="get_inbox"
            )
            logger.info("Fetching inbox (limit=%d)", limit)

            await ctx.report_progress(
                progress=0, total=100, message="Loading messaging inbox"
            )

            result = await extractor.get_inbox(limit=limit)

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_inbox")
        except Exception as e:
            raise_tool_error(e, "get_inbox")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Conversation",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"messaging", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_conversation(
        ctx: Context,
        linkedin_username: str | None = None,
        thread_id: str | None = None,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Read a specific messaging conversation.

        Provide either linkedin_username or thread_id to identify the conversation.

        Args:
            ctx: FastMCP context for progress reporting
            linkedin_username: LinkedIn username of the conversation participant
            thread_id: LinkedIn messaging thread ID

        Returns:
            Dict with url, sections (conversation -> raw text), and optional references.
        """
        if not linkedin_username and not thread_id:
            raise_tool_error(
                LinkedInScraperException(
                    "Provide at least one of linkedin_username or thread_id"
                ),
                "get_conversation",
            )

        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="get_conversation"
            )
            logger.info(
                "Fetching conversation: username=%s, thread_id=%s",
                linkedin_username,
                thread_id,
            )

            await ctx.report_progress(
                progress=0, total=100, message="Loading conversation"
            )

            result = await extractor.get_conversation(
                linkedin_username=linkedin_username,
                thread_id=thread_id,
            )

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_conversation")
        except Exception as e:
            raise_tool_error(e, "get_conversation")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Mark Conversations As Read",
        annotations={"openWorldHint": True},
        tags={"messaging", "actions"},
        exclude_args=["extractor"],
    )
    async def mark_conversations_as_read(
        ctx: Context,
        thread_ids: list[str] | None = None,
        linkedin_usernames: list[str] | None = None,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Mark one or more conversations as read.

        Delegates to get_conversation, which clears the unread marker as a side
        effect of opening the thread. Provide thread_ids, linkedin_usernames, or
        both; each entry is processed independently.

        Args:
            ctx: FastMCP context for progress reporting
            thread_ids: LinkedIn messaging thread IDs to mark read
            linkedin_usernames: LinkedIn usernames whose conversations to mark read

        Returns:
            Dict with results (list of {identifier, kind, status, error?}) and
            counts of successes/failures.
        """
        targets: list[tuple[str, str]] = []
        for tid in thread_ids or []:
            targets.append(("thread_id", tid))
        for uname in linkedin_usernames or []:
            targets.append(("linkedin_username", uname))

        if not targets:
            raise_tool_error(
                LinkedInScraperException(
                    "Provide at least one thread_id or linkedin_username"
                ),
                "mark_conversations_as_read",
            )

        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="mark_conversations_as_read"
            )
            logger.info("Marking %d conversation(s) as read", len(targets))

            results: list[dict[str, Any]] = []
            succeeded = 0
            failed = 0

            for idx, (kind, identifier) in enumerate(targets):
                await ctx.report_progress(
                    progress=idx,
                    total=len(targets),
                    message=f"Opening {kind}={identifier}",
                )
                try:
                    kwargs = {kind: identifier}
                    await extractor.get_conversation(**kwargs)
                    results.append(
                        {"identifier": identifier, "kind": kind, "status": "ok"}
                    )
                    succeeded += 1
                except Exception as e:
                    logger.warning(
                        "Failed to mark %s=%s as read: %s", kind, identifier, e
                    )
                    results.append(
                        {
                            "identifier": identifier,
                            "kind": kind,
                            "status": "error",
                            "error": str(e),
                        }
                    )
                    failed += 1

            await ctx.report_progress(
                progress=len(targets), total=len(targets), message="Complete"
            )

            return {
                "results": results,
                "succeeded": succeeded,
                "failed": failed,
            }

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "mark_conversations_as_read")
        except Exception as e:
            raise_tool_error(e, "mark_conversations_as_read")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Search Conversations",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"messaging", "search"},
        exclude_args=["extractor"],
    )
    async def search_conversations(
        keywords: str,
        ctx: Context,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Search messages by keyword.

        Args:
            keywords: Search keywords to filter conversations
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with url, sections (search_results -> raw text), and optional references.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="search_conversations"
            )
            logger.info("Searching conversations: keywords='%s'", keywords)

            await ctx.report_progress(
                progress=0, total=100, message="Searching messages"
            )

            result = await extractor.search_conversations(keywords)

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "search_conversations")
        except Exception as e:
            raise_tool_error(e, "search_conversations")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Pending Invitations",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"network", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_pending_invitations(
        ctx: Context,
        invite_type: Literal["received", "sent"] = "received",
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        List pending LinkedIn connection invitations.

        Args:
            ctx: FastMCP context for progress reporting
            invite_type: "received" for incoming invites, "sent" for outgoing (default "received")
            limit: Maximum number of invitations to load (1-100, default 20)

        Returns:
            Dict with url, sections ({invite_type}_invitations -> raw text), and optional references.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="get_pending_invitations"
            )
            logger.info(
                "Fetching pending invitations (type=%s, limit=%d)", invite_type, limit
            )

            await ctx.report_progress(
                progress=0, total=100, message="Loading invitation manager"
            )

            result = await extractor.get_pending_invitations(
                invite_type=invite_type, limit=limit
            )

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_pending_invitations")
        except Exception as e:
            raise_tool_error(e, "get_pending_invitations")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Ignore Invitation",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"network", "actions"},
        exclude_args=["extractor"],
    )
    async def ignore_invitation(
        linkedin_username: str,
        ctx: Context,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Dismiss a pending received LinkedIn connection invitation.

        Locates the invite card for `linkedin_username` in the invitation
        manager and clicks its Ignore button. No-op with a structured
        status if the invite isn't present.

        Args:
            linkedin_username: LinkedIn username of the inviter (e.g. "margaretemons")
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with url, linkedin_username, status, and message.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="ignore_invitation"
            )
            logger.info("Ignoring invitation from %s", linkedin_username)

            await ctx.report_progress(
                progress=0, total=100, message="Opening invitation manager"
            )

            result = await extractor.ignore_invitation(linkedin_username)

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "ignore_invitation")
        except Exception as e:
            raise_tool_error(e, "ignore_invitation")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Send Message",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"messaging", "actions"},
        exclude_args=["extractor"],
    )
    async def send_message(
        linkedin_username: str,
        message: str,
        confirm_send: bool,
        ctx: Context,
        profile_urn: str | None = None,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Send a message to a LinkedIn user.

        The recipient must be directly messageable from the profile page. This is a
        write operation when confirm_send is True.

        Args:
            linkedin_username: LinkedIn username of the recipient
            message: The message text to send
            confirm_send: Must be True to send the message
            ctx: FastMCP context for progress reporting
            profile_urn: Optional profile URN (e.g. ACoAAB...) to construct the
                compose URL directly. Providing this bypasses the Message-button
                lookup and is more reliable when available. Obtain via
                get_person_profile. Note: inbox may not always show all
                messages; use search_conversations as a fallback.

        Returns:
            Dict with url, status, message, recipient_selected, and sent.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="send_message"
            )
            logger.info(
                "Sending message to %s (confirm_send=%s)",
                linkedin_username,
                confirm_send,
            )

            await ctx.report_progress(progress=0, total=100, message="Sending message")

            result = await extractor.send_message(
                linkedin_username,
                message,
                confirm_send=confirm_send,
                profile_urn=profile_urn,
            )

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "send_message")
        except Exception as e:
            raise_tool_error(e, "send_message")  # NoReturn
