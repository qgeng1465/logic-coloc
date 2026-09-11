# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from logic_coloc.agents.schemas import CritiqueResult, Concept, DiscoverLearningReport, HomologyResult, Intent, KnowledgeContext, LogicProfile, MappingResult
from logic_coloc.api import app
from logic_coloc.api.routes import get_service
from logic_coloc.api.service import LogicColocService
from logic_coloc.rag.schemas import CandidateConcept
from logic_coloc.sessions.manager import SessionManager


def profile() -> LogicProfile:
    return LogicProfile(**{name: 0.5 for name in LogicProfile.DIMENSIONS})


class FakeGraph:
    def __init__(self) -> None:
        self.calls = []

    def invoke(self, state):
        self.calls.append(dict(state))
        return {
            "knowledge_context": KnowledgeContext(source_text=state["user_input"], concept=Concept(name="负反馈"), logic_profile=profile()),
            "logic_profile": profile(),
            "homonomy_results": [HomologyResult(candidate_id="pid", score=0.9)],
            "candidate_concepts": [CandidateConcept(
                id="pid", concept="PID 控制", domain="控制工程", description="误差控制",
                mechanism="根据偏差形成反馈", logic_profile=profile(), keywords=["反馈"],
                examples=["恒温器"], retrieval_score=0.42,
            )],
            "retrieval_scores": {"pid": 0.42},
            "mapping_results": [MappingResult(candidate_id="pid", mapping={"免疫抑制": "控制器"})],
            "critique_results": [CritiqueResult(candidate_id="pid", is_valid=False, confidence=0.6, issues=["领域含义不同"], summary="仅结构相似")],
            "learning_reports": [DiscoverLearningReport(
                candidate_id="pid", source_concept=Concept(name="负反馈"), source_primer="来源概念入门",
                candidate_concept=Concept(name="PID 控制", domain="控制工程"), candidate_primer="候选概念入门",
                retrieval_score=0.42, homonomy_result=HomologyResult(candidate_id="pid", score=0.9),
                mechanism_summary="共同反馈结构", failure_boundaries=["领域机制不同"], known_differences=["实现方式不同"],
                verdict="NEEDS_REVIEW", verdict_reason="测试证据不足",
            )],
            "final_response": "测试回答",
            "errors": [],
        }


@pytest.fixture
def runtime():
    manager = SessionManager()
    graph = FakeGraph()
    service = LogicColocService(session_manager=manager, graph=graph)
    app.dependency_overrides[get_service] = lambda: service
    yield TestClient(app), service, graph
    app.dependency_overrides.clear()


def test_explain_creates_session_and_returns_200(runtime) -> None:
    client, service, _ = runtime
    response = client.post("/api/explain", json={"text": "什么是负反馈？"})
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"]
    assert body["explanation"] == "测试回答"
    assert service.session_manager.get_session(body["session_id"])


def test_chat_reuses_session(runtime) -> None:
    client, _, graph = runtime
    session_id = client.post("/api/explain", json={"text": "负反馈"}).json()["session_id"]
    response = client.post("/api/chat", json={"session_id": session_id, "message": "还是不懂"})
    assert response.status_code == 200
    assert response.json()["session_id"] == session_id
    assert graph.calls[-1]["user_input"] == "还是不懂"


def test_discover_passes_explicit_intent_without_changing_text(runtime) -> None:
    client, _, graph = runtime
    response = client.post("/api/discover", json={"text": "负反馈", "top_k": 3})
    assert response.status_code == 200
    assert response.json()["results"][0]["score"] == 0.9
    assert graph.calls[-1]["user_input"] == "负反馈"
    assert graph.calls[-1]["intent"] == Intent.DISCOVER_HOMOLOGY
    assert graph.calls[-1]["top_k"] == 3


