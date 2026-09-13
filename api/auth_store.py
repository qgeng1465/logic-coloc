# -*- coding: utf-8 -*-
"""账号：注册、登录、登录态 token，以及每个账号的资料（昵称 / 签名 / 头像 / 能量）。

两处刻意的设计，都值得先说明，免得后来者「顺手改回去」：

1. **密码哈希不引入任何新依赖。** `requirements.txt` 是版本锁定的已验证清单，里面
   没有 passlib / bcrypt / argon2。本地恰好装着 `bcrypt` 是别的包拖进来的，而线上
   镜像只 `pip install -r requirements.txt` —— 一旦 import 就崩。所以用标准库的
   PBKDF2-HMAC-SHA256（每账号独立随机盐 + 常量时间比较）。

2. **登录态是无状态的 HMAC 签名 token，不是内存 dict。** `SessionManager` 那种内存
   dict 服务一重启就把所有人踢回登录页，而这个应用部署在 CloudBase 上、每次重新部署
   都会重启。已知代价：**服务端无法吊销 token**，退出登录只能靠前端删掉它。对一个
   学习工具够用；真要吊销得再加一张黑名单表。

凭证与资料**分两个文件**：`data/users.json` 只放账号与 hash，
`data/users/<uid>/profile.json` 只放昵称/签名/头像/能量。分开是为了不会哪天顺手把
整个 user 记录（连带 hash）返回给前端。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from threading import Lock
from uuid import uuid4

from .. import config
from . import user_paths
from .card_store import _iso, utc_now


class UsernameTakenError(ValueError):
    """用户名已被注册。路由层转 409。"""


# 用户名：2-20 位中英文、数字、下划线、短横线。密码只限长度，不做复杂度要求
# ——这是个学习工具，不是网银，逼用户大小写符号混排只会让人把密码写在便签上。
# 汉字范围用 一-鿿（CJK 统一汉字区全段），别写成「一-龥」—— 龥 是
# U+9FA5，会把该区末尾那 90 来个字（U+9FA6-U+9FFF）误拒。另收 〇(U+3007)，
# 中文姓名/年份里会用到。扩展区 A/B 等冷僻字不收，用到的概率极低。
_USERNAME_RE = re.compile(r"\A[A-Za-z0-9_\-一-鿿〇]{2,20}\Z")
MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_LENGTH = 128
# PBKDF2 轮数。写进每条用户记录，将来调高不会让老账号登不上。
_ITERATIONS = 200_000
# 30 天。前端把 token 存在 localStorage，过期后重新登录。
TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
SALT_BYTES = 16

DEFAULT_PROFILE: dict = {
    "nickname": "学术萌新",
    "signature": "记录每一次深度思考，留给未来的自己。",
    "avatarUrl": "",
    "points": 0,
    "pointsReason": "",
    "pointsUpdatedAt": "",
}

# 一把进程级锁盖住所有 read-modify-write，和 review_store 的做法一致。
_accounts_lock = Lock()
_secret_cache: bytes | None = None


def users_file():
    """凭证索引。**函数而非常量**：常量会在 import 期定死，测试就没法只改一处隔离。"""
    return user_paths.DATA_DIR / "users.json"


def _secret_file():
    return user_paths.DATA_DIR / ".secret_key"


def _profile_file(user_id: str):
    return user_paths.user_dir(user_id) / "profile.json"


# --------------------------------------------------------------------------- 凭证读写


def _read_users_unlocked() -> list[dict]:
    try:
        value = json.loads(users_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _write_users_unlocked(users: list[dict]) -> None:
    """原子写：先 .tmp 再 replace，避免中途失败留下半截 JSON 把所有人挡在门外。"""
    path = users_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(users, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _hash_password(password: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def public_user(record: dict) -> dict:
    """对外投影：**绝不带 salt / passwordHash**。"""
    return {
        "id": str(record.get("id") or ""),
        "username": str(record.get("username") or ""),
        "createdAt": str(record.get("createdAt") or ""),
    }


def validate_credentials(username: str, password: str) -> tuple[str, str]:
    """校验并规整。抛 ValueError（消息直接给用户看），调用方转 422。"""
    username = (username or "").strip()
    password = password or ""
    if not _USERNAME_RE.match(username):
        raise ValueError("用户名需为 2-20 位中英文、数字、下划线或短横线")
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ValueError(f"密码长度需在 {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} 位之间")
    return username, password


def register(username: str, password: str) -> dict:
    """建账号并落盘。返回对外投影（不含 hash）。"""
    username, password = validate_credentials(username, password)
    key = username.casefold()
    salt = secrets.token_bytes(SALT_BYTES)
    record = {
        "id": uuid4().hex,
        "username": username,
        # 查重按 casefold 后的键：Alice 和 alice 是同一个账号，否则登录会撞车。
        "usernameLower": key,
        "salt": salt.hex(),
        "passwordHash": _hash_password(password, salt, _ITERATIONS),
        "iterations": _ITERATIONS,
        "createdAt": _iso(utc_now()),
    }
    with _accounts_lock:
        users = _read_users_unlocked()
        if any(str(item.get("usernameLower")) == key for item in users):
            raise UsernameTakenError("该用户名已被注册")
        users.append(record)
        _write_users_unlocked(users)
        is_first_account = len(users) == 1

    user_paths.ensure_user_storage(record["id"])
    if is_first_account:
        _adopt_legacy_data(record["id"])
    return public_user(record)


def authenticate(username: str, password: str) -> dict | None:
    """校验密码。失败一律返回 None（不区分「用户名不存在」和「密码错」）。"""
    key = (username or "").strip().casefold()
    with _accounts_lock:
        users = _read_users_unlocked()
    record = next((item for item in users if str(item.get("usernameLower")) == key), None)
    if record is None:
        # 用户名不存在时也付一次同等的哈希代价：否则响应时间直接暴露「这个账号存不存在」，
        # 等于免费送人一个用户名枚举接口。
        _hash_password(password or "", b"\x00" * SALT_BYTES, _ITERATIONS)
        return None
    try:
        salt = bytes.fromhex(str(record.get("salt") or ""))
    except ValueError:
        return None
    digest = _hash_password(password or "", salt, int(record.get("iterations") or _ITERATIONS))
    if not hmac.compare_digest(digest, str(record.get("passwordHash") or "")):
        return None
    return public_user(record)


def get_user(user_id: str) -> dict | None:
    if not user_paths.is_valid_user_id(user_id):
        return None
    with _accounts_lock:
        users = _read_users_unlocked()
    record = next((item for item in users if str(item.get("id")) == user_id), None)
    return public_user(record) if record else None


# --------------------------------------------------------------------- 登录态 token


def _load_secret() -> bytes:
    configured = str(config.SECRET_KEY or "").strip()
    if configured:
        return configured.encode("utf-8")
    # 没配环境变量就自己生成一份存盘：本地开箱即用，且重启后 token 依然有效。
    # 线上应当由平台注入 LC_SECRET_KEY（见 docs/部署指南-CloudBase.md）。
    path = _secret_file()
    try:
        existing = path.read_bytes()
        if existing:
            return existing
    except OSError:
        pass
    generated = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(generated)
    temporary.replace(path)
    return generated


def secret() -> bytes:
    global _secret_cache
    if _secret_cache is None:
        with _accounts_lock:
            if _secret_cache is None:
                _secret_cache = _load_secret()
    return _secret_cache


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def issue_token(user_id: str, *, ttl_seconds: int = TOKEN_TTL_SECONDS) -> str:
    """签一个无状态 token：`base64url(payload).base64url(HMAC-SHA256(payload))`。"""
    payload = json.dumps(
        {"uid": user_id, "exp": int(utc_now().timestamp()) + int(ttl_seconds)},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signature = hmac.new(secret(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def verify_token(token: str) -> str | None:
    """校验签名与有效期，通过则返回 uid，否则 None。任何异常都当校验失败。"""
    if not token or "." not in token:
        return None
    body, _, signature = token.partition(".")
    try:
        payload = _unb64(body)
        provided = _unb64(signature)
    except (ValueError, TypeError):
        return None
    # 先比签名再看内容：签名不过就没必要解析（也避免对未验证的输入做 JSON 解析）。
    if not hmac.compare_digest(hmac.new(secret(), payload, hashlib.sha256).digest(), provided):
        return None
    try:
        claims = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(claims, dict):
        return None
    try:
        expired = int(claims.get("exp") or 0) < int(utc_now().timestamp())
    except (TypeError, ValueError):
        return None
    if expired:
        return None
    user_id = str(claims.get("uid") or "")
    return user_id if user_paths.is_valid_user_id(user_id) else None


# -------------------------------------------------------------------------- 资料


def load_profile(user_id: str) -> dict:
    profile = dict(DEFAULT_PROFILE)
    try:
        stored = json.loads(_profile_file(user_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return profile
    if isinstance(stored, dict):
        for key in DEFAULT_PROFILE:
            # 只在键**缺失**（或为 null）时补默认值。空串是用户明确清空的，不能当成
            # 「没设过」—— 否则「清空签名」这个操作会永远无效。0 是合法值（能量可以是 0）。
            if stored.get(key) is not None:
                profile[key] = stored[key]
    return profile


def _write_profile_unlocked(user_id: str, profile: dict) -> None:
    directory = user_paths.ensure_user_storage(user_id)
    path = directory / "profile.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def save_profile(user_id: str, **fields) -> dict:
    """只接受资料字段；未传的字段保持原值。"""
    with _accounts_lock:
        profile = load_profile(user_id)
        for key in ("nickname", "signature", "avatarUrl"):
            if key in fields and fields[key] is not None:
                profile[key] = str(fields[key])
        _write_profile_unlocked(user_id, profile)
    return profile


def add_points(user_id: str, delta: int, reason: str = "") -> dict:
    """**服务端是能量的唯一权威值**，客户端只做乐观显示。

    下限夹在 0：扣分逻辑将来若不慎多扣，也不该出现负能量。
    """
    with _accounts_lock:
        profile = load_profile(user_id)
        total = max(0, int(profile.get("points") or 0) + int(delta))
        profile["points"] = total
        profile["pointsReason"] = str(reason or "")
        profile["pointsUpdatedAt"] = _iso(utc_now())
        _write_profile_unlocked(user_id, profile)
    return {
        "total": total,
        "reason": profile["pointsReason"],
        "lastUpdatedAt": profile["pointsUpdatedAt"],
    }


# ------------------------------------------------------------------------ 旧数据


def _adopt_legacy_data(user_id: str) -> None:
    """把旧的「无主」全局数据转交给第一个注册的账号。

    只在本地开发时真的会有内容：这三个文件在 `.dockerignore` 里被排除，线上镜像里
    `ensure_storage()` 只会造出一个空的 `notes.json`，所以线上这一步是空操作。

    旧文件**只读不删**：万一判断有误，原始数据还在原处，可人工抢救。
    """
    directory = user_paths.ensure_user_storage(user_id)
    for filename in ("notes.json", "cards.json", "review_records.json"):
        source = user_paths.DATA_DIR / filename
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, list) or not payload:
            continue
        (directory / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
