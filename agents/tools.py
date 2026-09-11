# -*- coding: utf-8 -*-
"""Small adapter layer exposing existing Core, RAG, and Session services."""
from __future__ import annotations

import json
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .. import config, feature_extractor, homonomy, mapper
from ..agents.schemas import (
    Concept,
    CritiqueResult,
    DimensionComparison,
    DiscoverLearningReport,
    HomologyResult,
    KnowledgeContext,
    LearningSection,
    RecommendedBook,
    LogicProfile,
    MappingEvidence,
    MappingResult,
    Message,
    MessageRole,
)
from ..rag.retriever import Retriever
from ..rag.schemas import CandidateConcept
from ..sessions.manager import SessionManager


EXPLANATION_SYSTEM = """你是 Logic-Coloc 的知识解释器。你的任务不是摘要或复述原文，而是帮助普通中文读者真正理解它。
请严格基于用户提供的材料，不虚构论文结论或事实。回答应当：
1. 先用一句话说清楚核心意思；
2. 用通俗语言拆解关键机制和因果关系；
3. 给出一个贴切、具体的例子或类比，并说明类比的边界；
4. 对用户当前的问题直接作答。
避免大段照抄原文，只有必要时才引用极短片段。不要描述你自己的分析步骤。"""

LEARNING_REPORT_SYSTEM = """你是 Logic-Coloc 的跨学科教师和学习报告编辑器。系统已经用确定性算法计算了结构相似度，你不能修改、重算或夸大该分数。
请只使用输入中的用户原文、候选知识卡、来源和初始映射，生成严格 JSON。不得编造事实、来源、论文、URL、DOI 或页码。
首要目标是让只熟悉来源领域的读者，利用两个领域的交集真正学会候选领域的知识，而不是凑出三个词对。报告内容约六成用于教学、四成用于判定依据。
先写一段完整的 intersection_lesson：从读者已知的来源概念出发，逐步引入候选领域的对象、术语、机制和一个具体例子，不要只讨论两者像不像。
再生成 3 至 5 个 target_domain_lessons。每节必须包含 title、explanation、connection_to_source、example、check_question、check_answer；check_answer 必须直接回答自测问题。explanation 应讲候选领域本身的知识，不能只是映射理由。每节控制在 180 至 320 个中文字，其他列表每项控制在 80 个中文字以内，确保 JSON 完整闭合。
transferable_knowledge 说明来源领域哪些直觉可以带过去；new_knowledge 说明进入候选领域必须另学、不能从类比推出的知识；understanding_checks 给出读者可自测的问题。
映射类型只能是 entity、process、signal、state、objective、constraint、failure_mode。不要保留层级不一致的映射；不确定时宁可省略。
推荐 2 至 4 本真实存在且适合入门的候选领域书籍，放入 recommended_books，每项包含 title、author、reason、scope；不确定书名时不要编造。输出字段：source_primer, candidate_primer, mechanism_summary, intersection_lesson, target_domain_lessons, transferable_knowledge, new_knowledge, understanding_checks, mapping_evidence, valid_conditions, failure_boundaries, known_differences, prohibited_claims, learning_next_steps, recommended_books。
mapping_evidence 每项字段：source_term, source_type, source_role, target_term, target_type, target_role, correspondence_reason, evidence_refs, limitations。
所有列表都输出 JSON 数组，不要输出 Markdown 或额外文字。"""

DIMENSION_LABELS = {
    "system_closure": "系统封闭性",
    "causal_chain_length": "因果链长度",
    "negative_feedback_strength": "负反馈强度",
    "randomness_entropy": "随机性与熵",
    "zero_sum_resource_level": "资源零和性",
}


class CritiqueOutput(BaseModel):
    """Tool-facing critique contract; logic similarity is not scientific equivalence."""

    model_config = ConfigDict(extra="forbid")

    is_reliable: bool
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason: str
    warnings: list[str] = Field(default_factory=list)


