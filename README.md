# Obsidian Bot

Traditional Chinese version: [README.zh-TW.md](README.zh-TW.md)

Obsidian Bot is a polling-based Telegram bot that captures messages, links, photos, and documents from Telegram and turns them into Markdown notes inside an Obsidian vault.

It is designed for a personal knowledge workflow where quick input happens in Telegram, while long-term organization stays in Obsidian.

## Highlights

- Restricts usage to configured Telegram chat IDs
- Captures text, URLs, photos, and supported documents into Markdown notes
- Automatically appends short plain-text messages to Daily notes
- Supports guided capture modes for thoughts, article summaries, and topic notes
- Optionally uses Gemini to classify notes, suggest tags, and auto-move notes out of `Inbox`
- Reads reusable notes from `常用/` and supports structured note shortcuts such as bank info and address snippets
- Recommends credit cards from a structured Markdown note and can enrich answers with official web snippets
- Supports vault Q&A by searching existing notes
- Deduplicates by Telegram message ID and canonical URL where possible

## How it works

### Input flow

The bot listens to Telegram updates via `python-telegram-bot` polling.

Depending on the incoming content, it follows different storage paths:

- **Short plain text** from allowed chats is appended to the configured Daily note
- **Long text, forwarded content, and URLs** trigger a capture-mode flow so the content can become a quick thought, article summary, topic note, or still go to Daily
- **Photos and documents** are downloaded into the vault attachments folder and an associated Markdown note is created in `Inbox`
- **Explicit commands** can force capture, classification, movement, Q&A, or card recommendation

### Note organization

By default, newly captured notes land in `Inbox`.

If AI classification is enabled and confidence is high enough, the bot can automatically move the note into one of the configured folders. Otherwise, the note stays in `Inbox` and the bot provides manual move options.

Default folder set in code:

- `stock`
- `ai`
- `food`
- `佛教`
- `Option`
- `量化交易`
- `job`
- `Inbox`

### Capture modes

When content needs more structure, the bot can prepare different note templates:

- **Thought**: quick capture for ideas or rough notes
- **Article**: summary-oriented structure with key points and optional extracted images
- **Topic**: topic note structure that can link related notes from the vault
- **Daily**: skip structured capture and append directly to the Daily note

## Supported commands

| Command | Description |
| --- | --- |
| `/start` | Show the bot overview and available actions |
| `/health` | Basic health check |
| `/capture <text>` | Force text into an Inbox-style capture flow |
| `/task <text>` | Append a task to the Daily note |
| `/task @明天 <text>` | Append a task to a date-adjusted Daily note |
| `/common` | List common notes from `常用/` |
| `/reload_common` | Reload common-note access flow |
| `/card <merchant or question>` | Recommend a credit card or answer card-related questions |
| `/ask <question>` | Ask questions against notes already stored in the vault |
| `/url <url>` | Capture a URL through the structured URL flow |
| `/classify` | Re-run AI classification for the most recently captured note |
| `/move <folder>` | Manually move the latest captured note |

## Feature details

### 1. URL capture and article extraction

URL handling is implemented in `src/obsidian_bot/url_extractor.py`.

The bot:

- fetches pages with retry logic
- extracts readable article content with Readability
- converts HTML into Markdown
- attempts to preserve article images into the attachments directory
- deduplicates captures using canonical URLs when available

### 2. Media and document capture

Media handling is implemented in `src/obsidian_bot/media_handler.py`.

Supported image extensions:

- `.jpg`
- `.jpeg`
- `.png`
- `.gif`
- `.webp`
- `.heic`

Supported document extensions:

- `.pdf`
- `.doc`
- `.docx`
- `.txt`
- `.csv`
- `.xlsx`
- `.xls`

Downloaded files are stored under the configured attachments directory, grouped by date, and linked from a generated note in `Inbox`.

### 3. AI classification and tag suggestions

AI classification is implemented in `src/obsidian_bot/ai_classifier.py`.

When `GEMINI_API_KEY` is present and `AI_AUTO_CLASSIFY=true`:

- the bot asks Gemini to choose the best target folder
- existing tags from the vault are preferred
- the model can propose new tags when necessary
- high-confidence notes may be auto-moved
- low-confidence notes stay in `Inbox` and are marked for review

### 4. Common notes and structured snippets

`src/obsidian_bot/common_notes.py` supports loading reusable notes from the `常用/` directory.

Special structured handling exists for:

- `銀行資訊`
- `地址`
- `信用卡`

This allows the bot to answer with copy-friendly sections instead of dumping an entire note every time.