def test_discover_returns_complete_candidate_and_distinct_scores(runtime) -> None:
    client, service, _ = runtime
    response = client.post("/api/discover", json={"text": "负反馈"})
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"]
    assert body["code"] == 0
    item = body["candidates"][0]
    assert item["candidate"]["id"] == "pid"
    assert item["candidate"]["concept"] == "PID 控制"
    assert item["candidate"]["logic_profile"] == profile().model_dump()
    assert item["candidate"]["retrieval_score"] == 0.42
    assert item["candidate"]["mechanism_roles"] == {}
    assert item["candidate"]["sources"] == []
    assert item["homonomy_score"] == 0.9
    assert item["candidate"]["retrieval_score"] != item["homonomy_score"]
    assert item["mapping"]["mapping"] == {"免疫抑制": "控制器"}
    assert item["learning_report"]["verdict"] == "NEEDS_REVIEW"
    assert body["learning_reports"][0]["mechanism_summary"] == "共同反馈结构"
    session = service.session_manager.get_session(body["session_id"])
    assert any(message.metadata.get("kind") == "discover_learning_reports" for message in session.recent_messages)


def test_unreliable_candidate_stays_unreliable(runtime) -> None:
    client, _, _ = runtime
    item = client.post("/api/discover", json={"text": "负反馈"}).json()["candidates"][0]
    assert item["critique"]["is_valid"] is False
    assert item["critique"]["summary"] == "仅结构相似"


def test_session_get_and_not_found(runtime) -> None:
    client, _, _ = runtime
    session_id = client.post("/api/explain", json={"text": "负反馈"}).json()["session_id"]
    assert client.get(f"/api/session/{session_id}").status_code == 200
    response = client.get("/api/session/not-found")
    assert response.status_code == 404
    assert response.json() == {"detail": "session not found"}


def test_invalid_request_returns_422(runtime) -> None:
    client, _, _ = runtime
    assert client.post("/api/explain", json={}).status_code == 422
    assert client.post("/api/chat", json={"message": "missing session"}).status_code == 422


def test_ocr_returns_text(runtime, monkeypatch) -> None:
    client, service, _ = runtime
    monkeypatch.setattr(service, "ocr", lambda image_bytes: {"code": 0, "text": "截图中的文字"})
    response = client.post("/api/ocr", files={"file": ("screen.png", b"fake-png", "image/png")})
    assert response.status_code == 200
    assert response.json() == {"code": 0, "text": "截图中的文字"}


def test_ocr_rejects_non_image(runtime) -> None:
    client, _, _ = runtime
    response = client.post("/api/ocr", files={"file": ("note.txt", b"hello", "text/plain")})
    assert response.status_code == 415


def test_note_attachments_are_bound_to_note_id(runtime, monkeypatch, tmp_path) -> None:
    from logic_coloc.api import note_store

    notes_file = tmp_path / "notes.json"
    notes_file.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(note_store, "NOTES_FILE", notes_file)
    monkeypatch.setattr(note_store, "DATA_DIR", tmp_path)
    first = {
        "id": "binary-tree", "title": "二叉树", "folderId": "default",
        "attachments": [{"noteId": "binary-tree", "name": "tree.pdf", "url": "/uploads/tree.pdf"}],
    }
    second = {
        "id": "backtracking", "title": "回溯", "folderId": "default",
        "attachments": [{"noteId": "binary-tree", "name": "wrong.pdf", "url": "/uploads/wrong.pdf"}],
    }
    assert runtime[0].post("/api/notes/save", json=first).status_code == 200
    assert runtime[0].post("/api/notes/save", json=second).status_code == 200
    notes = runtime[0].get("/api/notes").json()["notes"]
    assert notes[0]["attachments"][0]["noteId"] == "binary-tree"
    assert notes[1]["attachments"] == []


def test_move_note_updates_folder(runtime, monkeypatch, tmp_path) -> None:
    from logic_coloc.api import note_store

    notes_file = tmp_path / "notes.json"
    notes_file.write_text('[{"id":"move-me","title":"笔记","attachments":[]}]\n', encoding="utf-8")
    monkeypatch.setattr(note_store, "NOTES_FILE", notes_file)
    monkeypatch.setattr(note_store, "DATA_DIR", tmp_path)
    response = runtime[0].patch("/api/notes/move", json={"id": "move-me", "folderId": "research"})
    assert response.status_code == 200
    assert runtime[0].get("/api/notes").json()["notes"][0]["folderId"] == "research"


