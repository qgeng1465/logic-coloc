# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from logic_coloc.agents.schemas import (  # noqa: E402
    Concept,
    HomologyResult,
    Intent,
    KnowledgeContext,
    LogicProfile,
    Message,
    MessageRole,
    Session,
)
from logic_coloc.agents.state import AgentState  # noqa: E402
from logic_coloc.api.schemas import (  # noqa: E402
    ChatRequest,
    ChatResponse,
    DiscoverRequest,
    DiscoverResponse,
    ExplainRequest,
    ExplainResponse,
)
from logic_coloc.rag.schemas import CandidateConcept  # noqa: E402


def _profile() -> LogicProfile:
    return LogicProfile(
        system_closure=0.8,
        causal_chain_length=0.7,
        negative_feedback_strength=0.95,
        randomness_entropy=0.3,
        zero_sum_resource_level=0.2,
    )


def _concept() -> Concept:
    return Concept(name="免疫负反馈", domain="免疫学", key_terms=["负反馈", "稳态"])


def test_logic_profile_creation() -> None:
    profile = _profile()

    assert profile.system_closure == 0.8
    assert profile.negative_feedback_strength == 0.95


def test_logic_profile_range_validation() -> None:
    with pytest.raises(ValidationError):
        LogicProfile(
            system_closure=1.2,
            causal_chain_length=0.7,
            negative_feedback_strength=0.95,
            randomness_entropy=0.3,
            zero_sum_resource_level=0.2,
        )


def test_logic_profile_core_conversion() -> None:
    profile = LogicProfile.from_core_vector([0, 50, 100, 25, 75])

    assert profile.system_closure == 0
    assert profile.causal_chain_length == 0.5
    assert profile.negative_feedback_strength == 1
    assert profile.to_core_vector() == [0, 50, 100, 25, 75]


def test_intent_enum() -> None:
    assert Intent.EXPLAIN.value == "EXPLAIN"
    assert Intent("DISCOVER_HOMOLOGY") is Intent.DISCOVER_HOMOLOGY


def test_candidate_concept_serialization() -> None:
    candidate = CandidateConcept(
        id="immune_negative_feedback",
        concept="免疫负反馈",
        domain="免疫学",
        logic_profile=_profile(),
        keywords=["负反馈"],
        retrieval_score=0.73,
    )

    restored = CandidateConcept.model_validate_json(candidate.model_dump_json())
    assert restored.id == candidate.id
    assert restored.retrieval_score == 0.73


def test_homology_result_default_threshold() -> None:
    result = HomologyResult(candidate_id="server_circuit_breaker", score=0.9)

    assert result.threshold == 0.85
    assert result.passed is True


def test_session_creation() -> None:
    session = Session(
        session_id="s1",
        source={"platform": "h5", "selected_text": "文本"},
        knowledge=KnowledgeContext(
            source_text="文本",
            concept=_concept(),
            logic_profile=_profile(),
        ),
    )

    assert session.session_id == "s1"
    assert session.recent_messages == []


def test_message_creation() -> None:
    message = Message(role=MessageRole.USER, content="为什么？")

    assert message.role is MessageRole.USER
    assert message.metadata == {}


def test_api_request_response_schemas() -> None:
    explain_request = ExplainRequest(text="专业文本")
    explain_response = ExplainResponse(
        session_id="s1",
        concept=_concept(),
        logic_profile=_profile(),
        explanation="一句话解释",
    )
    chat_request = ChatRequest(session_id="s1", message="为什么？")
    chat_response = ChatResponse(session_id="s1", answer="因为存在负反馈。")
    discover_request = DiscoverRequest(text="专业文本", top_k=3)
    discover_response = DiscoverResponse(concept=_concept())

    assert explain_request.source == ""
    assert explain_response.session_id == "s1"
    assert chat_request.message == "为什么？"
    assert chat_response.answer
    assert discover_request.top_k == 3
    assert discover_response.results == []


def test_agent_state_creation() -> None:
    state = AgentState(
        session_id="s1",
        user_input="发现同源",
        intent=Intent.DISCOVER_HOMOLOGY,
        concept=_concept(),
        logic_profile=_profile(),
    )

    assert state.session_id == "s1"
    assert state.candidate_concepts == []
    assert state.errors == []

