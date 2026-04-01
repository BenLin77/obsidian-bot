from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .note_metadata import load_frontmatter

_TAG_RE = re.compile(r"#([\w\-/\u4e00-\u9fff]+)")
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{1,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$")
_MSG_ID_RE = re.compile(r"-t(\d+)m(\d+)\.md$")
_WHITESPACE_RE = re.compile(r"\s+")
_QUESTION_NOISE = (
    "請問",
    "幫我",
    "一下",
    "一下子",
    "有沒有",
    "可不可以",
    "能不能",
    "在哪裡",
    "哪裡",
    "如何",
    "怎麼",
    "是什麼",
    "什麼",
    "嗎",
    "呢",
    "啊",
    "呀",
    "我的",
    "我",
    "想知道",
    "家",
)
_TERM_STOPWORDS = {
    "什麼",
    "今天",
    "昨天",
    "最近",
    "一下",
    "請問",
    "可以",
    "有沒有",
    "哪裡",
}


@dataclass(frozen=True)
class NoteIndexEntry:
    note_path: Path
    relative_path: str
    title: str
    folder: str
    tags: tuple[str, ...]
    aliases: tuple[str, ...]
    headings: tuple[str, ...]
    preview: str
    modified_ts: float
    telegram_chat_id: int | None
    telegram_message_id: int | None
    source_url: str | None


@dataclass(frozen=True)
class NoteSearchResult:
    title: str
    relative_path: str
    tags: tuple[str, ...]
    score: int
    snippets: tuple[str, ...]
    modified_ts: float

    def to_ai_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "path": self.relative_path,
            "tags": list(self.tags),
            "snippets": list(self.snippets),
            "modified_ts": self.modified_ts,
        }


class VaultAdapter:
    INDEX_SCAN_INTERVAL_SECONDS = 5.0

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._system_tags = settings.system_tags
        self._index: dict[str, NoteIndexEntry] = {}
        self._index_mtimes: dict[str, float] = {}
        self._message_index: dict[tuple[int, int], Path] = {}
        self._source_url_index: dict[str, Path] = {}
        self._last_scan_at = 0.0

    @property
    def backend_name(self) -> str:
        return "filesystem-index"

    def search(self, question: str, *, limit: int = 4) -> tuple[NoteSearchResult, ...]:
        plan = _build_query_plan(question)
        if not plan.terms and not plan.explicit_tags:
            return ()

        self._refresh_index()
        scored: list[tuple[int, NoteIndexEntry]] = []
        for entry in self._index.values():
            score = _score_entry(entry, plan)
            if score <= 0:
                continue
            scored.append((score, entry))

        scored.sort(
            key=lambda item: (
                -item[0],
                -_recent_bonus(item[1].modified_ts),
                item[1].relative_path,
            )
        )

        top = scored[:limit]
        if not top:
            return ()

        results: list[NoteSearchResult] = []
        for score, entry in top:
            snippets = _extract_snippets(entry.note_path, plan)
            results.append(
                NoteSearchResult(
                    title=entry.title,
                    relative_path=entry.relative_path,
                    tags=entry.tags,
                    score=score,
                    snippets=snippets,
                    modified_ts=entry.modified_ts,
                )
            )
        return tuple(results)

    def find_existing_note_by_message(
        self,
        *,
        chat_id: int,
        message_id: int,
    ) -> Path | None:
        self._refresh_index()
        return self._message_index.get((chat_id, message_id))

    def find_existing_note_by_canonical_url(self, canonical_url: str) -> Path | None:
        self._refresh_index()
        return self._source_url_index.get(canonical_url)

    def available_tags(self) -> tuple[str, ...]:
        self._refresh_index()
        ordered: list[str] = []
        seen: set[str] = set()
        for entry in self._index.values():
            for tag in entry.tags:
                lowered = tag.casefold()
                if lowered in seen or lowered in self._system_tags:
                    continue
                seen.add(lowered)
                ordered.append(tag)
        ordered.sort(key=lambda value: value.casefold())
        return tuple(ordered)

    def register_note(self, note_path: Path) -> None:
        if not note_path.exists():
            return
        relative_path = note_path.relative_to(self._settings.vault_path)
        if _should_skip_note(relative_path):
            return
        stat = note_path.stat()
        entry = _build_entry(self._settings.vault_path, note_path, stat.st_mtime)
        self._index[entry.relative_path] = entry
        self._index_mtimes[entry.relative_path] = stat.st_mtime
        self._rebuild_lookup_indexes()

    def _refresh_index(self) -> None:
        now = time.time()
        if now - self._last_scan_at < self.INDEX_SCAN_INTERVAL_SECONDS:
            return
        self._last_scan_at = now

        current_paths: set[str] = set()
        for note_path in self._settings.vault_path.rglob("*.md"):
            relative_path_obj = note_path.relative_to(self._settings.vault_path)
            if _should_skip_note(relative_path_obj):
                continue
            try:
                stat = note_path.stat()
            except FileNotFoundError:
                continue
            relative_path = str(relative_path_obj)
            current_paths.add(relative_path)
            modified_ts = stat.st_mtime
            if self._index_mtimes.get(relative_path) == modified_ts:
                continue
            entry = _build_entry(self._settings.vault_path, note_path, modified_ts)
            self._index[relative_path] = entry
            self._index_mtimes[relative_path] = modified_ts

        removed_paths = [path for path in self._index if path not in current_paths]
        for removed_path in removed_paths:
            self._index.pop(removed_path, None)
            self._index_mtimes.pop(removed_path, None)
        self._rebuild_lookup_indexes()

    def _rebuild_lookup_indexes(self) -> None:
        message_index: dict[tuple[int, int], Path] = {}
        source_url_index: dict[str, Path] = {}
        for entry in self._index.values():
            if (
                entry.telegram_chat_id is not None
                and entry.telegram_message_id is not None
            ):
                message_index[(entry.telegram_chat_id, entry.telegram_message_id)] = (
                    entry.note_path
                )
            if entry.source_url:
                source_url_index.setdefault(entry.source_url, entry.note_path)
        self._message_index = message_index
        self._source_url_index = source_url_index


