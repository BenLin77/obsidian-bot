from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .ai_classifier import AIClassifier
from .capture_modes import CaptureMode, capture_mode_label, prepare_capture
from .common_notes import (
    find_common_note,
    find_structured_common_note_by_key,
    list_common_notes,
    load_credit_cards,
    parse_structured_common_note,
    read_common_note_text,
)
from .card_recommender import recommend_cards
from .config import Settings
from .daily_note import DailyNoteWriter
from .media_handler import MediaHandler
from .note_writer import NoteWriter
from .note_metadata import (
    CaptureMetadata,
    canonicalize_url,
    domain_from_url,
    platform_from_url,
    upsert_note_metadata,
)
from .url_extractor import URLExtractor
from .vault_adapter import NoteSearchResult, VaultAdapter
from .web_lookup import OfficialWebLookup

logger = logging.getLogger(__name__)
_SETTINGS_KEY = "settings"
_WRITER_KEY = "writer"
_DAILY_KEY = "daily"
_MEDIA_KEY = "media"
_URL_KEY = "url"
_AI_KEY = "ai"
_VAULT_KEY = "vault"
_WEB_KEY = "web"
_PENDING_ACTION_KEY = "pending_action"
_PENDING_QUESTION_KEY = "pending_question"
_PENDING_CAPTURE_KEY = "pending_capture"
_PENDING_TAG_APPROVAL_KEY = "pending_tag_approval"
_COMMON_CALLBACK_PREFIX = "common:"
_MOVE_CALLBACK_PREFIX = "move:"
_MODE_CALLBACK_PREFIX = "mode:"
_CAPTURE_CALLBACK_PREFIX = "capture:"
_TAG_CALLBACK_PREFIX = "tag:"
_CANCEL_BUTTON = "取消"
_CARD_BUTTON = "信用卡推薦"
_ASK_BUTTON = "筆記問答"

_MAIN_MENU_BUTTONS = [
    ["銀行資訊", "地址"],
    [_CARD_BUTTON, _ASK_BUTTON],
]
_QUESTION_MARKERS = (
    "？",
    "?",
    "嗎",
    "呢",
    "何時",
    "怎麼",
    "如何",
    "有沒有",
    "可不可以",
    "能不能",
)
_CARD_QUESTION_KEYWORDS = (
    "信用卡",
    "卡片",
    "刷哪張",
    "哪張卡",
    "哪一張",
    "回饋",
    "哩程",
    "剪卡",
    "取消",
    "保留",
    "重複",
    "推薦",
)
_CARD_MERCHANT_DISQUALIFIERS = (
    "有沒有",
    "可不可以",
    "能不能",
    "哪張",
    "哪一張",
    "刷哪",
    "推薦我",
    "推薦哪",
    "取消",
    "剪卡",
    "保留",
    "重複",
)


@dataclass(frozen=True)
class AppServices:
    settings: Settings
    writer: NoteWriter
    daily: DailyNoteWriter
    media: MediaHandler
    url: URLExtractor
    ai: AIClassifier
    vault: VaultAdapter
    web: OfficialWebLookup


@dataclass(frozen=True)
class PendingAction:
    kind: Literal["capture", "task", "card", "ask"]
    target_modifier: str | None = None


@dataclass(frozen=True)
class PendingCaptureRequest:
    text: str
    metadata: CaptureMetadata


@dataclass(frozen=True)
class PendingTagApproval:
    note_path: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class AutoClassifyOutcome:
    relative_path: Path
    note_path: Path
    message: str
    proposed_new_tags: tuple[str, ...]


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("health", health_command))
    application.add_handler(CommandHandler("capture", capture_command))
    application.add_handler(CommandHandler("common", common_command))
    application.add_handler(CommandHandler("reload_common", common_command))
    application.add_handler(CommandHandler("card", card_command))
    application.add_handler(CommandHandler("ask", ask_command))
    application.add_handler(CommandHandler("task", task_command))
    application.add_handler(CommandHandler("url", url_command))
    application.add_handler(CommandHandler("classify", classify_command))
    application.add_handler(CommandHandler("move", move_command))
    application.add_handler(
        CallbackQueryHandler(
            common_callback_handler, pattern=rf"^{_COMMON_CALLBACK_PREFIX}"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            move_callback_handler, pattern=rf"^{_MOVE_CALLBACK_PREFIX}"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            mode_callback_handler, pattern=rf"^{_MODE_CALLBACK_PREFIX}"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            capture_callback_handler, pattern=rf"^{_CAPTURE_CALLBACK_PREFIX}"
        )
    )
    application.add_handler(
        CallbackQueryHandler(tag_callback_handler, pattern=rf"^{_TAG_CALLBACK_PREFIX}")
    )
    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    application.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler)
    )


def store_services(application: Application, services: AppServices) -> None:
    application.bot_data[_SETTINGS_KEY] = services.settings
    application.bot_data[_WRITER_KEY] = services.writer
    application.bot_data[_DAILY_KEY] = services.daily
    application.bot_data[_MEDIA_KEY] = services.media
    application.bot_data[_URL_KEY] = services.url
    application.bot_data[_AI_KEY] = services.ai
    application.bot_data[_VAULT_KEY] = services.vault
    application.bot_data[_WEB_KEY] = services.web


def _services(context: ContextTypes.DEFAULT_TYPE) -> AppServices:
    settings = context.application.bot_data[_SETTINGS_KEY]
    writer = context.application.bot_data[_WRITER_KEY]
    daily = context.application.bot_data[_DAILY_KEY]
    media = context.application.bot_data[_MEDIA_KEY]
    url = context.application.bot_data[_URL_KEY]
    ai = context.application.bot_data[_AI_KEY]
    vault = context.application.bot_data[_VAULT_KEY]
    web = context.application.bot_data[_WEB_KEY]
    if (
        not isinstance(settings, Settings)
        or not isinstance(writer, NoteWriter)
        or not isinstance(daily, DailyNoteWriter)
        or not isinstance(media, MediaHandler)
        or not isinstance(url, URLExtractor)
        or not isinstance(ai, AIClassifier)
        or not isinstance(vault, VaultAdapter)
        or not isinstance(web, OfficialWebLookup)
    ):
        raise RuntimeError("Application services are not initialized")
    return AppServices(
        settings=settings,
        writer=writer,
        daily=daily,
        media=media,
        url=url,
        ai=ai,
        vault=vault,
        web=web,
    )


def _allowed(update: Update, settings: Settings) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.id in settings.allowed_chat_ids


def _main_menu_markup() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        _MAIN_MENU_BUTTONS,
        resize_keyboard=True,
        input_field_placeholder="直接轉傳內容，或輸入指令",
    )


