from __future__ import annotations

import re
from dataclasses import dataclass

from .common_notes import CreditCard

_TRAVEL_HINTS = (
    "旅",
    "海外",
    "航空",
    "air",
    "hotel",
    "飯店",
    "agoda",
    "booking",
    "trip",
    "klook",
    "機場",
)


@dataclass(frozen=True)
class RecommendationCandidate:
    card: CreditCard
    score: int
    matched_keywords: tuple[str, ...]
    matched_categories: tuple[str, ...]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class RecommendationResult:
    merchant: str
    best: RecommendationCandidate | None
    backup: RecommendationCandidate | None
    candidates: tuple[RecommendationCandidate, ...]
    used_fallback: bool


def recommend_cards(merchant: str, cards: tuple[CreditCard, ...]) -> RecommendationResult:
    normalized_merchant = _normalize(merchant)
    if not normalized_merchant or not cards:
        return RecommendationResult(
            merchant=merchant,
            best=None,
            backup=None,
            candidates=(),
            used_fallback=False,
        )

    scored = [_build_candidate(card, merchant) for card in cards]
    strong_matches = [candidate for candidate in scored if candidate.score > 0]
    ordered = tuple(sorted(strong_matches, key=_sort_key))

    if ordered:
        return RecommendationResult(
            merchant=merchant,
            best=ordered[0],
            backup=ordered[1] if len(ordered) > 1 else None,
            candidates=ordered,
            used_fallback=False,
        )

    fallback = tuple(_fallback_candidates(merchant, cards))
    return RecommendationResult(
        merchant=merchant,
        best=fallback[0] if fallback else None,
        backup=fallback[1] if len(fallback) > 1 else None,
        candidates=fallback,
        used_fallback=True,
    )


def _build_candidate(card: CreditCard, merchant: str) -> RecommendationCandidate:
    keyword_matches = _matched_values(merchant, card.merchant_keywords)
    category_matches = _matched_values(merchant, card.applicable_categories)

    score = 0
    if keyword_matches:
        score += 120
        score += 5 * len(keyword_matches)
    if category_matches:
        score += 45
        score += 3 * len(category_matches)

    reasons: list[str] = []
    if keyword_matches:
        reasons.append(f"命中商家關鍵字：{'、'.join(keyword_matches[:3])}")
    if category_matches:
        reasons.append(f"符合適用類別：{'、'.join(category_matches[:2])}")

    bonus_line = _first_non_empty(card.bonus_rewards)
    if bonus_line:
        reasons.append(bonus_line)
    elif not reasons:
        hint_line = _first_non_empty(card.recommendation_hints)
        if hint_line:
            reasons.append(hint_line)

    warnings = _collect_warnings(card)
    confidence = min(0.95, 0.35 + (score / 200)) if score > 0 else 0.2

    return RecommendationCandidate(
        card=card,
        score=score,
        matched_keywords=keyword_matches,
        matched_categories=category_matches,
        reasons=tuple(reasons[:3]),
        warnings=warnings,
        confidence=round(confidence, 2),
    )


def _matched_values(merchant: str, values: tuple[str, ...]) -> tuple[str, ...]:
    normalized_merchant = _normalize(merchant)
    matches: list[str] = []
    for value in values:
        normalized_value = _normalize(value)
        if not normalized_value:
            continue
        if normalized_value == normalized_merchant:
            matches.append(value)
            continue
        if len(normalized_value) >= 2 and normalized_value in normalized_merchant:
            matches.append(value)
            continue
        if len(normalized_merchant) >= 2 and normalized_merchant in normalized_value:
            matches.append(value)
    return tuple(matches)


def _collect_warnings(card: CreditCard) -> tuple[str, ...]:
    warnings: list[str] = []
    if card.payment_restrictions:
        warnings.append(card.payment_restrictions[0])
    if card.limits:
        warnings.append(card.limits[0])
    if card.effective_periods:
        warnings.append(card.effective_periods[0])
    return tuple(warnings[:3])


def _fallback_candidates(
    merchant: str, cards: tuple[CreditCard, ...]
) -> list[RecommendationCandidate]:
    fallback_scores = _fallback_score_map(merchant)
    candidates: list[RecommendationCandidate] = []
    for card in cards:
        base_score = fallback_scores.get(card.name, 0)
        if base_score <= 0:
            continue
        reasons = list(card.recommendation_hints[:1]) or ["依目前資料推估的通用備選卡"]
        candidates.append(
            RecommendationCandidate(
                card=card,
                score=base_score,
                matched_keywords=(),
                matched_categories=(),
                reasons=tuple(reasons),
                warnings=_collect_warnings(card),
                confidence=0.25,
            )
        )
    return sorted(candidates, key=_sort_key)


def _fallback_score_map(merchant: str) -> dict[str, int]:
    normalized = _normalize(merchant)
    if any(keyword in normalized for keyword in _TRAVEL_HINTS):
        return {
            "滙豐旅人無限卡": 40,
            "台新Richart卡": 35,
            "樂天虎航卡": 30,
            "樂天Panda JCB卡": 28,
            "台北富邦Costco卡": 10,
        }
    return {
        "台新Richart卡": 35,
        "樂天Panda JCB卡": 30,
        "滙豐旅人無限卡": 22,
        "樂天虎航卡": 20,
        "台北富邦Costco卡": 10,
    }


def _sort_key(candidate: RecommendationCandidate) -> tuple[int, int, int, int, str]:
    return (
        -candidate.score,
        _card_priority(candidate.card),
        -len(candidate.matched_keywords),
        -len(candidate.matched_categories),
        candidate.card.name,
    )


def _card_priority(card: CreditCard) -> int:
    if "Costco" in card.name or "虎航" in card.name:
        return 0
    if "Panda" in card.name:
        return 1
    if "旅人" in card.name:
        return 2
    return 3


def _first_non_empty(values: tuple[str, ...]) -> str:
    for value in values:
        if value.strip():
            return value.strip()
    return ""


def _normalize(text: str) -> str:
    lowered = text.casefold()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", lowered)
