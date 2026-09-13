# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from logic_coloc import config
from logic_coloc.agents import tools
from logic_coloc.agents.graph import build_graph
from logic_coloc.agents.schemas import Concept, DiscoverLearningReport, HomologyResult, Intent, KnowledgeContext, LogicProfile, MappingResult, Message, MessageRole
from logic_coloc.rag.schemas import CandidateConcept
from logic_coloc.sessions.manager import SessionManager


def profile(value: float = 0.5) -> LogicProfile:
    return LogicProfile(**{name: value for name in LogicProfile.DIMENSIONS})


def candidate(candidate_id: str, retrieval_score: float = 0.6) -> CandidateConcept:
    return CandidateConcept(id=candidate_id, concept=candidate_id, domain="测试", logic_profile=profile(), retrieval_score=retrieval_score)


def learning_report(candidate_id: str, score: float, reliable: bool = True) -> DiscoverLearningReport:
    return DiscoverLearningReport(
        candidate_id=candidate_id,
        source_concept=Concept(name="来源"),
        source_primer="来源入门",
        candidate_concept=Concept(name=candidate_id, domain="测试"),
        candidate_primer="候选入门",
        retrieval_score=0.6,
        homonomy_result=HomologyResult(candidate_id=candidate_id, score=score),
        mechanism_summary="共同机制",
        failure_boundaries=["边界"],
        known_differences=["差异"],
        verdict="RELIABLE_WITH_LIMITS" if reliable else "NEEDS_REVIEW",
        verdict_reason="检查结果",
    )


@pytest.fixture
def mocked_extract(monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(tools, "extract_features", lambda text: calls.append(text) or {"logic_profile": profile(), "key_terms": ["负反馈"]})
    monkeypatch.setattr(tools, "generate_explanation", lambda *args, **kwargs: "这是经过重组的通俗解释。")
    return calls


def invoke(text: str, *, manager=None, retriever=None, session_id=None, intent=None):
    state = {"user_input": text, "session_id": session_id}
    if intent is not None:
        state["intent"] = intent
    return build_graph(manager, retriever).invoke(state)


def test_explain_routes_extracts_and_generates(mocked_extract) -> None:
    result = invoke("什么是负反馈？")
    assert result["intent"] == Intent.TERM
    assert mocked_extract == ["什么是负反馈？"]
    assert isinstance(result["logic_profile"], LogicProfile)
    assert result["final_response"] == "这是经过重组的通俗解释。"


def test_explain_passes_context_to_explanation_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}
    monkeypatch.setattr(tools, "extract_features", lambda text: {"logic_profile": profile(), "key_terms": ["负反馈"]})
    monkeypatch.setattr(tools, "generate_explanation", lambda *args: seen.update(args=args) or "通俗解释")
    result = invoke("解释一下负反馈")
    assert result["final_response"] == "通俗解释"
    assert seen["args"][0] == "解释一下负反馈"
    assert seen["args"][2].key_terms == ["负反馈"]


def test_explicit_discover_intent_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_discover(monkeypatch, [], candidates=[])
    result = invoke("负反馈", intent=Intent.DISCOVER_HOMOLOGY)
    assert result["intent"] == Intent.DISCOVER_HOMOLOGY
    assert calls == ["extract", "retrieve"]


def test_discover_retrieval_receives_logic_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}
    monkeypatch.setattr(tools, "extract_features", lambda text: {"logic_profile": profile(0.6), "key_terms": ["神经网络"]})
    monkeypatch.setattr(tools, "retrieve_candidates", lambda retriever, query, **kwargs: seen.update(query=query) or [])
    invoke("神经网络", intent=Intent.DISCOVER_HOMOLOGY)
    assert seen["query"]["text"] == "神经网络"
    assert seen["query"]["logic_profile"] == profile(0.6)


def test_automatic_router_is_unchanged(mocked_extract) -> None:
    assert invoke("为什么负反馈可以提高稳定性？")["intent"] == Intent.WHY
    assert invoke("这个机制在其他学科有没有类似的？")["intent"] == Intent.DISCOVER_HOMOLOGY


def test_explicit_explain_overrides_user_input(mocked_extract) -> None:
    result = invoke("找一个跨学科同源概念", intent=Intent.EXPLAIN)
    assert result["intent"] == Intent.EXPLAIN


def test_all_understanding_intents_use_explain_workflow(mocked_extract) -> None:
    cases = {"解释一下": Intent.EXPLAIN, "简单一点": Intent.SIMPLIFY, "举个例子": Intent.EXAMPLE, "为什么会稳定": Intent.WHY, "两者有什么区别": Intent.COMPARE}
    for text, expected in cases.items():
        assert invoke(text)["intent"] == expected


def test_follow_up_uses_full_bounded_context(mocked_extract) -> None:
    manager = SessionManager(recent_message_limit=2)
    knowledge = KnowledgeContext(source_text="负反馈原文", concept=Concept(name="负反馈"))
    manager.create_session(session_id="s1", knowledge=knowledge, conversation_summary="之前讨论稳定性", recent_messages=[Message(role=MessageRole.USER, content="上一问")])
    result = invoke("还是不懂", manager=manager, session_id="s1")
    assert result["intent"] == Intent.FOLLOW_UP
    assert all(part in mocked_extract[-1] for part in ("负反馈原文", "之前讨论稳定性", "上一问", "还是不懂"))


