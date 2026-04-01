from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from lxml import html as lxml_html
from lxml.etree import ParserError
from markdownify import markdownify
from readability import Document

from .config import Settings
from .http_utils import get_with_retry
from .note_lookup import NoteLookupMixin
from .note_metadata import (
    CaptureMetadata,
    canonicalize_url,
    default_tags,
    domain_from_url,
    dump_frontmatter,
    platform_from_url,
    title_from_note,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .vault_adapter import VaultAdapter

URL_PATTERN = re.compile(
    r"https?://[^\s<>\"']+",
    re.IGNORECASE,
)
_LEADING_TIMESTAMP_PATTERNS = (
    re.compile(
        r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APMapm]{2})?)?$"
    ),
    re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$"),
    re.compile(
        r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?\s?(?:AM|PM))?$",
        re.IGNORECASE,
    ),
    re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APMapm]{2})?$"),
)
_IMAGE_SOURCE_ATTRS = ("src", "data-src", "data-original", "data-lazy-src")


@dataclass(frozen=True)
class ExtractedArticle:
    title: str
    url: str
    content: str
    note_path: Path
    note_relative_path: Path
    already_exists: bool = False


class URLExtractor(NoteLookupMixin):
    MAX_FETCH_ATTEMPTS = 3

    def __init__(self, settings: Settings, vault: "VaultAdapter | None" = None) -> None:
        self._settings = settings
        self._vault = vault
        self._tz = ZoneInfo(settings.timezone)

    def find_url(self, text: str) -> str | None:
        match = URL_PATTERN.search(text)
        if match:
            return match.group(0)
        return None

    async def fetch_article(self, url: str) -> dict | None:
        return await self._fetch_article(url)

    async def extract_and_save(
        self,
        url: str,
        *,
        metadata: CaptureMetadata,
    ) -> ExtractedArticle | None:
        canonical_url = metadata.canonical_url or canonicalize_url(url)
        existing_by_message = self._find_existing_note_by_message(
            chat_id=metadata.telegram_chat_id,
            message_id=metadata.telegram_message_id,
        )
        if existing_by_message is not None:
            return ExtractedArticle(
                title=title_from_note(existing_by_message),
                url=url,
                content="",
                note_path=existing_by_message,
                note_relative_path=existing_by_message.relative_to(
                    self._settings.vault_path
                ),
                already_exists=True,
            )

        existing_by_url = self._find_existing_note_by_canonical_url(canonical_url)
        if existing_by_url is not None:
            return ExtractedArticle(
                title=title_from_note(existing_by_url),
                url=url,
                content="",
                note_path=existing_by_url,
                note_relative_path=existing_by_url.relative_to(
                    self._settings.vault_path
                ),
                already_exists=True,
            )

        try:
            article = await self._fetch_article(url)
            if article is None:
                return None

            note_path, note_relative = await asyncio.to_thread(
                self._save_article,
                title=article["title"],
                url=url,
                content=article["content"],
                image_embeds=tuple(article.get("image_embeds", ())),
                metadata=CaptureMetadata(
                    source=metadata.source,
                    capture_type=metadata.capture_type,
                    telegram_chat_id=metadata.telegram_chat_id,
                    telegram_message_id=metadata.telegram_message_id,
                    is_forwarded=metadata.is_forwarded,
                    forward_origin_type=metadata.forward_origin_type,
                    forward_origin_name=metadata.forward_origin_name,
                    source_url=canonical_url,
                    canonical_url=canonical_url,
                    source_platform=platform_from_url(url),
                    source_domain=domain_from_url(url),
                    extraction_quality="full",
                    content_hash=metadata.content_hash,
                    extra_tags=metadata.extra_tags,
                ),
            )

            return ExtractedArticle(
                title=article["title"],
                url=url,
                content=article["content"],
                note_path=note_path,
                note_relative_path=note_relative,
            )
        except Exception as e:
            logger.error(f"Failed to extract URL {url}: {e}")
            return None

    async def _fetch_article(self, url: str) -> dict | None:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

        async with httpx.AsyncClient(
            follow_redirects=True, timeout=30.0, headers=headers
        ) as client:
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
            title = doc.title()
            summary_html = doc.summary()

            # Readability often strips <img>/<figure> from its summary.
            # Extract images from the *raw* HTML article body first,
            # then fall back to the summary if it already contains images.
            raw_image_urls = self._collect_article_image_urls(html, page_url=url)

            image_embeds, cleaned_summary_html = await self._extract_image_embeds(
                client=client,
                summary_html=summary_html,
                page_url=url,
                extra_image_urls=raw_image_urls,
            )

            content = markdownify(
                cleaned_summary_html,
                heading_style="ATX",
                strip=["script", "style"],
            )
            content = self._sanitize_extracted_content(content)

            return {
                "title": title,
                "content": content,
                "image_embeds": image_embeds,
            }

    def _save_article(
        self,
        *,
        title: str,
        url: str,
        content: str,
        image_embeds: tuple[str, ...] = (),
        metadata: CaptureMetadata,
    ) -> tuple[Path, Path]:
        domain = metadata.source_domain or domain_from_url(url) or "web"

        safe_title = re.sub(r"[^\w\u4e00-\u9fff\s-]", "", title)[:50].strip()
        if not safe_title:
            safe_title = domain

        slug = re.sub(r"\s+", "-", safe_title)
        message_suffix = f"-t{metadata.telegram_chat_id}m{metadata.telegram_message_id}"
        filename = f"{slug}{message_suffix}.md"
        note_relative = Path(self._settings.inbox_dir) / filename
        note_absolute = self._settings.vault_path / note_relative
        counter = 1
        while note_absolute.exists():
            filename = f"{slug}-{counter}{message_suffix}.md"
            note_relative = Path(self._settings.inbox_dir) / filename
            note_absolute = self._settings.vault_path / note_relative
            counter += 1

        frontmatter = {
            "title": title,
            "source_url": metadata.source_url or url,
            "tags": default_tags(metadata),
        }

        body = "\n".join(
            [
                f"# {title}",
                "",
                f"> Source: [{domain}]({url})",
                "",
                *(["## 圖片", *image_embeds, ""] if image_embeds else []),
                content,
                "",
            ]
        )
        note_absolute.write_text(dump_frontmatter(frontmatter, body), encoding="utf-8")
        if self._vault is not None:
            self._vault.register_note(note_absolute)

        return note_absolute, note_relative

    async def _extract_image_embeds(
        self,
        *,
        client: httpx.AsyncClient,
        summary_html: str,
        page_url: str,
        extra_image_urls: tuple[str, ...] = (),
    ) -> tuple[tuple[str, ...], str]:
        if not summary_html.strip():
            return (), summary_html
        try:
            fragment = lxml_html.fragment_fromstring(summary_html, create_parent="div")
        except ParserError:
            return (), summary_html
        now = datetime.now(self._tz)
        attachment_dir = self._settings.attachments_path / now.strftime("%Y%m%d")
        attachment_dir.mkdir(parents=True, exist_ok=True)

        embeds: list[str] = []
        seen_urls: set[str] = set()
        sequence = 0

        for image in fragment.xpath(".//img"):
            source_url = self._image_source_url(image, page_url=page_url)
            if source_url is None or source_url in seen_urls:
                self._drop_image_node(image)
                continue
            seen_urls.add(source_url)
            sequence += 1
            embed = await self._download_image_embed(
                client=client,
                image_url=source_url,
                attachment_dir=attachment_dir,
                sequence=sequence,
                now=now,
            )
            if embed is not None:
                embeds.append(embed)
            self._drop_image_node(image)

        for extra_url in extra_image_urls:
            if extra_url in seen_urls:
                continue
            seen_urls.add(extra_url)
            sequence += 1
            embed = await self._download_image_embed(
                client=client,
                image_url=extra_url,
                attachment_dir=attachment_dir,
                sequence=sequence,
                now=now,
            )
            if embed is not None:
                embeds.append(embed)

        return tuple(embeds), lxml_html.tostring(fragment, encoding="unicode")

    def _image_source_url(self, image, *, page_url: str) -> str | None:
        for attr_name in _IMAGE_SOURCE_ATTRS:
            raw_value = str(image.attrib.get(attr_name, "")).strip()
            if not raw_value:
                continue
            if raw_value.startswith(("data:", "blob:")):
                return None
            return urljoin(page_url, raw_value)
        return None

    _ARTICLE_IMG_XPATHS = (
        ".//article//img",
        './/div[contains(@class,"entry-content")]//img',
        './/div[contains(@class,"post-content")]//img',
        './/div[contains(@class,"article-body")]//img',
        './/div[contains(@class,"article-content")]//img',
        './/div[contains(@class,"content-area")]//img',
    )
    _SMALL_IMAGE_THRESHOLD = 48

    def _collect_article_image_urls(
        self, raw_html: str, *, page_url: str
    ) -> tuple[str, ...]:
        try:
            tree = lxml_html.fromstring(raw_html)
        except (ParserError, Exception):
            return ()

        seen: set[str] = set()
        urls: list[str] = []
        for xpath in self._ARTICLE_IMG_XPATHS:
            for img in tree.xpath(xpath):
                if self._is_tiny_image(img):
                    continue
                source_url = self._image_source_url(img, page_url=page_url)
                if source_url is not None and source_url not in seen:
                    seen.add(source_url)
                    urls.append(source_url)
        return tuple(urls)

    def _is_tiny_image(self, img) -> bool:
        for dim in ("width", "height"):
            raw = str(img.attrib.get(dim, "")).strip()
            if raw.isdigit() and int(raw) < self._SMALL_IMAGE_THRESHOLD:
                return True
        return False

    async def _download_image_embed(
        self,
        *,
        client: httpx.AsyncClient,
        image_url: str,
        attachment_dir: Path,
        sequence: int,
        now: datetime,
    ) -> str | None:
        try:
            response = await get_with_retry(
                client,
                image_url,
                logger=logger,
                max_attempts=2,
            )
        except Exception as exc:
            logger.warning("Failed to download article image %s: %s", image_url, exc)
            return None

        content_type = (
            response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        )
        if not content_type.startswith("image/"):
            return None

        file_suffix = Path(urlsplit(image_url).path).suffix
        if not file_suffix:
            file_suffix = mimetypes.guess_extension(content_type) or ".jpg"
        digest = hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:10]
        filename = f"{now.strftime('%H%M%S')}-clip-{sequence:02d}-{digest}{file_suffix}"
        absolute_path = attachment_dir / filename
        await asyncio.to_thread(absolute_path.write_bytes, response.content)
        relative_path = (
            Path(self._settings.attachments_dir) / attachment_dir.name / filename
        )
        return f"![[{relative_path.as_posix()}]]"

    def _drop_image_node(self, image) -> None:
        parent = image.getparent()
        if parent is not None:
            parent.remove(image)

    def _sanitize_extracted_content(self, content: str) -> str:
        cleaned = re.sub(r"\n{3,}", "\n\n", content).strip()
        cleaned = self._strip_leading_timestamp_lines(cleaned)
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    def _strip_leading_timestamp_lines(self, content: str) -> str:
        lines = content.splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)

        removed = 0
        while lines and removed < 2:
            candidate = lines[0].strip().strip("-•·|—– ")
            if not candidate or not self._looks_like_timestamp_line(candidate):
                break
            lines.pop(0)
            removed += 1
            while lines and not lines[0].strip():
                lines.pop(0)

        return "\n".join(lines)

    def _looks_like_timestamp_line(self, line: str) -> bool:
        compact = re.sub(r"\s+", " ", line).strip()
        if not compact or len(compact) > 48:
            return False
        return any(pattern.match(compact) for pattern in _LEADING_TIMESTAMP_PATTERNS)