### 5. Credit-card recommendation workflow

Credit-card support combines:

- structured card data parsed from a Markdown note in `常用/`
- local recommendation scoring in `src/obsidian_bot/card_recommender.py`
- optional official-web context lookup in `src/obsidian_bot/web_lookup.py`
- AI summarization logic in `src/obsidian_bot/ai_classifier.py`

This makes `/card` useful for both “which card should I use here?” and follow-up card comparison questions.

### 6. Vault search and note Q&A

`src/obsidian_bot/vault_adapter.py` maintains a lightweight filesystem-backed index of Markdown notes.

The index is used for:

- searching existing notes
- locating notes by Telegram message ID
- locating notes by canonical URL
- gathering available tags for AI classification
- powering note-based Q&A flows

## Project structure

```text
bot/
├── .env.example
├── pyproject.toml
├── src/obsidian_bot/
│   ├── ai_classifier.py
│   ├── card_recommender.py
│   ├── common_notes.py
│   ├── config.py
│   ├── daily_note.py
│   ├── handlers.py
│   ├── http_utils.py
│   ├── main.py
│   ├── media_handler.py
│   ├── note_writer.py
│   ├── url_extractor.py
│   ├── vault_adapter.py
│   └── web_lookup.py
├── tests/
└── uv.lock
```

## Requirements

- Python 3.12+
- `uv`
- A Telegram bot token
- A writable Obsidian vault path
- Optional: Gemini API key for AI classification

## Setup

```bash
cd /home/ubuntu/ob-bot/bot
cp .env.example .env
```

Then edit `.env` and fill in the required values.

Install dependencies:

```bash
uv sync
```

Run the bot:

```bash
uv run obsidian-bot
```

## Development

Run tests:

```bash
uv run pytest
```

Run linting if you have dev dependencies installed:

```bash
uv run ruff check
```

## Environment variables

### Required

| Variable | Description |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_ALLOWED_CHAT_IDS` | Allowed chat IDs, comma-separated |
| `OBSIDIAN_VAULT_PATH` | Absolute path to the Obsidian vault |

### Core paths and behavior

| Variable | Description | Default |
| --- | --- | --- |
| `BOT_STATE_PATH` | Bot persistence file path | `.runtime/telegram-state.pkl` |
| `OBSIDIAN_INBOX_DIR` | Inbox folder for newly captured notes | `Inbox` |
| `OBSIDIAN_COMMON_DIR` | Folder for reusable/common notes | `常用` |
| `OBSIDIAN_ATTACHMENTS_DIR` | Folder for downloaded media and files | `attachments` |
| `OBSIDIAN_DAILY_DIR` | Folder for Daily notes | `Daily` |
| `BOT_TIMEZONE` | Bot timezone | `Asia/Taipei` |
| `NOTE_PREFIX` | Prefix used in generated note names | `telegram` |
| `DAILY_NOTE_THRESHOLD` | Heuristic threshold for when text should go through structured capture instead of Daily append | `100` |

### AI and classification

| Variable | Description | Default |
| --- | --- | --- |
| `GEMINI_API_KEY` | Gemini API key | empty |
| `AI_AUTO_CLASSIFY` | Enable AI-based auto classification | `true` or `false` depending on your `.env` |
| `VALID_FOLDERS` | Comma-separated folder whitelist for classification and manual moves | code default list |
| `AUTO_MOVE_CONFIDENCE_THRESHOLD` | Minimum confidence for auto-moving a note | `0.8` |
| `LOW_CONFIDENCE_THRESHOLD` | Threshold used to surface low-confidence handling | `0.55` |
| `SYSTEM_TAGS` | Reserved/system tags excluded from reusable tag suggestions | code default list |

## Security notes

- Do not commit `.env`, API keys, Telegram tokens, vault contents, or runtime state files
- This repository tracks `.env.example` only and ignores `.env`
- `.venv/`, `.runtime/`, `.pytest_cache/`, and `.ruff_cache/` should remain ignored
- If you use a real personal vault, keep the vault itself outside this repository unless you explicitly want it versioned

## Tested areas

The test suite currently covers behavior such as:

- AI classification decision parsing and metadata updates
- capture-mode template generation
- credit-card recommendation behavior
- text handling and handler behavior
- HTTP retry behavior
- note lookup and deduplication
- note output generation

## Notes and limitations

- The bot currently uses polling instead of webhooks
- AI features are optional and degrade gracefully when `GEMINI_API_KEY` is not configured
- Some workflows are optimized for a personal Obsidian setup, especially the folder names and common-note structure
