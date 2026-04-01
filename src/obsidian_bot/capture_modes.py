from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

from .config import Settings

if TYPE_CHECKING:
    from .vault_adapter import VaultAdapter

CaptureMode = Literal["thought", "article", "topic"]

_MODE_LABELS: dict[CaptureMode, str] = {
    "thought": "隨手想法",
    "article": "文章摘要",
    "topic": "主題筆記",
}
_MODE_TAGS: dict[CaptureMode, tuple[str, ...]] = {
    "thought": ("capture", "capture-thought"),
    "article": ("capture", "capture-article"),
    "topic": ("capture", "capture-topic"),
}
_WIKI_SUFFIX_RE = re.compile(r"\.md$", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？.!?])\s+|\n+")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class PreparedCapture:
    mode: CaptureMode
    title: str
    body: str
    extra_tags: tuple[str, ...]


def capture_mode_label(mode: CaptureMode) -> str:
    return _MODE_LABELS[mode]


def prepare_capture(
    *,
    mode: CaptureMode,
    text: str,
    settings: Settings,
    vault: "VaultAdapter | None" = None,
    source_url: str | None = None,
    source_title: str | None = None,
    image_embeds: tuple[str, ...] = (),
    now: datetime | None = None,
) -> PreparedCapture:
    _ = now or datetime.now(ZoneInfo(settings.timezone))
    title = (source_title or _infer_title(text)).strip() or capture_mode_label(mode)
    cleaned_text = text.strip()
    related_links = _related_links(vault, query=title, limit=3)

    if mode == "thought":
        body = _build_thought_body(
            title=title,
            text=cleaned_text,
            source_url=source_url,
            image_embeds=image_embeds,
        )
    elif mode == "article":
        body = _build_article_body(
            title=title,
            text=cleaned_text,
            source_url=source_url,
            image_embeds=image_embeds,
        )
    else:
        body = _build_topic_body(
            title=title,
            text=cleaned_text,
            source_url=source_url,
            related_links=related_links,
            image_embeds=image_embeds,
        )

    return PreparedCapture(
        mode=mode,
        title=title,
        body=body,
        extra_tags=_MODE_TAGS[mode],
    )


def _build_thought_body(
    *,
    title: str,
    text: str,
    source_url: str | None,
    image_embeds: tuple[str, ...],
) -> str:
    lines = [
        f"# {title}",
        "",
        "## 想法",
        text or "(空白)",
    ]
    if source_url:
        lines.extend(["", "## 來源", f"- {source_url}"])
    _append_image_section(lines, image_embeds)
    return "\n".join(lines).rstrip() + "\n"


def _build_article_body(
    *,
    title: str,
    text: str,
    source_url: str | None,
    image_embeds: tuple[str, ...],
) -> str:
    summary = _summary_sentence(text)
    key_points = _key_points(text, max_items=4)
    lines = [
        f"# {title}",
        "",
        "## 一句摘要",
        summary or "待補摘要",
        "",
        "## 關鍵重點",
    ]
    if key_points:
        lines.extend([f"- {item}" for item in key_points])
    else:
        lines.append("- 待補重點")
    if source_url:
        lines.extend(["", "## 來源", f"- {source_url}"])
    _append_image_section(lines, image_embeds)
    lines.extend(["", "## 原始內容", text or "(空白)"])
    return "\n".join(lines).rstrip() + "\n"


def _build_topic_body(
    *,
    title: str,
    text: str,
    source_url: str | None,
    related_links: tuple[str, ...],
    image_embeds: tuple[str, ...],
) -> str:
    summary = _summary_sentence(text)
    key_points = _key_points(text, max_items=5)
    lines = [
        f"# {title}",
        "",
        "## 主題摘要",
        summary or "待補主題摘要",
        "",
        "## 核心線索",
    ]
    if key_points:
        lines.extend([f"- {item}" for item in key_points])
    else:
        lines.append("- 待補核心線索")
    if related_links:
        lines.extend(["", "## 相關筆記"])
        lines.extend([f"- {item}" for item in related_links])
    if source_url:
        lines.extend(["", "## 來源", f"- {source_url}"])
    _append_image_section(lines, image_embeds)
    lines.extend(["", "## 原始內容", text or "(空白)"])
    return "\n".join(lines).rstrip() + "\n"


def _append_image_section(lines: list[str], image_embeds: tuple[str, ...]) -> None:
    if not image_embeds:
        return
    lines.extend(["", "## 圖片"])
    lines.extend(list(image_embeds))


def _infer_title(text: str) -> str:
    for raw_line in text.splitlines():
        cleaned = _WHITESPACE_RE.sub(" ", raw_line.strip().lstrip("#")).strip()
        if cleaned:
            return cleaned[:60]
    return ""


def _summary_sentence(text: str) -> str:
    for sentence in _split_sentences(text):
        cleaned = _WHITESPACE_RE.sub(" ", sentence).strip()
        if cleaned:
            return cleaned[:140]
    return ""


def _key_points(text: str, *, max_items: int) -> tuple[str, ...]:
    points: list[str] = []
    seen: set[str] = set()
    for sentence in _split_sentences(text):
        cleaned = _WHITESPACE_RE.sub(" ", sentence).strip().lstrip("-* ")
        if len(cleaned) < 8:
            continue
        lowered = cleaned.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        points.append(cleaned[:120])
        if len(points) >= max_items:
            break
    return tuple(points)


def _split_sentences(text: str) -> tuple[str, ...]:
    return tuple(chunk for chunk in _SENTENCE_SPLIT_RE.split(text) if chunk.strip())


def _related_links(
    vault: "VaultAdapter | None", *, query: str, limit: int
) -> tuple[str, ...]:
    if vault is None or not query.strip():
        return ()
    results = vault.search(query, limit=limit)
    links: list[str] = []
    for result in results:
        wikilink = _WIKI_SUFFIX_RE.sub("", result.relative_path)
        if not wikilink or wikilink in links:
            continue
        links.append(f"[[{wikilink}]]")
    return tuple(links[:limit])
