# -*- coding: utf-8 -*-
"""后台发现任务的记录表会挂着完整结果常驻内存，必须有 TTL 清理（回归用）。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from logic_coloc.api import routes


def test_prune_keeps_running_and_fresh_jobs(monkeypatch) -> None:
    monkeypatch.setattr(routes, "DISCOVER_JOB_TTL_SECONDS", 900)
    now = time.monotonic()
    routes._discover_jobs.clear()
    routes._discover_jobs.update({
        ("u1", "fresh-done"): {"status": "done", "result": object(), "created": now - 10},
        ("u1", "stale-done"): {"status": "done", "result": object(), "created": now - 100000},
        ("u2", "stale-cancelled"): {"status": "cancelled", "result": object(), "created": now - 100000},
        ("u2", "stale-running"): {"status": "running", "result": None, "created": now - 100000},
        ("u2", "stale-queued"): {"status": "queued", "result": None, "created": now - 100000},
    })
    with routes._discover_jobs_lock:
        routes._prune_discover_jobs_locked()
    assert ("u1", "fresh-done") in routes._discover_jobs
    assert ("u1", "stale-done") not in routes._discover_jobs
    assert ("u2", "stale-cancelled") not in routes._discover_jobs
    # 跑着的任务不能清：轮询方还在等它
    assert ("u2", "stale-running") in routes._discover_jobs
    assert ("u2", "stale-queued") in routes._discover_jobs
    routes._discover_jobs.clear()


def test_prune_tolerates_missing_created(monkeypatch) -> None:
    monkeypatch.setattr(routes, "DISCOVER_JOB_TTL_SECONDS", 900)
    routes._discover_jobs.clear()
    routes._discover_jobs[("u3", "no-timestamp")] = {"status": "done", "result": None}
    with routes._discover_jobs_lock:
        routes._prune_discover_jobs_locked()
    # 没有 created 字段时按「未过期」处理，宁可慢清理也不能误删在查的结果
    assert ("u3", "no-timestamp") in routes._discover_jobs
    routes._discover_jobs.clear()
