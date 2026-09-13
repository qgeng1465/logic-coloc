# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from logic_coloc import config
from logic_coloc.agents.schemas import Concept, EvidenceSource, HomologyResult, KnowledgeContext, LogicProfile, MappingResult, MessageRole
from logic_coloc.agents import tools
from logic_coloc.rag.corpus import Corpus
from logic_coloc.rag.retriever import Retriever
from logic_coloc.rag.schemas import CandidateConcept
from logic_coloc.sessions.manager import SessionManager


def profile(value: float = 0.5) -> LogicProfile:
    return LogicProfile(**{name: value for name in LogicProfile.DIMENSIONS})


def test_extract_features_converts_core_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools.feature_extractor, "extract_features", lambda text: ([0, 50, 100, 25, 75], ["反馈"]))
    result = tools.extract_features("文本")
    assert isinstance(result["logic_profile"], LogicProfile)
    assert result["logic_profile"].to_core_vector() == [0, 50, 100, 25, 75]
    assert result["key_terms"] == ["反馈"]


def test_generate_explanation_reuses_existing_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    monkeypatch.setattr(
        tools.feature_extractor,
        "llm",
        lambda system, user, **kwargs: captured.update(system=system, user=user, kwargs=kwargs) or "通俗回答",
    )
    result = tools.generate_explanation("专业原文", "EXPLAIN")
    assert result == "通俗回答"
    assert "不是摘要或复述原文" in captured["system"]
    assert "专业原文" in captured["user"]


def test_learning_report_rejects_type_mismatch_and_keeps_grounding(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = """{
      "source_primer": "人工网络入门",
      "candidate_primer": "生物网络入门",
      "mechanism_summary": "连接强度随经验改变",
      "intersection_lesson": "从可调连接出发，理解生物突触如何随活动改变，并形成网络适应。",
      "target_domain_lessons": [
        {"title":"神经元与突触","explanation":"神经元通过突触传递影响。","connection_to_source":"类似人工网络的节点和连接。","example":"感觉通路。","check_question":"突触承担什么作用？"},
        {"title":"突触可塑性","explanation":"活动能够改变突触效能。","connection_to_source":"可借连接参数变化理解。","example":"共同激活。","check_question":"可塑性改变的是什么？"}
      ],
      "transferable_knowledge":["连接强度影响信号传播"],
      "new_knowledge":["生物可塑性包含多种局部机制"],
      "understanding_checks":["为什么反向传播不能直接等同于突触可塑性？"],
      "mapping_evidence": [
        {"source_term":"连接权重","source_type":"state","source_role":"连接参数","target_term":"突触效能","target_type":"state","target_role":"连接强度","correspondence_reason":"二者都调节连接影响","evidence_refs":["source-1"],"limitations":"更新规则不同"},
        {"source_term":"误差反向传播","source_type":"process","source_role":"参数更新过程","target_term":"突触可塑性","target_type":"process","target_role":"连接变化过程","correspondence_reason":"二者都改变连接影响","evidence_refs":["source-1"],"limitations":["驱动信号不同"]},
        {"source_term":"机器学习","source_type":"process","source_role":"方法集合","target_term":"神经元","target_type":"entity","target_role":"细胞","correspondence_reason":"错误层级","evidence_refs":["source-1"],"limitations":[]}
      ],
      "valid_conditions":["只比较连接适应结构"],
      "failure_boundaries":["标准反向传播不是已证实的大脑算法"],
      "known_differences":["人工网络使用明确目标函数"],
      "prohibited_claims":["反向传播等于大脑学习"],
      "learning_next_steps":["比较局部学习规则"]
    }"""
    monkeypatch.setattr(tools.feature_extractor, "llm", lambda *args, **kwargs: payload)
    source = EvidenceSource(id="source-1", title="Reference", publisher_or_author="Author", url_or_identifier="id")
    candidate = CandidateConcept(
        id="bio", concept="生物神经网络", domain="神经科学", logic_profile=profile(), sources=[source],
    )
    homology = HomologyResult(candidate_id="bio", score=0.9)
    report = tools.build_learning_report(
        "人工神经网络", Concept(name="人工神经网络"), profile(), candidate, homology,
        MappingResult(candidate_id="bio", mapping={"连接权重": "突触效能", "机器学习": "神经元"}),
    )
    assert report.verdict == "RELIABLE_WITH_LIMITS"
    assert [item.source_term for item in report.mapping_evidence] == ["连接权重", "误差反向传播"]
    assert report.mapping_evidence[0].evidence_refs == ["source-1"]
    assert report.mapping_evidence[0].limitations == ["更新规则不同"]
    assert len(report.target_domain_lessons) == 2
    assert report.target_domain_lessons[0].check_answer == ""
    assert report.intersection_lesson
    assert any("机器学习 ≠ 神经元" in claim for claim in report.prohibited_claims)
    assert len(report.dimension_comparisons) == 5
    assert tools.critique_learning_report(report).is_reliable is True


def test_learning_report_retries_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    valid_payload = """{
      "source_primer":"来源入门","candidate_primer":"候选入门","mechanism_summary":"共同机制",
      "intersection_lesson":"从来源机制进入候选领域。",
      "target_domain_lessons":[],"transferable_knowledge":[],"new_knowledge":[],"understanding_checks":[],
      "mapping_evidence":[],"valid_conditions":[],"failure_boundaries":[],"known_differences":[],
      "prohibited_claims":[],"learning_next_steps":[]
    }"""
    calls = []

    def fake_llm(system, user, **kwargs):
        calls.append((system, kwargs))
        return '{"source_primer":' if len(calls) == 1 else valid_payload

    monkeypatch.setattr(tools.feature_extractor, "llm", fake_llm)
    candidate = CandidateConcept(id="candidate", concept="候选", domain="领域", logic_profile=profile())
    report = tools.build_learning_report(
        "来源", Concept(name="来源"), profile(), candidate,
        HomologyResult(candidate_id="candidate", score=0.9),
        MappingResult(candidate_id="candidate", mapping={}),
    )

    assert report.source_primer == "来源入门"
    assert len(calls) == 2
    assert calls[0][1]["max_tokens"] == 4600
    assert "上一次输出无法解析" in calls[1][0]


def test_high_score_without_valid_mapping_is_review_not_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = """{
      "source_primer":"来源入门","candidate_primer":"候选入门","mechanism_summary":"共享反馈结构",
      "intersection_lesson":"两者都根据状态变化形成反向调节。",
      "target_domain_lessons":[],"transferable_knowledge":[],"new_knowledge":[],"understanding_checks":[],
      "mapping_evidence":[],"valid_conditions":[],"failure_boundaries":["实现机制不同"],
      "known_differences":["领域对象不同"],"prohibited_claims":[],"learning_next_steps":[]
    }"""
    monkeypatch.setattr(tools.feature_extractor, "llm", lambda *args, **kwargs: payload)
    source = EvidenceSource(id="source-1", title="Reference", publisher_or_author="Author", url_or_identifier="id")
    candidate = CandidateConcept(id="eco", concept="生态稳态", domain="生态学", logic_profile=profile(), sources=[source])
    report = tools.build_learning_report(
        "负反馈", Concept(name="负反馈"), profile(), candidate,
        HomologyResult(candidate_id="eco", score=0.98, threshold=config.THRESHOLD),
        MappingResult(candidate_id="eco", mapping={}),
    )
    assert report.verdict == "NEEDS_REVIEW"
    assert "结构分数已通过" in report.verdict_reason


def test_calculate_homonomy_uses_core_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}
    def fake(a, b, method=None):
        seen["vectors"] = (a, b)
        return 0.7
    monkeypatch.setattr(tools.homonomy, "homonomy_score", fake)
    result = tools.calculate_homonomy(profile(0), profile(1))
    assert seen["vectors"] == ([0, 0, 0, 0, 0], [100, 100, 100, 100, 100])
    assert result == {"score": 0.7, "method": config.METHOD}


