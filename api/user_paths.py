# -*- coding: utf-8 -*-
"""用户数据的落盘路径推导 —— 全项目唯一一处「这是谁的文件」判断。

note / card / review 三个 store 都是「读一个文件 → 改 → 原子写」。按账号隔离最省事
的做法不是给每个函数各塞一套路径常量（那会变成三份互不相干的推导，外加一处像
`routes.py` 里 `UPLOAD_DIR` 那样被 `from ... import` 取成 import 期快照的坑），
而是统一走这里：

    scoped(user_id, NOTES_FILE, "notes.json")

- **user_id 为空** → 返回传进来的旧全局路径（`data/notes.json` 等）。这条兼容路径是
  刻意保留的：既有测试、离线脚本都在用它，加账号体系不该把它们一起推翻。
- **user_id 非空** → `data/users/<uid>/<filename>`。

uid 由服务端 `uuid4().hex` 生成。这里仍用正则把格式钉死，是因为它是唯一一处
「外部输入直连文件路径」的地方，`user_id="../../etc"` 必须在这里就被拦住。

所有常量都在**函数体内**取（不是默认参数、不是 import 期快照），这样
`monkeypatch.setattr(user_paths, "DATA_DIR", tmp_path)` 一处生效、全体跟随。
"""
from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
UPLOAD_DIR = PROJECT_ROOT / "uploads"
USERS_DIR = DATA_DIR / "users"

_USER_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")


def is_valid_user_id(user_id: str) -> bool:
    return bool(_USER_ID_RE.match(str(user_id or "")))


def user_dir(user_id: str) -> Path:
    """某个账号的数据目录。uid 非法直接抛，绝不拼进路径。"""
    if not is_valid_user_id(user_id):
        raise ValueError("invalid user id")
    return USERS_DIR / user_id


def ensure_user_storage(user_id: str) -> Path:
    directory = user_dir(user_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def scoped(user_id: str, legacy_path: Path, filename: str) -> Path:
    """user_id 为空 → 旧的全局文件；否则该账号目录下的同名文件。"""
    if not user_id:
        return legacy_path
    return user_dir(user_id) / filename


def scoped_upload(user_id: str, filename: str) -> Path:
    """上传附件同样按账号分子目录。

    注意 `/uploads` 这个静态挂载**没有鉴权**，是故意的：`<img src="...">` 不会带
    Authorization 头，挂上认证整个附件预览就全裂了。这里靠「32 位随机 uid +
    uuid4 文件名」提供不可猜性，与改动前的安全级别相同（此前是扁平目录 + uuid 文件名）。
    """
    if not user_id:
        return UPLOAD_DIR / filename
    return UPLOAD_DIR / user_id / filename
