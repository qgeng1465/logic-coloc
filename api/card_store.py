# -*- coding: utf-8 -*-
"""Card persistence and spaced-repetition scheduling (independent of AI logic)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import user_paths


CARDS_FILE = Path(__file__).resolve().parents[1] / "data" / "cards.json"
# Complete Ebbinghaus review schedule, expressed in seconds.
EBBINGHAUS_INTERVALS = (
    5 * 60,          # 5 minutes
    30 * 60,         # 30 minutes
    12 * 60 * 60,     # 12 hours
    24 * 60 * 60,    # 1 day
    2 * 24 * 60 * 60, # 2 days
    4 * 24 * 60 * 60, # 4 days
    7 * 24 * 60 * 60, # 7 days
    15 * 24 * 60 * 60, # 15 days
)
EBBINGHAUS_LONG_INTERVALS = (30 * 86400, 90 * 86400, 180 * 86400, 365 * 86400)


def _cards_path(user_id: str) -> Path:
    """卡片文件路径。带 user_id 走该账号的目录，为空则维持旧的全局文件。"""
    if user_id:
        user_paths.ensure_user_storage(user_id)
    return user_paths.scoped(user_id, CARDS_FILE, "cards.json")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 10_000_000_000 else value, timezone.utc)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def normalize_card(card: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    current = now or utc_now()
    normalized = dict(card)
    normalized.setdefault("status", "unmastered")
    normalized.setdefault("last_reviewed_at", None)
    if not normalized.get("next_review_due"):
        normalized["next_review_due"] = _iso(current)
    normalized.setdefault("review_stage", 0)
    normalized.setdefault("ease_factor", 2.5)
    return normalized


def calculate_next_review(card: dict[str, Any], quality: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Return a card updated according to the requested Ebbinghaus intervals."""
    current = now or utc_now()
    updated = normalize_card(card, now=current)
    aliases = {"忘记了": "forgot", "forgot": "forgot", "模糊": "vague", "vague": "vague", "掌握": "mastered", "mastered": "mastered"}
    normalized_quality = aliases.get(quality)
    if normalized_quality is None:
        raise ValueError("quality must be 忘记了, 模糊, or 掌握")

    updated["last_reviewed_at"] = _iso(current)
    updated["status"] = "unmastered"
    if normalized_quality == "forgot":
        updated["review_stage"] = 0
        updated["next_review_due"] = _iso(current)
    elif normalized_quality == "vague":
        updated["next_review_due"] = _iso(current + timedelta(seconds=30 * 60))
    else:
        stage = max(0, int(updated.get("review_stage") or 0))
        updated["review_stage"] = stage + 1
        stage_index = updated["review_stage"] - 1
        delay = EBBINGHAUS_INTERVALS[stage_index] if stage_index < len(EBBINGHAUS_INTERVALS) else EBBINGHAUS_LONG_INTERVALS[min(stage_index - len(EBBINGHAUS_INTERVALS), len(EBBINGHAUS_LONG_INTERVALS) - 1)]
        updated["next_review_due"] = _iso(current + timedelta(seconds=delay))
        updated["status"] = "mastered" if updated["review_stage"] >= len(EBBINGHAUS_INTERVALS) else "unmastered"
    return updated


def load_cards(*, now: datetime | None = None, user_id: str = "") -> list[dict[str, Any]]:
    try:
        raw = json.loads(_cards_path(user_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        raw = []
    current = now or utc_now()
    return [normalize_card(item, now=current) for item in raw if isinstance(item, dict)]


def save_cards(cards: list[dict[str, Any]], *, user_id: str = "") -> None:
    """原子写：先 .tmp 再 replace。

    此前这里是全项目唯一一处直接 `write_text` 截断重写的落盘点 —— 写一半崩掉
    整份卡片就没了，另外两个 store 早就改成了 .tmp + replace，这里补齐。
    """
    path = _cards_path(user_id)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(cards, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def save_card(card: dict[str, Any], *, user_id: str = "") -> dict[str, Any]:
    normalized = normalize_card(card)
    cards = [item for item in load_cards(user_id=user_id) if item.get("id") != normalized.get("id")]
    cards.insert(0, normalized)
    save_cards(cards, user_id=user_id)
    return normalized


def due_cards(*, now: datetime | None = None, user_id: str = "") -> list[dict[str, Any]]:
    current = now or utc_now()
    return [
        card for card in load_cards(now=current, user_id=user_id)
        if card.get("status") == "unmastered"
        and (_parse_timestamp(card.get("next_review_due")) or current) <= current
    ]


def review_card(card_id: str, quality: str, *, now: datetime | None = None, user_id: str = "") -> dict[str, Any] | None:
    cards = load_cards(user_id=user_id)
    for index, card in enumerate(cards):
        if card.get("id") == card_id:
            cards[index] = calculate_next_review(card, quality, now=now)
            save_cards(cards, user_id=user_id)
            return cards[index]
    return None
