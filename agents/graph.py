# -*- coding: utf-8 -*-
"""Stage 6B deterministic Single-Agent workflows."""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .. import config
from ..rag.corpus import Corpus
from ..rag.retriever import Retriever
from ..sessions.manager import SessionManager
from . import tools
from .schemas import Concept, CritiqueResult, HomologyResult, Intent, KnowledgeContext, MappingResult
from .state import AgentState

EXPLAIN_INTENTS = {Intent.EXPLAIN, Intent.SIMPLIFY, Intent.EXAMPLE, Intent.TERM, Intent.WHY, Intent.COMPARE, Intent.FOLLOW_UP}

def _get(state: AgentState | dict[str, Any], key: str, default: Any = None) -> Any:
    return state.get(key, default) if isinstance(state, dict) else getattr(state, key, default)

def load_context(state: AgentState | dict[str, Any], manager: SessionManager | None = None) -> dict[str, Any]:
    session_id = _get(state, "session_id")
    if not session_id or manager is None:
        return {}
    context = tools.get_session_context(manager, session_id)
    return {"session_id": context["session_id"], "knowledge_context": context["stable_knowledge_context"], "conversation_summary": context["conversation_summary"], "recent_messages": context["recent_messages"]}

def route_intent(state: AgentState | dict[str, Any]) -> dict[str, Intent]:
    explicit_intent = _get(state, "intent")
    if explicit_intent is not None:
        return {"intent": Intent(explicit_intent)}
    text = str(_get(state, "user_input") or "").strip().lower()
    recent = _get(state, "recent_messages") or []
    has_context = bool(_get(state, "knowledge_context") or _get(state, "conversation_summary") or recent)
    has = lambda *xs: any(x in text for x in xs)
    if has("跨学科", "同源", "逻辑相似", "底层逻辑", "其他领域类似", "其他学科", "类似的"): intent = Intent.DISCOVER_HOMOLOGY
    elif has("区别", "不同", "比较", "相比"): intent = Intent.COMPARE
    elif has_context and len(text) <= 12 and has("还是不懂", "继续讲", "再解释", "刚才那个", "再举一个", "你再说", "为什么", "什么意思"): intent = Intent.FOLLOW_UP
    elif has("为什么", "原因是什么", "原理是什么", "怎么导致"): intent = Intent.WHY
    elif has("举个例子", "举例", "给个例子", "实际例子", "示例"): intent = Intent.EXAMPLE
    elif has("简单一点", "简单解释", "说简单", "通俗一点", "大白话", "小白能懂"): intent = Intent.SIMPLIFY
    elif has("什么是", "指什么", "的定义", "这个词是什么意思"): intent = Intent.TERM
    else: intent = Intent.EXPLAIN
    return {"intent": intent}

def _knowledge(state: AgentState, profile: Any, terms: list[str]) -> KnowledgeContext:
    if state.knowledge_context is not None:
        concept = state.knowledge_context.concept
        if terms and concept.name.strip() == state.knowledge_context.source_text.strip():
            concept = concept.model_copy(update={"name": terms[0], "key_terms": terms})
        return state.knowledge_context.model_copy(update={"concept": concept, "logic_profile": profile, "key_terms": terms or state.knowledge_context.key_terms})
    text = state.user_input or ""
    return KnowledgeContext(source_text=text, concept=Concept(name=text), logic_profile=profile, key_terms=terms)

def extract_for_workflow(state: AgentState, manager: SessionManager | None = None) -> dict[str, Any]:
    extraction_text = state.user_input or ""
    if state.intent == Intent.FOLLOW_UP and state.knowledge_context is not None:
        history = "\n".join(message.content for message in state.recent_messages)
        extraction_text = "\n".join(filter(None, [state.knowledge_context.source_text, state.conversation_summary or "", history, extraction_text]))
    try:
        result = tools.extract_features(extraction_text)
        knowledge = _knowledge(state, result["logic_profile"], list(result.get("key_terms", [])))
        if manager and state.session_id: tools.update_session_knowledge(manager, state.session_id, knowledge)
        return {"logic_profile": result["logic_profile"], "knowledge_context": knowledge}
    except Exception as exc:
        return {"errors": [*state.errors, f"extract_features: {exc}"]}

def retrieve_for_discover(state: AgentState, retriever: Retriever | None = None) -> dict[str, Any]:
    if state.logic_profile is None:
        return {"candidate_concepts": [], "retrieval_scores": {}}
    retriever = retriever or Retriever(Corpus().load())
    query = {"text": state.user_input or "", "logic_profile": state.logic_profile}
    candidates = tools.retrieve_candidates(retriever, query, top_k=state.top_k, keywords=state.knowledge_context.key_terms if state.knowledge_context else None)
    return {"candidate_concepts": candidates, "retrieval_scores": {c.id: c.retrieval_score for c in candidates}}