def test_map_entities_threshold_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []
    monkeypatch.setattr(tools.mapper, "map_entities", lambda a, b: called.append((a, b)) or {"A_terms": [], "B_terms": [], "mapping": {}})
    assert tools.map_entities("a", "b", config.THRESHOLD - 0.01)["status"] == "skipped"
    assert called == []
    result = tools.map_entities("a", "b", config.THRESHOLD)
    assert called == [("a", "b")]
    assert result.mapping == {}


def test_retrieve_candidates_delegates() -> None:
    retriever = Retriever(Corpus().load())
    results = tools.retrieve_candidates(retriever, "免疫负反馈", top_k=1, domain="免疫学", keywords=["负反馈"])
    assert len(results) == 1 and results[0].retrieval_score > 0


def test_critique_reliable_and_insufficient() -> None:
    reliable = tools.critique_homology("a", "b", 0.95, {"mapping": {"x": "y"}})
    weak = tools.critique_homology("a", "b", 0.2, {"mapping": {}})
    assert reliable.is_reliable is True and 0 <= reliable.confidence <= 1
    assert weak.is_reliable is False and weak.warnings


def test_session_tools_share_manager_storage() -> None:
    manager = SessionManager()
    knowledge = KnowledgeContext(source_text="文本", concept={"name": "概念"})
    session = manager.create_session(session_id="s1", knowledge=knowledge)
    tools.add_session_message(manager, session.session_id, MessageRole.USER, "问题")
    tools.update_session_summary(manager, "s1", "摘要")
    context = tools.get_session_context(manager, "s1")
    assert context["conversation_summary"] == "摘要"
    assert context["recent_messages"][0].content == "问题"