def extract_features(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    vector, key_terms = feature_extractor.extract_features(text)
    if len(vector) != len(LogicProfile.DIMENSIONS):
        raise ValueError("feature extractor returned an invalid profile dimension count")
    return {"logic_profile": LogicProfile.from_core_vector(vector), "key_terms": list(key_terms)}


def generate_explanation(
    text: str,
    intent: str,
    knowledge_context: KnowledgeContext | None = None,
    conversation_summary: str | None = None,
    recent_messages: Sequence[Message] | None = None,
) -> str:
    """Generate a grounded explanation through the project's existing LLM client."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    payload = {
        "intent": getattr(intent, "value", intent),
        "current_question": text,
        "knowledge_context": knowledge_context.model_dump(mode="json") if knowledge_context else None,
        "conversation_summary": conversation_summary or "",
        "recent_messages": [message.model_dump(mode="json") for message in recent_messages or []],
    }
    return feature_extractor.llm(
        EXPLANATION_SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        max_tokens=1600,
        temperature=0.4,
    ).strip()


def _profile(value: LogicProfile | dict[str, float]) -> LogicProfile:
    return value if isinstance(value, LogicProfile) else LogicProfile.model_validate(value)


def calculate_homonomy(
    logic_profile_a: LogicProfile | dict[str, float],
    logic_profile_b: LogicProfile | dict[str, float],
    method: str | None = None,
) -> dict[str, Any]:
    profile_a, profile_b = _profile(logic_profile_a), _profile(logic_profile_b)
    score = homonomy.homonomy_score(profile_a.to_core_vector(), profile_b.to_core_vector(), method=method)
    if not 0.0 <= score <= 1.0:
        raise ValueError("homonomy score must be between 0 and 1")
    return {"score": float(score), "method": method or config.METHOD}


def map_entities(
    text_a: str,
    text_b: str,
    homonomy_score: float,
    threshold: float | None = None,
) -> MappingResult | dict[str, Any]:
    if not text_a.strip() or not text_b.strip():
        raise ValueError("text_a and text_b must be non-empty")
    if not 0.0 <= homonomy_score <= 1.0:
        raise ValueError("homonomy_score must be between 0 and 1")
    limit = config.THRESHOLD if threshold is None else threshold
    if not 0.0 <= limit <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    if homonomy_score < limit:
        return {"status": "skipped", "reason": "homonomy score is below threshold", "threshold": limit}
    raw = mapper.map_entities(text_a, text_b)
    return MappingResult(
        candidate_id="",
        a_terms=list(raw.get("A_terms", raw.get("a_terms", []))),
        b_terms=list(raw.get("B_terms", raw.get("b_terms", []))),
        mapping=dict(raw.get("mapping", {})),
    )


def _infer_mapping_type(term: str, proposed: str) -> str:
    value = term.strip().lower()
    if "反向传播" in value or ("传播" in value and "信号" not in value):
        return "process"
    if "信号" in value:
        return "signal"
    rules = (
        ("failure_mode", ("故障", "失控", "崩溃", "过拟合", "失败")),
        ("objective", ("目标", "稳定", "收敛", "最小化", "最大化")),
        ("constraint", ("约束", "阈值", "容量", "上限", "资源限制")),
        ("state", ("状态", "稳态", "均衡", "拥塞", "叠加态")),
        ("signal", ("误差", "输入", "输出", "概率幅")),
        ("process", ("学习", "训练", "传播", "更新", "调节", "控制", "定价", "熔断", "可塑性", "反馈")),
        ("entity", ("神经元", "细胞", "服务器", "节点", "捕食者", "猎物", "队列", "市场", "系统", "网络")),
    )
    for mapping_type, markers in rules:
        if any(marker in value for marker in markers):
            return mapping_type
    allowed = {"entity", "process", "signal", "state", "objective", "constraint", "failure_mode"}
    return proposed if proposed in allowed else "entity"


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = [value] if isinstance(value, str) else value
    return [str(item).strip() for item in values if str(item).strip()]


BOOK_CATALOG: dict[str, list[RecommendedBook]] = {
    "免疫学": [RecommendedBook(title="Janeway's Immunobiology", author="Kenneth Murphy, Casey Weaver", reason="从免疫识别、信号调节到稳态建立系统框架。", scope="入门到进阶")],
    "分布式系统": [RecommendedBook(title="Designing Data-Intensive Applications", author="Martin Kleppmann", reason="用真实系统理解可靠性、一致性与故障处理。", scope="入门到进阶")],
    "神经生物学": [RecommendedBook(title="神经科学：探索脑", author="Mark F. Bear 等", reason="系统介绍神经元、突触与学习机制。", scope="入门")],
    "控制工程": [RecommendedBook(title="Feedback Control of Dynamic Systems", author="Gene F. Franklin 等", reason="从反馈、稳定性到控制器设计建立基础。", scope="入门")],
    "经济学": [RecommendedBook(title="经济学原理", author="N. Gregory Mankiw", reason="用供需与均衡理解市场机制。", scope="入门")],
    "生态学": [RecommendedBook(title="生态学：从个体到生态系统", author="Michael Begon 等", reason="覆盖种群、群落与生态系统稳态。", scope="入门")],
    "计算机网络": [RecommendedBook(title="Computer Networking: A Top-Down Approach", author="James Kurose, Keith Ross", reason="从应用到传输层理解拥塞与流量控制。", scope="入门")],
    "量子物理": [RecommendedBook(title="Introduction to Quantum Mechanics", author="David J. Griffiths, Darrell Schroeter", reason="循序建立叠加、测量与量子态的数学直觉。", scope="入门到进阶")],
    "金融学": [RecommendedBook(title="Options, Futures, and Other Derivatives", author="John C. Hull", reason="系统学习期权、无套利与风险中性定价。", scope="入门到进阶")],
    "运筹学": [RecommendedBook(title="Fundamentals of Queueing Theory", author="John F. Shortle 等", reason="建立到达过程、服务过程和等待时间的基础。", scope="进阶")],
}


def _dimension_comparisons(source: LogicProfile, candidate: LogicProfile) -> list[DimensionComparison]:
    comparisons = []
    for dimension in LogicProfile.DIMENSIONS:
        source_value = getattr(source, dimension)
        candidate_value = getattr(candidate, dimension)
        delta = abs(source_value - candidate_value)
        if delta <= 0.1:
            relation = "非常接近"
        elif delta <= 0.25:
            relation = "大体接近但存在差异"
        else:
            relation = "差异明显"
        comparisons.append(DimensionComparison(
            dimension=dimension,
            source_value=source_value,
            candidate_value=candidate_value,
            similarity=max(0.0, 1.0 - delta),
            plain_language_reason=f"{DIMENSION_LABELS[dimension]}在两边{relation}（差值 {delta:.2f}）。",
        ))
    return comparisons


def _generate_learning_report_data(prompt: dict[str, Any]) -> dict[str, Any]:
    """Request one complete JSON report, retrying once on malformed/truncated output."""
    user_prompt = json.dumps(prompt, ensure_ascii=False)
    last_error: Exception | None = None
    for attempt in range(2):
        system_prompt = LEARNING_REPORT_SYSTEM
        if attempt:
            system_prompt += "\n上一次输出无法解析。请重新输出一个完整、合法、闭合的 JSON 对象；不要解释错误，也不要使用 Markdown 代码围栏。"
        raw = feature_extractor.llm(
            system_prompt,
            user_prompt,
            max_tokens=4600,
            temperature=0.2,
        )
        try:
            data = feature_extractor.extract_json(raw)
            if not isinstance(data, dict):
                raise TypeError("learning report root must be a JSON object")
            return data
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            last_error = exc
    raise ValueError(f"学习报告 JSON 解析失败（已重试 1 次）: {last_error}")


def build_learning_report(
    source_text: str,
    source_concept: Concept,
    source_profile: LogicProfile,
    candidate: CandidateConcept,
    homonomy_result: HomologyResult,
    mapping_result: MappingResult,
) -> DiscoverLearningReport:
    """Turn an existing deterministic result into a source-grounded learning report."""
    source_ids = {source.id for source in candidate.sources}
    prompt = {
        "source_text": source_text,
        "source_concept": source_concept.model_dump(mode="json"),
        "source_logic_profile": source_profile.model_dump(mode="json"),
        "candidate": candidate.model_dump(mode="json"),
        "homonomy_result": homonomy_result.model_dump(mode="json"),
        "initial_mapping": mapping_result.model_dump(mode="json"),
    }
    data = _generate_learning_report_data(prompt)
    mappings: list[MappingEvidence] = []
    rejected_pairs: list[str] = []
    for item in data.get("mapping_evidence", []):
        source_term = str(item.get("source_term", "")).strip()
        target_term = str(item.get("target_term", "")).strip()
        if not source_term or not target_term:
            continue
        source_type = _infer_mapping_type(source_term, str(item.get("source_type", "")))
        target_type = _infer_mapping_type(target_term, str(item.get("target_type", "")))
        type_match = source_type == target_type
        if not type_match:
            rejected_pairs.append(f"{source_term} ≠ {target_term}：机制层级或类型不一致。")
            continue
        refs = [ref for ref in _string_list(item.get("evidence_refs")) if ref in source_ids]
        if not refs and source_ids:
            refs = sorted(source_ids)
        mappings.append(MappingEvidence(
            source_term=source_term,
            source_type=source_type,
            source_role=str(item.get("source_role", "")).strip(),
            target_term=target_term,
            target_type=target_type,
            target_role=str(item.get("target_role", "")).strip(),
            correspondence_reason=str(item.get("correspondence_reason", "")).strip(),
            evidence_refs=refs,
            limitations=_string_list(item.get("limitations")),
            type_match=True,
        ))

    failure_boundaries = _string_list(data.get("failure_boundaries"))
    known_differences = _string_list(data.get("known_differences"))
    prohibited_claims = _string_list(data.get("prohibited_claims"))
    prohibited_claims.extend(rejected_pairs)
    lesson_sections = []
    for item in data.get("target_domain_lessons", []) if isinstance(data.get("target_domain_lessons", []), list) else []:
        if not isinstance(item, dict):
            continue
        try:
            lesson_sections.append(LearningSection.model_validate(item))
        except Exception:
            continue
    recommended_books = []
    for item in data.get("recommended_books", []) if isinstance(data.get("recommended_books", []), list) else []:
        if isinstance(item, dict):
            try:
                recommended_books.append(RecommendedBook.model_validate(item))
            except Exception:
                continue
    if not recommended_books:
        recommended_books = BOOK_CATALOG.get(candidate.domain, [])
    intersection_lesson = str(data.get("intersection_lesson", "")).strip()
    if not candidate.sources:
        verdict = "INSUFFICIENT_EVIDENCE"
        verdict_reason = "候选知识卡没有可核验来源，不能给出可靠结论。"
    elif not homonomy_result.passed:
        verdict = "REJECTED"
        verdict_reason = "确定性结构相似度未达到当前阈值。"
    elif not mappings:
        verdict = "REJECTED"
        verdict_reason = "没有通过类型校验的机制映射。"
    elif len(mappings) < 2 or len(lesson_sections) < 2 or not intersection_lesson or not failure_boundaries or not known_differences or any(
        not item.source_role or not item.target_role or not item.correspondence_reason
        or not item.evidence_refs or not item.limitations
        for item in mappings
    ):
        verdict = "NEEDS_REVIEW"
        verdict_reason = "结构分数已通过，但映射依据或边界信息仍不完整。"
    else:
        verdict = "RELIABLE_WITH_LIMITS"
        verdict_reason = "结构分数通过，且映射具有类型、来源与失效边界；结论仅在所列条件内成立。"

    return DiscoverLearningReport(
        candidate_id=candidate.id,
        source_concept=source_concept,
        source_primer=str(data.get("source_primer", "")).strip(),
        candidate_concept=Concept(
            name=candidate.concept,
            domain=candidate.domain,
            description=candidate.description,
            mechanism=candidate.mechanism,
            key_terms=candidate.keywords,
        ),
        candidate_primer=str(data.get("candidate_primer", "")).strip(),
        retrieval_score=candidate.retrieval_score,
        homonomy_result=homonomy_result,
        dimension_comparisons=_dimension_comparisons(source_profile, candidate.logic_profile),
        mechanism_summary=str(data.get("mechanism_summary", "")).strip(),
        intersection_lesson=intersection_lesson,
        target_domain_lessons=lesson_sections,
        transferable_knowledge=_string_list(data.get("transferable_knowledge")),
        new_knowledge=_string_list(data.get("new_knowledge")),
        understanding_checks=_string_list(data.get("understanding_checks")),
        mapping_evidence=mappings,
        valid_conditions=_string_list(data.get("valid_conditions")),
        failure_boundaries=failure_boundaries,
        known_differences=known_differences,
        prohibited_claims=prohibited_claims,
        source_references=candidate.sources,
        verdict=verdict,
        verdict_reason=verdict_reason,
        learning_next_steps=_string_list(data.get("learning_next_steps")),
        recommended_books=recommended_books,
    )


def critique_learning_report(report: DiscoverLearningReport) -> CritiqueOutput:
    reliable = report.verdict == "RELIABLE_WITH_LIMITS"
    warnings = ["底层逻辑相似不等于科学等价。", *report.failure_boundaries]
    return CritiqueOutput(
        is_reliable=reliable,
        confidence=report.homonomy_result.score if reliable else min(report.homonomy_result.score, 0.74),
        reason=report.verdict_reason,
        warnings=warnings,
    )


def retrieve_candidates(
    retriever: Retriever,
    query: str | dict[str, Any],
    top_k: int = 5,
    domain: str | None = None,
    keywords: Sequence[str] | None = None,
) -> list[CandidateConcept]:
    return retriever.retrieve(query, top_k=top_k, domain=domain, keywords=keywords)


def critique_homology(
    source_concept: Any,
    candidate_concept: Any,
    homonomy_score: float,
    mapping_result: MappingResult | dict[str, Any] | None = None,
) -> CritiqueOutput:
    if not 0.0 <= homonomy_score <= 1.0:
        raise ValueError("homonomy_score must be between 0 and 1")
    mapping = mapping_result.mapping if isinstance(mapping_result, MappingResult) else (mapping_result or {}).get("mapping", {})
    warnings = ["逻辑结构相似不等于科学含义或领域等价。"]
    sufficient = homonomy_score >= config.THRESHOLD and bool(mapping)
    if sufficient:
        return CritiqueOutput(
            is_reliable=True,
            confidence=min(1.0, homonomy_score),
            reason="已有逻辑相似度达到阈值且存在显式实体映射；仍需领域专家核验。",
            warnings=warnings,
        )
    warnings.append("证据不足，不能确认可靠类比。")
    return CritiqueOutput(is_reliable=False, confidence=homonomy_score, reason="逻辑相似度或映射证据不足。", warnings=warnings)


def get_session_context(manager: SessionManager, session_id: str) -> dict[str, Any]:
    return manager.get_context_for_agent(session_id)


def add_session_message(manager: SessionManager, session_id: str, role: MessageRole | str, content: str):
    if not content.strip():
        raise ValueError("content must be non-empty")
    resolved_role = role if isinstance(role, MessageRole) else MessageRole(role)
    return manager.add_message(session_id, Message(role=resolved_role, content=content))


def update_session_summary(manager: SessionManager, session_id: str, summary: str):
    return manager.update_summary(session_id, summary)


def update_session_knowledge(manager: SessionManager, session_id: str, knowledge_context: KnowledgeContext):
    return manager.update_knowledge_context(session_id, knowledge_context)