def score_and_map(state: AgentState) -> dict[str, Any]:
    homology, mappings, critiques, reports, errors = [], [], [], [], list(state.errors)
    for candidate in state.candidate_concepts:
        try:
            result = tools.calculate_homonomy(state.logic_profile, candidate.logic_profile); score = float(result["score"])
            homology_result = HomologyResult(candidate_id=candidate.id, score=score, method=result.get("method", config.METHOD))
            homology.append(homology_result)
            # Keep every retrieved candidate visible, including low-similarity
            # results. Entity mapping/report generation remains gated by the
            # deterministic threshold, but the score and rationale are public.
            if score < config.THRESHOLD:
                continue
            candidate_text = "\n".join(filter(None, [candidate.concept, candidate.description, candidate.mechanism]))
            mapped = tools.map_entities(state.user_input or "", candidate_text, score)
            if not isinstance(mapped, MappingResult): continue
            mapped = mapped.model_copy(update={"candidate_id": candidate.id}); mappings.append(mapped)
            source_concept = state.knowledge_context.concept if state.knowledge_context else Concept(name=state.user_input or "")
            report = tools.build_learning_report(
                state.user_input or "", source_concept, state.logic_profile,
                candidate, homology_result, mapped,
            )
            reports.append(report)
            critique = tools.critique_learning_report(report)
            critiques.append(CritiqueResult(
                candidate_id=candidate.id,
                is_valid=critique.is_reliable,
                confidence=critique.confidence,
                issues=critique.warnings,
                summary=critique.reason,
                verdict=report.verdict,
            ))
        except Exception as exc: errors.append(f"candidate {candidate.id}: {exc}")
    return {"homonomy_results": homology, "mapping_results": mappings, "critique_results": critiques, "learning_reports": reports, "errors": errors}

def generate_response(state: AgentState) -> dict[str, Any]:
    if state.errors and state.logic_profile is None:
        text = "无法完成特征提取：" + state.errors[-1]
        return {"final_response": text}
    if state.intent == Intent.DISCOVER_HOMOLOGY:
        reliable_ids = {report.candidate_id for report in state.learning_reports if report.verdict == "RELIABLE_WITH_LIMITS"}
        reliable_names = [candidate.concept for candidate in state.candidate_concepts if candidate.id in reliable_ids]
        review_count = sum(report.verdict == "NEEDS_REVIEW" for report in state.learning_reports)
        if reliable_names:
            text = "找到有证据支持的有限结构对应：" + "、".join(reliable_names) + "。请结合报告中的成立条件、来源和失效边界使用。"
        elif review_count:
            text = "找到值得进一步核验的候选，但当前证据尚不足以标记为可靠。"
        else:
            text = "目前没有足够证据找到可靠的跨学科逻辑对应关系。"
        return {"final_response": text}
    try:
        text = tools.generate_explanation(
            state.user_input or "",
            state.intent.value if state.intent else Intent.EXPLAIN.value,
            state.knowledge_context,
            state.conversation_summary,
            state.recent_messages,
        )
        return {"final_response": text}
    except Exception as exc:
        return {
            "final_response": "暂时无法生成通俗解释，请稍后重试。",
            "errors": [*state.errors, f"generate_explanation: {exc}"],
        }

def save_conversation(state: AgentState, manager: SessionManager | None = None) -> dict[str, Any]:
    if manager and state.session_id and state.final_response:
        tools.add_session_message(manager, state.session_id, "user", state.user_input or "")
        tools.add_session_message(manager, state.session_id, "assistant", state.final_response)
    return {}

def build_graph(manager: SessionManager | None = None, retriever: Retriever | None = None):
    builder = StateGraph(AgentState)
    builder.add_node("load_context", lambda state: load_context(state, manager))
    builder.add_node("route_intent", route_intent)
    builder.add_node("extract_features", lambda state: extract_for_workflow(state, manager))
    builder.add_node("retrieve_candidates", lambda state: retrieve_for_discover(state, retriever))
    builder.add_node("calculate_homonomy", score_and_map)
    builder.add_node("generate_response", generate_response)
    builder.add_node("save_conversation", lambda state: save_conversation(state, manager))
    builder.add_edge(START, "load_context"); builder.add_edge("load_context", "route_intent")
    builder.add_edge("route_intent", "extract_features")
    builder.add_conditional_edges("extract_features", lambda state: "discover" if state.intent == Intent.DISCOVER_HOMOLOGY else "explain", {"discover": "retrieve_candidates", "explain": "generate_response"})
    builder.add_edge("retrieve_candidates", "calculate_homonomy"); builder.add_edge("calculate_homonomy", "generate_response"); builder.add_edge("generate_response", "save_conversation"); builder.add_edge("save_conversation", END)
    return builder.compile()
