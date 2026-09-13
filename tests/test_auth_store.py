# -*- coding: utf-8 -*-
"""账号 store 的纯函数层测试（不碰 HTTP、不碰真实 data/）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from logic_coloc.api import auth_store, user_paths


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    """把落盘根目录整个换到临时目录。

    只 patch `user_paths` 上的三个常量：三个 store + 账号 store 全都从这里取路径，
    所以这一处生效、全体跟随（这正是把路径推导收进 user_paths 的目的）。
    密钥也钉成固定值，免得测试去写 data/.secret_key。
    """
    monkeypatch.setattr(user_paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(user_paths, "USERS_DIR", tmp_path / "users")
    monkeypatch.setattr(user_paths, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(auth_store, "_secret_cache", b"test-secret")
    return tmp_path


# ----------------------------------------------------------------------- 注册 / 登录


def test_register_then_authenticate() -> None:
    user = auth_store.register("Alice", "hunter2")
    assert user["username"] == "Alice"
    assert user_paths.is_valid_user_id(user["id"])

    assert auth_store.authenticate("Alice", "hunter2")["id"] == user["id"]
    assert auth_store.authenticate("alice", "hunter2")["id"] == user["id"], "用户名大小写不敏感"
    assert auth_store.authenticate("Alice", "wrong") is None
    assert auth_store.authenticate("nobody", "hunter2") is None


def test_public_user_never_exposes_credentials() -> None:
    user = auth_store.register("Alice", "hunter2")
    assert set(user) == {"id", "username", "createdAt"}
    assert "salt" not in user and "passwordHash" not in user


def test_duplicate_username_is_rejected_case_insensitively() -> None:
    auth_store.register("Alice", "hunter2")
    with pytest.raises(auth_store.UsernameTakenError):
        auth_store.register("alice", "hunter2")
    with pytest.raises(auth_store.UsernameTakenError):
        auth_store.register("Alice", "another-password")


def test_password_is_never_stored_in_clear(isolated_store) -> None:
    auth_store.register("Alice", "hunter2")
    raw = (isolated_store / "users.json").read_text(encoding="utf-8")
    assert "hunter2" not in raw
    record = json.loads(raw)[0]
    assert record["iterations"] >= 100_000, "轮数写进记录里，将来调高不会让老账号登不上"
    assert len(bytes.fromhex(record["salt"])) == 16


@pytest.mark.parametrize(
    "username,password",
    [
        ("a", "hunter2"),          # 用户名太短
        ("a" * 21, "hunter2"),     # 用户名太长
        ("bad name", "hunter2"),   # 空格不在允许字符集里
        ("Alice", "12345"),        # 密码太短
        ("Alice", "x" * 129),      # 密码太长
    ],
)
def test_invalid_credentials_are_rejected(username: str, password: str) -> None:
    with pytest.raises(ValueError):
        auth_store.register(username, password)


# ------------------------------------------------------------------------ token


def test_token_round_trip_and_tampering() -> None:
    user = auth_store.register("Alice", "hunter2")
    token = auth_store.issue_token(user["id"])
    assert auth_store.verify_token(token) == user["id"]

    assert auth_store.verify_token("") is None
    assert auth_store.verify_token("garbage") is None
    assert auth_store.verify_token("no-dot-here") is None
    assert auth_store.verify_token(token + "x") is None

    body, _, signature = token.partition(".")
    assert auth_store.verify_token(f"{body}.{signature[:-2]}xx") is None, "改签名必须失效"


def test_expired_token_is_rejected() -> None:
    user = auth_store.register("Alice", "hunter2")
    assert auth_store.verify_token(auth_store.issue_token(user["id"], ttl_seconds=-1)) is None


def test_token_signed_with_another_key_is_rejected(monkeypatch) -> None:
    """换密钥（换实例 / 平台重新注入 LC_SECRET_KEY）后，旧 token 必须失效。"""
    user = auth_store.register("Alice", "hunter2")
    token = auth_store.issue_token(user["id"])
    monkeypatch.setattr(auth_store, "_secret_cache", b"another-secret")
    assert auth_store.verify_token(token) is None


def test_secret_key_env_wins_over_generated_file(monkeypatch, isolated_store) -> None:
    monkeypatch.setattr(auth_store, "_secret_cache", None)
    monkeypatch.setattr("logic_coloc.config.SECRET_KEY", "from-platform")
    assert auth_store.secret() == b"from-platform"
    assert not (isolated_store / ".secret_key").exists(), "有环境变量就不该再落盘"


# ------------------------------------------------------------------- 目录穿越防护


def test_invalid_user_id_is_rejected_everywhere() -> None:
    """uid 是唯一一处外部输入直连文件路径的地方，穿越必须在这里就被拦下。"""
    for bad in ("", "../../etc", "..", "not-a-hex-id", "A" * 32, "../" + "a" * 29):
        assert not user_paths.is_valid_user_id(bad)
        assert auth_store.get_user(bad) is None
        with pytest.raises(ValueError):
            user_paths.user_dir(bad)


def test_scoped_falls_back_to_the_legacy_path_when_anonymous() -> None:
    """user_id 为空 = 旧的全局文件。既有测试与离线脚本全靠这条兼容路径。"""
    legacy = Path("/tmp/whatever/notes.json")
    assert user_paths.scoped("", legacy, "notes.json") == legacy
    user_id = "a" * 32
    assert user_paths.scoped(user_id, legacy, "notes.json") == user_paths.USERS_DIR / user_id / "notes.json"


# ------------------------------------------------------------------------ 资料


def test_profile_defaults_and_partial_updates() -> None:
    user = auth_store.register("Alice", "hunter2")
    profile = auth_store.load_profile(user["id"])
    assert profile["nickname"] == "学术萌新"
    assert profile["points"] == 0

    updated = auth_store.save_profile(user["id"], nickname="看山")
    assert updated["nickname"] == "看山"
    assert updated["signature"] == profile["signature"], "没传的字段保持原值"
    assert auth_store.load_profile(user["id"])["nickname"] == "看山"


def test_clearing_a_field_actually_clears_it() -> None:
    """空串是「我要清空」，不是「没设过」—— 否则用户永远删不掉自己的签名。"""
    user = auth_store.register("Alice", "hunter2")
    auth_store.save_profile(user["id"], signature="")
    assert auth_store.load_profile(user["id"])["signature"] == ""
    assert auth_store.load_profile(user["id"])["nickname"] == "学术萌新", "没碰的字段仍是默认值"


def test_profile_file_survives_a_missing_or_null_field() -> None:
    """键缺失或为 null 才回落到默认值；老文件少写一个键不该让页面出现空白。"""
    user = auth_store.register("Alice", "hunter2")
    (user_paths.user_dir(user["id"]) / "profile.json").write_text(
        json.dumps({"nickname": None, "points": 0}), encoding="utf-8"
    )
    profile = auth_store.load_profile(user["id"])
    assert profile["nickname"] == "学术萌新"
    assert profile["points"] == 0
    assert profile["avatarUrl"] == ""


def test_points_accumulate_and_never_go_negative() -> None:
    user = auth_store.register("Alice", "hunter2")
    assert auth_store.add_points(user["id"], 30, "创建笔记")["total"] == 30
    assert auth_store.add_points(user["id"], 20, "复习")["total"] == 50
    assert auth_store.add_points(user["id"], -999, "手滑")["total"] == 0, "下限夹在 0"
    assert auth_store.load_profile(user["id"])["pointsReason"] == "手滑"


# ------------------------------------------------------------------- 落盘卫生


def test_writes_leave_no_tmp_file(isolated_store) -> None:
    user = auth_store.register("Alice", "hunter2")
    auth_store.save_profile(user["id"], nickname="看山")
    auth_store.add_points(user["id"], 10, "复习")
    assert [path.name for path in isolated_store.rglob("*.tmp")] == []


def test_corrupt_users_file_reads_as_empty_instead_of_crashing(isolated_store) -> None:
    (isolated_store / "users.json").write_text("{坏掉的内容", encoding="utf-8")
    assert auth_store.get_user("a" * 32) is None
    assert auth_store.authenticate("Alice", "hunter2") is None
    # 坏文件不该挡住注册：新账号照常建起来。
    assert auth_store.register("Alice", "hunter2")["username"] == "Alice"


def test_records_are_written_as_readable_utf8(isolated_store) -> None:
    auth_store.register("张三", "hunter2")
    raw = (isolated_store / "users.json").read_text(encoding="utf-8")
    assert "张三" in raw, "中文不能转义成 \\uXXXX"


# --------------------------------------------------------------- 旧数据继承


def test_first_account_adopts_legacy_data(isolated_store) -> None:
    (isolated_store / "notes.json").write_text(json.dumps([{"id": "n1"}], ensure_ascii=False), encoding="utf-8")
    (isolated_store / "cards.json").write_text(json.dumps([{"id": "c1"}], ensure_ascii=False), encoding="utf-8")
    (isolated_store / "review_records.json").write_text("[]", encoding="utf-8")

    first = auth_store.register("Alice", "hunter2")
    adopted = json.loads((user_paths.user_dir(first["id"]) / "notes.json").read_text(encoding="utf-8"))
    assert adopted == [{"id": "n1"}]

    second = auth_store.register("Bob", "hunter2")
    assert not (user_paths.user_dir(second["id"]) / "notes.json").exists(), "只有第一个账号继承"
    assert not (user_paths.user_dir(second["id"]) / "cards.json").exists()


def test_legacy_files_are_left_in_place(isolated_store) -> None:
    """判断失误时原始数据还在原处，可人工抢救 —— 所以只读不删。"""
    (isolated_store / "notes.json").write_text(json.dumps([{"id": "n1"}]), encoding="utf-8")
    auth_store.register("Alice", "hunter2")
    assert (isolated_store / "notes.json").exists()
