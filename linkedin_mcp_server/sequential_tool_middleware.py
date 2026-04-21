"""Middleware that serializes MCP tool execution within one server process."""

from __future__ import annotations

import asyncio
import logging
import time

import mcp.types as mt

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult

from linkedin_mcp_server.error_handler import (
    _looks_like_browser_dead,
    _mark_browser_dead_safely,
)

logger = logging.getLogger(__name__)


DEFAULT_LOCK_WAIT_SECONDS = 90.0


class SequentialToolExecutionMiddleware(Middleware):
    """Ensure only one MCP tool call executes at a time per server process.

    Also short-circuits when the lock would be held beyond
    ``lock_wait_seconds`` so a stuck tool doesn't starve the queue, and
    converts transport-dead browser exceptions into structured ToolErrors
    (flagging the browser for rebuild) instead of letting them crash the
    stdio pipe.
    """

    def __init__(self, lock_wait_seconds: float = DEFAULT_LOCK_WAIT_SECONDS) -> None:
        self._lock = asyncio.Lock()
        self._lock_wait_seconds = lock_wait_seconds

    async def _report_progress(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        *,
        message: str,
    ) -> None:
        fastmcp_context = context.fastmcp_context
        if fastmcp_context is None or fastmcp_context.request_context is None:
            return

        await fastmcp_context.report_progress(
            progress=0,
            total=100,
            message=message,
        )

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        tool_name = context.message.name
        wait_started = time.perf_counter()
        logger.debug("Waiting for scraper lock for tool '%s'", tool_name)
        await self._report_progress(
            context,
            message="Queued waiting for scraper lock",
        )

        try:
            await asyncio.wait_for(
                self._lock.acquire(), timeout=self._lock_wait_seconds
            )
        except asyncio.TimeoutError:
            wait_seconds = time.perf_counter() - wait_started
            logger.warning(
                "Scraper lock wait timed out for tool '%s' after %.1fs",
                tool_name,
                wait_seconds,
            )
            raise ToolError(
                f"Server busy: another tool has held the scraper lock for "
                f">{self._lock_wait_seconds:.0f}s. Retry shortly."
            ) from None

        try:
            wait_seconds = time.perf_counter() - wait_started
            logger.debug(
                "Acquired scraper lock for tool '%s' after %.3fs",
                tool_name,
                wait_seconds,
            )
            await self._report_progress(
                context,
                message="Scraper lock acquired, starting tool",
            )
            hold_started = time.perf_counter()
            try:
                return await call_next(context)
            except ToolError:
                raise
            except Exception as exc:
                if _looks_like_browser_dead(exc):
                    logger.warning(
                        "Browser transport error in tool '%s' (%s): %s",
                        tool_name,
                        type(exc).__name__,
                        exc,
                    )
                    _mark_browser_dead_safely()
                    raise ToolError(
                        "Browser session was lost and will be rebuilt on the "
                        "next call. Retry this tool."
                    ) from exc
                raise
            finally:
                hold_seconds = time.perf_counter() - hold_started
                logger.debug(
                    "Released scraper lock for tool '%s' after %.3fs",
                    tool_name,
                    hold_seconds,
                )
        finally:
            self._lock.release()
