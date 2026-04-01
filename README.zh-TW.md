# Obsidian Bot（繁體中文）

English version: [README.md](README.md)

Obsidian Bot 是一個以 polling 模式運作的 Telegram Bot，會把 Telegram 內的文字、網址、照片與文件整理成 Markdown 筆記，並寫入 Obsidian vault。

它的定位是把「快速輸入」放在 Telegram，把「長期整理與查找」放在 Obsidian，適合個人知識管理工作流。

## 亮點

- 只允許設定過的 Telegram chat ID 使用
- 可把文字、網址、照片與支援文件轉成 Markdown 筆記
- 短純文字可自動追加到 Daily note
- 支援引導式 capture mode，可整理成隨手想法、文章摘要、主題筆記
- 可選用 Gemini 做筆記分類、tag 建議與自動移動
- 可從 `常用/` 讀取可重複使用的筆記，並支援銀行資訊、地址等結構化片段
- 可根據結構化信用卡筆記做刷卡推薦，並結合官網片段補充判斷
- 可針對 vault 內既有筆記做問答
- 會盡量用 Telegram message ID 與 canonical URL 做去重

## 整體運作方式

### 輸入流程

Bot 透過 `python-telegram-bot` 的 polling 接收 Telegram 更新。

收到不同內容時，會走不同的處理路徑：

- **短純文字**：直接追加到設定的 Daily note
- **長文字、轉傳內容、網址**：先進入 capture mode，讓你決定要存成隨手想法、文章摘要、主題筆記，或仍然寫進 Daily
- **照片與文件**：先下載到 vault 的附件目錄，再建立一篇對應的 Inbox 筆記
- **明確指令**：可強制做 capture、分類、搬移、問答或信用卡推薦

### 筆記整理邏輯

新建立的筆記預設會先進 `Inbox`。

如果啟用了 AI 分類，而且信心度夠高，Bot 會自動把筆記移到對應資料夾；若信心不足，就先留在 `Inbox`，並提供手動移動按鈕或 `/move` 流程。

程式內目前的預設分類資料夾為：

- `stock`
- `ai`
- `food`
- `佛教`
- `Option`
- `量化交易`
- `job`
- `Inbox`

### Capture mode

當內容需要比一般筆記更有結構時，Bot 會先產生不同模板：

- **隨手想法**：快速收錄想法或未整理內容
- **文章摘要**：偏向摘要與重點整理，也能帶入擷取到的圖片
- **主題筆記**：整理某一主題，並可自動連到 vault 內相關筆記
- **Daily**：略過結構化模板，直接寫進 Daily note

## 支援指令

| 指令 | 說明 |
| --- | --- |
| `/start` | 顯示 bot 總覽與可用功能 |
| `/health` | 基本健康檢查 |
| `/capture <文字>` | 強制把文字走 Inbox capture 流程 |
| `/task <文字>` | 把任務追加到 Daily note |
| `/task @明天 <文字>` | 把任務追加到指定日期偏移的 Daily note |
| `/common` | 列出 `常用/` 中可直接取用的筆記 |
| `/reload_common` | 重新整理常用筆記取用流程 |
| `/card <店家或問題>` | 推薦信用卡，或回答信用卡相關問題 |
| `/ask <問題>` | 針對 vault 內既有筆記做問答 |
| `/url <網址>` | 用結構化流程收錄網址 |
| `/classify` | 重新分析最近一筆筆記的 AI 分類 |
| `/move <資料夾>` | 手動移動最近一筆筆記 |

## 功能細節

### 1. 網址收錄與文章擷取

網址處理主要在 `src/obsidian_bot/url_extractor.py`。

Bot 會：

- 以 retry logic 抓取網頁
- 用 Readability 擷取主要文章內容
- 把 HTML 轉成 Markdown
- 嘗試保留文章圖片並寫入附件資料夾
- 盡量以 canonical URL 避免重複收錄

### 2. 照片與文件收錄

媒體處理主要在 `src/obsidian_bot/media_handler.py`。

支援的圖片副檔名：

- `.jpg`
- `.jpeg`
- `.png`
- `.gif`
- `.webp`
- `.heic`

支援的文件副檔名：

- `.pdf`
- `.doc`
- `.docx`
- `.txt`
- `.csv`
- `.xlsx`
- `.xls`

下載後的檔案會依日期放進附件目錄，並在 `Inbox` 生成一篇連回附件的 Markdown 筆記。

### 3. AI 分類與 tag 建議

AI 分類實作在 `src/obsidian_bot/ai_classifier.py`。

當 `GEMINI_API_KEY` 存在且 `AI_AUTO_CLASSIFY=true` 時：

- Bot 會讓 Gemini 選擇最適合的目標資料夾
- 優先沿用 vault 內已存在的 tags
- 若真的需要，模型可以提出新 tag 建議
- 高信心度時可自動搬移筆記
- 低信心度時保留在 `Inbox` 並標示需要複核

### 4. 常用筆記與結構化片段

