from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Iterable

import httpx
from markdownify import markdownify
from readability import Document

from .common_notes import CreditCard
from .http_utils import get_with_retry

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{1,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_GENERIC_TERMS = {"信用卡", "卡片", "重複", "取消", "保留", "推薦", "回饋", "刷哪張", "哪張卡"}


@dataclass(frozen=True)
class WebContextItem:
    card_name: str
    url: str
    title: str
    snippet: str

    def to_ai_dict(self) -> dict[str, str]:
        return {
            "card_name": self.card_name,
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
        }


class OfficialWebLookup:
    CACHE_TTL_SECONDS = 1800
    MAX_URLS = 6
    MAX_FETCH_ATTEMPTS = 3

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, str, str]] = {}

    async def lookup_credit_card_context(
        self,
        *,
        question: str,
        cards: Iterable[CreditCard],
        max_urls: int | None = None,
    ) -> tuple[WebContextItem, ...]:
        selected_urls: list[tuple[str, str]] = []
        seen_urls: set[str] = set()
        limit = max_urls or self.MAX_URLS
        for card in cards:
            for url in card.source_urls:
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                selected_urls.append((card.name, url))
                break
            if len(selected_urls) >= limit:
                break

        if not selected_urls:
            return ()

        terms = _extract_terms(question)
        fetched = await asyncio.gather(
            *(self._fetch(card_name=card_name, url=url, terms=terms) for card_name, url in selected_urls),
            return_exceptions=True,
        )

        results: list[WebContextItem] = []
        for item in fetched:
            if isinstance(item, Exception):
                logger.warning("Web lookup failed: %s", item)
                continue
            if item is not None:
                results.append(item)
        return tuple(results)

    async def _fetch(
        self,
        *,
        card_name: str,
        url: str,
        terms: tuple[str, ...],
    ) -> WebContextItem | None:
        cached = self._cache.get(url)
        now = time.time()
        if cached is not None and now - cached[0] < self.CACHE_TTL_SECONDS:
            title, content = cached[1], cached[2]
            snippet = _build_snippet(content, terms)
            return WebContextItem(card_name=card_name, url=url, title=title, snippet=snippet)

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        }
        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0, headers=headers) as client:
            response = await get_with_retry(
                client,
                url,
                logger=logger,
                max_attempts=self.MAX_FETCH_ATTEMPTS,
            )
            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type:
                return None

            html = response.text
            doc = Document(html)
            title = _normalize_whitespace(doc.title() or card_name)
            summary_html = doc.summary()
            content = markdownify(summary_html, heading_style="ATX", strip=["script", "style"])
            content = _normalize_whitespace(_HTML_TAG_RE.sub(" ", content))
            if not content:
                return None

            self._cache[url] = (now, title, content)
            snippet = _build_snippet(content, terms)
            return WebContextItem(card_name=card_name, url=url, title=title, snippet=snippet)


def _extract_terms(question: str) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()

    for match in _CJK_RE.finditer(question):
        value = match.group(0).strip()
        if value in _GENERIC_TERMS:
            continue
        lowered = value.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        values.append(value)

    for match in _WORD_RE.finditer(question):
        value = match.group(0).strip()
        lowered = value.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        values.append(value)

    return tuple(values[:8])


def _build_snippet(content: str, terms: tuple[str, ...]) -> str:
    if not content:
        return ""
    sentences = re.split(r"(?<=[。.!?])\s+|\n+", content)
    normalized_terms = tuple(term.casefold() for term in terms if term)

    for sentence in sentences:
        cleaned = _normalize_whitespace(sentence)
        if not cleaned:
            continue
        lowered = cleaned.casefold()
        if normalized_terms and any(term in lowered for term in normalized_terms):
            return cleaned[:280]

    for sentence in sentences:
        cleaned = _normalize_whitespace(sentence)
        if cleaned:
            return cleaned[:220]
    return content[:220]


def _normalize_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()
