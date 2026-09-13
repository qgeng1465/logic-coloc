# -*- coding: utf-8 -*-
from datetime import datetime, timezone

from logic_coloc.api import card_store


NOW = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)


def test_mastered_review_uses_ebbinghaus_intervals() -> None:
    card = {"id": "c1", "status": "unmastered", "review_stage": 0}
    first = card_store.calculate_next_review(card, "掌握", now=NOW)
    assert first["review_stage"] == 1
    assert first["next_review_due"] == "2026-09-13T08:00:00Z"
    third = card_store.calculate_next_review({**first, "review_stage": 2}, "掌握", now=NOW)
    assert third["review_stage"] == 3
    assert third["next_review_due"] == "2026-09-16T08:00:00Z"


def test_forgot_and_vague_review_schedule() -> None:
    forgot = card_store.calculate_next_review({"id": "c1", "review_stage": 4}, "忘记了", now=NOW)
    assert forgot["review_stage"] == 0
    assert forgot["next_review_due"] == "2026-09-12T08:00:00Z"
    vague = card_store.calculate_next_review({"id": "c1", "review_stage": 4}, "模糊", now=NOW)
    assert vague["review_stage"] == 4
    assert vague["next_review_due"] == "2026-09-13T08:00:00Z"


def test_every_interval_is_a_whole_number_of_days() -> None:
    """排期一律以「天」为粒度 —— 不许再出现分钟级间隔。

    产品口径是「今天复习过就算已掌握」，排期若用 5 分钟/30 分钟，那句「已掌握」只维持几分钟，
    而学习日志页、复习阶段筛选（1/2/4/7/15 天）、上面的曲线图 x 轴都按天写死，两边必然对不上。
    这条把两端钉在一起：任何一档短于一天、或不是整天，都会在这里失败。
    """
    table = tuple(card_store.EBBINGHAUS_INTERVALS) + tuple(card_store.EBBINGHAUS_LONG_INTERVALS)
    assert table, "排期表不能是空的"
    for seconds in table:
        assert seconds % 86400 == 0, f"{seconds} 秒不是整天 —— 排期表漂回分钟级了"
        assert seconds >= 86400
    # 每一档都要严格变长，否则「后面复习得越来越疏」这句话不成立。
    assert list(table) == sorted(set(table)), "排期表必须严格递增且无重复"
    # 「模糊」走最短一档，同样得是整天（曾经是 30 分钟）。
    vague = card_store.calculate_next_review({"id": "c1", "review_stage": 4}, "模糊", now=NOW)
    assert vague["next_review_due"] == "2026-09-13T08:00:00Z"


def test_due_cards_ignores_the_stored_status(monkeypatch, tmp_path) -> None:
    """到期与否只看 next_review_due，记录的 status 不算数。

    fixture 故意写两条自相矛盾的卡片：一条说已掌握却早就过期、一条说未掌握却还没到点。
    新规则下必须以到期时间为准 —— 「今天复习过就是已掌握，到点自动变回未掌握」这条产品
    规则就落在这里；status 说谎时跟着时间走，才不会又出现两个口径对不上。
    """
    cards_file = tmp_path / "cards.json"
    monkeypatch.setattr(card_store, "CARDS_FILE", cards_file)
    card_store.save_cards([
        {"id": "due", "front": "A", "back": "B", "status": "mastered", "next_review_due": "2026-09-12T08:00:00Z"},
        {"id": "later", "front": "A", "back": "B", "status": "unmastered", "next_review_due": "2026-09-13T08:00:00Z"},
    ])
    assert [card["id"] for card in card_store.due_cards(now=NOW)] == ["due"]


def test_review_marks_mastered_until_the_next_due_time() -> None:
    """复习完就是已掌握；到了下次到期时间，不靠任何定时任务就自动变回未掌握。"""
    reviewed = card_store.calculate_next_review({"id": "c1", "review_stage": 0}, "掌握", now=NOW)
    assert reviewed["status"] == "mastered"
    assert card_store.normalize_card(reviewed, now=NOW)["status"] == "mastered"
    due = datetime.fromisoformat(reviewed["next_review_due"].replace("Z", "+00:00"))
    assert due > NOW, "复习后必须排到未来，否则「已掌握」一秒都维持不住"
    assert card_store.normalize_card(reviewed, now=due)["status"] == "unmastered"


def test_forgot_is_unmastered_at_once_and_vague_counts_as_reviewed() -> None:
    """「忘记了」把到期时间设成现在 → 立刻仍是未掌握；「模糊」排到最短一档（1 天）→ 算已掌握。"""
    forgot = card_store.calculate_next_review({"id": "c1", "review_stage": 3}, "忘记了", now=NOW)
    assert forgot["status"] == "unmastered"
    vague = card_store.calculate_next_review({"id": "c1", "review_stage": 3}, "模糊", now=NOW)
    assert vague["status"] == "mastered"
    assert vague["next_review_due"] == "2026-09-13T08:00:00Z"


def test_a_card_without_a_schedule_is_unmastered() -> None:
    """新卡（从没排过期）必须是未掌握 —— 这是「打开就有一张要复习」的那条路。"""
    assert card_store.normalize_card({"id": "c1"}, now=NOW)["status"] == "unmastered"