def _mock_discover(monkeypatch, scores, reliable=True, candidates=None):
    calls = []
    candidates = candidates if candidates is not None else [candidate("c1")]
    monkeypatch.setattr(tools, "extract_features", lambda text: calls.append("extract") or {"logic_profile": profile(), "key_terms": ["反馈"]})
    monkeypatch.setattr(tools, "retrieve_candidates", lambda *a, **k: calls.append("retrieve") or candidates)
    values = iter(scores)
    monkeypatch.setattr(tools, "calculate_homonomy", lambda *a, **k: calls.append("homonomy") or {"score": next(values), "method": "cosine"})
    monkeypatch.setattr(tools, "map_entities", lambda *a, **k: calls.append("mapper") or MappingResult(candidate_id="", mapping={"a": "b"}))
    monkeypatch.setattr(tools, "build_learning_report", lambda *a, **k: calls.append("report") or learning_report(a[3].id, a[4].score, reliable))
    monkeypatch.setattr(tools, "critique_learning_report", lambda *a, **k: calls.append("critique") or SimpleNamespace(is_reliable=reliable, confidence=0.9, warnings=["限制"], reason="检查结果"))
    return calls


def test_discover_calls_tools_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_discover(monkeypatch, [0.95])
    result = invoke("找一个跨学科类似的概念")
    assert calls == ["extract", "retrieve", "homonomy", "mapper", "report", "critique"]
    assert result["critique_results"][0].is_valid is True
    assert "有证据支持" in result["final_response"]


def test_below_threshold_skips_mapper(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_discover(monkeypatch, [config.THRESHOLD - 0.01])
    result = invoke("跨学科类似概念")
    assert "mapper" not in calls and result["mapping_results"] == []
    assert "没有足够证据" in result["final_response"]


def test_above_threshold_calls_mapper(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_discover(monkeypatch, [config.THRESHOLD])
    invoke("跨学科类似概念")
    assert calls.count("mapper") == 1


def test_critique_rejection_is_not_recommended(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_discover(monkeypatch, [0.95], reliable=False)
    result = invoke("跨学科类似概念")
    assert result["critique_results"][0].is_valid is False
    assert "进一步核验" in result["final_response"]


def test_no_candidate_is_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_discover(monkeypatch, [], candidates=[])
    result = invoke("跨学科类似概念")
    assert calls == ["extract", "retrieve"]
    assert "没有足够证据" in result["final_response"]


def test_multiple_candidates_keep_separate_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [candidate("a", 0.9), candidate("b", 0.4)]
    _mock_discover(monkeypatch, [0.86, 0.95], candidates=items)
    result = invoke("跨学科类似概念")
    assert result["retrieval_scores"] == {"a": 0.9, "b": 0.4}
    assert {x.candidate_id: x.score for x in result["homonomy_results"]} == {"a": 0.86, "b": 0.95}


def test_session_write_back_uses_tools(monkeypatch: pytest.MonkeyPatch, mocked_extract) -> None:
    manager = SessionManager(); knowledge = KnowledgeContext(source_text="原文", concept=Concept(name="概念")); manager.create_session(session_id="s1", knowledge=knowledge)
    calls = []; original = tools.add_session_message
    monkeypatch.setattr(tools, "add_session_message", lambda *args, **kwargs: calls.append((args[2], args[3])) or original(*args, **kwargs))
    result = invoke("解释一下", manager=manager, session_id="s1")
    assert calls == [("user", "解释一下"), ("assistant", result["final_response"])]
    assert len(manager.get_session("s1").recent_messages) == 2


def test_review_stores_user_message_not_the_injected_prompt(mocked_extract) -> None:
    """笔记复盘进会话历史的必须是用户原话，不是拼好的导师指令。

    这是「附件全文进提示词」能否成立的前提：历史每轮全量重发
    （`RECENT_MESSAGE_LIMIT = 10`），而导师指令里塞着整篇笔记和附件全文 —— 存它等于
    把附件放大成 11 份，6.7 万字的培养方案聊到第六轮就超出上下文。
    """
    manager = SessionManager()
    manager.create_session(session_id="s1", knowledge=KnowledgeContext(source_text="笔记", concept=Concept(name="笔记")))
    prompt = "你现在是用户的笔记复盘导师。\n笔记原文：\n" + "长" * 5000
    build_graph(manager).invoke({
        "session_id": "s1", "user_input": prompt, "skip_extraction": True, "stored_input": "请提出第一个问题",
    })
    stored = [m.content for m in manager.get_session("s1").recent_messages]
    assert stored[0] == "请提出第一个问题"
    assert all("长" * 10 not in content for content in stored)


def test_stored_input_absent_falls_back_to_user_input(mocked_extract) -> None:
    """不传 stored_input 时行为与改动前逐字一致 —— 普通对话走的就是这条路。"""
    manager = SessionManager()
    manager.create_session(session_id="s1", knowledge=KnowledgeContext(source_text="原文", concept=Concept(name="概念")))
    build_graph(manager).invoke({"session_id": "s1", "user_input": "解释一下"})
    assert manager.get_session("s1").recent_messages[0].content == "解释一下"


def test_extract_failure_returns_explicit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "extract_features", lambda text: (_ for _ in ()).throw(RuntimeError("failed")))
    result = invoke("解释一下")
    assert result["errors"] == ["extract_features: failed"]
    assert "无法完成特征提取" in result["final_response"]


def test_candidate_error_does_not_abort_others(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [candidate("bad"), candidate("good")]; calls = _mock_discover(monkeypatch, [0.9], candidates=items)
    count = iter([RuntimeError("bad score"), {"score": 0.9, "method": "cosine"}])
    monkeypatch.setattr(tools, "calculate_homonomy", lambda *a, **k: calls.append("homonomy") or (lambda v: (_ for _ in ()).throw(v) if isinstance(v, Exception) else v)(next(count)))
    result = invoke("跨学科类似概念")
    assert result["homonomy_results"][0].candidate_id == "good"
    assert "candidate bad" in result["errors"][0]
