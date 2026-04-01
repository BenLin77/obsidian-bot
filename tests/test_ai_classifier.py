from pathlib import Path

from obsidian_bot.ai_classifier import AIClassifier
from obsidian_bot.config import Settings
from obsidian_bot.note_metadata import dump_frontmatter, load_frontmatter


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
        ai_auto_classify=True,
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


def test_parse_decision_reads_structured_json(tmp_path: Path) -> None:
    classifier = AIClassifier(make_settings(tmp_path))
    decision = classifier._parse_decision(
        '{"folder":"ai","tags":["llm","agent","fresh-tag"],"proposed_new_tags":["extra-tag"],"confidence":0.82,"needs_review":false}',
        available_tags=("llm", "agent", "research"),
    )

    assert decision.suggested_folder == "ai"
    assert decision.suggested_tags == ("llm", "agent")
    assert decision.proposed_new_tags == ("extra-tag", "fresh-tag")
    assert decision.confidence == 0.82
    assert decision.needs_review is False


def test_apply_decision_metadata_updates_note_frontmatter(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    classifier = AIClassifier(settings)
    note_path = settings.inbox_path / "sample.md"
    note_path.write_text(
        dump_frontmatter(
            {
                "title": "Sample",
                "source": "telegram-message",
                "tags": ["inbox", "telegram", "未分類"],
            },
            "body\n",
        ),
        encoding="utf-8",
    )

    classifier._apply_decision_metadata(
        note_path,
        classifier._parse_decision(
            '{"folder":"ai","tags":["llm","research"],"proposed_new_tags":["fresh-tag"],"confidence":0.66,"needs_review":true}',
            available_tags=("llm", "research"),
        ),
    )

    frontmatter, _ = load_frontmatter(note_path)
    assert frontmatter["tags"] == ["llm", "research", "inbox"]
    assert set(frontmatter) == {"title", "source", "tags"}


def test_should_auto_move_requires_high_confidence_and_no_review(
    tmp_path: Path,
) -> None:
    classifier = AIClassifier(make_settings(tmp_path))

    assert (
        classifier._should_auto_move(
            classifier._parse_decision(
                '{"folder":"ai","tags":[],"confidence":0.91,"needs_review":false}',
                available_tags=(),
            )
        )
        is True
    )
    assert (
        classifier._should_auto_move(
            classifier._parse_decision(
                '{"folder":"ai","tags":[],"confidence":0.79,"needs_review":false}',
                available_tags=(),
            )
        )
        is False
    )
    assert (
        classifier._should_auto_move(
            classifier._parse_decision(
                '{"folder":"ai","tags":[],"confidence":0.95,"needs_review":true}',
                available_tags=(),
            )
        )
        is False
    )
    assert (
        classifier._should_auto_move(
            classifier._parse_decision(
                '{"folder":"Inbox","tags":[],"confidence":0.95,"needs_review":false}',
                available_tags=(),
            )
        )
        is False
    )
