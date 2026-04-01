from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_QUERY_PREFIXES = (
    "utm_",
    "fbclid",
    "gclid",
    "igsh",
    "igshid",
    "si",
)


@dataclass(frozen=True)
class CaptureMetadata:
    source: str
    capture_type: str
    telegram_chat_id: int
    telegram_message_id: int
    is_forwarded: bool = False
    forward_origin_type: str | None = None
    forward_origin_name: str | None = None
    source_url: str | None = None
    canonical_url: str | None = None
    source_platform: str = "telegram"
    source_domain: str | None = None
    extraction_quality: str | None = None
    content_hash: str | None = None
    extra_tags: tuple[str, ...] = field(default_factory=tuple)


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(TRACKING_QUERY_PREFIXES)
    ]
    normalized_path = parts.path.rstrip("/") or "/"
    normalized = parts._replace(
        scheme=(parts.scheme or "https").lower(),
        netloc=parts.netloc.lower(),
        path=normalized_path,
        query=urlencode(query_pairs),
        fragment="",
    )
    return urlunsplit(normalized)


def platform_from_url(url: str | None) -> str:
    if not url:
        return "telegram"
    netloc = urlsplit(url).netloc.lower()
    if "instagram.com" in netloc:
        return "instagram"
    if "threads.net" in netloc:
        return "threads"
    if "facebook.com" in netloc or "fb.watch" in netloc:
        return "facebook"
    return "web"


def domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    return urlsplit(url).netloc.lower().removeprefix("www.") or None


def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:16]


def normalize_tag(tag: str) -> str:
    cleaned = tag.strip().lstrip("#")
    return cleaned.replace(" ", "-")


def unique_tags(tags: Iterable[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        normalized = normalize_tag(tag)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def default_tags(metadata: CaptureMetadata) -> list[str]:
    return ["inbox"]


def _parse_scalar(raw: str) -> object:
    if raw.startswith('"') and raw.endswith('"'):
        return json.loads(raw)
    lowered = raw.lower()
    if lowered in {"null", "none"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def load_frontmatter(note_path: Path) -> tuple[dict[str, object], str]:
    text = note_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}, text

    lines = text.splitlines()
    frontmatter_lines: list[str] = []
    body_start_index = 0
    for index, line in enumerate(lines[1:], start=1):
        if line == "---":
            body_start_index = index + 1
            break
        frontmatter_lines.append(line)
    else:
        return {}, text

    data: dict[str, object] = {}
    current_list_key: str | None = None
    for line in frontmatter_lines:
        if line.startswith("  - ") and current_list_key is not None:
            values = data.setdefault(current_list_key, [])
            if isinstance(values, list):
                values.append(line[4:])
            continue

        current_list_key = None
        if ":" not in line:
            continue

        key, raw = line.split(":", 1)
        value = raw.strip()
        if value == "":
            data[key] = []
            current_list_key = key
            continue
        data[key] = _parse_scalar(value)

    body = "\n".join(lines[body_start_index:])
    if text.endswith("\n"):
        body += "\n"
    return data, body


def dump_frontmatter(data: dict[str, object], body: str) -> str:
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
            continue
        if value is None:
            rendered = "null"
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (int, float)):
            rendered = str(value)
        else:
            rendered = json.dumps(str(value), ensure_ascii=False)
        lines.append(f"{key}: {rendered}")
    lines.extend(["---", "", body.rstrip("\n")])
    return "\n".join(lines).rstrip() + "\n"


def upsert_note_metadata(
    note_path: Path,
    *,
    fields: dict[str, object] | None = None,
    add_tags: Iterable[str] = (),
    replace_tags: Iterable[str] | None = None,
    remove_tags: Iterable[str] = (),
) -> None:
    data, body = load_frontmatter(note_path)
    if fields:
        for key, value in fields.items():
            if value is None:
                continue
            data[key] = value
    if replace_tags is not None:
        data["tags"] = unique_tags(replace_tags)
    else:
        tags = data.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        normalized_remove_tags = {normalize_tag(tag).casefold() for tag in remove_tags}
        merged_tags = unique_tags([*(str(tag) for tag in tags), *add_tags])
        data["tags"] = [
            tag for tag in merged_tags if tag.casefold() not in normalized_remove_tags
        ]
    note_path.write_text(dump_frontmatter(data, body), encoding="utf-8")


def title_from_note(note_path: Path) -> str:
    data, _ = load_frontmatter(note_path)
    title = data.get("title")
    if isinstance(title, str) and title.strip():
        return title
    return note_path.stem


def find_existing_note_by_message(
    vault_path: Path,
    *,
    chat_id: int,
    message_id: int,
) -> Path | None:
    suffix = f"-t{chat_id}m{message_id}.md"
    for note_path in vault_path.rglob("*.md"):
        data, _ = load_frontmatter(note_path)
        if (
            data.get("telegram_chat_id") == chat_id
            and data.get("telegram_message_id") == message_id
        ):
            return note_path
        if note_path.name.endswith(suffix):
            return note_path
    return None


def find_existing_note_by_canonical_url(
    vault_path: Path, canonical_url: str
) -> Path | None:
    for note_path in vault_path.rglob("*.md"):
        data, _ = load_frontmatter(note_path)
        if data.get("source_url") == canonical_url:
            return note_path
    return None
