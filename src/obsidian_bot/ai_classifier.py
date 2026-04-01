from __future__ import annotations

import json
import logging
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from google import genai

from .config import Settings
from .note_metadata import upsert_note_metadata

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .vault_adapter import VaultAdapter

CLASSIFICATION_PROMPT = """你是 Obsidian 筆記分類助手。請根據以下筆記內容，回傳 JSON。

可用的資料夾：
{folder_descriptions}

目前可直接使用的既有 tags（只能從這份清單挑 0~2 個）：
{available_tags}

回傳 JSON 格式：
{{
  "folder": "其中一個合法資料夾名稱",
  "tags": ["只能從既有 tags 清單挑選，最多 2 個"],
  "proposed_new_tags": ["若真的缺少關鍵 tag，再提議最多 2 個新 tag"],
  "confidence": 0.0 到 1.0 之間的小數,
  "needs_review": true 或 false
}}

規則：
1. 只回 JSON，不要其他說明。
2. 如果不確定，folder 回 Inbox。
3. tags 只能從既有 tags 清單挑選，且 0~2 個就好，不要亂加。
4. 如果既有 tags 都不適合，tags 可為空；只有在真的必要時才填 proposed_new_tags。
5. inbox 不是分類 tag，不要放進 tags 或 proposed_new_tags。
4. needs_review 在不確定、內容殘缺、或需要人眼再確認時設為 true。

筆記內容：
---
{content}
---
"""

CARD_RECOMMENDATION_PROMPT = """你是 Telegram 信用卡推薦助手。請只根據提供的信用卡資料與官方來源摘錄，為使用者輸入的店家推薦最佳信用卡與備選卡。

使用者輸入店家：{merchant}

系統先做的建議：
- suggested_best: {suggested_best}
- suggested_backup: {suggested_backup}

可用信用卡資料（JSON）：
{cards_json}

官方來源摘錄（JSON，可為空）：
{web_context_json}

請綜合考量：
1. 商家關鍵字是否直接命中
2. 適用類別是否合理
3. 支付方式限制、需綁定、需特殊連結、需當地幣別、需切方案等限制
4. 回饋上限、活動期限、排除項目
5. 如果資料不足，仍要給出最合理的首選與備選，但 confidence 要降低
6. 若官方來源摘錄提到活動期限、支付方式或權益限制，優先反映在理由與 warnings

請只回傳 JSON：
{{
  "best_card": "卡名",
  "backup_card": "卡名或空字串",
  "best_reason": "一句繁中理由",
  "backup_reason": "一句繁中理由或空字串",
  "warnings": ["重要限制1", "重要限制2"],
  "confidence": 0.0
}}

規則：
- 只能從提供的卡名中選 best_card 與 backup_card
- best_card 與 backup_card 不能相同
- 回答必須是繁體中文
- 只回 JSON，不要其他說明
"""

CARD_QA_PROMPT = """你是 Telegram 信用卡整理助手。請只根據提供的信用卡資料與官方來源摘錄，回答使用者的問題。

使用者問題：{question}

可用信用卡資料（JSON）：
{cards_json}

官方來源摘錄（JSON，可為空）：
{web_context_json}

請只回傳 JSON：
{{
  "answer": "繁體中文回答",
  "referenced_cards": ["有提到的卡名"],
  "confidence": 0.0
}}

規則：
- 只能依提供資料回答，不可編造未提供的權益或活動
- 只能提及提供資料中存在的卡名
- 若官方來源摘錄提到活動期限、支付限制、回饋條件或更新線索，應優先納入回答
- 如果資料不足，answer 要明確說資料不足，並提醒需再確認官網活動頁
- 如果問題是保留 / 取消 / 重複性比較，請比較使用場景、限制與重疊處，不要武斷下結論
- 若涉及支付方式、切方案、活動期限、回饋上限等限制，應在 answer 裡主動提醒
- 回答必須是繁體中文，控制在 6 句內
- 只回 JSON，不要其他說明
"""