def _cancel_markup() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[_CANCEL_BUTTON]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="輸入內容，或按取消",
    )


def _pending_action(
    context: ContextTypes.DEFAULT_TYPE,
) -> PendingAction | None:
    action = context.user_data.get(_PENDING_ACTION_KEY)
    if isinstance(action, PendingAction):
        return action
    return None


def _set_pending_action(
    context: ContextTypes.DEFAULT_TYPE, action: PendingAction | None
) -> None:
    if action is None:
        context.user_data.pop(_PENDING_ACTION_KEY, None)
        return
    context.user_data[_PENDING_ACTION_KEY] = action


def _pending_question(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    question = context.user_data.get(_PENDING_QUESTION_KEY)
    return question if isinstance(question, str) and question.strip() else None


def _set_pending_question(
    context: ContextTypes.DEFAULT_TYPE,
    question: str | None,
) -> None:
    if question is None:
        context.user_data.pop(_PENDING_QUESTION_KEY, None)
        return
    context.user_data[_PENDING_QUESTION_KEY] = question


def _pending_capture(
    context: ContextTypes.DEFAULT_TYPE,
) -> PendingCaptureRequest | None:
    request = context.user_data.get(_PENDING_CAPTURE_KEY)
    if isinstance(request, PendingCaptureRequest):
        return request
    return None


def _set_pending_capture(
    context: ContextTypes.DEFAULT_TYPE,
    request: PendingCaptureRequest | None,
) -> None:
    if request is None:
        context.user_data.pop(_PENDING_CAPTURE_KEY, None)
        return
    context.user_data[_PENDING_CAPTURE_KEY] = request


def _pending_tag_approval(
    context: ContextTypes.DEFAULT_TYPE,
) -> PendingTagApproval | None:
    approval = context.user_data.get(_PENDING_TAG_APPROVAL_KEY)
    if isinstance(approval, PendingTagApproval):
        return approval
    return None


def _set_pending_tag_approval(
    context: ContextTypes.DEFAULT_TYPE,
    approval: PendingTagApproval | None,
) -> None:
    if approval is None:
        context.user_data.pop(_PENDING_TAG_APPROVAL_KEY, None)
        return
    context.user_data[_PENDING_TAG_APPROVAL_KEY] = approval


def _tag_approval_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "加入新 tag", callback_data=f"{_TAG_CALLBACK_PREFIX}approve"
                ),
                InlineKeyboardButton(
                    "先不要", callback_data=f"{_TAG_CALLBACK_PREFIX}skip"
                ),
            ]
        ]
    )


def _start_text() -> str:
    return (
        "Obsidian bot 已啟動\n\n"
        "直接轉傳文章、網址、圖片或檔案給我，我會先寫入 Inbox，再自動分類。\n"
        "一般短文字會追加到 Daily；轉傳內容不會被當成 Daily。\n\n"
        "/task <內容> - 建立任務到 Daily Note\n"
        "/task @明天 <內容> - 建立明天的任務\n"
        "/common - 顯示常用筆記\n"
        "/card <店家> - 推薦最適合的信用卡\n"
        "/ask <問題> - 問我 vault 裡已有的筆記內容\n"
        "/capture <內容> - 強制寫入 Inbox\n\n"
        "銀行資訊 / 地址 / 信用卡推薦 / 筆記問答 可直接用下方按鈕開啟\n"
        "如果你直接輸入問句，我會先讓你選是信用卡挑選、正常問答，還是存到 Daily\n"
        "若 AI 暫時無法判斷，筆記會先留在 Inbox，並只保留 inbox tag"
    )


def _parse_task_args(
    daily: DailyNoteWriter, args: list[str]
) -> tuple[datetime | None, str, str | None]:
    target_date = None
    target_modifier: str | None = None
    task_text_parts: list[str] = []

    for arg in args:
        if arg.startswith("@"):
            modifier = arg[1:]
            parsed = daily.parse_date_modifier(modifier)
            if parsed is not None:
                target_date = parsed
                target_modifier = modifier
                continue
        task_text_parts.append(arg)

    return target_date, " ".join(task_text_parts).strip(), target_modifier


def _ordered_folders(
    valid_folders: frozenset[str], preferred: str | None = None
) -> list[str]:
    folders = sorted(valid_folders, key=lambda name: name.casefold())
    if preferred is not None and preferred in folders:
        folders.remove(preferred)
        folders.insert(0, preferred)
    return folders


def _move_markup(
    valid_folders: frozenset[str], preferred: str | None = None
) -> InlineKeyboardMarkup:
    ordered = _ordered_folders(valid_folders, preferred)
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for folder in ordered:
        row.append(
            InlineKeyboardButton(
                folder,
                callback_data=f"{_MOVE_CALLBACK_PREFIX}{folder}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _relative_note_path(settings: Settings, note_path: Path) -> Path:
    try:
        return note_path.relative_to(settings.vault_path)
    except ValueError:
        return note_path


def _format_classify_message(result) -> str:
    tag_text = "、".join(result.suggested_tags) if result.suggested_tags else "無"
    review_hint = "需要複核" if result.needs_review else "可先相信 AI"
    new_tag_hint = (
        f"，待確認新 tag: {'、'.join(result.proposed_new_tags)}"
        if getattr(result, "proposed_new_tags", ())
        else ""
    )
    return f"\nAI：{result.suggested_folder}（信心 {result.confidence:.2f}，tags: {tag_text}，{review_hint}{new_tag_hint}）"


def _schedule_tag_approval(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    note_path: Path,
    proposed_new_tags: tuple[str, ...],
) -> str | None:
    if not proposed_new_tags:
        _set_pending_tag_approval(context, None)
        return None
    _set_pending_tag_approval(
        context,
        PendingTagApproval(note_path=str(note_path), tags=proposed_new_tags),
    )
    return f"AI 建議新增 tag：{'、'.join(proposed_new_tags)}。要加入這些新 tag 嗎？"


def _looks_like_question(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    return any(marker in normalized for marker in _QUESTION_MARKERS)


def _looks_like_credit_card_question(text: str) -> bool:
    normalized = text.strip().casefold()
    if not normalized:
        return False
    if "信用卡推薦" == text.strip():
        return False
    keyword_hit = any(
        keyword.casefold() in normalized for keyword in _CARD_QUESTION_KEYWORDS
    )
    return keyword_hit and _looks_like_question(text)


def _looks_like_card_merchant_input(text: str) -> bool:
    normalized = text.strip()
    if not normalized or len(normalized) > 40 or "\n" in normalized:
        return False
    if _looks_like_question(normalized):
        return False
    lowered = normalized.casefold()
    return not any(
        keyword.casefold() in lowered for keyword in _CARD_MERCHANT_DISQUALIFIERS
    )


def _format_note_sources(results: tuple[NoteSearchResult, ...]) -> str:
    if not results:
        return ""
    lines = ["參考筆記："]
    for result in results:
        lines.append(f"- {result.title} ({result.relative_path})")
    return "\n".join(lines)


def _select_cards_for_web_lookup(
    *,
    question: str,
    cards: tuple,
    preferred_names: tuple[str, ...] = (),
) -> tuple:
    selected: list = []
    seen: set[str] = set()

    for preferred_name in preferred_names:
        for card in cards:
            if card.name != preferred_name or card.name in seen:
                continue
            selected.append(card)
            seen.add(card.name)
            break

    normalized = question.casefold()
    for card in cards:
        if card.name in seen:
            continue
        if card.name.casefold() in normalized:
            selected.append(card)
            seen.add(card.name)
            continue
        joined_keywords = " ".join(
            [*card.merchant_keywords, *card.applicable_categories]
        ).casefold()
        if any(term in joined_keywords for term in normalized.split()):
            selected.append(card)
            seen.add(card.name)

    if not selected:
        selected.extend(card for card in cards[:4])
    return tuple(selected[:6])


def _format_web_context_summary(web_context: tuple) -> str:
    if not web_context:
        return ""
    lines = ["最新官網線索："]
    for item in web_context[:3]:
        lines.append(f"- {item.card_name}：{item.title}")
    return "\n".join(lines)


def _question_mode_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "信用卡挑選", callback_data=f"{_MODE_CALLBACK_PREFIX}card"
                ),
                InlineKeyboardButton(
                    "正常問答", callback_data=f"{_MODE_CALLBACK_PREFIX}ask"
                ),
            ],
            [
                InlineKeyboardButton(
                    "存到 Daily", callback_data=f"{_MODE_CALLBACK_PREFIX}daily"
                ),
            ],
        ]
    )


