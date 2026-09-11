# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from logic_coloc import feature_extractor


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"content": [{"type": "thinking", "thinking": "hidden"}, {"type": "text", "text": "ok"}]}


def test_llm_sends_compatible_auth_headers(monkeypatch) -> None:
    captured = {}

    def fake_post(url, *, json, headers, timeout):
        captured.update(url=url, payload=json, headers=headers, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(feature_extractor.config, "API_KEY", "test-secret")
    monkeypatch.setattr(feature_extractor.requests, "post", fake_post)

    assert feature_extractor.llm("system", "user", retries=0) == "ok"
    assert captured["headers"] == {
        "anthropic-version": "2023-06-01",
        "Authorization": "Bearer test-secret",
        "x-api-key": "test-secret",
    }
    assert captured["payload"]["messages"] == [{"role": "user", "content": "user"}]


def test_llm_omits_auth_when_key_is_not_configured(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(feature_extractor.config, "API_KEY", "")
    monkeypatch.setattr(feature_extractor.requests, "post", lambda url, **kwargs: captured.update(kwargs) or FakeResponse())

    assert feature_extractor.llm("system", "user", retries=0) == "ok"
    assert captured["headers"] == {"anthropic-version": "2023-06-01"}
