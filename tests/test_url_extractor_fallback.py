from pathlib import Path

from obsidian_bot.config import Settings
from obsidian_bot.url_extractor import URLExtractor


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
    settings.attachments_path.mkdir(parents=True, exist_ok=True)
    return settings


def test_extract_fallback_content_html_promotes_prompt_blocks(tmp_path: Path) -> None:
    extractor = URLExtractor(make_settings(tmp_path))
    raw_html = """
    <html><body>
      <div class="container">
        <div class="phase-header"><div class="phase-num">1</div><div class="phase-title">一行安裝</div></div>
        <div class="card">
          <div class="label">核心指令</div>
          <h3>一行安裝 gstack</h3>
          <div class="prompt" id="p1"><button class="copy">複製</button>git clone foo && ./setup</div>
          <table><tr><th>技能</th><th>說明</th></tr><tr><td><code>/qa</code></td><td>自動 QA</td></tr></table>
        </div>
      </div>
    </body></html>
    """

    fallback_html = extractor._extract_fallback_content_html(raw_html)

    assert "git clone foo &amp;&amp; ./setup" in fallback_html
    assert "<pre><code>" in fallback_html
    assert "<button" not in fallback_html
    assert "phase-num" not in fallback_html
    assert "<table>" in fallback_html


def test_should_use_fallback_when_summary_drops_most_content(tmp_path: Path) -> None:
    extractor = URLExtractor(make_settings(tmp_path))
    raw_html = (
        """
    <html><body>
      <div class="container">
        <div class="card"><p>開頭說明。</p></div>
        <div class="prompt">git clone foo && ./setup</div>
        <div class="card"><p>這裡有很多很多內容。</p><p>"""
        + ("更多內容。" * 300)
        + """</p></div>
      </div>
    </body></html>
    """
    )
    summary_html = "<div><p>開頭說明。</p></div>"
    fallback_html = extractor._extract_fallback_content_html(raw_html)

    assert (
        extractor._should_use_fallback_content(
            raw_html=raw_html,
            summary_html=summary_html,
            fallback_html=fallback_html,
        )
        is True
    )


def test_should_not_use_fallback_for_reasonable_summary(tmp_path: Path) -> None:
    extractor = URLExtractor(make_settings(tmp_path))
    raw_html = (
        "<html><body><article><h1>標題</h1><p>"
        + ("內容。" * 300)
        + "</p></article></body></html>"
    )
    summary_html = "<article><h1>標題</h1><p>" + ("內容。" * 240) + "</p></article>"
    fallback_html = "<article><h1>標題</h1><p>" + ("內容。" * 260) + "</p></article>"

    assert (
        extractor._should_use_fallback_content(
            raw_html=raw_html,
            summary_html=summary_html,
            fallback_html=fallback_html,
        )
        is False
    )
