from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _env(key: str, default: str | None = None) -> str:
    value = os.getenv(key, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required env var: {key}")
    return value


def _parse_chat_ids(raw: str) -> frozenset[int]:
    chat_ids: set[int] = set()
    for chunk in raw.split(","):
        value = chunk.strip()
        if not value:
            continue
        chat_ids.add(int(value))
    if not chat_ids:
        raise RuntimeError(
            "TELEGRAM_ALLOWED_CHAT_IDS must contain at least one chat id"
        )
    return frozenset(chat_ids)


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    allowed_chat_ids: frozenset[int]
    vault_path: Path
    state_path: Path
    inbox_dir: str
    common_dir: str
    timezone: str
    note_prefix: str
    # 新增設定
    attachments_dir: str
    daily_dir: str
    daily_threshold: int
    gemini_api_key: str
    ai_auto_classify: bool
    valid_folders: frozenset[str]
    auto_move_confidence_threshold: float
    low_confidence_threshold: float
    system_tags: frozenset[str]

    @property
    def inbox_path(self) -> Path:
        return self.vault_path / self.inbox_dir

    @property
    def common_path(self) -> Path:
        return self.vault_path / self.common_dir

    @property
    def attachments_path(self) -> Path:
        return self.vault_path / self.attachments_dir

    @property
    def daily_path(self) -> Path:
        return self.vault_path / self.daily_dir


def _env_bool(key: str, default: bool = False) -> bool:
    value = os.getenv(key, "").lower()
    if value in ("true", "1", "yes"):
        return True
    if value in ("false", "0", "no", ""):
        return default
    return default


def _env_int(key: str, default: int) -> int:
    value = os.getenv(key, "")
    if not value:
        return default
    return int(value)


def _env_float(key: str, default: float) -> float:
    value = os.getenv(key, "")
    if not value:
        return default
    return float(value)


def _env_path(key: str, default: Path) -> Path:
    raw = os.getenv(key, "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        return path
    return default.resolve()


def load_settings() -> Settings:
    vault_path = Path(_env("OBSIDIAN_VAULT_PATH")).expanduser().resolve()
    if not vault_path.exists():
        raise RuntimeError(f"Vault path does not exist: {vault_path}")

    return Settings(
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        allowed_chat_ids=_parse_chat_ids(_env("TELEGRAM_ALLOWED_CHAT_IDS")),
        vault_path=vault_path,
        state_path=_env_path(
            "BOT_STATE_PATH",
            PROJECT_ROOT / ".runtime" / "telegram-state.pkl",
        ),
        inbox_dir=_env("OBSIDIAN_INBOX_DIR", "Inbox"),
        common_dir=_env("OBSIDIAN_COMMON_DIR", "常用"),
        timezone=_env("BOT_TIMEZONE", "Asia/Taipei"),
        note_prefix=_env("NOTE_PREFIX", "telegram"),
        # 新增設定
        attachments_dir=_env("OBSIDIAN_ATTACHMENTS_DIR", "attachments"),
        daily_dir=_env("OBSIDIAN_DAILY_DIR", "Daily"),
        daily_threshold=_env_int("DAILY_NOTE_THRESHOLD", 100),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        ai_auto_classify=_env_bool("AI_AUTO_CLASSIFY", False),
        valid_folders=frozenset(
            filter(
                None,
                _env(
                    "VALID_FOLDERS",
                    "stock,ai,food,佛教,Option,量化交易,job,Inbox",
                ).split(","),
            )
        ),
        auto_move_confidence_threshold=_env_float(
            "AUTO_MOVE_CONFIDENCE_THRESHOLD", 0.8
        ),
        low_confidence_threshold=_env_float("LOW_CONFIDENCE_THRESHOLD", 0.55),
        system_tags=frozenset(
            filter(
                None,
                _env(
                    "SYSTEM_TAGS",
                    "inbox,telegram,未分類,待整理,capture,capture-thought,capture-article,capture-topic,text,photo,document,url,url-fallback,web-clip,forwarded,web,instagram,facebook,threads",
                ).split(","),
            )
        ),
    )
