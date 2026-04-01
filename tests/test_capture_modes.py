from datetime import datetime
from pathlib import Path

from obsidian_bot.capture_modes import prepare_capture
from obsidian_bot.config import Settings
from obsidian_bot.note_metadata import dump_frontmatter
from obsidian_bot.vault_adapter import VaultAdapter


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


def test_prepare_article_capture_builds_summary_template(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    prepared = prepare_capture(
        mode="article",
        text="這篇文章主要在講 Obsidian 工作流。它提到每天整理 Inbox 很重要。",
        settings=settings,
        source_url="https://example.com/post",
        source_title="Obsidian 工作流",
        now=datetime.fromisoformat("2026-03-26T09:00:00+08:00"),
    )

    assert prepared.title == "Obsidian 工作流"
    assert "## 一句摘要" in prepared.body
    assert "## 關鍵重點" in prepared.body


def test_prepare_article_capture_appends_image_section(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    prepared = prepare_capture(
        mode="article",
        text="文章內容。",
        settings=settings,
        source_url="https://example.com/post",
        source_title="含圖片文章",
        image_embeds=("![[attachments/20260326/example.jpg]]",),
        now=datetime.fromisoformat("2026-03-26T09:00:00+08:00"),
    )

    assert "## 圖片" in prepared.body
    assert "![[attachments/20260326/example.jpg]]" in prepared.body


def test_prepare_topic_capture_links_related_notes(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    vault = VaultAdapter(settings)
    related_note = settings.common_path / "銀行資訊.md"
    related_note.write_text(
        dump_frontmatter(
            {"title": "銀行資訊", "tags": ["bank"]}, "# 銀行資訊\n帳號整理方式\n"
        ),
        encoding="utf-8",
    )
    vault.register_note(related_note)

    prepared = prepare_capture(
        mode="topic",
        text="銀行資訊整理方式與常見欄位",
        settings=settings,
        vault=vault,
        now=datetime.fromisoformat("2026-03-26T09:00:00+08:00"),
    )

    assert "## 相關筆記" in prepared.body
    assert "[[常用/銀行資訊]]" in prepared.body
