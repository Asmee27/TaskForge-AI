from __future__ import annotations

import time
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FuturesTimeoutError,
)

from pydantic import ValidationError
from langchain_core.tools import tool

from app.core.config import SETTINGS
from app.core.logging import log_event
from app.models.tool_models import (
    SearchInput,
    ToolError,
    ToolResult,
)


def _error(
    error_type: str,
    message: str,
    retryable: bool,
    started: float,
) -> ToolResult:
    return ToolResult(
        ok=False,
        tool="web_search",
        data=None,
        error=ToolError(
            type=error_type,
            message=message,
            retryable=retryable,
        ),
        latency_ms=int(
            (
                time.perf_counter()
                - started
            )
            * 1000
        ),
    )


def _run_tavily(
    query: str,
    max_results: int,
):
    from langchain_tavily import (
        TavilySearch,
    )

    search = TavilySearch(
        max_results=max_results,
        tavily_api_key=(
            SETTINGS.tavily_api_key
        ),
    )

    return search.invoke(
        {
            "query": query,
        }
    )


@tool("web_search")
def web_search(
    query: str,
    max_results: int = 4,
) -> dict:
    """Search the live web for current or externally verifiable information."""

    started = time.perf_counter()

    log_event(
        "TOOL",
        "web_search called",
    )

    try:
        payload = SearchInput(
            query=query,
            max_results=min(
                max_results,
                4,
            ),
        )

    except ValidationError as exc:
        result = _error(
            "VALIDATION_ERROR",
            str(exc),
            False,
            started,
        )

        log_event(
            "TOOL",
            (
                "web_search failed "
                f"({result.error.type})"
            ),
        )

        return result.model_dump()

    if not SETTINGS.tavily_api_key:
        result = _error(
            "AUTH_ERROR",
            "TAVILY_API_KEY is missing.",
            False,
            started,
        )

        log_event(
            "TOOL",
            (
                "web_search failed "
                f"({result.error.type})"
            ),
        )

        return result.model_dump()

    pool = ThreadPoolExecutor(
        max_workers=1
    )

    future = pool.submit(
        _run_tavily,
        payload.query,
        payload.max_results,
    )

    try:
        raw = future.result(
            timeout=(
                SETTINGS
                .tool_timeout_seconds
            )
        )

        result = ToolResult(
            ok=True,
            tool="web_search",
            data={
                "query": payload.query,
                "results": raw,
            },
            error=None,
            latency_ms=int(
                (
                    time.perf_counter()
                    - started
                )
                * 1000
            ),
        )

        log_event(
            "TOOL",
            (
                "web_search success "
                f"| {result.latency_ms} ms"
            ),
        )

        return result.model_dump()

    except FuturesTimeoutError:
        future.cancel()

        result = _error(
            "TIMEOUT",
            (
                "Search exceeded the configured "
                f"{SETTINGS.tool_timeout_seconds:g}s timeout."
            ),
            True,
            started,
        )

    except Exception as exc:
        text = str(exc).lower()

        is_auth = any(
            marker in text
            for marker in (
                "401",
                "403",
                "unauthorized",
                "api key",
            )
        )

        result = _error(
            (
                "AUTH_ERROR"
                if is_auth
                else "PROVIDER_ERROR"
            ),
            str(exc),
            not is_auth,
            started,
        )

    finally:
        # IMPORTANT: wait=False prevents a provider call that already
        # timed out from holding the workflow open during executor shutdown.
        pool.shutdown(
            wait=False,
            cancel_futures=True,
        )

    log_event(
        "TOOL",
        (
            "web_search failed "
            f"({result.error.type}) "
            f"| {result.latency_ms} ms"
        ),
    )

    return result.model_dump()