def _capture_mode_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "隨手想法", callback_data=f"{_CAPTURE_CALLBACK_PREFIX}thought"
                ),
                InlineKeyboardButton(
                    "文章摘要", callback_data=f"{_CAPTURE_CALLBACK_PREFIX}article"
                ),
            ],
            [
                InlineKeyboardButton(
                    "主題筆記", callback_data=f"{_CAPTURE_CALLBACK_PREFIX}topic"
                ),
                InlineKeyboardButton(
                    "存到 Daily", callback_data=f"{_CAPTURE_CALLBACK_PREFIX}daily"
                ),
            ],
            [
                InlineKeyboardButton(
                    "取消", callback_data=f"{_CAPTURE_CALLBACK_PREFIX}cancel"
                ),
            ],
        ]
    )


async def _prompt_capture_mode(
    *,
    message,
    context: ContextTypes.DEFAULT_TYPE,
    request: PendingCaptureRequest,
    intro: str,
) -> None:
    _set_pending_capture(context, request)
    await message.reply_text(intro, reply_markup=_capture_mode_markup())


async def _auto_classify_note(
    *,
    services: AppServices,
    context: ContextTypes.DEFAULT_TYPE,
    title: str,
    absolute_path: Path,
    relative_path: Path,
) -> AutoClassifyOutcome:
    services.writer.remember_captured_note(
        title=title,
        absolute_path=absolute_path,
        relative_path=relative_path,
    )

    if not services.ai.is_available:
        _set_pending_tag_approval(context, None)
        return AutoClassifyOutcome(
            relative_path=relative_path,
            note_path=absolute_path,
            message="",
            proposed_new_tags=(),
        )

    result = await services.ai.classify_and_move(absolute_path, auto_move=True)
    if result is None:
        _set_pending_tag_approval(context, None)
        return AutoClassifyOutcome(
            relative_path=relative_path,
            note_path=absolute_path,
            message="\nAI 自動分類失敗，先留在 Inbox。",
            proposed_new_tags=(),
        )

    if result.moved and result.new_path is not None:
        new_relative_path = _relative_note_path(services.settings, result.new_path)
        services.writer.remember_captured_note(
            title=title,
            absolute_path=result.new_path,
            relative_path=new_relative_path,
        )
        return AutoClassifyOutcome(
            relative_path=new_relative_path,
            note_path=result.new_path,
            message=f"\n已自動分類到 {result.suggested_folder}/。{_format_classify_message(result)}",
            proposed_new_tags=result.proposed_new_tags,
        )

    services.writer.remember_captured_note(
        title=title,
        absolute_path=absolute_path,
        relative_path=relative_path,
    )
    return AutoClassifyOutcome(
        relative_path=relative_path,
        note_path=absolute_path,
        message=f"\n目前先留在 Inbox；如果你想新增分類再告訴我。{_format_classify_message(result)}",
        proposed_new_tags=result.proposed_new_tags,
    )


def _forward_origin_name(message) -> str | None:
    forward_origin = getattr(message, "forward_origin", None)
    if forward_origin is None:
        return None
    for attr in ("sender_user", "sender_chat"):
        sender = getattr(forward_origin, attr, None)
        if sender is not None:
            title = getattr(sender, "title", None)
            username = getattr(sender, "username", None)
            first_name = getattr(sender, "first_name", None)
            full_name = " ".join(
                part
                for part in [first_name, getattr(sender, "last_name", None)]
                if part
            )
            return title or username or full_name or None
    sender_name = getattr(forward_origin, "sender_user_name", None)
    if sender_name:
        return sender_name
    return type(forward_origin).__name__


def _message_metadata(
    *,
    message,
    capture_type: str,
    source: str,
    text: str = "",
    source_url: str | None = None,
    extra_tags: tuple[str, ...] = (),
    extraction_quality: str | None = None,
) -> CaptureMetadata:
    canonical_url = canonicalize_url(source_url) if source_url else None
    platform = platform_from_url(source_url)
    domain = domain_from_url(source_url)
    return CaptureMetadata(
        source=source,
        capture_type=capture_type,
        telegram_chat_id=message.chat_id,
        telegram_message_id=message.message_id,
        is_forwarded=_is_forwarded_message(message),
        forward_origin_type=(
            type(message.forward_origin).__name__
            if _is_forwarded_message(message)
            else None
        ),
        forward_origin_name=_forward_origin_name(message),
        source_url=canonical_url,
        canonical_url=canonical_url,
        source_platform=platform,
        source_domain=domain,
        extraction_quality=extraction_quality,
        extra_tags=extra_tags,
    )


