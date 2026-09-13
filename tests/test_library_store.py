# -*- coding: utf-8 -*-
"""书架/笔记本 store 的纯函数层测试（不碰 HTTP、不碰真实 data/）。

这个 store 存在的理由：书架和笔记本此前只活在浏览器 localStorage 里，换台设备登录
同一个账号就没了。它必须做到「新账号是空的」和「只有自己看得到自己的」两件事。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from logic_coloc.api import library_store, user_paths


UID = "a" * 32
OTHER_UID = "b" * 32


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    """把落盘根目录整个换到临时目录（照 test_auth_store.py 的做法）。"""
    monkeypatch.setattr(user_paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(user_paths, "USERS_DIR", tmp_path / "users")
    monkeypatch.setattr(user_paths, "UPLOAD_DIR", tmp_path / "uploads")
    return tmp_path


# --------------------------------------------------------------------------- 默认值


def test_missing_file_is_two_empty_arrays() -> None:
    """新账号第一次读必须是空的 —— 这正是「第一次登录不应该有默认值」的服务端一半。

    注意返回的是 `[]` 而不是 None：前端拿它直接当书架渲染，null 会在 map 时炸掉。
    """
    library = library_store.load_library(user_id=UID)
    assert library == {"books": [], "categories": []}


def test_load_does_not_create_the_file() -> None:
    """只读不该顺手写盘：新账号登录一次就在磁盘上留个空文件没有意义。"""
    library_store.load_library(user_id=UID)
    assert not (user_paths.USERS_DIR / UID / "library.json").exists()


# --------------------------------------------------------------------------- 读写往返


def test_round_trip_preserves_nested_cards() -> None:
    """卡片内嵌在书里，往返一圈不能掉东西 —— 前端全局都靠 getBooks() 这一个形状。"""
    books = [
        {
            "id": "book_1",
            "name": "系统科学",
            "icon": "▦",
            "cards": [
                {"id": "card_1", "front": "负反馈", "back": "输出反过来抑制偏差。", "status": "unmastered"}
            ],
        }
    ]
    library_store.save_library(books=books, user_id=UID)
    assert library_store.load_library(user_id=UID)["books"] == books


def test_categories_and_books_are_independent_halves() -> None:
    """只传一半时，另一半必须原样不动。

    这是这个接口最容易写错的地方：如果保存书架时把 categories 也当成"没传就是空"，
    用户每次存卡片都会把笔记本清光。
    """
    library_store.save_library(books=[{"id": "book_1"}], categories=[{"id": "cat_1"}], user_id=UID)

    after_books = library_store.save_library(books=[{"id": "book_2"}], user_id=UID)
    assert after_books["categories"] == [{"id": "cat_1"}], "存书架不该动笔记本"

    after_categories = library_store.save_library(categories=[{"id": "cat_2"}], user_id=UID)
    assert after_categories["books"] == [{"id": "book_2"}], "存笔记本不该动书架"


def test_explicit_empty_list_clears_that_half() -> None:
    """`[]` 和 `None` 必须是两回事 —— 删光最后一本书不能被当成"这次不传书架"。"""
    library_store.save_library(books=[{"id": "book_1"}], categories=[{"id": "cat_1"}], user_id=UID)
    result = library_store.save_library(books=[], user_id=UID)
    assert result["books"] == []
    assert result["categories"] == [{"id": "cat_1"}], "清空书架不该连带清空笔记本"


def test_save_returns_the_merged_library() -> None:
    """返回值直接回给前端当权威值，不能只回它刚提交的那一半。"""
    result = library_store.save_library(books=[{"id": "book_1"}], user_id=UID)
    assert set(result) == {"books", "categories"}


# --------------------------------------------------------------------------- 脏数据


def test_corrupt_json_falls_back_to_empty() -> None:
    path = user_paths.USERS_DIR / UID / "library.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 这不是 json", encoding="utf-8")
    assert library_store.load_library(user_id=UID) == {"books": [], "categories": []}


def test_non_dict_entries_are_dropped() -> None:
    """前端传 null 混进数组、或文件被手改坏时，丢坏项而不是让整份库读不出来。"""
    path = user_paths.USERS_DIR / UID / "library.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"books": [{"id": "ok"}, None, "字符串", 42], "categories": "不是数组"}),
        encoding="utf-8",
    )
    library = library_store.load_library(user_id=UID)
    assert library["books"] == [{"id": "ok"}]
    assert library["categories"] == []


def test_top_level_not_a_dict_falls_back_to_empty() -> None:
    path = user_paths.USERS_DIR / UID / "library.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert library_store.load_library(user_id=UID) == {"books": [], "categories": []}


def test_saving_garbage_does_not_crash() -> None:
    result = library_store.save_library(books="不是数组", categories=None, user_id=UID)
    assert result["books"] == []


# --------------------------------------------------------------------------- 原子写


def test_no_tmp_file_left_behind() -> None:
    library_store.save_library(books=[{"id": "book_1"}], user_id=UID)
    directory = user_paths.USERS_DIR / UID
    assert [p.name for p in directory.iterdir()] == ["library.json"], "不该留 .tmp 残骸"


def test_chinese_is_not_escaped_on_disk() -> None:
    """落盘要人可读：出问题时能直接打开文件看，而不是一堆 \\uXXXX。"""
    library_store.save_library(books=[{"id": "book_1", "name": "系统科学"}], user_id=UID)
    raw = (user_paths.USERS_DIR / UID / "library.json").read_text(encoding="utf-8")
    assert "系统科学" in raw
    assert raw.endswith("\n")


# --------------------------------------------------------------------------- 隔离与穿越


def test_accounts_are_isolated() -> None:
    library_store.save_library(books=[{"id": "mine"}], user_id=UID)
    assert library_store.load_library(user_id=OTHER_UID) == {"books": [], "categories": []}


@pytest.mark.parametrize("bad_id", ["../../etc", "..", "not-a-hex-id", "A" * 32, "../" + "a" * 29])
def test_path_traversal_is_rejected(bad_id: str) -> None:
    """uid 是唯一一处外部输入直连文件路径的地方。

    空串**不在这里**：它是合法的"走旧全局文件"兼容路径，单独由下一个用例覆盖
    （那个用例需要额外 patch 常量，混在参数化里会把真实 data/ 写脏）。
    """
    with pytest.raises(ValueError):
        library_store.save_library(books=[{"id": "x"}], user_id=bad_id)


def test_empty_user_id_uses_the_legacy_global_path(isolated_store, monkeypatch) -> None:
    """空 uid 走旧全局路径 `data/library.json`，与另外三个 store 的约定一致。

    ⚠️ 这里必须额外 patch `LIBRARY_FILE`：它是模块级常量（`Path(__file__).parents[1]/…`），
    **不经过 `user_paths`**，所以上面的 fixture 换不掉它。不 patch 的话这个用例会真的
    往仓库的 `data/library.json` 里写东西 —— `test_api.py` 里 patch `note_store.NOTES_FILE`
    和 `card_store.CARDS_FILE` 是同一个原因。
    """
    monkeypatch.setattr(library_store, "LIBRARY_FILE", isolated_store / "library.json")
    library_store.save_library(books=[{"id": "legacy"}], user_id="")
    assert (isolated_store / "library.json").exists()
    assert not user_paths.USERS_DIR.exists(), "不该因为空 uid 去建账号目录"
