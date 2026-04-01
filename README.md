# Obsidian Bot

中文說明在前，English version follows below.

## 中文

Obsidian Bot 是一個用 Python 撰寫的 Telegram Bot，用來把 Telegram 訊息、網址、圖片與文件整理成 Markdown 筆記，並寫入 Obsidian vault。

### 主要功能

- 只允許白名單 chat ID 使用
- 支援 `/capture`、`/task`、`/common`、`/move`、`/review_today`、`/review_week`
- 可把長文字、網址、圖片、文件轉成筆記
- 短文字可直接追加到 Daily note
- 可從 `常用/` 目錄讀取常用筆記內容
- 可選用 Gemini 做自動分類
- 可把附件寫入 vault 的 `attachments/` 目錄

### 專案結構

- `src/obsidian_bot/`：主程式
- `tests/`：測試
- `.env.example`：環境變數範例
- `.runtime/`：執行時狀態檔（已加入 `.gitignore`）

### 環境需求

- Python 3.12+
- `uv`
- Telegram Bot Token
- 一個可寫入的 Obsidian vault 路徑

### 安裝與啟動

```bash
cd /home/ubuntu/ob-bot/bot
cp .env.example .env
# 編輯 .env，填入 TELEGRAM_BOT_TOKEN 與 TELEGRAM_ALLOWED_CHAT_IDS
uv sync
uv run obsidian-bot
```

### 測試

```bash
uv run pytest
```

### 重要環境變數

| 變數 | 說明 |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot token |
| `TELEGRAM_ALLOWED_CHAT_IDS` | 允許使用 bot 的 chat ID，逗號分隔 |
| `OBSIDIAN_VAULT_PATH` | Obsidian vault 絕對路徑 |
| `OBSIDIAN_INBOX_DIR` | 收件匣資料夾名稱，預設 `Inbox` |
| `OBSIDIAN_COMMON_DIR` | 常用筆記資料夾名稱，預設 `常用` |
| `OBSIDIAN_ATTACHMENTS_DIR` | 附件資料夾名稱，預設 `attachments` |
| `OBSIDIAN_DAILY_DIR` | Daily note 資料夾名稱，預設 `Daily` |
| `BOT_STATE_PATH` | Bot 狀態檔位置 |
| `BOT_TIMEZONE` | 時區，預設 `Asia/Taipei` |
| `NOTE_PREFIX` | 筆記檔名前綴 |
| `GEMINI_API_KEY` | Gemini API key，若要啟用 AI 分類才需要 |
| `AI_AUTO_CLASSIFY` | 是否啟用 AI 自動分類 |

### 安全提醒

- 不要把 `.env`、token、vault 內容或執行時狀態檔提交到 Git
- repo 目前只追蹤 `.env.example`，不追蹤 `.env`

---

## English

Obsidian Bot is a Python Telegram bot that turns Telegram messages, URLs, photos, and documents into Markdown notes and writes them into an Obsidian vault.

### Features

- Restricts access to allowed chat IDs only
- Supports `/capture`, `/task`, `/common`, `/move`, `/review_today`, and `/review_week`
- Converts long text, URLs, photos, and documents into notes
- Appends short plain-text messages to a Daily note
- Reads reusable notes from the `常用/` folder
- Optionally uses Gemini for auto-classification
- Stores attachments inside the vault `attachments/` directory

### Project layout

- `src/obsidian_bot/`: application source code
- `tests/`: test suite
- `.env.example`: sample environment file
- `.runtime/`: runtime state files (ignored by Git)

### Requirements

- Python 3.12+
- `uv`
- A Telegram bot token
- A writable Obsidian vault path

### Setup and run

```bash
cd /home/ubuntu/ob-bot/bot
cp .env.example .env
# edit .env and provide TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_CHAT_IDS
uv sync
uv run obsidian-bot
```

### Run tests

```bash
uv run pytest
```

### Key environment variables

| Variable | Description |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_ALLOWED_CHAT_IDS` | Allowed chat IDs, comma-separated |
| `OBSIDIAN_VAULT_PATH` | Absolute path to the Obsidian vault |
| `OBSIDIAN_INBOX_DIR` | Inbox folder name, default `Inbox` |
| `OBSIDIAN_COMMON_DIR` | Shared/common notes folder name, default `常用` |
| `OBSIDIAN_ATTACHMENTS_DIR` | Attachments folder name, default `attachments` |
| `OBSIDIAN_DAILY_DIR` | Daily notes folder name, default `Daily` |
| `BOT_STATE_PATH` | Bot state file path |
| `BOT_TIMEZONE` | Time zone, default `Asia/Taipei` |
| `NOTE_PREFIX` | Note filename prefix |
| `GEMINI_API_KEY` | Gemini API key, only needed for AI classification |
| `AI_AUTO_CLASSIFY` | Enables automatic AI classification |

### Security notes

- Do not commit `.env`, tokens, vault contents, or runtime state files
- This repository tracks `.env.example` only and ignores `.env`
