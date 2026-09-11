# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from logic_coloc import config
from logic_coloc.agents import tools
from logic_coloc.rag.corpus import Corpus
from logic_coloc.rag.retriever import Retriever


def score(corpus: Corpus, left_id: str, right_id: str) -> float:
    left = corpus.get(left_id)
    right = corpus.get(right_id)
    assert left is not None and right is not None
    return tools.calculate_homonomy(left.logic_profile, right.logic_profile)["score"]


def test_curated_corpus_has_provenance_and_learning_boundaries() -> None:
    corpus = Corpus().load()
    assert corpus.all()
    for item in corpus.all():
        assert item.sources, item.id
        assert item.mechanism_roles, item.id
        assert item.valid_conditions, item.id
        assert item.common_misconceptions, item.id


def test_positive_immune_circuit_breaker_baseline() -> None:
    corpus = Corpus().load()
    assert score(corpus, "immune_negative_feedback", "circuit_breaker") >= config.THRESHOLD


def test_negative_immune_quantum_baseline() -> None:
    corpus = Corpus().load()
    assert score(corpus, "immune_negative_feedback", "quantum_superposition") < config.THRESHOLD


def test_boundary_quantum_option_pair_is_recorded_not_silently_reclassified() -> None:
    corpus = Corpus().load()
    value = score(corpus, "quantum_superposition", "option_pricing")
    assert value >= config.THRESHOLD
    assert value < 0.95


def test_neural_network_retrieval_recall_at_five() -> None:
    corpus = Corpus().load()
    results = tools.retrieve_candidates(
        Retriever(corpus),
        "人工神经网络通过调整连接权重学习",
        top_k=5,
        keywords=["神经网络", "连接权重"],
    )
    assert "biological_neural_network" in {item.id for item in results}
