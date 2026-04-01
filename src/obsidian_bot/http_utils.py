from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

import httpx

RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})


async def get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    logger: logging.Logger,
    max_attempts: int = 3,
    backoff_base_seconds: float = 0.5,
    retryable_status_codes: Iterable[int] = RETRYABLE_STATUS_CODES,
) -> httpx.Response:
    allowed_status_codes = frozenset(retryable_status_codes)
    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.get(url)
            if response.is_error:
                response.raise_for_status()
            return response
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            httpx.HTTPStatusError,
        ) as exc:
            if not _should_retry(exc, allowed_status_codes) or attempt >= max_attempts:
                raise
            delay_seconds = backoff_base_seconds * (2 ** (attempt - 1))
            logger.warning(
                "Retrying GET %s in %.1fs (%s/%s) because of %s",
                url,
                delay_seconds,
                attempt,
                max_attempts,
                _describe_error(exc),
            )
            await asyncio.sleep(delay_seconds)
    raise RuntimeError("Retry loop exhausted unexpectedly")


def _should_retry(exc: Exception, retryable_status_codes: frozenset[int]) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in retryable_status_codes
    return False


def _describe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http {exc.response.status_code}"
    return exc.__class__.__name__