def _structured_common_markup(note_key: str, labels: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for index, label in enumerate(labels):
        row.append(
            InlineKeyboardButton(
                label,
                callback_data=f"{_COMMON_CALLBACK_PREFIX}{note_key}:{index}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                "全部內容",
                callback_data=f"{_COMMON_CALLBACK_PREFIX}{note_key}:all",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


async def _reply_with_common_note(message, common_note) -> None:
    structured_note = parse_structured_common_note(common_note)
    if structured_note is None:
        await message.reply_text(read_common_note_text(common_note))
        return

    await message.reply_text(
        f"選擇要顯示的「{structured_note.label}」項目：",
        reply_markup=_structured_common_markup(
            structured_note.key,
            [item.label for item in structured_note.items],
        ),
    )


def _recommendation_candidate_from_card(card):
    reasons = tuple(card.recommendation_hints[:1]) or (
        card.profile or "依目前筆記資料綜合判斷",
    )
    warnings = tuple(
        list(card.payment_restrictions[:1])
        + list(card.limits[:1])
        + list(card.effective_periods[:1])
    )
    return {
        "card": card,
        "reasons": reasons,
        "warnings": warnings,
    }


def _candidate_by_name(result, cards, card_name: str | None):
    if not card_name:
        return None
    for candidate in result.candidates:
        if candidate.card.name == card_name:
            return {
                "card": candidate.card,
                "reasons": candidate.reasons,
                "warnings": candidate.warnings,
            }
    for card in cards:
        if card.name == card_name:
            return _recommendation_candidate_from_card(card)
    return None


async def _reply_card_recommendation(
    *,
    services: AppServices,
    message,
    merchant: str,
    reply_markup=None,
) -> None:
    merchant_text = merchant.strip()
    markup = reply_markup or _main_menu_markup()
    if not merchant_text:
        await message.reply_text(
            "請只輸入店家名稱，例如：/card Costco", reply_markup=markup
        )
        return

    cards = load_credit_cards(services.settings)
    if not cards:
        await message.reply_text(
            "目前找不到常用/信用卡.md，請先確認信用卡資料是否存在。",
            reply_markup=markup,
        )
        return

    result = recommend_cards(merchant_text, cards)
    if result.best is None:
        await message.reply_text(
            "目前無法根據信用卡資料推薦，請先補充更多卡片資訊。", reply_markup=markup
        )
        return

    web_cards = _select_cards_for_web_lookup(
        question=merchant_text,
        cards=cards,
        preferred_names=tuple(
            name
            for name in (
                result.best.card.name if result.best is not None else "",
                result.backup.card.name if result.backup is not None else "",
            )
            if name
        ),
    )
    web_context = await services.web.lookup_credit_card_context(
        question=merchant_text,
        cards=web_cards,
    )

    ai_decision = None
    if services.ai.is_available:
        ai_decision = await services.ai.recommend_credit_cards(
            merchant=merchant_text,
            cards=[card.to_ai_dict() for card in cards],
            web_context=[item.to_ai_dict() for item in web_context],
            suggested_best=result.best.card.name,
            suggested_backup=result.backup.card.name
            if result.backup is not None
            else None,
        )

    best_name = (
        ai_decision.best_card
        if ai_decision is not None and ai_decision.best_card
        else result.best.card.name
    )
    backup_name = (
        ai_decision.backup_card
        if ai_decision is not None and ai_decision.backup_card
        else (result.backup.card.name if result.backup is not None else "")
    )
    if backup_name == best_name:
        backup_name = ""

    best_entry = _candidate_by_name(result, cards, best_name)
    backup_entry = _candidate_by_name(result, cards, backup_name)
    if best_entry is None:
        best_entry = _candidate_by_name(result, cards, result.best.card.name)
    if best_entry is None:
        await message.reply_text(
            "目前無法組合推薦結果，請稍後再試。", reply_markup=markup
        )
        return

    best_reason = (
        ai_decision.best_reason.strip()
        if ai_decision is not None and ai_decision.best_reason.strip()
        else "；".join(best_entry["reasons"])
    )
    backup_reason = ""
    if backup_entry is not None:
        backup_reason = (
            ai_decision.backup_reason.strip()
            if ai_decision is not None and ai_decision.backup_reason.strip()
            else "；".join(backup_entry["reasons"])
        )

    warnings: list[str] = []
    if ai_decision is not None:
        warnings.extend(list(ai_decision.warnings))
    warnings.extend(list(best_entry["warnings"]))
    deduped_warnings: list[str] = []
    for warning in warnings:
        cleaned = warning.strip()
        if not cleaned or cleaned in deduped_warnings:
            continue
        deduped_warnings.append(cleaned)

    lines = [
        f"店家：{merchant_text}",
        f"首選：{best_entry['card'].name}",
        f"原因：{best_reason}",
    ]
    if deduped_warnings:
        lines.append(f"提醒：{'；'.join(deduped_warnings[:2])}")

    if backup_entry is not None:
        lines.extend(
            [
                "",
                f"備選：{backup_entry['card'].name}",
                f"原因：{backup_reason}",
            ]
        )

    if result.used_fallback:
        lines.extend(
            [
                "",
                "補充：這次沒有直接命中商家關鍵字，建議結帳前再確認活動期限與支付方式。",
            ]
        )
    summary = _format_web_context_summary(web_context)
    if summary:
        lines.extend(["", summary])

    await message.reply_text("\n".join(lines), reply_markup=markup)


async def _reply_credit_card_question(
    *,
    services: AppServices,
    message,
    question: str,
    reply_markup=None,
) -> None:
    markup = reply_markup or _main_menu_markup()
    cards = load_credit_cards(services.settings)
    if not cards:
        await message.reply_text(
            "目前找不到常用/信用卡.md，請先確認信用卡資料是否存在。",
            reply_markup=markup,
        )
        return

    if not services.ai.is_available:
        await message.reply_text(
            "目前信用卡問答需要 AI 才能分析；你也可以改用「信用卡推薦」按鈕直接輸入店家名稱。",
            reply_markup=markup,
        )
        return

    web_cards = _select_cards_for_web_lookup(
        question=question,
        cards=cards,
    )
    web_context = await services.web.lookup_credit_card_context(
        question=question,
        cards=web_cards,
    )

    decision = await services.ai.answer_credit_card_question(
        question=question,
        cards=[card.to_ai_dict() for card in cards],
        web_context=[item.to_ai_dict() for item in web_context],
    )
    if decision is None:
        await message.reply_text(
            "這題我暫時整理不出可靠答案，建議你換個問法，或直接問店家名稱讓我推薦刷哪張。",
            reply_markup=markup,
        )
        return

    lines = [decision.answer]
    if decision.referenced_cards:
        lines.extend(["", f"參考卡片：{'、'.join(decision.referenced_cards)}"])
    summary = _format_web_context_summary(web_context)
    if summary:
        lines.extend(["", summary])
    if decision.confidence < services.settings.low_confidence_threshold:
        lines.extend(
            [
                "",
                "提醒：這題偏整理與推估，實際出手前仍建議再確認活動期限、支付方式與回饋上限。",
            ]
        )

    await message.reply_text("\n".join(lines), reply_markup=markup)


async def _reply_note_question(
    *,
    services: AppServices,
    message,
    question: str,
    force: bool,
) -> bool:
    matches = services.vault.search(question, limit=4)
    if not matches:
        if force:
            await message.reply_text(
                "我目前找不到明確相關的筆記。你可以再加上關鍵字、tag、資料夾名稱，或直接指定筆記名稱。",
                reply_markup=_main_menu_markup(),
            )
        return False

    if not force and matches[0].score < 90:
        return False

    ai_decision = None
    if services.ai.is_available:
        ai_decision = await services.ai.answer_note_question(
            question=question,
            notes=[match.to_ai_dict() for match in matches],
        )

    chosen_paths = set(ai_decision.citations if ai_decision is not None else ())
    cited_matches = tuple(
        match for match in matches if match.relative_path in chosen_paths
    )
    if not cited_matches:
        cited_matches = matches[:2]

    if ai_decision is not None:
        lines = [ai_decision.answer]
    else:
        lines = ["我先找到最接近的筆記內容："]
        for match in cited_matches:
            lines.append(
                f"- {match.title}：{match.snippets[0] if match.snippets else match.relative_path}"
            )

    lines.extend(["", _format_note_sources(cited_matches)])
    if (
        ai_decision is not None
        and ai_decision.confidence < services.settings.low_confidence_threshold
    ):
        lines.extend(
            [
                "",
                "提醒：這題的匹配度普通，我是根據最接近的幾篇筆記整理，建議你再縮小範圍問一次。",
            ]
        )

    await message.reply_text("\n".join(lines), reply_markup=_main_menu_markup())
    return True


async def _prompt_card(
    message,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    _set_pending_action(context, PendingAction(kind="card"))
    await message.reply_text(
        "請輸入店家名稱，例如：Costco、LINE Pay、台灣虎航\n如果你是想問哪張該保留 / 取消，也可以直接輸入完整問題。",
        reply_markup=_cancel_markup(),
    )


async def _prompt_ask(
    message,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    _set_pending_action(context, PendingAction(kind="ask"))
    await message.reply_text(
        "請輸入你要查的筆記問題，例如：我的銀行帳號在哪裡？或 今天記了什麼？",
        reply_markup=_cancel_markup(),
    )


async def _capture_text(
    *,
    services: AppServices,
    context: ContextTypes.DEFAULT_TYPE,
    message,
    chat_id: int,
    message_id: int,
    text: str,
    source: str,
    source_url: str | None = None,
    extra_tags: tuple[str, ...] = (),
    extraction_quality: str | None = None,
) -> None:
    metadata = _message_metadata(
        message=message,
        capture_type="text" if source_url is None else "url-fallback",
        source=source,
        text=text,
        source_url=source_url,
        extra_tags=extra_tags,
        extraction_quality=extraction_quality,
    )
    note = await asyncio.to_thread(
        services.writer.capture_text,
        text=text,
        metadata=metadata,
    )
    if note.already_exists:
        await message.reply_text(
            f"已存在相同內容：{note.relative_path}",
            reply_markup=_main_menu_markup(),
        )
        return
    outcome = await _auto_classify_note(
        services=services,
        context=context,
        title=note.title,
        absolute_path=note.absolute_path,
        relative_path=note.relative_path,
    )
    await message.reply_text(
        f"已寫入 {outcome.relative_path}{outcome.message}",
        reply_markup=_main_menu_markup(),
    )
    tag_prompt = _schedule_tag_approval(
        context=context,
        note_path=outcome.note_path,
        proposed_new_tags=outcome.proposed_new_tags,
    )
    if tag_prompt is not None:
        await message.reply_text(tag_prompt, reply_markup=_tag_approval_markup())


async def _complete_capture_request(
    *,
    services: AppServices,
    context: ContextTypes.DEFAULT_TYPE,
    message,
    request: PendingCaptureRequest,
    mode: CaptureMode,
) -> None:
    source_text = request.text
    source_title: str | None = None
    source_images: tuple[str, ...] = ()
    source_hint = ""
    source_url = request.metadata.source_url

    if source_url is not None:
        article = await services.url.fetch_article(source_url)
        if article is not None:
            fetched_content = str(article.get("content", "")).strip()
            if fetched_content and mode in ("article", "topic"):
                source_text = fetched_content
            source_title = str(article.get("title", "")).strip() or None
            source_images = tuple(
                str(embed).strip()
                for embed in article.get("image_embeds", ())
                if str(embed).strip()
            )
            source_hint = f"\n已用網頁內容整理成「{capture_mode_label(mode)}」。"
        else:
            source_hint = "\n網頁內容暫時抓不到，先用你貼的原文建立。"

    prepared = prepare_capture(
        mode=mode,
        text=source_text,
        settings=services.settings,
        vault=services.vault,
        source_url=source_url,
        source_title=source_title,
        image_embeds=source_images,
    )
    extra_tags = tuple(
        dict.fromkeys([*request.metadata.extra_tags, *prepared.extra_tags])
    )
    metadata = CaptureMetadata(
        source=request.metadata.source,
        capture_type=f"capture-{mode}",
        telegram_chat_id=request.metadata.telegram_chat_id,
        telegram_message_id=request.metadata.telegram_message_id,
        is_forwarded=request.metadata.is_forwarded,
        forward_origin_type=request.metadata.forward_origin_type,
        forward_origin_name=request.metadata.forward_origin_name,
        source_url=request.metadata.source_url,
        canonical_url=request.metadata.canonical_url,
        source_platform=request.metadata.source_platform,
        source_domain=request.metadata.source_domain,
        extraction_quality=request.metadata.extraction_quality,
        content_hash=request.metadata.content_hash,
        extra_tags=extra_tags,
    )
    note = await asyncio.to_thread(
        services.writer.capture_text,
        text=source_text,
        metadata=metadata,
        title_override=prepared.title,
        body_override=prepared.body,
    )
    if note.already_exists:
        await message.reply_text(
            f"已存在相同內容：{note.relative_path}",
            reply_markup=_main_menu_markup(),
        )
        return
    outcome = await _auto_classify_note(
        services=services,
        context=context,
        title=note.title,
        absolute_path=note.absolute_path,
        relative_path=note.relative_path,
    )
    await message.reply_text(
        f"已用「{capture_mode_label(mode)}」寫入 {outcome.relative_path}{source_hint}{outcome.message}",
        reply_markup=_main_menu_markup(),
    )
    tag_prompt = _schedule_tag_approval(
        context=context,
        note_path=outcome.note_path,
        proposed_new_tags=outcome.proposed_new_tags,
    )
    if tag_prompt is not None:
        await message.reply_text(tag_prompt, reply_markup=_tag_approval_markup())


async def _create_task(
    *,
    services: AppServices,
    message,
    text: str,
    target_modifier: str | None = None,
) -> None:
    target_date = (
        services.daily.parse_date_modifier(target_modifier)
        if target_modifier is not None
        else None
    )
    entry = await asyncio.to_thread(
        services.daily.append_entry,
        text=text,
        is_task=True,
        target_date=target_date,
    )
    await message.reply_text(
        f"已建立任務到 {entry.relative_path}",
        reply_markup=_main_menu_markup(),
    )


async def _prompt_capture(
    message,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    _set_pending_action(context, PendingAction(kind="capture"))
    await message.reply_text(
        "請輸入要寫入 Inbox 的內容：",
        reply_markup=_cancel_markup(),
    )


async def _prompt_task(
    *,
    message,
    context: ContextTypes.DEFAULT_TYPE,
    target_modifier: str | None = None,
) -> None:
    _set_pending_action(
        context,
        PendingAction(kind="task", target_modifier=target_modifier),
    )
    target_hint = f"{target_modifier}的" if target_modifier else "今天的"
    await message.reply_text(
        f"請輸入要建立成 {target_hint}任務的內容：",
        reply_markup=_cancel_markup(),
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected /start from unauthorized chat")
        return
    message = update.effective_message
    if message is None:
        return
    _set_pending_action(context, None)
    await message.reply_text(_start_text(), reply_markup=_main_menu_markup())


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected /health from unauthorized chat")
        return
    message = update.effective_message
    if message is None:
        return
    notes = list_common_notes(services.settings)
    await message.reply_text(
        "\n".join(
            [
                "status: ok",
                f"vault: {services.settings.vault_path}",
                f"inbox: {services.settings.inbox_path}",
                f"common_notes: {len(notes)}",
            ]
        ),
        reply_markup=_main_menu_markup(),
    )


async def capture_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected /capture from unauthorized chat")
        return
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    text = " ".join(context.args).strip()
    if not text:
        await _prompt_capture(message, context)
        return
    _set_pending_action(context, None)
    await _capture_text(
        services=services,
        context=context,
        message=message,
        chat_id=chat.id,
        message_id=message.message_id,
        text=text,
        source="telegram-command",
    )


def _is_forwarded_message(message) -> bool:
    return getattr(message, "forward_origin", None) is not None


def _should_store_text_in_inbox(
    *, text: str, threshold: int, is_forwarded: bool
) -> bool:
    return is_forwarded or len(text) > threshold


async def common_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected /common from unauthorized chat")
        return
    message = update.effective_message
    if message is None:
        return
    notes = list_common_notes(services.settings)
    if not notes:
        await message.reply_text(
            "常用/ 目前還沒有 .md 筆記。",
            reply_markup=_main_menu_markup(),
        )
        return
    keyboard: list[list[str]] = []
    row: list[str] = []
    for note in notes:
        row.append(note.label)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append(["/start"])
    await message.reply_text(
        "選一個常用項目，或直接輸入同名文字：",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
    )


async def text_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected text message from unauthorized chat")
        return
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    text = (message.text or "").strip()
    if not text:
        await message.reply_text("目前只支援純文字訊息存檔。")
        return

    if text == _CANCEL_BUTTON:
        _set_pending_action(context, None)
        _set_pending_question(context, None)
        _set_pending_capture(context, None)
        _set_pending_tag_approval(context, None)
        await message.reply_text("已取消目前操作。", reply_markup=_main_menu_markup())
        return

    pending = _pending_action(context)
    if pending is not None:
        if pending.kind == "capture":
            _set_pending_action(context, None)
            await _capture_text(
                services=services,
                context=context,
                message=message,
                chat_id=chat.id,
                message_id=message.message_id,
                text=text,
                source="telegram-menu",
            )
            return
        if pending.kind == "card":
            if _looks_like_card_merchant_input(text):
                await _reply_card_recommendation(
                    services=services,
                    message=message,
                    merchant=text,
                    reply_markup=_cancel_markup(),
                )
                return
            if _looks_like_credit_card_question(text):
                await _reply_credit_card_question(
                    services=services,
                    message=message,
                    question=text,
                    reply_markup=_cancel_markup(),
                )
                return
            await message.reply_text(
                "這句看起來不像店家名稱。如果你是想問哪張該保留、取消或比較，我可以直接回答；不然請重新輸入店家名稱。",
                reply_markup=_cancel_markup(),
            )
            return
        if pending.kind == "ask":
            _set_pending_action(context, None)
            if _looks_like_credit_card_question(text):
                _set_pending_question(context, text)
                await message.reply_text(
                    "這題比較適合走信用卡挑選，因為我會額外比對結構化卡片資料與最新官網線索。你也可以仍然用正常問答。",
                    reply_markup=_question_mode_markup(),
                )
                return
            await _reply_note_question(
                services=services,
                message=message,
                question=text,
                force=True,
            )
            return
        _set_pending_action(context, None)
        await _create_task(
            services=services,
            message=message,
            text=text,
            target_modifier=pending.target_modifier,
        )
        return

    if text == _CARD_BUTTON:
        await _prompt_card(message, context)
        return
    if text == _ASK_BUTTON:
        await _prompt_ask(message, context)
        return

    common_note = find_common_note(services.settings, text)
    if common_note is not None:
        await _reply_with_common_note(message, common_note)
        return

    detected_url = services.url.find_url(text)
    if detected_url:
        await _prompt_capture_mode(
            message=message,
            context=context,
            request=PendingCaptureRequest(
                text=text,
                metadata=_message_metadata(
                    message=message,
                    capture_type="url",
                    source="telegram-url",
                    text=text,
                    source_url=detected_url,
                    extra_tags=("web-clip",),
                    extraction_quality="full",
                ),
            ),
            intro="偵測到網址了。要把這則內容收成隨手想法、文章摘要、主題筆記，還是先存到 Daily？",
        )
        return

    if _looks_like_question(text):
        _set_pending_question(context, text)
        await message.reply_text(
            "這句看起來像在問問題。要當成信用卡挑選、正常問答，還是直接存到 Daily？",
            reply_markup=_question_mode_markup(),
        )
        return

    threshold = services.settings.daily_threshold
    if _should_store_text_in_inbox(
        text=text,
        threshold=threshold,
        is_forwarded=_is_forwarded_message(message),
    ):
        await _prompt_capture_mode(
            message=message,
            context=context,
            request=PendingCaptureRequest(
                text=text,
                metadata=_message_metadata(
                    message=message,
                    capture_type="text",
                    source="telegram-message",
                    text=text,
                    extra_tags=(
                        ("forwarded",) if _is_forwarded_message(message) else ()
                    ),
                ),
            ),
            intro="這則內容比較適合先整理後再收。要存成隨手想法、文章摘要、主題筆記，還是直接丟到 Daily？",
        )
    else:
        entry = await asyncio.to_thread(services.daily.append_entry, text=text)
        await message.reply_text(f"已追加到 {entry.relative_path}")
        return


async def task_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected /task from unauthorized chat")
        return
    message = update.effective_message
    if message is None:
        return

    args = context.args or []
    target_date, task_text, target_modifier = _parse_task_args(services.daily, args)

    if not task_text:
        await _prompt_task(
            message=message,
            context=context,
            target_modifier=target_modifier,
        )
        return

    _set_pending_action(context, None)
    entry = await asyncio.to_thread(
        services.daily.append_entry,
        text=task_text,
        is_task=True,
        target_date=target_date,
    )
    await message.reply_text(
        f"已建立任務到 {entry.relative_path}",
        reply_markup=_main_menu_markup(),
    )


async def card_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected /card from unauthorized chat")
        return
    message = update.effective_message
    if message is None:
        return

    merchant = " ".join(context.args).strip()
    if not merchant:
        await _prompt_card(message, context)
        return

    _set_pending_action(context, None)
    if _looks_like_credit_card_question(merchant):
        await _reply_credit_card_question(
            services=services,
            message=message,
            question=merchant,
        )
        return
    await _reply_card_recommendation(
        services=services,
        message=message,
        merchant=merchant,
    )


async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected /ask from unauthorized chat")
        return
    message = update.effective_message
    if message is None:
        return

    question = " ".join(context.args).strip()
    if not question:
        await _prompt_ask(message, context)
        return

    if _looks_like_credit_card_question(question):
        _set_pending_question(context, question)
        await message.reply_text(
            "這題比較適合走信用卡挑選，因為我會額外比對結構化卡片資料與最新官網線索。你也可以仍然用正常問答。",
            reply_markup=_question_mode_markup(),
        )
        return

    _set_pending_action(context, None)
    await _reply_note_question(
        services=services,
        message=message,
        question=question,
        force=True,
    )


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected photo from unauthorized chat")
        return
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.photo:
        return

    photo = message.photo[-1]
    caption = message.caption or ""

    saved = await services.media.save_photo(
        photo=photo,
        bot=context.bot,
        caption=caption,
        metadata=_message_metadata(
            message=message,
            capture_type="photo",
            source="telegram-photo",
            text=caption,
            source_url=services.url.find_url(caption),
            extra_tags=(("forwarded",) if _is_forwarded_message(message) else ()),
        ),
    )
    if saved.already_exists:
        await message.reply_text(f"已存在相同圖片筆記：{saved.note_relative_path}")
        return
    outcome = await _auto_classify_note(
        services=services,
        context=context,
        title=saved.note_path.stem,
        absolute_path=saved.note_path,
        relative_path=saved.note_relative_path,
    )
    await message.reply_text(f"已保存圖片到 {outcome.relative_path}{outcome.message}")
    tag_prompt = _schedule_tag_approval(
        context=context,
        note_path=outcome.note_path,
        proposed_new_tags=outcome.proposed_new_tags,
    )
    if tag_prompt is not None:
        await message.reply_text(tag_prompt, reply_markup=_tag_approval_markup())


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected document from unauthorized chat")
        return
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or message.document is None:
        return

    caption = message.caption or ""

    saved = await services.media.save_document(
        document=message.document,
        bot=context.bot,
        caption=caption,
        metadata=_message_metadata(
            message=message,
            capture_type="document",
            source="telegram-document",
            text=caption,
            source_url=services.url.find_url(caption),
            extra_tags=(("forwarded",) if _is_forwarded_message(message) else ()),
        ),
    )

    if saved is None:
        await message.reply_text("不支援此檔案格式")
        return
    if saved.already_exists:
        await message.reply_text(f"已存在相同檔案筆記：{saved.note_relative_path}")
        return

    outcome = await _auto_classify_note(
        services=services,
        context=context,
        title=saved.note_path.stem,
        absolute_path=saved.note_path,
        relative_path=saved.note_relative_path,
    )
    await message.reply_text(f"已保存檔案到 {outcome.relative_path}{outcome.message}")
    tag_prompt = _schedule_tag_approval(
        context=context,
        note_path=outcome.note_path,
        proposed_new_tags=outcome.proposed_new_tags,
    )
    if tag_prompt is not None:
        await message.reply_text(tag_prompt, reply_markup=_tag_approval_markup())


async def url_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected /url from unauthorized chat")
        return
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    args = context.args or []
    if not args:
        await message.reply_text("請輸入網址：/url <網址>")
        return

    url = args[0]
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    await _prompt_capture_mode(
        message=message,
        context=context,
        request=PendingCaptureRequest(
            text=url,
            metadata=_message_metadata(
                message=message,
                capture_type="url",
                source="telegram-url",
                text=url,
                source_url=url,
                extra_tags=("web-clip",),
                extraction_quality="full",
            ),
        ),
        intro="要把這個網址收成隨手想法、文章摘要、主題筆記，還是先存到 Daily？",
    )
    return


async def classify_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected /classify from unauthorized chat")
        return
    message = update.effective_message
    if message is None:
        return

    if not services.ai.is_available:
        await message.reply_text("AI 分類功能未啟用，請設定 GEMINI_API_KEY")
        return

    last_note = services.writer.last_captured
    if last_note is None:
        await message.reply_text("沒有最近的筆記可以分類")
        return

    await message.reply_text("正在重新分析最近一筆筆記...")

    result = await services.ai.classify_and_move(
        last_note.absolute_path,
        auto_move=True,
    )

    if result is None:
        await message.reply_text("分類失敗")
        return

    if result.moved:
        new_relative_path = (
            _relative_note_path(services.settings, result.new_path)
            if result.new_path is not None
            else last_note.relative_path
        )
        if result.new_path is not None:
            services.writer.remember_captured_note(
                title=last_note.title,
                absolute_path=result.new_path,
                relative_path=new_relative_path,
            )
        await message.reply_text(
            f"已將筆記移動到 {result.suggested_folder}/\n"
            f"新路徑：{new_relative_path}{_format_classify_message(result)}"
        )
        tag_prompt = _schedule_tag_approval(
            context=context,
            note_path=result.new_path or last_note.absolute_path,
            proposed_new_tags=result.proposed_new_tags,
        )
        if tag_prompt is not None:
            await message.reply_text(tag_prompt, reply_markup=_tag_approval_markup())
    else:
        await message.reply_text(
            f"目前仍留在 Inbox；若要改分類可用 /move <資料夾>。{_format_classify_message(result)}",
            reply_markup=_move_markup(services.settings.valid_folders, "Inbox"),
        )
        tag_prompt = _schedule_tag_approval(
            context=context,
            note_path=last_note.absolute_path,
            proposed_new_tags=result.proposed_new_tags,
        )
        if tag_prompt is not None:
            await message.reply_text(tag_prompt, reply_markup=_tag_approval_markup())


async def common_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected common callback from unauthorized chat")
        return

    query = update.callback_query
    if query is None or query.data is None:
        return

    payload = query.data.removeprefix(_COMMON_CALLBACK_PREFIX)
    note_key, _, item_key = payload.partition(":")
    structured_note = find_structured_common_note_by_key(services.settings, note_key)
    if structured_note is None:
        await query.answer("找不到這份常用資料", show_alert=True)
        return

    text = structured_note.full_text
    if item_key != "all":
        try:
            item_index = int(item_key)
        except ValueError:
            await query.answer("找不到這個項目", show_alert=True)
            return
        if not 0 <= item_index < len(structured_note.items):
            await query.answer("找不到這個項目", show_alert=True)
            return
        text = structured_note.items[item_index].text

    await query.answer()
    if query.message is None:
        return
    await query.message.reply_text(text)


async def mode_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected mode callback from unauthorized chat")
        return

    query = update.callback_query
    if query is None or query.data is None:
        return

    question = _pending_question(context)
    if question is None:
        await query.answer("這個問題已失效，請重新輸入一次。", show_alert=True)
        return

    _set_pending_question(context, None)
    await query.answer()
    if query.message is not None:
        await query.edit_message_text(f"已收到問題：{question}")

    mode = query.data.removeprefix(_MODE_CALLBACK_PREFIX)
    if query.message is None:
        return

    if mode == "card":
        merchant = question.rstrip("？? ").strip()
        if _looks_like_card_merchant_input(merchant):
            await _reply_card_recommendation(
                services=services,
                message=query.message,
                merchant=merchant,
            )
            return
        await _reply_credit_card_question(
            services=services,
            message=query.message,
            question=question,
        )
        return

    if mode == "ask":
        await _reply_note_question(
            services=services,
            message=query.message,
            question=question,
            force=True,
        )
        return

    if mode == "daily":
        entry = await asyncio.to_thread(services.daily.append_entry, text=question)
        await query.message.reply_text(
            f"已追加到 {entry.relative_path}",
            reply_markup=_main_menu_markup(),
        )
        return

    await query.message.reply_text(
        "未知模式，請再試一次。", reply_markup=_main_menu_markup()
    )


async def capture_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected capture callback from unauthorized chat")
        return

    query = update.callback_query
    if query is None or query.data is None:
        return

    pending_capture = _pending_capture(context)
    if pending_capture is None:
        await query.answer("這筆內容已失效，請重新貼一次。", show_alert=True)
        return

    _set_pending_capture(context, None)
    await query.answer()
    mode = query.data.removeprefix(_CAPTURE_CALLBACK_PREFIX)
    if query.message is None:
        return

    if mode == "cancel":
        await query.edit_message_text("已取消這次收納。")
        return
    if mode == "daily":
        entry = await asyncio.to_thread(
            services.daily.append_entry,
            text=pending_capture.text,
        )
        await query.edit_message_text(f"已追加到 {entry.relative_path}")
        await query.message.reply_text("已完成收納。", reply_markup=_main_menu_markup())
        return
    if mode not in {"thought", "article", "topic"}:
        await query.message.reply_text(
            "未知收納模式，請再試一次。", reply_markup=_main_menu_markup()
        )
        return

    await query.edit_message_text(f"已選擇：{capture_mode_label(mode)}")
    await _complete_capture_request(
        services=services,
        context=context,
        message=query.message,
        request=pending_capture,
        mode=mode,
    )


async def tag_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected tag callback from unauthorized chat")
        return

    query = update.callback_query
    if query is None or query.data is None:
        return

    pending = _pending_tag_approval(context)
    if pending is None:
        await query.answer("這個 tag 詢問已失效。", show_alert=True)
        return

    note_path = Path(pending.note_path)
    if not note_path.exists():
        _set_pending_tag_approval(context, None)
        await query.answer("找不到這篇筆記，可能已被移動。", show_alert=True)
        return

    action = query.data.removeprefix(_TAG_CALLBACK_PREFIX)
    _set_pending_tag_approval(context, None)
    await query.answer()

    if action == "approve":
        await asyncio.to_thread(
            upsert_note_metadata,
            note_path,
            add_tags=pending.tags,
            fields={},
        )
        await query.edit_message_text(f"已加入新 tag：{'、'.join(pending.tags)}")
        return

    if action == "skip":
        await asyncio.to_thread(upsert_note_metadata, note_path, fields={})
        await query.edit_message_text("這次先不新增 tag。")
        return

    await query.edit_message_text("未知的 tag 操作。")


async def move_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected /move from unauthorized chat")
        return
    message = update.effective_message
    if message is None:
        return

    args = context.args or []
    if not args:
        await message.reply_text(
            "請選擇目標資料夾：",
            reply_markup=_move_markup(services.settings.valid_folders),
        )
        return

    target_folder = args[0]
    last_note = services.writer.last_captured

    if last_note is None:
        await message.reply_text("沒有最近的筆記可以移動")
        return

    if not last_note.absolute_path.exists():
        await message.reply_text("筆記已不存在")
        return

    result = services.ai.move_note(last_note.absolute_path, target_folder)

    if result.moved:
        await message.reply_text(f"已移動到 {result.suggested_folder}/")
    else:
        await message.reply_text(f"無效的資料夾：{target_folder}")


async def move_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    services = _services(context)
    if not _allowed(update, services.settings):
        logger.warning("Rejected move callback from unauthorized chat")
        return

    query = update.callback_query
    if query is None or query.data is None:
        return

    await query.answer()

    target_folder = query.data.removeprefix(_MOVE_CALLBACK_PREFIX)
    last_note = services.writer.last_captured

    if last_note is None:
        await query.edit_message_text("沒有最近的筆記可以移動")
        return

    if not last_note.absolute_path.exists():
        await query.edit_message_text("筆記已不存在")
        return

    result = services.ai.move_note(last_note.absolute_path, target_folder)
    if result.moved:
        await query.edit_message_text(f"已移動到 {result.suggested_folder}/")
        return

    await query.edit_message_text(f"無效的資料夾：{target_folder}")
