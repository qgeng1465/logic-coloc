# -*- coding: utf-8 -*-
"""复盘记录 store 的纯函数层测试（不碰 HTTP、不碰真实 data/）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from logic_coloc.api import review_store


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(review_store, "REVIEWS_FILE", tmp_path / "review_records.json")
    monkeypatch.setattr(review_store, "DATA_DIR", tmp_path)
    return tmp_path / "review_records.json"


def test_missing_file_reads_as_empty() -> None:
    assert review_store.load_reviews() == []
    assert review_store.get_review("nope") is None


def test_append_creates_then_appends_to_same_record() -> None:
    first = review_store.append_review_turn(
        "sess-a", question="第一个问题", answer="点评 A", note_id="n1", note_title="二叉树",
    )
    assert first["id"] == "sess-a"
    assert first["noteTitle"] == "二叉树"
    assert first["startedAt"].endswith("Z"), "时间戳统一 Z 结尾 UTC"
    assert [message["role"] for message in first["messages"]] == ["user", "assistant"]

    second = review_store.append_review_turn("sess-a", question="第二个问题", answer="点评 B")
    assert len(second["messages"]) == 4
    assert len(review_store.load_reviews()) == 1, "同一次复盘只能有一条记录"
    assert second["startedAt"] == first["startedAt"], "追加不该改动开始时间"


def test_append_keeps_first_non_empty_note_title() -> None:
    """标题可能只在某几轮才带上；已有值不被后来的空值覆盖。"""
    review_store.append_review_turn("sess-a", question="q", answer="a", note_title="二叉树")
    record = review_store.append_review_turn("sess-a", question="q2", answer="a2", note_title="")
    assert record["noteTitle"] == "二叉树"
    assert record["noteId"] == ""


def test_load_reviews_sorted_newest_first() -> None:
    review_store.append_review_turn("sess-old", question="q", answer="a")
    review_store.append_review_turn("sess-new", question="q", answer="a")
    assert [record["id"] for record in review_store.load_reviews()] == ["sess-new", "sess-old"]


def test_review_list_item_hides_messages_and_counts_turns() -> None:
    review_store.append_review_turn("sess-a", question="q1", answer="a1", note_title="二叉树")
    review_store.append_review_turn("sess-a", question="q2", answer="a2")
    item = review_store.review_list_item(review_store.load_reviews()[0])
    assert "messages" not in item
    assert item["turnCount"] == 2, "轮数按用户消息数算"
    assert set(item) == {"id", "noteId", "noteTitle", "startedAt", "updatedAt", "turnCount", "summary"}


def test_save_summary_and_delete() -> None:
    review_store.append_review_turn("sess-a", question="q", answer="a")
    saved = review_store.save_review_summary("sess-a", "这次复盘覆盖了……")
    assert saved and saved["summary"] == "这次复盘覆盖了……"
    assert review_store.save_review_summary("nope", "x") is None

    assert review_store.delete_review("sess-a") is True
    assert review_store.delete_review("sess-a") is False, "重复删除应返回 False"
    assert review_store.load_reviews() == []


def test_write_leaves_no_tmp_file(isolated_store) -> None:
    review_store.append_review_turn("sess-a", question="q", answer="a")
    assert not [path.name for path in isolated_store.parent.iterdir() if path.suffix == ".tmp"]


def test_corrupt_file_is_moved_aside_instead_of_wiped(isolated_store) -> None:
    """内容坏掉时不能直接返回 [] —— 那会让下一次 append 用一条新记录覆盖掉全部历史。"""
    isolated_store.write_text("{坏掉的内容", encoding="utf-8")

    assert review_store.load_reviews() == []
    assert isolated_store.with_suffix(".bak").exists(), "坏文件应被挪到 .bak 留作抢救"
    assert not isolated_store.exists()

    # 挪走之后再写，读到的是干净的新文件
    review_store.append_review_turn("sess-a", question="q", answer="a")
    assert [record["id"] for record in review_store.load_reviews()] == ["sess-a"]


def test_non_list_json_reads_as_empty(isolated_store) -> None:
    isolated_store.write_text('{"not": "a list"}', encoding="utf-8")
    assert review_store.load_reviews() == []


def test_records_are_written_as_readable_utf8(isolated_store) -> None:
    review_store.append_review_turn("sess-a", question="负反馈是什么？", answer="闭环抑制", note_title="负反馈")
    raw = isolated_store.read_text(encoding="utf-8")
    assert "负反馈是什么？" in raw, "中文不能转义成 \\uXXXX"
    assert json.loads(raw)[0]["noteTitle"] == "负反馈"
