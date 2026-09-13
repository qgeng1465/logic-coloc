# -*- coding: utf-8 -*-
"""书架与笔记本（笔记分类）的按账号存储。

这两样此前**只存在浏览器 localStorage**（键是 `logic_coloc_books_v1::<uid>` 和
`logic_coloc_note_categories_v1::<uid>`）。笔记、卡片、复盘、昵称、头像、能量早就搬到
服务端了，就剩它们没有，后果是换个浏览器或换台电脑登录同一个账号，书架和笔记本
会凭空消失。这个 store 补上这个洞。

结构上整份库存一个文件（`data/users/<uid>/library.json`），因为前端的模型就是
「书里装着卡」（`allReviewCards()` = `getBooks().flatMap(book => book.cards)`），
拆成两个文件只会让"书和卡不同步"变成可能。

⚠️ **已知的双轨：卡片的服务端位置有两处。** 这里 `library.json` 里内嵌着一份，
`card_store` 的 `cards.json`（`/api/cards/save`、`/api/cards/schedule` 在用）另有一份。
这不是本次引入的 —— 改动前「localStorage 里的书架」和「服务端卡片」本来就在各存各的。
本次**有意不合并**（合并要动 `/api/cards/*` 那一整条链路，是独立的一块），
所以两边都别当成脏数据删掉。真要合并时，以哪边为准得先想清楚。

写盘沿用另外三个 store 的规矩：`threading.Lock()` 锁住整个读-改-写、`.tmp` +
`Path.replace()` 原子写、`ensure_ascii=False` + 缩进 2。
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

from . import user_paths


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_FILE = PROJECT_ROOT / "data" / "library.json"
_library_lock = Lock()


def _library_path(user_id: str) -> Path:
    """库文件路径。带 user_id 走该账号目录，为空则维持旧的全局文件。

    与 `note_store._notes_path` 一样顺手补一次目录：账号目录是注册时建的，但
    「旧账号 + 从零挂载的持久化卷」这种组合下目录可能不在，写之前补比写的时候炸好。
    """
    if user_id:
        user_paths.ensure_user_storage(user_id)
    return user_paths.scoped(user_id, LIBRARY_FILE, "library.json")


def _empty() -> dict[str, list[dict[str, Any]]]:
    return {"books": [], "categories": []}


def _clean(value: Any) -> list[dict[str, Any]]:
    """只收 dict。文件被手改坏、或前端传来 null/字符串时，宁可丢这一项也别让整份库崩。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _read_unlocked(user_id: str) -> dict[str, list[dict[str, Any]]]:
    try:
        raw = json.loads(_library_path(user_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty()
    if not isinstance(raw, dict):
        return _empty()
    return {"books": _clean(raw.get("books")), "categories": _clean(raw.get("categories"))}


def load_library(*, user_id: str = "") -> dict[str, list[dict[str, Any]]]:
    """整份库。文件不在（新账号）就是两个空数组 —— 这正是"第一次登录应该是空的"。"""
    with _library_lock:
        return _read_unlocked(user_id)


def save_library(
    books: Any = None,
    categories: Any = None,
    *,
    user_id: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """只覆盖传进来的那部分，返回合并后的完整库。

    `None` 表示"这次不动这一半"，`[]` 表示"清空这一半" —— 前端保存书架时不该顺手
    把笔记本也覆盖掉，反之亦然。传 `None` 和传 `[]` 必须区分开，否则删光最后一本书
    会连带把分类也清掉。
    """
    with _library_lock:
        current = _read_unlocked(user_id)
        if books is not None:
            current["books"] = _clean(books)
        if categories is not None:
            current["categories"] = _clean(categories)
        path = _library_path(user_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
        return current
