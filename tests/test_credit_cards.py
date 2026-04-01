import asyncio
from pathlib import Path
from types import SimpleNamespace

from obsidian_bot.ai_classifier import AIClassifier
from obsidian_bot.common_notes import load_credit_cards
from obsidian_bot.config import Settings
from obsidian_bot.daily_note import DailyNoteWriter
from obsidian_bot.handlers import (
    AppServices,
    PendingAction,
    card_command,
    text_message_handler,
)
from obsidian_bot.media_handler import MediaHandler
from obsidian_bot.note_writer import NoteWriter
from obsidian_bot.card_recommender import recommend_cards
from obsidian_bot.url_extractor import URLExtractor
from obsidian_bot.vault_adapter import VaultAdapter
from obsidian_bot.web_lookup import OfficialWebLookup

CARD_NOTE = """---
tags:
  - 常用
  - 信用卡
---

# 信用卡資料庫

## 使用說明
- 說明

## 台新Richart卡
**銀行:** 台新銀行
**卡片定位:** 日常分眾切換卡
**最後檢查:** 2026-03-26
**更新來源:**
- https://richart.example
**信心度:** 高
**商家關鍵字:**
- Agoda
- Uber Eats
**適用類別:**
- 餐飲
- 網購
**基礎回饋:**
- 最高 3.3%
**加碼回饋 / 附加價值:**
- 好饗刷餐飲高回饋
**支付方式限制:**
- 需切方案
**上限/門檻:**
- 依官網公告
**排除項目:**
- 依官網公告
**適用期限:**
- 2026-03-26
**推薦提示:**
- 一般日常消費主力卡

## 台北富邦Costco卡
**銀行:** 台北富邦銀行
**卡片定位:** Costco 主力卡
**最後檢查:** 2026-03-26
**更新來源:**
- https://costco.example
**信心度:** 高
**商家關鍵字:**
- Costco
- 好市多
**適用類別:**
- 賣場
**基礎回饋:**
- 2% 好多金
**加碼回饋 / 附加價值:**
- Costco 線上購物 3%
**支付方式限制:**
- 需用聯名卡
**上限/門檻:**
- 無上限
**排除項目:**
- 特店分期不回饋
**適用期限:**
- 2027-12-31
**推薦提示:**
- Costco 直接首選

## 樂天Panda JCB卡
**銀行:** 樂天信用卡
**卡片定位:** 指定支付卡
**最後檢查:** 2026-03-26
**更新來源:**
- https://panda.example
**信心度:** 高
**商家關鍵字:**
- LINE Pay
- 街口支付
**適用類別:**
- 行動支付
**基礎回饋:**
- 0.5%
**加碼回饋 / 附加價值:**
- 指定支付最高 3%
**支付方式限制:**
- 指定通路才有加碼
**上限/門檻:**
- NT$500 / 每期帳單
**排除項目:**
- 稅費不回饋
**適用期限:**
- 2026-06-30
**推薦提示:**
- LINE Pay 常用首選

## 樂天虎航卡
**銀行:** 樂天信用卡
**卡片定位:** 台灣虎航聯名卡
**最後檢查:** 2026-03-26
**更新來源:**
- https://tiger.example
**信心度:** 高
**商家關鍵字:**
- 台灣虎航
- LINE Pay
**適用類別:**
- 航空
- 行動支付
**基礎回饋:**
- 0.5%
**加碼回饋 / 附加價值:**
- 虎航最高 3%
**支付方式限制:**
- 虎航優惠需指定連結
**上限/門檻:**
- 500 tigerpoints / 每期帳單
**排除項目:**
- 稅費不回饋
**適用期限:**
- 2026-12-31
**推薦提示:**
- 台灣虎航固定首選

## 更新紀錄
- 建立測試資料
"""


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


def write_card_note(settings: Settings) -> None:
    (settings.common_path / "信用卡.md").write_text(CARD_NOTE, encoding="utf-8")


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.replies: list[str] = []
        self.chat_id = 1
        self.message_id = 1
        self.forward_origin = None

    async def reply_text(self, text: str, reply_markup=None) -> None:
        self.replies.append(text)


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
    def __init__(self, services: AppServices, args: list[str] | None = None) -> None:
        self.args = args or []
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


def test_load_credit_cards_skips_non_card_sections(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    write_card_note(settings)

    cards = load_credit_cards(settings)

    assert [card.name for card in cards] == [
        "台新Richart卡",
        "台北富邦Costco卡",
        "樂天Panda JCB卡",
        "樂天虎航卡",
    ]
    assert cards[0].merchant_keywords[:2] == ("Agoda", "Uber Eats")
    assert cards[1].source_urls == ("https://costco.example",)


def test_recommend_cards_prefers_costco_card_for_costco(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    write_card_note(settings)

    result = recommend_cards("Costco", load_credit_cards(settings))

    assert result.best is not None
    assert result.best.card.name == "台北富邦Costco卡"


def test_recommend_cards_returns_two_payment_candidates_for_line_pay(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    write_card_note(settings)

    result = recommend_cards("LINE Pay", load_credit_cards(settings))

    assert result.best is not None
    assert result.backup is not None
    assert {result.best.card.name, result.backup.card.name} == {
        "樂天Panda JCB卡",
        "樂天虎航卡",
    }


def test_card_command_without_args_starts_pending_prompt(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    services = make_services(settings)
    message = FakeMessage()
    update = SimpleNamespace(
        effective_message=message, effective_chat=SimpleNamespace(id=1)
    )
    context = FakeContext(services, args=[])

    asyncio.run(card_command(update, context))

    action = context.user_data.get("pending_action")
    assert action is not None
    assert getattr(action, "kind") == "card"
    assert message.replies[-1].startswith("請輸入店家名稱")


def test_card_button_starts_pending_prompt(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    services = make_services(settings)
    message = FakeMessage("信用卡推薦")
    update = SimpleNamespace(
        effective_message=message, effective_chat=SimpleNamespace(id=1)
    )
    context = FakeContext(services)

    asyncio.run(text_message_handler(update, context))

    action = context.user_data.get("pending_action")
    assert action is not None
    assert getattr(action, "kind") == "card"
    assert message.replies[-1].startswith("請輸入店家名稱")


def test_card_command_replies_with_costco_recommendation(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    write_card_note(settings)
    services = make_services(settings)
    message = FakeMessage()
    update = SimpleNamespace(
        effective_message=message, effective_chat=SimpleNamespace(id=1)
    )
    context = FakeContext(services, args=["Costco"])

    asyncio.run(card_command(update, context))

    assert message.replies
    assert "首選：台北富邦Costco卡" in message.replies[-1]


def test_card_mode_stays_active_after_recommendation(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    write_card_note(settings)
    services = make_services(settings)
    message = FakeMessage("Costco")
    update = SimpleNamespace(
        effective_message=message, effective_chat=SimpleNamespace(id=1)
    )
    context = FakeContext(services)
    context.user_data["pending_action"] = PendingAction(kind="card")

    asyncio.run(text_message_handler(update, context))

    action = context.user_data.get("pending_action")
    assert action is not None
    assert getattr(action, "kind") == "card"
    assert "首選：台北富邦Costco卡" in message.replies[-1]
