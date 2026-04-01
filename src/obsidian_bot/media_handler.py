from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from telegram import Document, PhotoSize

from .config import Settings
from .note_lookup import NoteLookupMixin
from .note_metadata import (
    CaptureMetadata,
    default_tags,
    dump_frontmatter,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .vault_adapter import VaultAdapter


@dataclass(frozen=True)
class SavedMedia:
    filename: str
    absolute_path: Path
    relative_path: Path
    note_path: Path
    note_relative_path: Path
    already_exists: bool = False


class MediaHandler(NoteLookupMixin):
    SUPPORTED_IMAGES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
    SUPPORTED_DOCS = {".pdf", ".doc", ".docx", ".txt", ".csv", ".xlsx", ".xls"}

    def __init__(self, settings: Settings, vault: "VaultAdapter | None" = None) -> None:
        self._settings = settings
        self._vault = vault
        self._tz = ZoneInfo(settings.timezone)

    async def save_photo(
        self,
        photo: PhotoSize,
        bot,
        *,
        caption: str = "",
        metadata: CaptureMetadata,
    ) -> SavedMedia:
        existing = self._existing_note(metadata)
        if existing is not None:
            return existing

        now = datetime.now(self._tz)
        date_dir = now.strftime("%Y%m%d")
        attachment_dir = self._settings.attachments_path / date_dir
        attachment_dir.mkdir(parents=True, exist_ok=True)

        file = await bot.get_file(photo.file_id)
        ext = Path(file.file_path or "photo.jpg").suffix or ".jpg"
        filename = f"{now.strftime('%H%M%S')}-{photo.file_unique_id}{ext}"

        absolute_path = attachment_dir / filename
        await file.download_to_drive(absolute_path)

        relative_path = Path(self._settings.attachments_dir) / date_dir / filename

        note_path, note_relative = await asyncio.to_thread(
            self._create_media_note,
            media_relative_path=relative_path,
            caption=caption,
            media_type="photo",
            now=now,
            metadata=metadata,
        )

        return SavedMedia(
            filename=filename,
            absolute_path=absolute_path,
            relative_path=relative_path,
            note_path=note_path,
            note_relative_path=note_relative,
        )

    async def save_document(
        self,
        document: Document,
        bot,
        *,
        caption: str = "",
        metadata: CaptureMetadata,
    ) -> SavedMedia | None:
        if document.file_name is None:
            return None

        existing = self._existing_note(metadata)
        if existing is not None:
            return existing

        ext = Path(document.file_name).suffix.lower()
        if ext not in self.SUPPORTED_IMAGES and ext not in self.SUPPORTED_DOCS:
            return None

        now = datetime.now(self._tz)
        date_dir = now.strftime("%Y%m%d")
        attachment_dir = self._settings.attachments_path / date_dir
        attachment_dir.mkdir(parents=True, exist_ok=True)

        file = await bot.get_file(document.file_id)
        filename = f"{now.strftime('%H%M%S')}-{document.file_name}"

        absolute_path = attachment_dir / filename
        await file.download_to_drive(absolute_path)

        relative_path = Path(self._settings.attachments_dir) / date_dir / filename

        media_type = "image" if ext in self.SUPPORTED_IMAGES else "document"
        note_path, note_relative = await asyncio.to_thread(
            self._create_media_note,
            media_relative_path=relative_path,
            caption=caption or document.file_name,
            media_type=media_type,
            now=now,
            metadata=metadata,
        )

        return SavedMedia(
            filename=filename,
            absolute_path=absolute_path,
            relative_path=relative_path,
            note_path=note_path,
            note_relative_path=note_relative,
        )

    def _existing_note(self, metadata: CaptureMetadata) -> SavedMedia | None:
        existing_note = self._find_existing_note_by_message(
            chat_id=metadata.telegram_chat_id,
            message_id=metadata.telegram_message_id,
        )
        if existing_note is None and metadata.canonical_url is not None:
            existing_note = self._find_existing_note_by_canonical_url(
                metadata.canonical_url
            )
        if existing_note is None:
            return None
        relative_note_path = existing_note.relative_to(self._settings.vault_path)
        return SavedMedia(
            filename=existing_note.name,
            absolute_path=existing_note,
            relative_path=relative_note_path,
            note_path=existing_note,
            note_relative_path=relative_note_path,
            already_exists=True,
        )

    def _create_media_note(
        self,
        *,
        media_relative_path: Path,
        caption: str,
        media_type: str,
        now: datetime,
        metadata: CaptureMetadata,
    ) -> tuple[Path, Path]:
        title = caption[:60] if caption else media_relative_path.stem
        slug = title.replace(" ", "-")[:40]
        if not slug:
            slug = f"{media_type}-{now.strftime('%Y%m%d-%H%M%S')}"

        filename = f"{slug}.md"
        note_relative = Path(self._settings.inbox_dir) / filename
        note_absolute = self._settings.vault_path / note_relative
        counter = 1
        while note_absolute.exists():
            filename = f"{slug}-{counter}.md"
            note_relative = Path(self._settings.inbox_dir) / filename
            note_absolute = self._settings.vault_path / note_relative
            counter += 1

        if media_type in ("photo", "image"):
            embed = f"![[{media_relative_path}]]"
        else:
            embed = f"[[{media_relative_path}]]"

        frontmatter = {
            "title": title,
            "source_url": metadata.source_url or metadata.canonical_url,
            "tags": default_tags(metadata),
            "telegram_chat_id": metadata.telegram_chat_id,
            "telegram_message_id": metadata.telegram_message_id,
        }

        body_lines = [embed, ""]
        if caption:
            body_lines.extend([caption, ""])

        note_absolute.write_text(
            dump_frontmatter(frontmatter, "\n".join(body_lines)),
            encoding="utf-8",
        )
        if self._vault is not None:
            self._vault.register_note(note_absolute)

        return note_absolute, note_relative
