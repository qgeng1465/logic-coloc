# -*- coding: utf-8 -*-
"""Card persistence and spaced-repetition scheduling (independent of AI logic)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import user_paths


CARDS_FILE = Path(__file__).resolve().parents[1] / "data" / "cards.json"
# 艾宾浩斯复习排期，单位秒，**一律以「天」为粒度**。
#
# 真实记忆曲线前两档是 5 分钟、30 分钟，但本站的产品口径是「今天复习过就算已掌握」，
# 用分钟级间隔会让「已掌握」只维持几分钟（读完就掉），与学习日志页、复习阶段筛选
# （都按 1/2/4/7/15 天）也对不上。所以从第一天起。
EBBINGHAUS_INTERVALS = (
    1 * 86400,        # 1 天
    2 * 86400,        # 2 天
    4 * 86400,        # 4 天
    7 * 86400,        # 7 天
    15 * 86400,       # 15 天
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
    """规范化一张卡片。**status 是派生字段**：由 next_review_due 到没到推出来。

    产品规则：到期 = 未掌握（该复习了），没到期 = 已掌握（这一轮已经巩固过）。复习完
    写盘的是已掌握，到点再读出来自动是未掌握 —— 不需要任何定时任务，也不会出现
    「既不算已掌握、又还没到期」的第三态（那正是前端三个 tab 出现过 0 + 0 ≠ 全部 1 的原因）。

    所以 users.json 里存的 status 只当缓存看，这里一律按到期时间覆盖掉（老数据因此自愈）。
    ⚠️ 前端 web/app.js 的 cardStatusOf() 是同一套规则，两边要一起改。
    """
    current = now or utc_now()
    normalized = dict(card)
    normalized.setdefault("last_reviewed_at", None)
    if not normalized.get("next_review_due"):
        normalized["next_review_due"] = _iso(current)
    normalized.setdefault("review_stage", 0)
    normalized.setdefault("ease_factor", 2.5)
    due = _parse_timestamp(normalized.get("next_review_due"))
    normalized["status"] = "mastered" if due is not None and due > current else "unmastered"
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
    if normalized_quality == "forgot":
        updated["review_stage"] = 0
        updated["next_review_due"] = _iso(current)
    elif normalized_quality == "vague":
        # 模糊：算复习过（所以是已掌握），但排期拉回最短一档，明天再来一次。
        updated["next_review_due"] = _iso(current + timedelta(seconds=24 * 60 * 60))
    else:
        stage = max(0, int(updated.get("review_stage") or 0))
        updated["review_stage"] = stage + 1
        stage_index = updated["review_stage"] - 1
        delay = EBBINGHAUS_INTERVALS[stage_index] if stage_index < len(EBBINGHAUS_INTERVALS) else EBBINGHAUS_LONG_INTERVALS[min(stage_index - len(EBBINGHAUS_INTERVALS), len(EBBINGHAUS_LONG_INTERVALS) - 1)]
        updated["next_review_due"] = _iso(current + timedelta(seconds=delay))
    # 状态跟着排期走，不按复习次数算（review_stage 只管排期）。刚复习完就是已掌握；
    # 只有「忘记了」是例外 —— 它把到期时间设成现在，于是立刻回到未掌握。
    due = _parse_timestamp(updated.get("next_review_due"))
    updated["status"] = "mastered" if due is not None and due > current else "unmastered"
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
    """到期的卡片。**唯一判据是 next_review_due**，不再看 status —— normalize_card 已经
    把 status 定义成「就是没到期」，两处各判一次迟早会不一致。"""
    current = now or utc_now()
    return [
        card for card in load_cards(now=current, user_id=user_id)
        if (_parse_timestamp(card.get("next_review_due")) or current) <= current
    ]


def review_card(card_id: str, quality: str, *, now: datetime | None = None, user_id: str = "") -> dict[str, Any] | None:
    cards = load_cards(user_id=user_id)
    for index, card in enumerate(cards):
        if card.get("id") == card_id:
            cards[index] = calculate_next_review(card, quality, now=now)
            save_cards(cards, user_id=user_id)
            return cards[index]
    return None
