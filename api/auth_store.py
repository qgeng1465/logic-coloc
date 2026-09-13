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

3. **匿名（游客）身份是一等公民，不是「没登录」。** `create_guest()` 产出的记录和
   正式账号共用同一张表、同一种 token、同一套按 uid 分目录的数据隔离 —— 区别只有两点：
   它没有密码哈希（`guest: True` 标记），以及它的 `usernameLower` 带 `guest:` 前缀，
   登录接口永远匹配不到。这样「打开就能用」和「数据是账号的」两件事可以同时成立：
   之后 `bind_credentials()` 把用户名密码补到**同一个 uid** 上，攒下的数据一个不丢。
   前端只在拿不到匿名身份时才回退到登录页。
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


class NotAGuestError(ValueError):
    """该账号已经绑定过用户名密码，不能重复绑定。路由层转 400。"""


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

# 匿名身份相关。见模块 docstring 第 3 条。
GUEST_FLAG = "guest"
# 匿名记录的 usernameLower 一律带这个前缀。`_USERNAME_RE` 不许出现 `:`，所以任何
# 能注册出来的用户名都不会撞上它 —— 登录接口的按 key 查表天然查不到匿名记录。
_GUEST_LOGIN_PREFIX = "guest:"
# 匿名身份上限。公开部署下每个首次访问的浏览器都会建一条，这是 users.json 唯一会被
# 陌生人持续写入的来源，必须封顶。超限时**淘汰最早建的那条记录**（只从 users.json 里
# 摘掉，磁盘上的 data/users/<uid>/ 与 uploads/<uid>/ 不删）—— 那个 token 从此校验不过，
# 等价于「很久没来过的匿名身份过期了」。正式账号永远不参与淘汰。
# ⚠️ 它必须是模块级常量（而不是每次读 config.MAX_GUESTS）：测试要能只改这一处。
MAX_GUEST_ACCOUNTS = config.MAX_GUESTS

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
    """对外投影：**绝不带 salt / passwordHash**。

    `guest` 键**只在是匿名身份时出现**。这一点是刻意的，别改成恒定输出：正式账号的
    投影就是「id / username / createdAt」三个键，两个既有测试
    （test_auth_store.test_public_user_never_exposes_credentials 与
    test_api.test_register_login_and_me）用 `set(user) == {...}` 钉住了这个形状，
    那是防凭证字段外泄的护栏 —— 为了多一个布尔值去改它不划算。
    消费端一律按「缺省即非匿名」读（`user.get("guest")`、JS 里 `!!user.guest`）。
    """
    projection = {
        "id": str(record.get("id") or ""),
        "username": str(record.get("username") or ""),
        "createdAt": str(record.get("createdAt") or ""),
    }
    if record.get(GUEST_FLAG):
        projection[GUEST_FLAG] = True
    return projection


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
        # 「第一个账号」只数正式账号：匿名身份可能先被建出来（前端首访就建），
        # 那不该把旧全局数据交给一个随时会过期的游客。
        is_first_account = len([item for item in users if not item.get(GUEST_FLAG)]) == 1

    user_paths.ensure_user_storage(record["id"])
    if is_first_account:
        _adopt_legacy_data(record["id"])
    return public_user(record)


def create_guest() -> dict:
    """建一个**免注册的匿名身份**并落盘，返回对外投影。

    和 `register()` 的差别只有：没有密码（`passwordHash`/`salt` 为空串）、带
    `guest: True` 标记、不继承旧全局数据（游客是临时的，不该接手本地开发数据）。
    其余一切照旧 —— 同一个 uid 命名空间、同一套 `data/users/<uid>/` 隔离、同一种 token，
    所以前端「打开就能用」而所有接口一行都不用为游客特殊处理。

    要变成正式账号就调 `bind_credentials()`，uid 不变，数据不丢。
    """
    user_id = uuid4().hex
    record = {
        "id": user_id,
        "username": f"游客{user_id[:6]}",
        "usernameLower": f"{_GUEST_LOGIN_PREFIX}{user_id}",
        "salt": "",
        "passwordHash": "",
        "iterations": _ITERATIONS,
        "createdAt": _iso(utc_now()),
        GUEST_FLAG: True,
    }
    with _accounts_lock:
        users = _read_users_unlocked()
        guests = [item for item in users if item.get(GUEST_FLAG)]
        if len(guests) >= MAX_GUEST_ACCOUNTS:
            # users 是按建立顺序追加的，第一个 guest 就是最早的。只摘记录，不动磁盘：
            # 删文件是不可逆的，而这次淘汰本来就是「很久没来的匿名身份过期了」。
            evicted = str(guests[0].get("id") or "")
            users = [item for item in users if str(item.get("id") or "") != evicted]
        users.append(record)
        _write_users_unlocked(users)

    user_paths.ensure_user_storage(user_id)
    return public_user(record)


def bind_credentials(user_id: str, username: str, password: str) -> dict:
    """给一个匿名身份补上用户名密码，**uid 不变**（所以数据一个都不丢）。

    抛 `NotAGuestError`（已经是正式账号）、`UsernameTakenError`（重名）、
    `ValueError`（格式不合规），路由层分别转 400 / 409 / 422。
    """
    username, password = validate_credentials(username, password)
    key = username.casefold()
    salt = secrets.token_bytes(SALT_BYTES)
    with _accounts_lock:
        users = _read_users_unlocked()
        record = next((item for item in users if str(item.get("id") or "") == user_id), None)
        if record is None:
            raise ValueError("账号不存在")
        if not record.get(GUEST_FLAG):
            raise NotAGuestError("这个账号已经绑定过用户名和密码了")
        if any(str(item.get("usernameLower")) == key for item in users):
            raise UsernameTakenError("该用户名已被注册")
        record.update({
            "username": username,
            "usernameLower": key,
            "salt": salt.hex(),
            "passwordHash": _hash_password(password, salt, _ITERATIONS),
            "iterations": _ITERATIONS,
            "boundAt": _iso(utc_now()),
        })
        record.pop(GUEST_FLAG, None)
        _write_users_unlocked(users)
    return public_user(record)


def authenticate(username: str, password: str) -> dict | None:
    """校验密码。失败一律返回 None（不区分「用户名不存在」和「密码错」）。"""
    key = (username or "").strip().casefold()
    with _accounts_lock:
        users = _read_users_unlocked()
    record = next((item for item in users if str(item.get("usernameLower")) == key), None)
    if record is not None and record.get(GUEST_FLAG):
        # 匿名记录没有密码哈希，正常输入（不含 `:`）也匹配不到它。这里再挡一次是
        # 防御性的：把「falsy 的 passwordHash 恰好被比中」这件事从可能变成不可能。
        record = None
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
