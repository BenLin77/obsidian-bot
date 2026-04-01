from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import Settings

_ADDRESS_PREFIXES = (
    "235603新北市中和區",
    "235新北市中和區",
    "新北市中和區",
    "新北市",
    "中和區",
)
_STRUCTURED_NOTE_KEYS = {
    "銀行資訊": "bank",
    "地址": "address",
}
_STRUCTURED_NOTE_LABELS = {
    key: label for label, key in _STRUCTURED_NOTE_KEYS.items()
}
_CARD_NOTE_LABEL = "信用卡"
_CARD_SECTION_SKIP_LABELS = {"使用說明", "更新紀錄"}
_BOLD_FIELD_RE = re.compile(r"^\*\*(.+?)\*\*\s*(.*)$")
_URL_RE = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class CommonNote:
    label: str
    path: Path


@dataclass(frozen=True)
class StructuredCommonItem:
    label: str
    text: str


@dataclass(frozen=True)
class StructuredCommonNote:
    key: str
    label: str
    full_text: str
    items: tuple[StructuredCommonItem, ...]


@dataclass(frozen=True)
class CreditCard:
    name: str
    bank: str
    profile: str
    last_checked: str
    source_urls: tuple[str, ...]
    confidence: str
    merchant_keywords: tuple[str, ...]
    applicable_categories: tuple[str, ...]
    base_rewards: tuple[str, ...]
    bonus_rewards: tuple[str, ...]
    payment_restrictions: tuple[str, ...]
    limits: tuple[str, ...]
    exclusions: tuple[str, ...]
    effective_periods: tuple[str, ...]
    recommendation_hints: tuple[str, ...]

    def to_ai_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "bank": self.bank,
            "profile": self.profile,
            "last_checked": self.last_checked,
            "confidence": self.confidence,
            "merchant_keywords": list(self.merchant_keywords),
            "applicable_categories": list(self.applicable_categories),
            "base_rewards": list(self.base_rewards),
            "bonus_rewards": list(self.bonus_rewards),
            "payment_restrictions": list(self.payment_restrictions),
            "limits": list(self.limits),
            "exclusions": list(self.exclusions),
            "effective_periods": list(self.effective_periods),
            "recommendation_hints": list(self.recommendation_hints),
            "source_urls": list(self.source_urls),
        }


def list_common_notes(settings: Settings) -> list[CommonNote]:
    common_root = settings.common_path
    common_root.mkdir(parents=True, exist_ok=True)
    notes: list[CommonNote] = []
    for path in sorted(common_root.glob("*.md")):
        notes.append(CommonNote(label=path.stem, path=path))
    return notes


def find_common_note(settings: Settings, label: str) -> CommonNote | None:
    normalized = label.strip().casefold()
    for note in list_common_notes(settings):
        if note.label.casefold() == normalized:
            return note
    return None


def read_common_note_text(note: CommonNote) -> str:
    raw_text = note.path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return _strip_frontmatter(raw_text).strip()


def find_structured_common_note(
    settings: Settings, label: str
) -> StructuredCommonNote | None:
    note = find_common_note(settings, label)
    if note is None:
        return None
    return parse_structured_common_note(note)


def find_structured_common_note_by_key(
    settings: Settings, key: str
) -> StructuredCommonNote | None:
    label = _STRUCTURED_NOTE_LABELS.get(key)
    if label is None:
        return None
    return find_structured_common_note(settings, label)


def parse_structured_common_note(note: CommonNote) -> StructuredCommonNote | None:
    key = _STRUCTURED_NOTE_KEYS.get(note.label)
    if key is None:
        return None

    full_text = read_common_note_text(note)
    if not full_text:
        return None

    items = _parse_heading_sections(full_text)
    if not items and key == "bank":
        items = _parse_bank_items(full_text)
    elif not items and key == "address":
        items = _parse_address_items(full_text)
    elif not items:
        items = ()

    if not items:
        return None

    return StructuredCommonNote(
        key=key,
        label=note.label,
        full_text=full_text,
        items=items,
    )


def find_credit_card_note(settings: Settings) -> CommonNote | None:
    return find_common_note(settings, _CARD_NOTE_LABEL)


def load_credit_cards(settings: Settings) -> tuple[CreditCard, ...]:
    note = find_credit_card_note(settings)
    if note is None:
        return ()
    return parse_credit_cards_note(note)


def parse_credit_cards_note(note: CommonNote) -> tuple[CreditCard, ...]:
    full_text = read_common_note_text(note)
    if not full_text:
        return ()

    cards: list[CreditCard] = []
    for section in _parse_heading_sections(full_text):
        if section.label in _CARD_SECTION_SKIP_LABELS:
            continue
        fields = _parse_markdown_fields(section.text)
        if not fields:
            continue
        cards.append(
            CreditCard(
                name=section.label,
                bank=_first_field(fields, "銀行"),
                profile=_first_field(fields, "卡片定位"),
                last_checked=_first_field(fields, "最後檢查"),
                source_urls=tuple(_collect_urls(fields.get("更新來源", ()))),
                confidence=_first_field(fields, "信心度"),
                merchant_keywords=fields.get("商家關鍵字", ()),
                applicable_categories=fields.get("適用類別", ()),
                base_rewards=fields.get("基礎回饋", ()),
                bonus_rewards=(
                    fields.get("加碼回饋 / 附加價值")
                    or fields.get("加碼回饋")
                    or ()
                ),
                payment_restrictions=fields.get("支付方式限制", ()),
                limits=fields.get("上限/門檻", ()),
                exclusions=fields.get("排除項目", ()),
                effective_periods=fields.get("適用期限", ()),
                recommendation_hints=fields.get("推薦提示", ()),
            )
        )
    return tuple(cards)