@dataclass(frozen=True)
class QueryPlan:
    raw_question: str
    normalized_question: str
    terms: tuple[str, ...]
    explicit_tags: tuple[str, ...]
    wants_common: bool
    wants_daily: bool


def _build_entry(
    vault_path: Path, note_path: Path, modified_ts: float
) -> NoteIndexEntry:
    data, body = load_frontmatter(note_path)
    msg_match = _MSG_ID_RE.search(note_path.name)
    telegram_chat_id = _normalize_optional_int(data.get("telegram_chat_id"))
    telegram_message_id = _normalize_optional_int(data.get("telegram_message_id"))
    if telegram_chat_id is None or telegram_message_id is None:
        telegram_chat_id = int(msg_match.group(1)) if msg_match else None
        telegram_message_id = int(msg_match.group(2)) if msg_match else None
    title = str(data.get("title", "")).strip() or note_path.stem
    tags = _normalize_iterable(data.get("tags"))
    aliases = _normalize_iterable(data.get("aliases"))
    headings = _extract_headings(body)
    preview = _build_preview(body)
    relative_path = str(note_path.relative_to(vault_path))
    folder = relative_path.split("/", maxsplit=1)[0] if "/" in relative_path else ""
    return NoteIndexEntry(
        note_path=note_path,
        relative_path=relative_path,
        title=title,
        folder=folder,
        tags=tags,
        aliases=aliases,
        headings=headings,
        preview=preview,
        modified_ts=modified_ts,
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
        source_url=_normalize_optional_text(data.get("source_url")),
    )


def _should_skip_note(relative_path: Path) -> bool:
    return (
        any(part.startswith(".") for part in relative_path.parts)
        or ".sync-conflict-" in relative_path.name
    )


def _normalize_optional_text(raw_value: object) -> str | None:
    if not isinstance(raw_value, str):
        return None
    cleaned = raw_value.strip()
    return cleaned or None


def _normalize_optional_int(raw_value: object) -> int | None:
    if isinstance(raw_value, bool):
        return None
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, float) and raw_value.is_integer():
        return int(raw_value)
    if isinstance(raw_value, str):
        cleaned = raw_value.strip()
        if cleaned.isdigit():
            return int(cleaned)
    return None


def _normalize_iterable(raw_value: object) -> tuple[str, ...]:
    if isinstance(raw_value, str):
        values = [raw_value]
    elif isinstance(raw_value, list):
        values = [str(item) for item in raw_value]
    else:
        return ()

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip().lstrip("#")
        if not cleaned:
            continue
        lowered = cleaned.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(cleaned)
    return tuple(normalized)


def _extract_headings(body: str) -> tuple[str, ...]:
    headings: list[str] = []
    for raw_line in body.splitlines():
        matched = _HEADING_RE.match(raw_line.strip())
        if matched is None:
            continue
        title = matched.group(1).strip()
        if title:
            headings.append(title)
        if len(headings) >= 8:
            break
    return tuple(headings)


def _build_preview(body: str) -> str:
    lines: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
        if len(lines) >= 8:
            break
    return "\n".join(lines)[:500]