NOTE_QA_PROMPT = """你是 Obsidian 筆記問答助手。請只根據提供的候選筆記內容回答使用者問題。

使用者問題：{question}

候選筆記（JSON）：
{notes_json}

請只回傳 JSON：
{{
  "answer": "繁體中文回答",
  "citations": ["候選筆記中的 path"],
  "confidence": 0.0
}}

規則：
- 只能根據候選筆記回答，不可補充未出現在候選筆記中的事實
- 若候選筆記不足以回答，要明確說資料不足或找到的內容有限
- 優先回答重點，必要時簡短整理，不要抄整段原文
- citations 只能填候選筆記中真的存在的 path
- 回答必須是繁體中文，控制在 8 句內
- 只回 JSON，不要其他說明
"""


@dataclass(frozen=True)
class ClassificationDecision:
    suggested_folder: str
    suggested_tags: tuple[str, ...]
    proposed_new_tags: tuple[str, ...]
    confidence: float
    needs_review: bool


@dataclass(frozen=True)
class ClassificationResult:
    suggested_folder: str
    original_path: Path
    new_path: Path | None
    moved: bool
    suggested_tags: tuple[str, ...]
    proposed_new_tags: tuple[str, ...]
    confidence: float
    needs_review: bool


@dataclass(frozen=True)
class CardRecommendationDecision:
    best_card: str
    backup_card: str
    best_reason: str
    backup_reason: str
    warnings: tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class CardQuestionDecision:
    answer: str
    referenced_cards: tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class NoteQuestionDecision:
    answer: str
    citations: tuple[str, ...]
    confidence: float


