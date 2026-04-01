from pathlib import Path

from obsidian_bot.config import Settings
from obsidian_bot.note_lookup import NoteLookupMixin
from obsidian_bot.note_metadata import dump_frontmatter


def make_settings(tmp_path: Path) -> Settings:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    settings = Settings(
        telegram_bot_token="token",
        allowed_chat_ids=frozenset({1}),
        vault_path=vault_path,
        state_path=tmp_path / "telegram-state.pkl",
        inbox_dir="Inbox",
        common_dir="常用",
        timezone="Asia/Taipei",
        note_prefix="telegram",
        attachments_dir="attachments",
        daily_dir="Daily",
        daily_threshold=100,
        gemini_api_key="",
        ai_auto_classify=False,
        valid_folders=frozenset(
            {"stock", "ai", "food", "佛教", "Option", "量化交易", "job", "Inbox"}
        ),
        auto_move_confidence_threshold=0.8,
        low_confidence_threshold=0.55,
        system_tags=frozenset(
            {
                "inbox",
                "telegram",
                "未分類",
                "待整理",
                "capture",
                "capture-thought",
                "capture-article",
                "capture-topic",
                "text",
                "photo",
                "document",
                "url",
                "url-fallback",
                "web-clip",
                "forwarded",
                "web",
                "instagram",
                "facebook",
                "threads",
            }
        ),
    )
    settings.inbox_path.mkdir(parents=True, exist_ok=True)
    return settings


class LookupHost(NoteLookupMixin):
    def __init__(self, settings: Settings, vault=None) -> None:
        self._settings = settings
        self._vault = vault


class FakeVault:
    def __init__(
        self, message_result: Path | None, canonical_result: Path | None
    ) -> None:
        self.message_result = message_result
        self.canonical_result = canonical_result
        self.message_calls: list[tuple[int, int]] = []
        self.canonical_calls: list[str] = []

    def find_existing_note_by_message(
        self, *, chat_id: int, message_id: int
    ) -> Path | None:
        self.message_calls.append((chat_id, message_id))
        return self.message_result

    def find_existing_note_by_canonical_url(self, canonical_url: str) -> Path | None:
        self.canonical_calls.append(canonical_url)
        return self.canonical_result


def test_note_lookup_mixin_delegates_to_vault_when_present(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    expected_message_path = settings.inbox_path / "vault-message-t1m2.md"
    expected_canonical_path = settings.inbox_path / "vault-url-t1m2.md"
    vault = FakeVault(expected_message_path, expected_canonical_path)
    host = LookupHost(settings, vault=vault)

    assert (
        host._find_existing_note_by_message(chat_id=1, message_id=2)
        == expected_message_path
    )
    assert (
        host._find_existing_note_by_canonical_url("https://example.com/post")
        == expected_canonical_path
    )
    assert vault.message_calls == [(1, 2)]
    assert vault.canonical_calls == ["https://example.com/post"]


def test_note_lookup_mixin_falls_back_to_file_scan_when_vault_missing(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    expected_message_path = settings.inbox_path / "scan-message-t1m2.md"
    expected_message_path.write_text("message body\n", encoding="utf-8")
    expected_canonical_path = settings.inbox_path / "scan-canonical-t9m9.md"
    expected_canonical_path.write_text(
        dump_frontmatter(
            {
                "title": "Canonical",
                "source_url": "https://example.com/post",
                "tags": ["inbox"],
            },
            "body\n",
        ),
        encoding="utf-8",
    )
    host = LookupHost(settings)

    assert (
        host._find_existing_note_by_message(chat_id=1, message_id=2)
        == expected_message_path
    )
    assert (
        host._find_existing_note_by_canonical_url("https://example.com/post")
        == expected_canonical_path
    )
