import asyncio
import logging
from collections import deque

import httpx

from obsidian_bot.http_utils import get_with_retry


class FakeAsyncClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = deque(responses)
        self.calls = 0

    async def get(self, url: str) -> httpx.Response:
        self.calls += 1
        return self._responses.popleft()


def test_get_with_retry_retries_retryable_status_codes() -> None:
    request = httpx.Request("GET", "https://example.com")
    client = FakeAsyncClient(
        [
            httpx.Response(502, request=request),
            httpx.Response(503, request=request),
            httpx.Response(200, request=request, text="ok"),
        ]
    )

    response = asyncio.run(
        get_with_retry(
            client,
            "https://example.com",
            logger=logging.getLogger("test"),
            max_attempts=3,
            backoff_base_seconds=0,
        )
    )

    assert response.status_code == 200
    assert client.calls == 3


def test_get_with_retry_does_not_retry_non_retryable_status_codes() -> None:
    request = httpx.Request("GET", "https://example.com/missing")
    client = FakeAsyncClient([httpx.Response(404, request=request)])

    try:
        asyncio.run(
            get_with_retry(
                client,
                "https://example.com/missing",
                logger=logging.getLogger("test"),
                max_attempts=3,
                backoff_base_seconds=0,
            )
        )
    except httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("Expected HTTPStatusError for non-retryable status")
    assert client.calls == 1