def test_legacy_duplicate_attachment_is_kept_by_first_note_only(runtime, monkeypatch, tmp_path) -> None:
    from logic_coloc.api import note_store

    notes_file = tmp_path / "notes.json"
    notes_file.write_text(
        '[{"id":"tree","title":"二叉树","attachments":[{"id":"same","name":"tree.pdf","url":"/tree.pdf"}]},'
        '{"id":"back","title":"回溯","attachments":[{"id":"same","name":"tree.pdf","url":"/tree.pdf"}]}]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(note_store, "NOTES_FILE", notes_file)
    monkeypatch.setattr(note_store, "DATA_DIR", tmp_path)
    notes = runtime[0].get("/api/notes").json()["notes"]
    assert notes[0]["attachments"][0]["noteId"] == "tree"
    assert notes[1]["attachments"] == []


def test_legacy_default_folder_migrates_to_root(runtime, monkeypatch, tmp_path) -> None:
    from logic_coloc.api import note_store

    notes_file = tmp_path / "notes.json"
    notes_file.write_text('[{"id":"root-note","title":"根目录笔记","folderId":"default","attachments":[]}]\n', encoding="utf-8")
    monkeypatch.setattr(note_store, "NOTES_FILE", notes_file)
    monkeypatch.setattr(note_store, "DATA_DIR", tmp_path)
    notes = runtime[0].get("/api/notes").json()["notes"]
    assert notes[0]["folderId"] is None


@pytest.mark.parametrize("text", ["", "   "])
def test_blank_text_returns_422(runtime, text: str) -> None:
    client, _, _ = runtime
    assert client.post("/api/explain", json={"text": text}).status_code == 422
    assert client.post("/api/discover", json={"text": text}).status_code == 422


def test_agent_error_returns_structured_500(runtime) -> None:
    client, service, _ = runtime
    service.graph = type("BrokenGraph", (), {"invoke": lambda self, state: (_ for _ in ()).throw(RuntimeError("secret"))})()
    response = client.post("/api/explain", json={"text": "负反馈"})
    assert response.status_code == 500
    assert response.json() == {"detail": "internal service error"}


def test_llm_backend_error_returns_sanitized_503(runtime) -> None:
    client, service, _ = runtime
    internal = "extract_features: LLM 调用失败: HTTPConnectionPool(host='127.0.0.1', port=8388) traceback WinError 10061"
    service.graph = type("UnavailableGraph", (), {"invoke": lambda self, state: {"errors": [internal], "final_response": internal}})()
    response = client.post("/api/explain", json={"text": "负反馈"})
    assert response.status_code == 503
    assert response.json() == {"error": {"code": "LLM_BACKEND_UNAVAILABLE", "message": "模型服务暂时不可用，请稍后重试。"}}
    serialized = response.text
    assert "127.0.0.1" not in serialized
    assert "8388" not in serialized
    assert "traceback" not in serialized


def test_explanation_generation_backend_error_returns_sanitized_503(runtime) -> None:
    client, service, _ = runtime
    internal = "generate_explanation: LLM 调用失败: connection details"
    service.graph = type("UnavailableGraph", (), {"invoke": lambda self, state: {"errors": [internal]}})()
    response = client.post("/api/explain", json={"text": "负反馈"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_BACKEND_UNAVAILABLE"
    assert "connection details" not in response.text


def test_discover_backend_error_returns_structured_business_error(runtime) -> None:
    client, service, _ = runtime
    internal = "extract_features: LLM 调用失败: private gateway details"
    service.graph = type("UnavailableGraph", (), {"invoke": lambda self, state: {"errors": [internal]}})()
    response = client.post("/api/discover", json={"text": "负反馈"})
    assert response.status_code == 200
    assert response.json() == {
        "code": 500,
        "message": "模型服务异常，请稍后重试",
        "session_id": "",
        "concept": {"name": "负反馈", "domain": None, "description": None, "mechanism": None, "key_terms": []},
        "candidates": [],
        "results": [],
        "mappings": [],
        "critiques": [],
        "learning_reports": [],
        "report": "",
        "errors": ["MODEL_SERVICE_UNAVAILABLE"],
    }
    assert "private gateway details" not in response.text
