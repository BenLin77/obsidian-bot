import asyncio
from pathlib import Path
from types import SimpleNamespace

from obsidian_bot.ai_classifier import AIClassifier
from obsidian_bot.config import Settings
from obsidian_bot.daily_note import DailyNoteWriter
from obsidian_bot.handlers import (
    AppServices,
    _MAIN_MENU_BUTTONS,
    _should_store_text_in_inbox,
    text_message_handler,
)
from obsidian_bot.media_handler import MediaHandler
from obsidian_bot.note_writer import NoteWriter
from obsidian_bot.url_extractor import URLExtractor
from obsidian_bot.vault_adapter import VaultAdapter
from obsidian_bot.web_lookup import OfficialWebLookup


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
    settings.common_path.mkdir(parents=True, exist_ok=True)
    settings.daily_path.mkdir(parents=True, exist_ok=True)
    settings.attachments_path.mkdir(parents=True, exist_ok=True)
    return settings


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.replies: list[str] = []
        self.reply_markups: list[object | None] = []
        self.chat_id = 1
        self.message_id = 1
        self.forward_origin = None

    async def reply_text(self, text: str, reply_markup=None) -> None:
        self.replies.append(text)
        self.reply_markups.append(reply_markup)


class FakeApplication:
    def __init__(self, services: AppServices) -> None:
        self.bot_data = {
            "settings": services.settings,
            "writer": services.writer,
            "daily": services.daily,
            "media": services.media,
            "url": services.url,
            "ai": services.ai,
            "vault": services.vault,
            "web": services.web,
        }


class FakeContext:
    def __init__(self, services: AppServices) -> None:
        self.args: list[str] = []
        self.application = FakeApplication(services)
        self.user_data: dict[str, object] = {}
        self.bot = object()


def make_services(settings: Settings) -> AppServices:
    vault = VaultAdapter(settings)
    return AppServices(
        settings=settings,
        writer=NoteWriter(settings, vault=vault),
        daily=DailyNoteWriter(settings),
        media=MediaHandler(settings, vault=vault),
        url=URLExtractor(settings, vault=vault),
        ai=AIClassifier(settings, vault=vault),
        vault=vault,
        web=OfficialWebLookup(),
    )


def test_main_menu_includes_credit_card_shortcut() -> None:
    assert _MAIN_MENU_BUTTONS == [
        ["銀行資訊", "地址"],
        ["信用卡推薦", "筆記問答"],
    ]


def test_forwarded_short_text_goes_to_inbox() -> None:
    assert (
        _should_store_text_in_inbox(text="短文", threshold=100, is_forwarded=True)
        is True
    )


def test_regular_short_text_stays_in_daily() -> None:
    assert (
        _should_store_text_in_inbox(text="短文", threshold=100, is_forwarded=False)
        is False
    )


def test_regular_long_text_goes_to_inbox() -> None:
    assert (
        _should_store_text_in_inbox(text="長" * 101, threshold=100, is_forwarded=False)
        is True
    )


def test_long_text_prompts_capture_mode(tmp_path: Path) -> None:
    services = make_services(make_settings(tmp_path))
    message = FakeMessage("長" * 101)
    update = SimpleNamespace(
        effective_message=message, effective_chat=SimpleNamespace(id=1)
    )
    context = FakeContext(services)

    asyncio.run(text_message_handler(update, context))

    pending_capture = context.user_data.get("pending_capture")
    assert pending_capture is not None
    assert message.replies[-1].startswith("這則內容比較適合先整理後再收")