def _parse_heading_sections(text: str) -> tuple[StructuredCommonItem, ...]:
    items: list[StructuredCommonItem] = []
    current_label: str | None = None
    current_lines: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("## "):
            if current_label is not None:
                content = "\n".join(current_lines).strip()
                if content:
                    items.append(
                        StructuredCommonItem(
                            label=current_label,
                            text=content,
                        )
                    )
            current_label = stripped[3:].strip()
            current_lines = []
            continue

        if current_label is not None:
            current_lines.append(line)

    if current_label is not None:
        content = "\n".join(current_lines).strip()
        if content:
            items.append(
                StructuredCommonItem(
                    label=current_label,
                    text=content,
                )
            )

    return tuple(items)


def _parse_markdown_fields(text: str) -> dict[str, tuple[str, ...]]:
    fields: dict[str, list[str]] = {}
    current_label: str | None = None

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue

        matched = _BOLD_FIELD_RE.match(stripped)
        if matched is not None:
            current_label = matched.group(1).strip().rstrip(":：")
            fields.setdefault(current_label, [])
            remainder = matched.group(2).lstrip(":：").strip()
            if remainder:
                fields[current_label].append(remainder)
            continue

        if current_label is None:
            continue

        item_text = stripped[2:].strip() if stripped.startswith("- ") else stripped
        if item_text:
            fields[current_label].append(item_text)

    return {label: tuple(values) for label, values in fields.items()}


def _collect_urls(values: tuple[str, ...]) -> list[str]:
    urls: list[str] = []
    for value in values:
        urls.extend(_URL_RE.findall(value))
    return urls


def _first_field(fields: dict[str, tuple[str, ...]], label: str) -> str:
    values = fields.get(label, ())
    return values[0] if values else ""


def _strip_frontmatter(raw_text: str) -> str:
    text = raw_text.lstrip("\ufeff")
    if not text.startswith("---\n"):
        return text

    lines = text.splitlines()
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1 :])
    return text


def _split_paragraphs(text: str) -> list[list[str]]:
    paragraphs: list[list[str]] = []
    current: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                paragraphs.append(current)
                current = []
            continue
        current.append(line)

    if current:
        paragraphs.append(current)
    return paragraphs


def _join_paragraphs(paragraphs: list[list[str]]) -> str:
    blocks = ["\n".join(paragraph) for paragraph in paragraphs if paragraph]
    return "\n\n".join(blocks).strip()


def _find_paragraph_index(
    paragraphs: list[list[str]], predicate
) -> int | None:
    for index, paragraph in enumerate(paragraphs):
        if paragraph and predicate(paragraph):
            return index
    return None


def _parse_bank_items(text: str) -> tuple[StructuredCommonItem, ...]:
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return ()

    items: list[StructuredCommonItem] = []
    foreign_index = _find_paragraph_index(
        paragraphs, lambda paragraph: paragraph[0].startswith("1.收款銀行")
    )
    ibkr_index = _find_paragraph_index(paragraphs, lambda paragraph: paragraph[0] == "IBKR")
    eva_index = _find_paragraph_index(
        paragraphs, lambda paragraph: paragraph[0].startswith("Evar air卡號")
    )

    simple_end = foreign_index if foreign_index is not None else len(paragraphs)
    for paragraph in paragraphs[:simple_end]:
        content = "\n".join(paragraph).strip()
        if not content:
            continue
        items.append(
            StructuredCommonItem(
                label=_bank_item_label(paragraph[0]),
                text=content,
            )
        )

    if foreign_index is not None:
        foreign_end = ibkr_index if ibkr_index is not None else eva_index
        if foreign_end is None:
            foreign_end = len(paragraphs)
        content = _join_paragraphs(paragraphs[foreign_index:foreign_end])
        if content:
            items.append(StructuredCommonItem(label="外幣收款", text=content))

    if ibkr_index is not None:
        ibkr_end = eva_index if eva_index is not None else len(paragraphs)
        content = _join_paragraphs(paragraphs[ibkr_index:ibkr_end])
        if content:
            items.append(StructuredCommonItem(label="IBKR 匯款", text=content))

    if eva_index is not None:
        content = _join_paragraphs(paragraphs[eva_index:])
        if content:
            items.append(StructuredCommonItem(label="EVA Air 卡號", text=content))

    return tuple(items)


def _bank_item_label(first_line: str) -> str:
    line = first_line.strip()
    if "：" in line:
        return line.split("：", maxsplit=1)[0].strip()
    if ":" in line:
        return line.split(":", maxsplit=1)[0].strip()

    label = re.sub(r"\s*[0-9][0-9 ]{5,}$", "", line).strip()
    return label or line


def _parse_address_items(text: str) -> tuple[StructuredCommonItem, ...]:
    lines = [line for paragraph in _split_paragraphs(text) for line in paragraph]
    if not lines:
        return ()

    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if _looks_like_address_header(line):
            if current:
                paragraphs.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        paragraphs.append(current)

    items: list[StructuredCommonItem] = []
    for paragraph in paragraphs:
        content = "\n".join(paragraph).strip()
        if not content:
            continue
        items.append(
            StructuredCommonItem(
                label=_address_item_label(paragraph[0]),
                text=content,
            )
        )
    return tuple(items)


def _looks_like_address_header(line: str) -> bool:
    stripped = line.strip()
    return any(stripped.startswith(prefix) for prefix in _ADDRESS_PREFIXES)


def _address_item_label(line: str) -> str:
    stripped = line.strip()
    for prefix in _ADDRESS_PREFIXES:
        if stripped.startswith(prefix):
            return prefix
    return stripped
