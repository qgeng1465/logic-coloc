# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from logic_coloc.rag.corpus import Corpus
from logic_coloc.rag.retriever import Retriever


def _record(concept_id: str = "a", value: float = 0.5) -> dict:
    profile = {name: value for name in (
        "system_closure", "causal_chain_length", "negative_feedback_strength",
        "randomness_entropy", "zero_sum_resource_level",
    )}
    return {"id": concept_id, "concept": concept_id, "domain": "测试", "description": "d", "mechanism": "m", "logic_profile": profile, "keywords": [], "examples": []}


def _write(path: Path, *records: object) -> None:
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8")


def test_normal_load_and_order(tmp_path: Path) -> None:
    path = tmp_path / "corpus.jsonl"
    _write(path, _record("first"), _record("second"))
    corpus = Corpus(path).load()
    assert len(corpus) == 2
    assert [item.id for item in corpus.all()] == ["first", "second"]


def test_empty_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert len(Corpus(path).load()) == 0


def test_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        Corpus(path).load()


def test_missing_required_field(tmp_path: Path) -> None:
    record = _record()
    del record["concept"]
    path = tmp_path / "missing.jsonl"
    _write(path, record)
    with pytest.raises(ValidationError):
        Corpus(path).load()


def test_extra_field_rejected(tmp_path: Path) -> None:
    record = _record()
    record["unexpected"] = True
    path = tmp_path / "extra.jsonl"
    _write(path, record)
    with pytest.raises(ValidationError):
        Corpus(path).load()


def test_get_and_missing_get(tmp_path: Path) -> None:
    path = tmp_path / "corpus.jsonl"
    _write(path, _record("found"))
    corpus = Corpus(path).load()
    assert corpus.get("found").id == "found"
    assert corpus.get("missing") is None


def test_logic_profile_range_validation(tmp_path: Path) -> None:
    record = _record(value=1.1)
    path = tmp_path / "range.jsonl"
    _write(path, record)
    with pytest.raises(ValidationError):
        Corpus(path).load()


def test_default_corpus() -> None:
    corpus = Corpus().load()
    assert len(corpus) >= 10
    assert len({item.domain for item in corpus.all()}) >= 5


def test_default_corpus_can_recall_biological_neural_network() -> None:
    results = Retriever(Corpus().load()).retrieve("人工神经网络会调整连接权重进行学习", top_k=5)
    assert any(item.id == "biological_neural_network" for item in results)


def test_retriever_matching_filters_and_scores(tmp_path: Path) -> None:
    path = tmp_path / "corpus.jsonl"
    first, second = _record("负反馈"), _record("其他")
    first.update({"concept": "免疫负反馈", "domain": "免疫学", "description": "维持稳态", "mechanism": "抑制信号形成负反馈", "keywords": ["负反馈", "稳态"]})
    second.update({"concept": "熔断机制", "domain": "软件工程", "description": "故障隔离", "mechanism": "失败后切断请求", "keywords": ["熔断"]})
    _write(path, first, second)
    retriever = Retriever(Corpus(path).load())
    results = retriever.retrieve("免疫负反馈", keywords=["稳态"], domain="免疫学", top_k=1)
    assert len(results) == 1 and results[0].concept == "免疫负反馈"
    assert 0.0 <= results[0].retrieval_score <= 1.0


def test_retriever_top_k_validation_and_empty_results(tmp_path: Path) -> None:
    path = tmp_path / "corpus.jsonl"
    _write(path, _record("a"), _record("b"))
    retriever = Retriever(Corpus(path).load())
    with pytest.raises(ValueError, match="top_k"):
        retriever.retrieve("a", top_k=0)
    assert retriever.retrieve("不存在的词") == []


def test_retriever_order_is_stable_and_does_not_mutate_corpus(tmp_path: Path) -> None:
    path = tmp_path / "corpus.jsonl"
    a, b = _record("a"), _record("b")
    a["concept"], b["concept"] = "同分甲", "同分乙"
    a["keywords"], b["keywords"] = ["共同"], ["共同"]
    corpus = Corpus(path)
    _write(path, a, b)
    corpus.load()
    retriever = Retriever(corpus)
    first = retriever.retrieve("共同")
    second = retriever.retrieve("共同")
    assert [item.id for item in first] == [item.id for item in second]
    assert [item.concept for item in first] == sorted(["同分甲", "同分乙"])
    assert all(item.retrieval_score == 0.0 for item in corpus.all())


def test_retriever_profile_similarity_without_llm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "corpus.jsonl"
    _write(path, _record("a"))
    corpus = Corpus(path).load()
    import logic_coloc.feature_extractor as feature_extractor
    monkeypatch.setattr(feature_extractor, "llm", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LLM called")))
    query = {"text": "a", "logic_profile": _record()["logic_profile"]}
    assert Retriever(corpus).retrieve(query)


def test_profile_alone_does_not_force_unrelated_candidate() -> None:
    query = {
        "text": "完全不在当前知识库覆盖范围内的孤立事实",
        "logic_profile": _record()["logic_profile"],
    }
    assert Retriever(Corpus().load()).retrieve(query) == []


def test_profile_recall_is_not_vetoed_by_cross_domain_wording() -> None:
    query = {
        "text": "机器遇到磨损、参数漂移和外界扰动时，根据偏差不断自我纠错",
        "logic_profile": {
            "system_closure": 0.92,
            "causal_chain_length": 0.75,
            "negative_feedback_strength": 0.96,
            "randomness_entropy": 0.25,
            "zero_sum_resource_level": 0.1,
        },
    }
    results = Retriever(Corpus().load()).retrieve(query, keywords=["自我纠错", "扰动", "偏差"], top_k=5)
    ids = {item.id for item in results}
    assert "immune_negative_feedback" in ids
    assert "ecosystem_homeostasis" in ids


def test_feedback_aliases_recall_negative_feedback_without_profile() -> None:
    results = Retriever(Corpus().load()).retrieve("系统根据误差不断自我纠错", top_k=5)
    assert any(item.id == "immune_negative_feedback" for item in results)


def test_explicit_feedback_anchor_survives_unhelpful_extracted_terms() -> None:
    profile = {
        "system_closure": 0.9,
        "causal_chain_length": 0.75,
        "negative_feedback_strength": 0.95,
        "randomness_entropy": 0.25,
        "zero_sum_resource_level": 0.1,
    }
    results = Retriever(Corpus().load()).retrieve(
        {"text": "负反馈让机器根据输出偏差自我纠错", "logic_profile": profile},
        keywords=["机器磨损", "参数漂移", "不确定性"],
        top_k=5,
    )
    assert any(item.id == "immune_negative_feedback" for item in results)