def _build_query_plan(question: str) -> QueryPlan:
    explicit_tags = tuple(
        sorted(
            {match.group(1).strip().lstrip("#") for match in _TAG_RE.finditer(question)}
        )
    )
    normalized = _normalize_text(question)
    cleaned = question
    for noise in _QUESTION_NOISE:
        cleaned = cleaned.replace(noise, " ")
    terms = _extract_terms(cleaned)
    if explicit_tags:
        terms = tuple(dict.fromkeys([*terms, *explicit_tags]))
    lowered = question.casefold()
    wants_common = "常用" in question or any(
        token in lowered for token in ("地址", "銀行", "信用卡")
    )
    wants_daily = any(
        token in question for token in ("今天", "昨日", "昨天", "剛剛", "最近", "daily")
    )
    return QueryPlan(
        raw_question=question,
        normalized_question=normalized,
        terms=terms,
        explicit_tags=explicit_tags,
        wants_common=wants_common,
        wants_daily=wants_daily,
    )


def _extract_terms(text: str) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()

    for match in _CJK_RE.finditer(text):
        value = match.group(0).strip()
        if len(value) < 2:
            continue
        _append_term(values, seen, value)
        if 2 < len(value) <= 8:
            for size in range(2, min(5, len(value) + 1)):
                for start in range(0, len(value) - size + 1):
                    _append_term(values, seen, value[start : start + size])

    for match in _WORD_RE.finditer(text):
        value = match.group(0).strip()
        if len(value) < 2:
            continue
        _append_term(values, seen, value)

    return tuple(values[:12])


def _append_term(values: list[str], seen: set[str], raw_value: str) -> None:
    cleaned = raw_value.strip()
    if len(cleaned) < 2 or cleaned in _TERM_STOPWORDS:
        return
    lowered = cleaned.casefold()
    if lowered in seen:
        return
    seen.add(lowered)
    values.append(cleaned)


def _score_entry(entry: NoteIndexEntry, plan: QueryPlan) -> int:
    score = 0
    title_norm = _normalize_text(entry.title)
    path_norm = _normalize_text(entry.relative_path)
    folder_norm = _normalize_text(entry.folder)
    tags_norm = tuple(_normalize_text(tag) for tag in entry.tags)
    aliases_norm = tuple(_normalize_text(alias) for alias in entry.aliases)
    headings_norm = tuple(_normalize_text(heading) for heading in entry.headings)
    preview_norm = _normalize_text(entry.preview)

    if plan.wants_common and entry.relative_path.startswith("常用/"):
        score += 20
    if plan.wants_daily and entry.relative_path.startswith("Daily/"):
        score += 40 + _recent_bonus(entry.modified_ts)

    if plan.normalized_question and plan.normalized_question in title_norm:
        score += 140
    if plan.normalized_question and plan.normalized_question in path_norm:
        score += 90

    for explicit_tag in plan.explicit_tags:
        normalized_tag = _normalize_text(explicit_tag)
        if normalized_tag in tags_norm:
            score += 80

    for term in plan.terms:
        normalized_term = _normalize_text(term)
        if not normalized_term:
            continue
        if normalized_term == title_norm:
            score += 100
        elif normalized_term in title_norm:
            score += 60

        if normalized_term in path_norm:
            score += 45
        if normalized_term == folder_norm:
            score += 35
        if any(normalized_term in tag for tag in tags_norm):
            score += 35
        if any(normalized_term in alias for alias in aliases_norm):
            score += 30
        if any(normalized_term in heading for heading in headings_norm):
            score += 20
        if normalized_term in preview_norm:
            score += 8

    return score


def _extract_snippets(note_path: Path, plan: QueryPlan) -> tuple[str, ...]:
    _, body = load_frontmatter(note_path)
    lines = [line.rstrip() for line in body.splitlines()]
    snippets: list[str] = []
    seen: set[str] = set()
    normalized_terms = tuple(_normalize_text(term) for term in plan.terms)

    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        normalized_line = _normalize_text(line)
        if normalized_terms and not any(
            term and term in normalized_line for term in normalized_terms
        ):
            continue
        window = [
            item.strip()
            for item in lines[max(0, index - 1) : min(len(lines), index + 2)]
        ]
        snippet = "\n".join(item for item in window if item)[:320]
        if not snippet or snippet in seen:
            continue
        seen.add(snippet)
        snippets.append(snippet)
        if len(snippets) >= 3:
            return tuple(snippets)

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line in seen:
            continue
        seen.add(line)
        snippets.append(line[:220])
        if len(snippets) >= 3:
            break
    return tuple(snippets)


def _normalize_text(text: str) -> str:
    lowered = text.casefold()
    lowered = re.sub(r"[^\w\u4e00-\u9fff]+", " ", lowered)
    lowered = _WHITESPACE_RE.sub(" ", lowered).strip()
    return lowered


def _recent_bonus(modified_ts: float) -> int:
    age_seconds = max(0.0, time.time() - modified_ts)
    if age_seconds <= 86400:
        return 20
    if age_seconds <= 86400 * 7:
        return 10
    return 0
