# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from logic_coloc.agents.schemas import Concept, KnowledgeContext, LogicProfile, Message, MessageRole  # noqa: E402
from logic_coloc.sessions.manager import (  # noqa: E402
    RECENT_MESSAGE_LIMIT,
    DuplicateSessionError,
    SessionManager,
    SessionNotFoundError,
)


def _profile(value: float = 0.5) -> LogicProfile:
    return LogicProfile(
        system_closure=value,
        causal_chain_length=value,
        negative_feedback_strength=value,
        randomness_entropy=value,
        zero_sum_resource_level=value,
    )


def _knowledge(name: str = "免疫负反馈") -> KnowledgeContext:
    return KnowledgeContext(
        source_text="T细胞通过抑制性分子启动负反馈。",
        concept=Concept(name=name, domain="免疫学", key_terms=["T细胞", "负反馈"]),
        logic_profile=_profile(),
        domain="免疫学",
        mechanism="异常发生后通过抑制机制维持稳定。",
    )


def test_create_session() -> None:
    manager = SessionManager()
    session = manager.create_session(knowledge=_knowledge(), source={"platform": "h5"})

    assert session.session_id
    assert session.source == {"platform": "h5"}
    assert session.knowledge.concept.name == "免疫负反馈"
    assert session.created_at == session.updated_at


def test_session_id_uniqueness() -> None:
    manager = SessionManager()
    manager.create_session(session_id="fixed", knowledge=_knowledge())

    with pytest.raises(DuplicateSessionError):
        manager.create_session(session_id="fixed", knowledge=_knowledge())


def test_get_session() -> None:
    manager = SessionManager()
    created = manager.create_session(session_id="s1", knowledge=_knowledge())

    fetched = manager.get_session("s1")
    assert fetched == created
    assert fetched is not created


def test_missing_session_handling() -> None:
    manager = SessionManager()

    with pytest.raises(SessionNotFoundError):
        manager.get_session("missing")


def test_update_session() -> None:
    manager = SessionManager()
    manager.create_session(session_id="s1", knowledge=_knowledge())

    updated = manager.update_session("s1", source={"platform": "zhihu"}, conversation_summary="已有摘要")
    assert updated.source == {"platform": "zhihu"}
    assert updated.conversation_summary == "已有摘要"
    assert updated.updated_at >= updated.created_at


def test_add_message() -> None:
    manager = SessionManager()
    manager.create_session(session_id="s1", knowledge=_knowledge())

    updated = manager.add_message("s1", Message(role=MessageRole.USER, content="这个概念是什么意思？"))
    assert len(updated.recent_messages) == 1
    assert updated.recent_messages[0].role is MessageRole.USER
    assert updated.recent_messages[0].content == "这个概念是什么意思？"


def test_multiple_messages_keep_order() -> None:
    manager = SessionManager()
    manager.create_session(session_id="s1", knowledge=_knowledge())
    contents = ["这个概念是什么意思？", "这是一个解释。", "我还是没懂。", "换一种说法。", "能不能举个例子？"]

    for index, content in enumerate(contents):
        role = MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT
        manager.add_message("s1", Message(role=role, content=content))

    session = manager.get_session("s1")
    assert [message.content for message in session.recent_messages] == contents


def test_update_knowledge_context() -> None:
    manager = SessionManager()
    manager.create_session(session_id="s1", knowledge=_knowledge())
    new_context = _knowledge(name="服务器熔断")

    updated = manager.update_knowledge_context("s1", new_context)
    assert updated.knowledge.concept.name == "服务器熔断"


def test_update_summary() -> None:
    manager = SessionManager()
    manager.create_session(session_id="s1", knowledge=_knowledge())

    updated = manager.update_summary("s1", "用户已经理解负反馈。")
    assert updated.conversation_summary == "用户已经理解负反馈。"


def test_get_context_for_agent() -> None:
    manager = SessionManager(recent_message_limit=2)
    manager.create_session(session_id="s1", knowledge=_knowledge(), conversation_summary="稳定摘要")
    manager.add_message("s1", Message(role=MessageRole.USER, content="第一条"))
    manager.add_message("s1", Message(role=MessageRole.ASSISTANT, content="第二条"))
    manager.add_message("s1", Message(role=MessageRole.USER, content="第三条"))

    context = manager.get_context_for_agent("s1")
    assert context["session_id"] == "s1"
    assert context["stable_knowledge_context"].concept.name == "免疫负反馈"
    assert context["conversation_summary"] == "稳定摘要"
    assert [message.content for message in context["recent_messages"]] == ["第二条", "第三条"]


def test_recent_message_limit_constant() -> None:
    manager = SessionManager()
    manager.create_session(session_id="s1", knowledge=_knowledge())
    for index in range(RECENT_MESSAGE_LIMIT + 3):
        manager.add_message("s1", Message(role=MessageRole.USER, content=f"消息 {index}"))

    context = manager.get_context_for_agent("s1")
    assert len(context["recent_messages"]) == RECENT_MESSAGE_LIMIT
    assert context["recent_messages"][0].content == "消息 3"


def test_sessions_do_not_share_messages_or_source() -> None:
    manager = SessionManager()
    first = manager.create_session(session_id="s1", knowledge=_knowledge(), source={"platform": "h5"})
    second = manager.create_session(session_id="s2", knowledge=_knowledge("量子叠加"))

    manager.add_message("s1", Message(role=MessageRole.USER, content="只属于第一个会话"))
    first.source["platform"] = "mutated-outside"
    second.recent_messages.append(Message(role=MessageRole.USER, content="外部污染"))

    assert manager.get_session("s1").source == {"platform": "h5"}
    assert [message.content for message in manager.get_session("s1").recent_messages] == ["只属于第一个会话"]
    assert manager.get_session("s2").recent_messages == []


def test_summarize_session_does_not_call_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    import logic_coloc.feature_extractor as feature_extractor

    def fail_if_called(*args: object, **kwargs: object) -> str:
        raise AssertionError("summarize_session must not call llm")

    monkeypatch.setattr(feature_extractor, "llm", fail_if_called)

    manager = SessionManager(recent_message_limit=2)
    manager.create_session(session_id="s1", knowledge=_knowledge())
    manager.add_message("s1", Message(role=MessageRole.USER, content="我还是没懂。"))
    manager.add_message("s1", Message(role=MessageRole.ASSISTANT, content="可以把它想成刹车系统。"))
    manager.add_message("s1", Message(role=MessageRole.USER, content="能不能举个例子？"))

    summary = manager.summarize_session("s1")
    assert "assistant: 可以把它想成刹车系统。" in summary
    assert "user: 能不能举个例子？" in summary
    assert "我还是没懂" not in summary
    assert manager.get_session("s1").conversation_summary == summary

