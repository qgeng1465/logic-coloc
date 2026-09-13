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


def test_due_cards_only_returns_due_unmastered(monkeypatch, tmp_path) -> None:
    cards_file = tmp_path / "cards.json"
    monkeypatch.setattr(card_store, "CARDS_FILE", cards_file)
    card_store.save_cards([
        {"id": "due", "front": "A", "back": "B", "status": "unmastered", "next_review_due": "2026-09-12T08:00:00Z"},
        {"id": "later", "front": "A", "back": "B", "status": "unmastered", "next_review_due": "2026-09-13T08:00:00Z"},
        {"id": "done", "front": "A", "back": "B", "status": "mastered", "next_review_due": "2026-09-11T08:00:00Z"},
    ])
    assert [card["id"] for card in card_store.due_cards(now=NOW)] == ["due"]