`src/obsidian_bot/common_notes.py` 會從 `常用/` 載入可重複使用的筆記。

目前有特別支援結構化解析的項目：

- `銀行資訊`
- `地址`
- `信用卡`

因此 Bot 可以回傳比較容易複製的小段內容，而不是每次都丟整篇筆記。

### 5. 信用卡推薦流程

信用卡功能結合了：

- `常用/` 內的結構化信用卡 Markdown 筆記
- `src/obsidian_bot/card_recommender.py` 的本地推薦打分邏輯
- `src/obsidian_bot/web_lookup.py` 的官網片段抓取
- `src/obsidian_bot/ai_classifier.py` 內的 AI 彙整邏輯

所以 `/card` 不只可回答「這家刷哪張」，也能處理後續的比較、保留或取消等問題。

### 6. Vault 搜尋與筆記問答

`src/obsidian_bot/vault_adapter.py` 會維護一個以檔案系統為基礎的 Markdown 索引。

這個索引會用在：

- 搜尋既有筆記
- 用 Telegram message ID 找已存在筆記
- 用 canonical URL 找已存在筆記
- 收集 AI 分類可用的既有 tags
- 支援筆記問答流程

## 專案結構

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

## 環境需求

- Python 3.12+
- `uv`
- Telegram Bot Token
- 一個可寫入的 Obsidian vault 路徑
- 若要啟用 AI 分類，另外需要 Gemini API key

## 安裝

```bash
cd /home/ubuntu/ob-bot/bot
cp .env.example .env
```

接著編輯 `.env`，填入必要環境變數。

安裝依賴：

```bash
uv sync
```

啟動 Bot：

```bash
uv run obsidian-bot
```

## 開發

執行測試：

```bash
uv run pytest
```

若有安裝 dev 依賴，也可跑 lint：

```bash
uv run ruff check
```

## 環境變數

### 必填

| 變數 | 說明 |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot token |
| `TELEGRAM_ALLOWED_CHAT_IDS` | 允許使用 bot 的 chat ID，逗號分隔 |
| `OBSIDIAN_VAULT_PATH` | Obsidian vault 的絕對路徑 |

### 核心路徑與行為

| 變數 | 說明 | 預設值 |
| --- | --- | --- |
| `BOT_STATE_PATH` | Bot persistence 狀態檔位置 | `.runtime/telegram-state.pkl` |
| `OBSIDIAN_INBOX_DIR` | 新筆記預設收件匣資料夾 | `Inbox` |
| `OBSIDIAN_COMMON_DIR` | 常用筆記資料夾 | `常用` |
| `OBSIDIAN_ATTACHMENTS_DIR` | 附件與下載檔案資料夾 | `attachments` |
| `OBSIDIAN_DAILY_DIR` | Daily note 資料夾 | `Daily` |
| `BOT_TIMEZONE` | Bot 時區 | `Asia/Taipei` |
| `NOTE_PREFIX` | 產生筆記檔名時使用的前綴 | `telegram` |
| `DAILY_NOTE_THRESHOLD` | 判斷文字應走結構化 capture 還是直接進 Daily 的門檻 | `100` |

### AI 與分類

| 變數 | 說明 | 預設值 |
| --- | --- | --- |
| `GEMINI_API_KEY` | Gemini API key | 空值 |
| `AI_AUTO_CLASSIFY` | 是否啟用 AI 自動分類 | 依 `.env` 設定 |
| `VALID_FOLDERS` | 分類與手動搬移可用的資料夾白名單 | 程式內建預設清單 |
| `AUTO_MOVE_CONFIDENCE_THRESHOLD` | 自動搬移所需最低信心值 | `0.8` |
| `LOW_CONFIDENCE_THRESHOLD` | 判定低信心結果的門檻 | `0.55` |
| `SYSTEM_TAGS` | 不應被當作一般可重用 tag 的系統 tags | 程式內建預設清單 |

## 安全提醒

- 不要把 `.env`、API keys、Telegram token、vault 內容或 runtime 狀態檔提交到 Git
- 這個 repo 目前只追蹤 `.env.example`，不追蹤 `.env`
- `.venv/`、`.runtime/`、`.pytest_cache/`、`.ruff_cache/` 應持續維持在 ignore 狀態
- 如果你用的是私人 Obsidian vault，除非你真的要版本化它，否則不要把 vault 本身放進這個 repo

## 已有測試涵蓋範圍

目前測試大致涵蓋：

- AI 分類結果解析與 frontmatter 更新
- capture mode 模板生成
- 信用卡推薦邏輯
- handler 對訊息的處理行為
- HTTP retry 行為
- 筆記查找與去重
- 筆記輸出格式

## 備註與限制

- 目前使用 polling，不是 webhook
- AI 功能是選配，未設定 `GEMINI_API_KEY` 時仍可使用核心收錄流程
- 有些流程是針對個人化 Obsidian 結構優化，特別是資料夾名稱與 `常用/` 的結構化筆記格式
