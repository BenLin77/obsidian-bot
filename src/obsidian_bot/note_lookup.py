from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .note_metadata import (
    find_existing_note_by_canonical_url,
    find_existing_note_by_message,
)

if TYPE_CHECKING:
    from .config import Settings
    from .vault_adapter import VaultAdapter


class NoteLookupMixin:
    _settings: Settings
    _vault: VaultAdapter | None

    def _find_existing_note_by_message(
        self, *, chat_id: int, message_id: int
    ) -> Path | None:
        if self._vault is not None:
            return self._vault.find_existing_note_by_message(
                chat_id=chat_id,
                message_id=message_id,
            )
        return find_existing_note_by_message(
            self._settings.vault_path,
            chat_id=chat_id,
            message_id=message_id,
        )

    def _find_existing_note_by_canonical_url(self, canonical_url: str) -> Path | None:
        if self._vault is not None:
            return self._vault.find_existing_note_by_canonical_url(canonical_url)
        return find_existing_note_by_canonical_url(
            self._settings.vault_path, canonical_url
        )
