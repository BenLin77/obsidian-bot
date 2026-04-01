# Obsidian Bot

A Python Telegram bot scaffold that writes Markdown notes into `/home/ubuntu/ob-bot/obsidian`.

## Current scaffold

- `python-telegram-bot` polling bot
- allowed-chat guard
- `/capture` and `/task` can be triggered from Telegram keyboard buttons
- `銀行資訊` and `地址` can be opened from buttons and then drilled down into copy-friendly sub-items
- long text, URLs, photos and documents are saved as notes and auto-classified by AI
- long text and URLs can be captured as `隨手想法` / `文章摘要` / `主題筆記`
- `/capture <text>` writes a note and then auto-classifies it
- short plain text messages from allowed chats are appended to `Daily/`
- `/common` shows note names from `常用/`
- `/review_today` and `/review_week` summarize what still needs to be organized
- `/move` provides folder buttons for one-tap reclassification when needed
- tapping or typing a common-note name returns its content for easy copy/paste

## Setup

```bash
cd /home/ubuntu/ob-bot/bot
cp .env.example .env
# fill in TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_CHAT_IDS
uv sync
uv run obsidian-bot
```

## Env vars

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_CHAT_IDS`
- `BOT_STATE_PATH`
- `OBSIDIAN_VAULT_PATH`
- `OBSIDIAN_INBOX_DIR`
- `OBSIDIAN_COMMON_DIR`
- `BOT_TIMEZONE`
- `NOTE_PREFIX`