class AIClassifier:
    FOLDER_DESCRIPTIONS = {
        "stock": "股票、投資、市場分析、財報",
        "ai": "人工智慧、程式設計、技術、軟體開發",
        "food": "食譜、餐廳、烹飪、美食",
        "佛教": "佛學、修行、禪定、佛法",
        "Option": "期權、選擇權交易、Greeks",
        "量化交易": "程式交易、回測、策略",
        "job": "工作、職涯、履歷",
        "Inbox": "無法分類或暫時歸類",
    }

    def __init__(self, settings: Settings, vault: "VaultAdapter | None" = None) -> None:
        self._settings = settings
        self._vault = vault
        self._client = None
        if settings.gemini_api_key:
            self._client = genai.Client(api_key=settings.gemini_api_key)

    @property
    def is_available(self) -> bool:
        return self._client is not None

    async def classify(self, note_path: Path) -> ClassificationDecision | None:
        if not self.is_available or not note_path.exists():
            return None

        content = note_path.read_text(encoding="utf-8")
        if len(content) > 10000:
            content = content[:10000] + "\n...(截斷)"

        available_tags = self._available_tags()
        prompt = CLASSIFICATION_PROMPT.format(
            folder_descriptions=self._build_folder_descriptions(),
            content=content,
            available_tags=(
                "\n".join(f"- {tag}" for tag in available_tags)
                if available_tags
                else "- （目前沒有可用既有 tags）"
            ),
        )

        try:
            response = self._client.models.generate_content(
                model="gemini-3-flash-preview",
                contents=prompt,
            )
            return self._parse_decision(response.text, available_tags=available_tags)
        except Exception as e:
            logger.error(f"AI classification failed: {e}")
            return None

    def _build_folder_descriptions(self) -> str:
        lines = []
        for folder, description in self.FOLDER_DESCRIPTIONS.items():
            if folder not in self._settings.valid_folders:
                continue
            lines.append(f"- {folder}: {description}")
        return "\n".join(lines)

    async def recommend_credit_cards(
        self,
        *,
        merchant: str,
        cards: list[dict[str, object]],
        web_context: list[dict[str, str]] | None = None,
        suggested_best: str | None = None,
        suggested_backup: str | None = None,
    ) -> CardRecommendationDecision | None:
        if not self.is_available or not cards:
            return None

        prompt = CARD_RECOMMENDATION_PROMPT.format(
            merchant=merchant,
            suggested_best=suggested_best or "",
            suggested_backup=suggested_backup or "",
            cards_json=json.dumps(cards, ensure_ascii=False, indent=2),
            web_context_json=json.dumps(
                web_context or [], ensure_ascii=False, indent=2
            ),
        )

        try:
            response = self._client.models.generate_content(
                model="gemini-3-flash-preview",
                contents=prompt,
            )
            return self._parse_card_recommendation(response.text, cards)
        except Exception as e:
            logger.error(f"AI card recommendation failed: {e}")
            return None

    async def answer_credit_card_question(
        self,
        *,
        question: str,
        cards: list[dict[str, object]],
        web_context: list[dict[str, str]] | None = None,
    ) -> CardQuestionDecision | None:
        if not self.is_available or not cards:
            return None

        prompt = CARD_QA_PROMPT.format(
            question=question,
            cards_json=json.dumps(cards, ensure_ascii=False, indent=2),
            web_context_json=json.dumps(
                web_context or [], ensure_ascii=False, indent=2
            ),
        )

        try:
            response = self._client.models.generate_content(
                model="gemini-3-flash-preview",
                contents=prompt,
            )
            return self._parse_card_question(response.text, cards)
        except Exception as e:
            logger.error(f"AI card question failed: {e}")
            return None

    async def answer_note_question(
        self,
        *,
        question: str,
        notes: list[dict[str, object]],
    ) -> NoteQuestionDecision | None:
        if not self.is_available or not notes:
            return None

        prompt = NOTE_QA_PROMPT.format(
            question=question,
            notes_json=json.dumps(notes, ensure_ascii=False, indent=2),
        )

        try:
            response = self._client.models.generate_content(
                model="gemini-3-flash-preview",
                contents=prompt,
            )
            return self._parse_note_question(response.text, notes)
        except Exception as e:
            logger.error(f"AI note question failed: {e}")
            return None

    def _parse_decision(
        self,
        response_text: str | None,
        available_tags: Iterable[str] = (),
    ) -> ClassificationDecision:
        raw = (response_text or "").strip()
        parsed = self._extract_json(raw)
        folder = self._normalize_folder(str(parsed.get("folder", "Inbox")))
        suggested_tags, proposed_from_tags = self._normalize_tags(
            parsed.get("tags", []),
            available_tags=available_tags,
        )
        proposed_new_tags = self._normalize_proposed_new_tags(
            parsed.get("proposed_new_tags", []),
            available_tags=available_tags,
            existing_tags=suggested_tags,
            fallback_tags=proposed_from_tags,
        )
        confidence = self._normalize_confidence(parsed.get("confidence", 0.0))
        needs_review = bool(
            parsed.get("needs_review", folder == "Inbox" or confidence < 0.75)
        )
        return ClassificationDecision(
            suggested_folder=folder,
            suggested_tags=suggested_tags,
            proposed_new_tags=proposed_new_tags,
            confidence=confidence,
            needs_review=needs_review,
        )

    def _parse_card_recommendation(
        self,
        response_text: str | None,
        cards: list[dict[str, object]],
    ) -> CardRecommendationDecision | None:
        parsed = self._extract_json((response_text or "").strip())
        valid_names = {
            str(card.get("name", "")).strip()
            for card in cards
            if str(card.get("name", "")).strip()
        }

        best_card = str(parsed.get("best_card", "")).strip()
        backup_card = str(parsed.get("backup_card", "")).strip()
        if best_card not in valid_names:
            return None
        if backup_card and backup_card not in valid_names:
            backup_card = ""
        if backup_card == best_card:
            backup_card = ""

        raw_warnings = parsed.get("warnings", [])
        warnings = self._normalize_text_list(raw_warnings)
        return CardRecommendationDecision(
            best_card=best_card,
            backup_card=backup_card,
            best_reason=str(parsed.get("best_reason", "")).strip(),
            backup_reason=str(parsed.get("backup_reason", "")).strip(),
            warnings=warnings,
            confidence=self._normalize_confidence(parsed.get("confidence", 0.0)),
        )

    def _parse_card_question(
        self,
        response_text: str | None,
        cards: list[dict[str, object]],
    ) -> CardQuestionDecision | None:
        raw = (response_text or "").strip()
        parsed = self._extract_json(raw)
        answer = str(parsed.get("answer", "")).strip()
        if not answer and raw and not raw.startswith("{"):
            answer = raw
        if not answer:
            return None

        valid_names = {
            str(card.get("name", "")).strip()
            for card in cards
            if str(card.get("name", "")).strip()
        }
        raw_referenced_cards = parsed.get("referenced_cards", [])
        referenced_cards = tuple(
            value
            for value in self._normalize_text_list(raw_referenced_cards)
            if value in valid_names
        )
        return CardQuestionDecision(
            answer=answer,
            referenced_cards=referenced_cards,
            confidence=self._normalize_confidence(parsed.get("confidence", 0.0)),
        )

    def _parse_note_question(
        self,
        response_text: str | None,
        notes: list[dict[str, object]],
    ) -> NoteQuestionDecision | None:
        raw = (response_text or "").strip()
        parsed = self._extract_json(raw)
        answer = str(parsed.get("answer", "")).strip()
        if not answer and raw and not raw.startswith("{"):
            answer = raw
        if not answer:
            return None

        valid_paths = {
            str(note.get("path", "")).strip()
            for note in notes
            if str(note.get("path", "")).strip()
        }
        raw_citations = parsed.get("citations", [])
        citations = tuple(
            value
            for value in self._normalize_text_list(raw_citations)
            if value in valid_paths
        )
        return NoteQuestionDecision(
            answer=answer,
            citations=citations,
            confidence=self._normalize_confidence(parsed.get("confidence", 0.0)),
        )

    def _extract_json(self, text: str) -> dict[str, object]:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match is not None:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        for folder in self._settings.valid_folders:
            if folder.lower() in text.lower():
                return {
                    "folder": folder,
                    "tags": [],
                    "confidence": 0.3,
                    "needs_review": True,
                }
        return {"folder": "Inbox", "tags": [], "confidence": 0.0, "needs_review": True}

    def _normalize_folder(self, raw_folder: str) -> str:
        if raw_folder in self._settings.valid_folders:
            return raw_folder
        for folder in self._settings.valid_folders:
            if folder.lower() == raw_folder.lower():
                return folder
        return "Inbox"

    def _normalize_tags(
        self,
        raw_tags: object,
        *,
        available_tags: Iterable[str],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if not isinstance(raw_tags, list):
            return (), ()
        allowed_lookup = {
            str(tag).strip().casefold(): str(tag).strip()
            for tag in available_tags
            if str(tag).strip()
        }
        normalized: list[str] = []
        proposed: list[str] = []
        seen: set[str] = set()
        seen_proposed: set[str] = set()
        for raw_tag in raw_tags[:5]:
            tag = str(raw_tag).strip().lstrip("#").replace(" ", "-")
            if not tag:
                continue
            lowered = tag.casefold()
            if lowered in allowed_lookup:
                if lowered in seen or len(normalized) >= 2:
                    continue
                seen.add(lowered)
                normalized.append(allowed_lookup[lowered])
                continue
            if lowered == "inbox" or lowered in seen_proposed:
                continue
            seen_proposed.add(lowered)
            proposed.append(tag)
        return tuple(normalized[:2]), tuple(proposed[:2])

    def _normalize_proposed_new_tags(
        self,
        raw_tags: object,
        *,
        available_tags: Iterable[str],
        existing_tags: Iterable[str],
        fallback_tags: Iterable[str] = (),
    ) -> tuple[str, ...]:
        allowed_lookup = {
            str(tag).strip().casefold(): str(tag).strip()
            for tag in available_tags
            if str(tag).strip()
        }
        existing_lookup = {
            str(tag).strip().casefold() for tag in existing_tags if str(tag).strip()
        }
        normalized: list[str] = []
        seen: set[str] = set()
        candidates = raw_tags if isinstance(raw_tags, list) else []
        for raw_tag in [*candidates, *fallback_tags]:
            tag = str(raw_tag).strip().lstrip("#").replace(" ", "-")
            if not tag:
                continue
            lowered = tag.casefold()
            if (
                lowered == "inbox"
                or lowered in allowed_lookup
                or lowered in existing_lookup
                or lowered in seen
            ):
                continue
            seen.add(lowered)
            normalized.append(tag)
            if len(normalized) >= 2:
                break
        return tuple(normalized)

    def _normalize_text_list(self, raw_values: object) -> tuple[str, ...]:
        if not isinstance(raw_values, list):
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_value in raw_values[:5]:
            value = str(raw_value).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return tuple(normalized)

    def _normalize_confidence(self, raw_confidence: object) -> float:
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            return 0.0
        return min(max(confidence, 0.0), 1.0)

    def _apply_decision_metadata(
        self,
        note_path: Path,
        decision: ClassificationDecision,
    ) -> None:
        final_tags = [*decision.suggested_tags]
        if "inbox" not in final_tags:
            final_tags.append("inbox")
        upsert_note_metadata(
            note_path,
            fields={},
            replace_tags=final_tags,
        )

    def _should_auto_move(self, decision: ClassificationDecision) -> bool:
        return (
            decision.suggested_folder != "Inbox"
            and not decision.needs_review
            and decision.confidence >= self._settings.auto_move_confidence_threshold
        )

    def move_note(self, note_path: Path, target_folder: str) -> ClassificationResult:
        if target_folder not in self._settings.valid_folders:
            return ClassificationResult(
                suggested_folder=target_folder,
                original_path=note_path,
                new_path=None,
                moved=False,
                suggested_tags=(),
                proposed_new_tags=(),
                confidence=0.0,
                needs_review=True,
            )

        target_dir = self._settings.vault_path / target_folder
        target_dir.mkdir(parents=True, exist_ok=True)

        new_path = target_dir / note_path.name
        if new_path.exists():
            stem = note_path.stem
            suffix = note_path.suffix
            counter = 1
            while new_path.exists():
                new_path = target_dir / f"{stem}-{counter}{suffix}"
                counter += 1

        shutil.move(str(note_path), str(new_path))

        return ClassificationResult(
            suggested_folder=target_folder,
            original_path=note_path,
            new_path=new_path,
            moved=True,
            suggested_tags=(),
            proposed_new_tags=(),
            confidence=0.0,
            needs_review=False,
        )

    async def classify_and_move(
        self, note_path: Path, auto_move: bool = False
    ) -> ClassificationResult | None:
        decision = await self.classify(note_path)
        if decision is None:
            return None

        should_move = auto_move and self._should_auto_move(decision)
        self._apply_decision_metadata(note_path, decision)

        if should_move:
            moved = self.move_note(note_path, decision.suggested_folder)
            return ClassificationResult(
                suggested_folder=decision.suggested_folder,
                original_path=moved.original_path,
                new_path=moved.new_path,
                moved=moved.moved,
                suggested_tags=decision.suggested_tags,
                proposed_new_tags=decision.proposed_new_tags,
                confidence=decision.confidence,
                needs_review=decision.needs_review,
            )

        return ClassificationResult(
            suggested_folder=decision.suggested_folder,
            original_path=note_path,
            new_path=None,
            moved=False,
            suggested_tags=decision.suggested_tags,
            proposed_new_tags=decision.proposed_new_tags,
            confidence=decision.confidence,
            needs_review=decision.needs_review,
        )

    def _available_tags(self) -> tuple[str, ...]:
        if self._vault is None:
            return ()
        return self._vault.available_tags()
