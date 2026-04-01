from datetime import datetime
from pathlib import Path

from obsidian_bot.config import Settings
from obsidian_bot.media_handler import MediaHandler
from obsidian_bot.note_metadata import (
    CaptureMetadata,
    canonicalize_url,
    dump_frontmatter,
    load_frontmatter,
)
from obsidian_bot.note_writer import NoteWriter
from obsidian_bot.url_extractor import URLExtractor
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
    settings.attachments_path.mkdir(parents=True, exist_ok=True)
    return settings


def make_metadata(**overrides: object) -> CaptureMetadata:
    base = {
        "source": "telegram-message",
        "capture_type": "text",
        "telegram_chat_id": 1,
        "telegram_message_id": 2,
        "is_forwarded": False,
        "forward_origin_type": None,
        "forward_origin_name": None,
        "source_url": None,
        "canonical_url": None,
        "source_platform": "telegram",
        "source_domain": None,
        "extraction_quality": None,
        "content_hash": None,
        "extra_tags": (),
    }
    base.update(overrides)
    return CaptureMetadata(**base)


def test_capture_text_writes_simplified_frontmatter(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    writer = NoteWriter(settings)

    note = writer.capture_text(
        text="hello world",
        metadata=make_metadata(is_forwarded=True, extra_tags=("forwarded",)),
    )

    frontmatter, _ = load_frontmatter(note.absolute_path)
    assert frontmatter["title"] == "hello world"
    assert frontmatter["source_url"] is None
    assert frontmatter["tags"] == ["inbox"]
    assert frontmatter["telegram_chat_id"] == 1
    assert frontmatter["telegram_message_id"] == 2
    assert set(frontmatter) == {
        "title",
        "source_url",
        "tags",
        "telegram_chat_id",
        "telegram_message_id",
    }


def test_capture_text_dedupes_same_telegram_message(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    writer = NoteWriter(settings)
    metadata = make_metadata()

    first = writer.capture_text(text="same message", metadata=metadata)
    second = writer.capture_text(text="same message", metadata=metadata)

    assert second.already_exists is True
    assert second.absolute_path == first.absolute_path


def test_capture_text_filename_has_no_timestamp_prefix(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    writer = NoteWriter(settings)

    note = writer.capture_text(text="hello world", metadata=make_metadata())

    assert note.relative_path.name == "hello-world.md"


def test_capture_text_filename_dedup_appends_counter(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    writer = NoteWriter(settings)
    (settings.inbox_path / "hello-world.md").write_text("existing", encoding="utf-8")

    filename = writer._unique_filename(
        title="hello world",
        now=datetime(2026, 3, 26, 9, 30),
    )

    assert filename == "hello-world-1.md"


def test_vault_adapter_indexes_message_and_source_url(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    vault = VaultAdapter(settings)
    writer = NoteWriter(settings, vault=vault)
    metadata = make_metadata(
        telegram_message_id=9,
        canonical_url="https://example.com/post/1",
        source_url="https://example.com/post/1",
        source_platform="web",
        source_domain="example.com",
    )

    note = writer.capture_text(text="indexed note", metadata=metadata)

    assert (
        vault.find_existing_note_by_message(
            chat_id=metadata.telegram_chat_id, message_id=9
        )
        == note.absolute_path
    )
    assert (
        vault.find_existing_note_by_canonical_url("https://example.com/post/1")
        == note.absolute_path
    )


def test_vault_adapter_available_tags_filters_system_tags(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    vault = VaultAdapter(settings)
    note_path = settings.inbox_path / "tags.md"
    note_path.write_text(
        dump_frontmatter(
            {
                "title": "Tagged",
                "tags": ["inbox", "telegram", "python", "research", "capture-article"],
            },
            "body\n",
        ),
        encoding="utf-8",
    )
    vault.register_note(note_path)

    assert vault.available_tags() == ("python", "research")


def test_url_clip_writes_simplified_frontmatter(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    extractor = URLExtractor(settings)
    metadata = make_metadata(
        source="telegram-url",
        capture_type="url",
        source_url="https://www.instagram.com/p/abc/?utm_source=ig_web_copy_link",
        canonical_url=canonicalize_url(
            "https://www.instagram.com/p/abc/?utm_source=ig_web_copy_link"
        ),
        source_platform="instagram",
        source_domain="instagram.com",
        extraction_quality="full",
        extra_tags=("web-clip",),
    )

    note_path, _ = extractor._save_article(
        title="Example",
        url="https://www.instagram.com/p/abc/?utm_source=ig_web_copy_link",
        content="body",
        metadata=metadata,
    )

    frontmatter, _ = load_frontmatter(note_path)
    assert note_path.name == "Example.md"
    assert frontmatter["title"] == "Example"
    assert frontmatter["source_url"] == metadata.source_url
    assert frontmatter["tags"] == ["inbox"]
    assert frontmatter["telegram_chat_id"] == 1
    assert frontmatter["telegram_message_id"] == 2
    assert set(frontmatter) == {
        "title",
        "source_url",
        "tags",
        "telegram_chat_id",
        "telegram_message_id",
    }


def test_url_clip_filename_dedup_appends_counter(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    extractor = URLExtractor(settings)
    metadata = make_metadata(
        source="telegram-url",
        capture_type="url",
        source_url="https://example.com/post",
        canonical_url="https://example.com/post",
        source_platform="web",
        source_domain="example.com",
        extraction_quality="full",
    )
    (settings.inbox_path / "Example.md").write_text("existing", encoding="utf-8")

    note_path, _ = extractor._save_article(
        title="Example",
        url="https://example.com/post",
        content="body",
        metadata=metadata,
    )

    assert note_path.name == "Example-1.md"


def test_media_note_writes_source_url_metadata(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    media = MediaHandler(settings)

    note_path, _ = media._create_media_note(
        media_relative_path=Path("attachments/20260326/file.jpg"),
        caption="caption",
        media_type="photo",
        now=datetime(2026, 3, 26, 9, 30),
        metadata=make_metadata(
            source="telegram-photo",
            capture_type="photo",
            source_url="https://threads.net/@demo/post/1",
            canonical_url="https://threads.net/@demo/post/1",
            source_platform="threads",
            source_domain="threads.net",
            extra_tags=("forwarded",),
        ),
    )

    frontmatter, _ = load_frontmatter(note_path)
    assert note_path.name == "caption.md"
    assert frontmatter["title"] == "caption"
    assert frontmatter["source_url"] == "https://threads.net/@demo/post/1"
    assert frontmatter["tags"] == ["inbox"]
    assert frontmatter["telegram_chat_id"] == 1
    assert frontmatter["telegram_message_id"] == 2
    assert set(frontmatter) == {
        "title",
        "source_url",
        "tags",
        "telegram_chat_id",
        "telegram_message_id",
    }


def test_media_note_filename_dedup_appends_counter(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    media = MediaHandler(settings)
    (settings.inbox_path / "caption.md").write_text("existing", encoding="utf-8")

    note_path, _ = media._create_media_note(
        media_relative_path=Path("attachments/20260326/file.jpg"),
        caption="caption",
        media_type="photo",
        now=datetime(2026, 3, 26, 9, 30),
        metadata=make_metadata(),
    )

    assert note_path.name == "caption-1.md"


def test_url_clip_writes_downloaded_image_section(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    extractor = URLExtractor(settings)
    metadata = make_metadata(
        source="telegram-url",
        capture_type="url",
        source_url="https://example.com/post",
        canonical_url="https://example.com/post",
        source_platform="web",
        source_domain="example.com",
        extraction_quality="full",
    )

    note_path, _ = extractor._save_article(
        title="Example",
        url="https://example.com/post",
        content="body",
        image_embeds=("![[attachments/20260326/image-1.jpg]]",),
        metadata=metadata,
    )

    _, body = load_frontmatter(note_path)
    assert "## 圖片" in body
    assert "![[attachments/20260326/image-1.jpg]]" in body


def test_strip_leading_timestamp_lines_from_article_content(tmp_path: Path) -> None:
    extractor = URLExtractor(make_settings(tmp_path))

    cleaned = extractor._sanitize_extracted_content(
        "2026-03-27 09:30\n\nMarch 27, 2026 9:30 AM\n\n真正內容第一段。\n\n第二段。"
    )

    assert cleaned.startswith("真正內容第一段。")
    assert "2026-03-27 09:30" not in cleaned


def test_collect_article_image_urls_from_raw_html(tmp_path: Path) -> None:
    extractor = URLExtractor(make_settings(tmp_path))

    raw_html = """
    <html><body>
    <article>
      <div class="entry-content">
        <p>Some text</p>
        <figure class="wp-block-image">
          <img src="/uploads/photo.png" width="800" height="600" />
        </figure>
        <img data-src="https://cdn.example.com/hero.jpg" width="1200" height="400" />
        <img src="/tiny-icon.gif" width="16" height="16" />
      </div>
    </article>
    </body></html>
    """

    urls = extractor._collect_article_image_urls(
        raw_html, page_url="https://example.com/post"
    )

    assert "https://example.com/uploads/photo.png" in urls
    assert "https://cdn.example.com/hero.jpg" in urls
    assert all("tiny-icon" not in u for u in urls)
