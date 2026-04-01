from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .config import Settings
from .note_lookup import NoteLookupMixin
from .note_metadata import (
    CaptureMetadata,
    default_tags,
    dump_frontmatter,
    title_from_note,
)

_SLUG_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff_-]+")

if TYPE_CHECKING:
    from .vault_adapter import VaultAdapter


@dataclass(frozen=True)
class CapturedNote:
    title: str
    absolute_path: Path
    relative_path: Path
    already_exists: bool = False


class NoteWriter(NoteLookupMixin):
    def __init__(self, settings: Settings, vault: "VaultAdapter | None" = None) -> None:
        self._settings = settings
        self._vault = vault
        self._tz = ZoneInfo(settings.timezone)
        self._settings.inbox_path.mkdir(parents=True, exist_ok=True)
        self._last_captured: CapturedNote | None = None

    @property
    def last_captured(self) -> CapturedNote | None:
        return self._last_captured

    def remember_captured_note(
        self,
        *,
        title: str,
        absolute_path: Path,
        relative_path: Path,
        already_exists: bool = False,
    ) -> CapturedNote:
        note = CapturedNote(
            title=title,
            absolute_path=absolute_path,
            relative_path=relative_path,
            already_exists=already_exists,
        )
        self._last_captured = note
        return note

    def capture_text(
        self,
        *,
        text: str,
        metadata: CaptureMetadata,
        title_override: str | None = None,
        body_override: str | None = None,
    ) -> CapturedNote:
        existing = self._find_existing_note_by_message(
            chat_id=metadata.telegram_chat_id,
            message_id=metadata.telegram_message_id,
        )
        if existing is not None:
            return self.remember_captured_note(
                title=title_from_note(existing),
                absolute_path=existing,
                relative_path=existing.relative_to(self._settings.vault_path),
                already_exists=True,
            )

        if metadata.canonical_url is not None:
            existing_by_url = self._find_existing_note_by_canonical_url(
                metadata.canonical_url
            )
            if existing_by_url is not None:
                return self.remember_captured_note(
                    title=title_from_note(existing_by_url),
                    absolute_path=existing_by_url,
                    relative_path=existing_by_url.relative_to(
                        self._settings.vault_path
                    ),
                    already_exists=True,
                )

        now = datetime.now(self._tz)
        title = (title_override or self._build_title(text=text, now=now)).strip()
        filename = self._unique_filename(
            title=title,
            now=now,
        )
        relative_path = Path(self._settings.inbox_dir) / filename
        absolute_path = self._settings.vault_path / relative_path

        frontmatter = {
            "title": title,
            "source_url": metadata.source_url or metadata.canonical_url,
            "tags": default_tags(metadata),
            "telegram_chat_id": metadata.telegram_chat_id,
            "telegram_message_id": metadata.telegram_message_id,
        }

        body = body_override if body_override is not None else text.strip() + "\n"
        absolute_path.write_text(dump_frontmatter(frontmatter, body), encoding="utf-8")
        if self._vault is not None:
            self._vault.register_note(absolute_path)
        return self.remember_captured_note(
            title=title,
            absolute_path=absolute_path,
            relative_path=relative_path,
        )

    def _build_title(self, *, text: str, now: datetime) -> str:
        for line in text.splitlines():
            cleaned = line.strip()
            if cleaned:
                return cleaned[:60]
        return f"{self._settings.note_prefix}-{now.strftime('%Y%m%d-%H%M%S')}"

    def _unique_filename(
        self,
        *,
        title: str,
        now: datetime,
    ) -> str:
        base = _SLUG_RE.sub("-", title).strip("-_")
        if not base:
            base = f"{self._settings.note_prefix}-{now.strftime('%Y%m%d-%H%M%S')}"
        base = re.sub(r"-{2,}", "-", base)

        candidate = f"{base}.md"
        path = self._settings.inbox_path / candidate
        if not path.exists():
            return candidate

        counter = 1
        while True:
            candidate = f"{base}-{counter}.md"
            path = self._settings.inbox_path / candidate
            if not path.exists():
                return candidate
            counter += 1
