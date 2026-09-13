# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from logic_coloc.agents.schemas import CritiqueResult, Concept, DiscoverLearningReport, HomologyResult, Intent, KnowledgeContext, LogicProfile, MappingResult
from logic_coloc.api import app, attachment_text, auth_store, card_store, library_store, note_store, review_store, routes, user_paths
from logic_coloc.api.routes import get_service
from logic_coloc.api.service import LogicColocService
from logic_coloc.rag.schemas import CandidateConcept
from logic_coloc.sessions.manager import SessionManager

TEST_USERNAME = "tester"
TEST_PASSWORD = "hunter2"


def test_zhihu_search_requires_access_secret(runtime, monkeypatch) -> None:
    client, _, _ = runtime
    monkeypatch.delenv("ZHIHU_ACCESS_SECRET", raising=False)
    response = client.post("/api/zhihu/search", json={"query": "负反馈"})
    assert response.status_code == 503


def test_zhihu_search_maps_official_response(runtime, monkeypatch) -> None:
    client, _, _ = runtime
    monkeypatch.setenv("ZHIHU_ACCESS_SECRET", "test-secret")
    class FakeResponse:
        ok = True
        def json(self):
            return {"Code": 0, "Data": {"Items": [{"Title": "测试问题", "ContentText": "知乎摘要内容", "Url": "https://www.zhihu.com/question/123", "ContentType": "Question", "AuthorName": "测试作者", "VoteUpCount": 9, "CommentCount": 2}]}}

    routes._zhihu_search_cache.clear()
    monkeypatch.setattr(routes.requests, "get", lambda *args, **kwargs: FakeResponse())
    response = client.post("/api/zhihu/search", json={"query": "负反馈", "count": 5})
    assert response.status_code == 200
    assert response.json()["items"][0]["title"] == "测试问题"
    assert response.json()["items"][0]["summary"] == "知乎摘要内容"


def test_zhihu_search_reuses_successful_cached_result(runtime, monkeypatch) -> None:
    client, _, _ = runtime
    monkeypatch.setenv("ZHIHU_ACCESS_SECRET", "test-secret")
    monkeypatch.setattr(routes, "_wait_for_zhihu_slot", lambda: None)
    routes._zhihu_search_cache.clear()
    calls = []

    class FakeResponse:
        ok = True

        def json(self):
            return {"Code": 0, "Data": {"Items": [{"Title": "缓存结果", "Url": "https://www.zhihu.com/question/1"}]}}

    def fake_get(*args, **kwargs):
        calls.append(1)
        return FakeResponse()

    monkeypatch.setattr(routes.requests, "get", fake_get)
    assert client.post("/api/zhihu/search", json={"query": "缓存测试", "count": 5}).status_code == 200
    assert client.post("/api/zhihu/search", json={"query": "缓存测试", "count": 5}).status_code == 200
    assert len(calls) == 1


def test_zhihu_rate_limit_is_reported_as_429(runtime, monkeypatch) -> None:
    client, _, _ = runtime
    monkeypatch.setenv("ZHIHU_ACCESS_SECRET", "test-secret")
    monkeypatch.setattr(routes, "_wait_for_zhihu_slot", lambda: None)
    routes._zhihu_search_cache.clear()

    class FakeResponse:
        ok = True

        def json(self):
            return {"Code": 30001, "Message": "too many requests"}

    monkeypatch.setattr(routes.requests, "get", lambda *args, **kwargs: FakeResponse())
    response = client.post("/api/zhihu/search", json={"query": "负反馈"})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "3"


def test_discover_cancel_can_arrive_before_discover_request(runtime) -> None:
    client, _, _ = runtime
    request_id = "cancel-before-start"
    response = client.post(f"/api/discover/{request_id}/cancel")
    assert response.status_code == 200
    assert response.json() == {"code": 0, "cancelled": True}


def test_discover_event_preserves_early_cancel_signal() -> None:
    import threading
    import time

    key = ("test-user", "early-cancel")
    early = threading.Event()
    early.set()
    routes._discover_cancel_events[key] = (early, time.monotonic())
    try:
        assert routes._discover_event(*key) is early
        assert early.is_set()
    finally:
        routes._discover_cancel_events.pop(key, None)


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
def user_root(monkeypatch, tmp_path):
    """把落盘根目录整体重定向到临时目录，并注册一个测试账号。

    以前只有部分用例自己 patch 落盘路径，`runtime` 本身不隔离 —— 漏 patch 的用例会
    写进真实的 `data/`。现在统一在这里收口：`user_paths` 的三个常量是三个 store 与
    账号 store 唯一的路径来源，改这一处全体跟随；再把三个 store 各自的模块级常量也
    指过来，保证任何一条漏传 `user_id` 的老路径同样落不到真实目录里。
    """
    monkeypatch.setattr(user_paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(user_paths, "USERS_DIR", tmp_path / "users")
    monkeypatch.setattr(user_paths, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(note_store, "NOTES_FILE", tmp_path / "notes.json")
    monkeypatch.setattr(note_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(card_store, "CARDS_FILE", tmp_path / "cards.json")
    monkeypatch.setattr(review_store, "REVIEWS_FILE", tmp_path / "review_records.json")
    monkeypatch.setattr(review_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(library_store, "LIBRARY_FILE", tmp_path / "library.json")
    monkeypatch.setattr(auth_store, "_secret_cache", b"test-secret")

    account = auth_store.register(TEST_USERNAME, TEST_PASSWORD)
    directory = user_paths.user_dir(account["id"])

    def seed(filename: str, payload) -> Path:
        """把初始数据直接播进该账号的目录（替代以前 patch `NOTES_FILE` 的做法）。"""
        path = directory / filename
        path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    return SimpleNamespace(uid=account["id"], dir=directory, root=tmp_path, seed=seed)


@pytest.fixture
def runtime(user_root):
    manager = SessionManager()
    graph = FakeGraph()
    service = LogicColocService(session_manager=manager, graph=graph)
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)
    # 默认带上测试账号的 token：绝大多数用例只关心业务逻辑，不该被认证噪音淹没。
    client.headers["Authorization"] = f"Bearer {auth_store.issue_token(user_root.uid)}"
    yield client, service, graph
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


def test_note_review_chat_bootstraps_session(runtime) -> None:
    """笔记复盘第一条消息：前端自编的 session_id 不存在，后端应当现场建会话而不是 404。"""
    client, service, _ = runtime
    response = client.post("/api/chat", json={
        "session_id": "note_review_1700000000000",
        "message": "请根据这篇笔记的内容，向我提出第一个问题",
        "note_content": "这篇笔记讲了负反馈。",
    })
    assert response.status_code == 200
    session_id = response.json()["session_id"]
    assert session_id != "note_review_1700000000000"
    assert service.session_manager.get_session(session_id).source["source"] == "note_review"


def test_note_review_chat_reuses_bootstrapped_session(runtime) -> None:
    """第二条消息用第一条返回的真 session_id，应当复用同一会话而不是 404。"""
    client, _, _ = runtime
    first = client.post("/api/chat", json={
        "session_id": "note_review_1700000000000",
        "message": "请根据这篇笔记的内容，向我提出第一个问题",
        "note_content": "这篇笔记讲了负反馈。",
    })
    session_id = first.json()["session_id"]
    second = client.post("/api/chat", json={
        "session_id": session_id,
        "message": "它主要讲了负反馈",
        "note_content": "这篇笔记讲了负反馈。",
    })
    assert second.status_code == 200
    assert second.json()["session_id"] == session_id


def test_chat_without_note_content_still_404(runtime) -> None:
    """不带 note_content 时保持 404 契约：只有笔记复盘才允许自动建会话。"""
    client, _, _ = runtime
    response = client.post("/api/chat", json={
        "session_id": "note_review_1700000000000",
        "message": "随便问问",
        "note_content": "",
    })
    assert response.status_code == 404


def test_note_review_chat_skips_feature_extraction(runtime) -> None:
    """笔记复盘整轮跳过特征提取。

    它的 user_input 是拼出来的导师指令而非待分析文本，对它做特征提取会让 LLM
    跟着指令回答、不输出 JSON（实测 500），且提取结果在 generate_response 里用不到。
    """
    client, _, graph = runtime
    client.post("/api/chat", json={
        "session_id": "note_review_1700000000000",
        "message": "请根据这篇笔记的内容，向我提出第一个问题",
        "note_content": "这篇笔记讲了负反馈。",
    })
    assert graph.calls[-1]["skip_extraction"] is True


def test_plain_chat_still_extracts_features(runtime) -> None:
    """普通对话不受影响，仍然走特征提取。"""
    client, _, graph = runtime
    session_id = client.post("/api/explain", json={"text": "负反馈"}).json()["session_id"]
    client.post("/api/chat", json={"session_id": session_id, "message": "还是不懂"})
    assert graph.calls[-1]["skip_extraction"] is False


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


def test_note_attachments_are_bound_to_note_id(runtime) -> None:
    client, _, _ = runtime
    first = {
        "id": "binary-tree", "title": "二叉树", "folderId": "default",
        "attachments": [{"noteId": "binary-tree", "name": "tree.pdf", "url": "/uploads/tree.pdf"}],
    }
    second = {
        "id": "backtracking", "title": "回溯", "folderId": "default",
        "attachments": [{"noteId": "binary-tree", "name": "wrong.pdf", "url": "/uploads/wrong.pdf"}],
    }
    assert client.post("/api/notes/save", json=first).status_code == 200
    assert client.post("/api/notes/save", json=second).status_code == 200
    notes = client.get("/api/notes").json()["notes"]
    assert notes[0]["attachments"][0]["noteId"] == "binary-tree"
    assert notes[1]["attachments"] == []


def test_move_note_updates_folder(runtime, user_root) -> None:
    user_root.seed("notes.json", [{"id": "move-me", "title": "笔记", "attachments": []}])
    response = runtime[0].patch("/api/notes/move", json={"id": "move-me", "folderId": "research"})
    assert response.status_code == 200
    assert runtime[0].get("/api/notes").json()["notes"][0]["folderId"] == "research"


def test_legacy_duplicate_attachment_is_kept_by_first_note_only(runtime, user_root) -> None:
    user_root.seed("notes.json", [
        {"id": "tree", "title": "二叉树", "attachments": [{"id": "same", "name": "tree.pdf", "url": "/tree.pdf"}]},
        {"id": "back", "title": "回溯", "attachments": [{"id": "same", "name": "tree.pdf", "url": "/tree.pdf"}]},
    ])
    notes = runtime[0].get("/api/notes").json()["notes"]
    assert notes[0]["attachments"][0]["noteId"] == "tree"
    assert notes[1]["attachments"] == []


def test_legacy_default_folder_migrates_to_root(runtime, user_root) -> None:
    user_root.seed("notes.json", [{"id": "root-note", "title": "根目录笔记", "folderId": "default", "attachments": []}])
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


# ------------------------------- 笔记复盘记录持久化 -------------------------------
#
# 复盘记录的落盘隔离不再需要单独的 fixture：`runtime` 依赖的 `user_root` 已经把
# 落盘根目录整体重定向，记录会写进该测试账号自己的目录。


def note_review_turn(client, session_id: str, message: str):
    return client.post("/api/chat", json={
        "session_id": session_id,
        "message": message,
        "note_content": "这篇笔记讲了负反馈。",
        "note_id": "negative-feedback",
        "note_title": "负反馈笔记",
    })


def test_note_review_first_turn_creates_record(runtime) -> None:
    client, _, _ = runtime
    response = note_review_turn(client, "note_review_1700000000000", "请提出第一个问题")
    assert response.status_code == 200

    reviews = client.get("/api/reviews").json()["reviews"]
    assert len(reviews) == 1
    assert reviews[0]["id"] == response.json()["session_id"]
    assert reviews[0]["noteTitle"] == "负反馈笔记"
    assert reviews[0]["turnCount"] == 1
    assert reviews[0]["summary"] == ""


def test_note_review_second_turn_appends_to_same_record(runtime) -> None:
    """一次复盘只对应一条记录：第二轮必须追加进同一条，而不是新建。"""
    client, _, _ = runtime
    session_id = note_review_turn(client, "note_review_1700000000000", "请提出第一个问题").json()["session_id"]
    assert note_review_turn(client, session_id, "它主要讲了负反馈").status_code == 200

    reviews = client.get("/api/reviews").json()["reviews"]
    assert len(reviews) == 1
    assert reviews[0]["turnCount"] == 2

    messages = client.get(f"/api/reviews/{session_id}").json()["review"]["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant", "user", "assistant"]
    # 存的必须是用户原话，不是 service 拼出来的导师指令模板（那个内嵌了整篇笔记）。
    assert messages[0]["content"] == "请提出第一个问题"
    assert messages[2]["content"] == "它主要讲了负反馈"


def test_review_list_omits_messages(runtime) -> None:
    client, _, _ = runtime
    note_review_turn(client, "note_review_1700000000000", "请提出第一个问题")
    item = client.get("/api/reviews").json()["reviews"][0]
    assert "messages" not in item
    assert client.get(f"/api/reviews/{item['id']}").json()["review"]["messages"]


def test_review_unknown_id_returns_404(runtime) -> None:
    client, _, _ = runtime
    assert client.get("/api/reviews/not-a-review").status_code == 404
    assert client.delete("/api/reviews/not-a-review").status_code == 404
    assert client.post("/api/reviews/not-a-review/summary").status_code == 404


def test_review_summary_is_cached_and_force_regenerates(runtime, monkeypatch) -> None:
    """小结要幂等：前端「关页面发一次 + 详情页兜底再发一次」不该重复烧 LLM。"""
    from logic_coloc.api import routes

    calls = []
    monkeypatch.setattr(routes.tools, "summarize_review", lambda title, messages: calls.append(title) or "小结正文")
    client, _, _ = runtime
    session_id = note_review_turn(client, "note_review_1700000000000", "请提出第一个问题").json()["session_id"]

    first = client.post(f"/api/reviews/{session_id}/summary")
    assert first.status_code == 200
    assert first.json()["summary"] == "小结正文"
    assert len(calls) == 1

    again = client.post(f"/api/reviews/{session_id}/summary")
    assert again.json() == {"code": 0, "summary": "小结正文", "cached": True}
    assert len(calls) == 1, "已有小结时不应再调 LLM"

    assert client.post(f"/api/reviews/{session_id}/summary?force=1").status_code == 200
    assert len(calls) == 2, "force=1 才重新生成"
    assert client.get("/api/reviews").json()["reviews"][0]["summary"] == "小结正文"


def test_review_delete_removes_record(runtime) -> None:
    client, _, _ = runtime
    session_id = note_review_turn(client, "note_review_1700000000000", "请提出第一个问题").json()["session_id"]
    assert client.delete(f"/api/reviews/{session_id}").status_code == 200
    assert client.get("/api/reviews").json()["reviews"] == []


def test_plain_chat_is_not_persisted(runtime) -> None:
    """只有笔记复盘落盘：普通解释与会话追问都不该产生记录。"""
    client, _, _ = runtime
    session_id = client.post("/api/explain", json={"text": "负反馈"}).json()["session_id"]
    client.post("/api/chat", json={"session_id": session_id, "message": "还是不懂"})
    assert client.get("/api/reviews").json()["reviews"] == []


def test_note_review_turn_survives_disk_failure(runtime, monkeypatch) -> None:
    """落盘是聊天的副作用：磁盘写失败也不该把已经拿到的回答变成 500。"""
    from logic_coloc.api import routes

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(routes, "append_review_turn", boom)
    client, _, _ = runtime
    response = note_review_turn(client, "note_review_1700000000000", "请提出第一个问题")
    assert response.status_code == 200
    assert response.json()["answer"] == "测试回答"


# ==================================== 账号 ====================================


def anonymous(client):
    """把 `runtime` 预置的认证头摘掉，回到未登录状态。"""
    client.headers.pop("Authorization", None)
    return client


def test_anonymous_is_locked_out_of_everything(runtime) -> None:
    """必须登录才能用：未认证时不止看不到数据，连花钱的 LLM 路由也不该受理。"""
    client = anonymous(runtime[0])
    for path in ("/api/notes", "/api/cards/due", "/api/cards/schedule", "/api/reviews", "/api/auth/me", "/api/user/profile"):
        assert client.get(path).status_code == 401, path
    for path, payload in (
        ("/api/explain", {"text": "负反馈"}),
        ("/api/discover", {"text": "负反馈"}),
        ("/api/chat", {"session_id": "s", "message": "m"}),
        ("/api/user/points", {"delta": 10, "reason": "x"}),
        ("/api/notes/save", {"id": "n", "title": "t"}),
    ):
        assert client.post(path, json=payload).status_code == 401, path
    assert client.get("/api/session/whatever").status_code == 401
    assert client.get("/api/reviews/whatever").status_code == 401
    assert client.delete("/api/reviews/whatever").status_code == 401


def test_bad_token_fails_exactly_like_no_token(runtime) -> None:
    """签名不对、格式不对、过期 —— 全部并成同一个 401，不告诉对方错在哪一步。"""
    client = runtime[0]
    for header in ("Bearer garbage", "Bearer a.b", "Basic dXNlcjpwYXNz", "Bearer", "  "):
        client.headers["Authorization"] = header
        response = client.get("/api/notes")
        assert response.status_code == 401, header
        assert response.json() == {"detail": "未登录或登录已过期"}


def test_health_stays_anonymous(runtime) -> None:
    """健康检查不能被登录挡住，否则平台探活会一直判定实例不健康。"""
    assert anonymous(runtime[0]).get("/api/health").status_code == 200


def test_index_page_stays_anonymous(runtime) -> None:
    assert anonymous(runtime[0]).get("/").status_code == 200


def test_register_login_and_me(runtime) -> None:
    client = anonymous(runtime[0])

    created = client.post("/api/auth/register", json={"username": "看山", "password": "hunter2"})
    assert created.status_code == 200
    assert created.json()["user"]["username"] == "看山"
    token = created.json()["token"]
    # 对外投影里绝不能出现任何凭证字段。
    assert set(created.json()["user"]) == {"id", "username", "createdAt"}

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["profile"]["nickname"] == "学术萌新"

    assert client.post("/api/auth/register", json={"username": "看山", "password": "hunter2"}).status_code == 409
    assert client.post("/api/auth/register", json={"username": "a", "password": "hunter2"}).status_code == 422

    assert client.post("/api/auth/login", json={"username": "看山", "password": "hunter2"}).status_code == 200
    assert client.post("/api/auth/login", json={"username": "看山", "password": "nope123"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "查无此人", "password": "hunter2"}).status_code == 401


def test_token_is_not_revocable_server_side(runtime, user_root) -> None:
    """无状态 token 的已知取舍，钉成用例免得日后困惑：

    服务端没有可吊销的表，所以「在别处重新登录」不会让已发出的 token 失效。代价是
    退出登录只能靠前端删掉本地那一份；要真吊销得再加一张黑名单。
    """
    client = runtime[0]
    issued_before = auth_store.issue_token(user_root.uid)
    assert anonymous(client).post(
        "/api/auth/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD}
    ).status_code == 200
    client.headers["Authorization"] = f"Bearer {issued_before}"
    assert client.get("/api/notes").status_code == 200


def test_expired_token_is_rejected_by_the_route(runtime, user_root) -> None:
    client = runtime[0]
    client.headers["Authorization"] = f"Bearer {auth_store.issue_token(user_root.uid, ttl_seconds=-1)}"
    assert client.get("/api/notes").status_code == 401


# ------------------------------- 账号之间互相隔离 -------------------------------


def register(client, username: str) -> str:
    """注册一个新账号并返回它的 token。"""
    response = client.post("/api/auth/register", json={"username": username, "password": "hunter2"})
    assert response.status_code == 200
    return response.json()["token"]


def test_new_account_library_is_empty(runtime) -> None:
    """新账号的书架和笔记本必须是空的 —— 这里曾经躺着 3 本种子书 + 2 个种子笔记本。

    种子是前端 `app.js` 里写死的常量，服务端从来没给过；这个用例钉住"服务端不给
    新账号造任何默认内容"这条线，免得以后有人"顺手"在后端补一份欢迎数据。
    """
    client = runtime[0]
    body = client.get("/api/library").json()
    assert body["books"] == []
    assert body["categories"] == []


def test_library_round_trip_keeps_nested_cards(runtime) -> None:
    client = runtime[0]
    books = [{"id": "book_1", "name": "系统科学", "cards": [{"id": "card_1", "front": "负反馈", "back": "输出反过来抑制偏差。"}]}]
    assert client.post("/api/library", json={"books": books}).status_code == 200
    assert client.get("/api/library").json()["books"] == books


def test_library_halves_do_not_clobber_each_other(runtime) -> None:
    """前端存书架和存笔记本打同一个接口，只传一半时另一半必须原样留着。

    写错成"没传就是空"的话，用户每存一张卡片都会把笔记本清光。
    """
    client = runtime[0]
    client.post("/api/library", json={"books": [{"id": "book_1"}], "categories": [{"id": "cat_1"}]})

    assert client.post("/api/library", json={"books": [{"id": "book_2"}]}).json()["categories"] == [{"id": "cat_1"}]
    assert client.post("/api/library", json={"categories": [{"id": "cat_2"}]}).json()["books"] == [{"id": "book_2"}]


def test_library_explicit_empty_list_clears_only_that_half(runtime) -> None:
    """删光最后一本书（传 `[]`）不能被当成"这次不传书架"。"""
    client = runtime[0]
    client.post("/api/library", json={"books": [{"id": "book_1"}], "categories": [{"id": "cat_1"}]})
    body = client.post("/api/library", json={"books": []}).json()
    assert body["books"] == []
    assert body["categories"] == [{"id": "cat_1"}]


def test_library_is_isolated_between_accounts(runtime, user_root) -> None:
    client = runtime[0]
    client.post("/api/library", json={"books": [{"id": "a-book"}]})

    client.headers["Authorization"] = f"Bearer {register(client, 'bob')}"
    assert client.get("/api/library").json()["books"] == []

    client.headers["Authorization"] = f"Bearer {auth_store.issue_token(user_root.uid)}"
    assert [book["id"] for book in client.get("/api/library").json()["books"]] == ["a-book"]


def test_library_is_not_anonymous(runtime) -> None:
    """漏挂 `Depends(current_user)` 就是一个匿名数据口子，钉住它。"""
    client = runtime[0]
    saved = client.headers.pop("Authorization")
    try:
        assert client.get("/api/library").status_code == 401
        assert client.post("/api/library", json={"books": [{"id": "x"}]}).status_code == 401
    finally:
        client.headers["Authorization"] = saved


def test_notes_are_invisible_across_accounts(runtime, user_root) -> None:
    """同一个路由、两个 token，各自只看得见自己的笔记。"""
    client = runtime[0]
    client.post("/api/notes/save", json={"id": "a-note", "title": "A 的笔记"})

    client.headers["Authorization"] = f"Bearer {register(client, 'bob')}"
    assert client.get("/api/notes").json()["notes"] == []
    client.post("/api/notes/save", json={"id": "b-note", "title": "B 的笔记"})
    assert [note["id"] for note in client.get("/api/notes").json()["notes"]] == ["b-note"]

    client.headers["Authorization"] = f"Bearer {auth_store.issue_token(user_root.uid)}"
    assert [note["id"] for note in client.get("/api/notes").json()["notes"]] == ["a-note"]


def test_cards_and_reviews_are_isolated_too(runtime, user_root) -> None:
    client = runtime[0]
    assert client.post("/api/cards/save", json={"id": "c-a", "front": "Q", "back": "A"}).status_code == 200
    note_review_turn(client, "note_review_1700000000000", "请提出第一个问题")

    client.headers["Authorization"] = f"Bearer {register(client, 'bob')}"
    assert client.get("/api/cards/due").json()["cards"] == []
    assert client.get("/api/reviews").json()["reviews"] == []

    client.headers["Authorization"] = f"Bearer {auth_store.issue_token(user_root.uid)}"
    assert [card["id"] for card in client.get("/api/cards/due").json()["cards"]] == ["c-a"]
    assert len(client.get("/api/reviews").json()["reviews"]) == 1


def test_sessions_are_owned_by_their_account(runtime) -> None:
    """会话归属校验：知道别人的 session_id 也读不到，且答 404 而非 403。

    403 等于承认「这个 id 是存在的，只是不属于你」。
    """
    client = runtime[0]
    session_id = client.post("/api/explain", json={"text": "负反馈"}).json()["session_id"]

    client.headers["Authorization"] = f"Bearer {register(client, 'bob')}"
    assert client.get(f"/api/session/{session_id}").status_code == 404
    assert client.post("/api/chat", json={"session_id": session_id, "message": "偷看"}).status_code == 404
    # discover 有「失败也不抛异常」的契约，所以这里不是 404，而是被它折成业务错误码；
    # 关键是它没有落到别人的会话上，也没把会话内容吐出来。
    stolen = client.post("/api/discover", json={"text": "负反馈", "session_id": session_id}).json()
    assert stolen["code"] == 500
    assert stolen["session_id"] == ""


def test_note_review_cannot_hijack_another_accounts_session(runtime) -> None:
    """带 note_content 时 `/api/chat` 会自动建会话，但不能因此绕过归属校验接到别人会话上。"""
    client = runtime[0]
    session_id = client.post("/api/explain", json={"text": "负反馈"}).json()["session_id"]

    client.headers["Authorization"] = f"Bearer {register(client, 'bob')}"
    response = note_review_turn(client, session_id, "接着聊")
    assert response.status_code == 200
    assert response.json()["session_id"] != session_id, "应当另起一个属于 bob 的会话"


# ---------------------------------- 资料与能量 ----------------------------------


def test_profile_is_written_server_side(runtime) -> None:
    client = runtime[0]
    assert client.get("/api/user/profile").json()["profile"]["points"] == 0

    client.post("/api/user/update", json={"nickname": "看山", "signature": ""})
    profile = client.get("/api/user/profile").json()["profile"]
    assert profile["nickname"] == "看山"
    assert profile["signature"] == "记录每一次深度思考，留给未来的自己。", "没传的字段不该被清空"

    # POST /api/user/profile 是「传了就写」，空串等于清空 —— 两条路径语义不同，都是刻意的。
    client.post("/api/user/profile", json={"signature": ""})
    assert client.get("/api/user/profile").json()["profile"]["signature"] == ""


def test_points_are_accumulated_by_the_server(runtime) -> None:
    client = runtime[0]
    assert client.post("/api/user/points", json={"delta": 30, "reason": "创建笔记"}).json()["points"]["total"] == 30
    assert client.post("/api/user/points", json={"delta": 20, "reason": "复习"}).json()["points"]["total"] == 50
    # 客户端不能直接声明绝对值，越界的增量会被挡在 schema 外。
    assert client.post("/api/user/points", json={"delta": 9999, "reason": "作弊"}).status_code == 422
    assert client.post("/api/user/points", json={"total": 9999}).status_code == 422


def test_avatar_upload_is_scoped_to_the_account(runtime, user_root) -> None:
    client = runtime[0]
    response = client.post("/api/user/avatar", files={"file": ("me.png", b"fake-png", "image/png")})
    assert response.status_code == 200
    url = response.json()["url"]
    assert f"/uploads/{user_root.uid}/" in url
    assert client.get("/api/user/profile").json()["profile"]["avatarUrl"] == url
    assert client.post("/api/user/avatar", files={"file": ("x.txt", b"hello", "text/plain")}).status_code == 415


def test_upload_lands_in_the_account_directory(runtime, user_root) -> None:
    client = runtime[0]
    url = client.post("/api/upload", files={"file": ("paper.pdf", b"%PDF-1.4", "application/pdf")}).json()["url"]
    assert f"/uploads/{user_root.uid}/" in url
    stored = [path for path in (user_root.root / "uploads" / user_root.uid).iterdir()]
    assert len(stored) == 1

    other = register(client, "bob")
    client.headers["Authorization"] = f"Bearer {other}"
    other_url = client.post("/api/upload", files={"file": ("mine.pdf", b"%PDF-1.4", "application/pdf")}).json()["url"]
    assert other_url != url
    assert f"/uploads/{user_root.uid}/" not in other_url, "别人的上传不该落进我的目录"


# --------------------------------------------------------- 笔记复盘读附件


@pytest.fixture
def review_attachment(user_root, monkeypatch):
    """在临时 uploads 里放一份附件，并把它挂到一篇正文只有占位串的笔记上。

    用图片而不是 PDF：`pypdf` 只是 requirements 里的一行，不一定装了，而 PDF 测试在
    `test_attachment_text.py` 里已经自造了一份最小 PDF；换成图片再加一行假 OCR，这套
    接口测试就完全不依赖任何 PDF 库，也不会真去加载 onnxruntime 模型。

    缓存也要清：它是模块级 dict，跨用例存活（同一条路径在同一进程里被读过就会命中）。
    """
    monkeypatch.setattr(attachment_text, "_cache", {})
    monkeypatch.setattr(attachment_text, "_image_text", lambda path: "附件里的识别文字")

    data = b"\x89PNG\r\n\x1a\nfake"
    directory = user_root.root / "uploads" / user_root.uid
    directory.mkdir(parents=True, exist_ok=True)
    filename = "aaaa1111_培养方案.png"
    (directory / filename).write_bytes(data)

    user_root.seed("notes.json", [{
        "id": "plan-note",
        "title": "培养方案",
        "content": "暂未填写正文。",
        "attachments": [{
            "id": "att-1",
            "name": "培养方案.png",
            "url": f"/uploads/{user_root.uid}/{filename}",
            "mimeType": "image/png",
            "size": len(data),
        }],
    }])


def review_request(client, **overrides):
    """发一轮笔记复盘。默认打的就是用户那几篇 PDF 笔记的形状：正文只有占位串。"""
    payload = {
        "session_id": "note_review_1700000000000",
        "message": "请根据这篇笔记的内容，向我提出第一个问题",
        "note_content": "暂未填写正文。",
        "note_title": "培养方案",
        "note_id": "plan-note",
    }
    payload.update(overrides)
    return client.post("/api/chat", json=payload)


def test_note_review_injects_attachment_text(runtime, review_attachment) -> None:
    """复盘时后端自己把附件读成文字，拼进导师的提示词。

    这就是用户要的功能：他的笔记正文是空的、内容全在附件里，以前导师只能回
    「笔记内容尚未填写」。
    """
    client, _, graph = runtime
    response = review_request(client)
    assert response.status_code == 200
    prompt = graph.calls[-1]["user_input"]
    assert "附件里的识别文字" in prompt
    assert "培养方案.png" in prompt
    assert graph.calls[-1]["skip_extraction"] is True
    # 读取结果是给用户看的：扫描件这类降级否则完全不可见。
    assert response.json()["attachment_notes"][0].startswith("已读取附件《培养方案.png》")


def test_note_review_history_stores_only_the_user_message(runtime, review_attachment) -> None:
    """注入发生了，但进会话历史的仍是用户原话。

    历史每轮全量重发（上限 10 条），存拼好的提示词等于把附件放大成 11 份 ——
    6.7 万字的培养方案聊到第六轮就会超出上下文。
    """
    client, _, graph = runtime
    review_request(client)
    assert "附件里的识别文字" in graph.calls[-1]["user_input"]
    assert graph.calls[-1]["stored_input"] == "请根据这篇笔记的内容，向我提出第一个问题"


def test_note_review_attachment_traversal_is_blocked(runtime, user_root, monkeypatch) -> None:
    """附件 url 客户端可控，越界路径绝不能把文件内容读进提示词。

    假的 `_image_text` 读的是「交给它的那个文件」，所以只要包含性校验被人拆掉，
    canary 的内容就会原样出现在提示词里 —— 这条用例是拆掉校验后唯一会红的地方。
    """
    client, _, graph = runtime
    monkeypatch.setattr(attachment_text, "_cache", {})
    monkeypatch.setattr(attachment_text, "_image_text", lambda path: path.read_text(encoding="utf-8"))
    # 与 UPLOAD_DIR 同级、后缀合法（否则会被「不支持的附件类型」挡下，测不出校验本身）
    (user_root.root / "secret.png").write_text("CANARY-DO-NOT-LEAK", encoding="utf-8")
    user_root.seed("notes.json", [{
        "id": "evil-note",
        "title": "坏笔记",
        "content": "正文",
        "attachments": [{
            "id": "att-evil", "name": "坏.png", "url": "/uploads/../secret.png",
            "mimeType": "image/png", "size": 18,
        }],
    }])
    response = review_request(client, note_id="evil-note", note_title="坏笔记", note_content="正文")
    assert response.status_code == 200
    prompt = graph.calls[-1]["user_input"]
    assert "CANARY-DO-NOT-LEAK" not in prompt
    assert "文件不存在或不可读取" in prompt


def test_note_review_placeholder_body_is_treated_as_empty(runtime, user_root) -> None:
    """正文只有占位串又没有附件时，提示词里不该出现那句占位串。

    「暂未填写正文。」是前端自己写死的，把它当「笔记原文」喂过去就是误导 ——
    导师会照着回答「笔记内容尚未填写」，也就是用户最初抱怨的现象。
    """
    client, _, graph = runtime
    user_root.seed("notes.json", [{"id": "blank", "title": "空笔记", "content": "暂未填写正文。", "attachments": []}])
    response = review_request(client, note_id="blank", note_title="空笔记")
    assert response.status_code == 200
    prompt = graph.calls[-1]["user_input"]
    assert "暂未填写正文。" not in prompt
    assert "（这篇笔记没有正文，也没有可读取的附件内容）" in prompt
    assert response.json()["attachment_notes"] == []


def test_note_review_without_attachment_keeps_plain_prompt(runtime, user_root) -> None:
    """无附件的老笔记：正文照原样进提示词，不出现任何附件段落（回归护栏）。"""
    client, _, graph = runtime
    response = review_request(client, note_id="none", note_content="这篇笔记讲了负反馈。")
    assert response.status_code == 200
    prompt = graph.calls[-1]["user_input"]
    assert "这篇笔记讲了负反馈。" in prompt
    assert "笔记附件" not in prompt
    assert response.json()["attachment_notes"] == []


def test_note_review_survives_an_unreadable_attachment(runtime, user_root) -> None:
    """附件丢了不能让整场复盘 500：降级成一行说明后照常提问。"""
    client, _, graph = runtime
    user_root.seed("notes.json", [{
        "id": "broken-note",
        "title": "坏附件",
        "content": "正文",
        "attachments": [{
            "id": "att-x", "name": "没了.pdf", "url": "/uploads/gone.pdf",
            "mimeType": "application/pdf", "size": 10,
        }],
    }])
    response = review_request(client, note_id="broken-note", note_title="坏附件", note_content="正文")
    assert response.status_code == 200
    assert "请勿就它的内容提问" in graph.calls[-1]["user_input"]
    assert "文件不存在或不可读取" in response.json()["attachment_notes"][0]


def test_note_review_attachments_are_scoped_to_the_account(runtime, user_root, review_attachment) -> None:
    """拿别人账号的 note_id 复盘，读不到那篇笔记的附件。

    路由是拿**当前账号**的 notes.json 去按 id 找笔记的，所以 alice 的 note_id 在 bob
    这边查不到 —— 附件文字自然也就不会进 bob 的提示词。
    """
    client, _, graph = runtime
    client.headers["Authorization"] = f"Bearer {register(client, 'bob')}"
    response = review_request(client)
    assert response.status_code == 200
    assert "附件里的识别文字" not in graph.calls[-1]["user_input"]
    assert response.json()["attachment_notes"] == []


def test_plain_chat_is_untouched_by_the_attachment_feature(runtime) -> None:
    """普通对话完全不碰附件：没有 attachment_notes，也不设 stored_input。"""
    client, _, graph = runtime
    session_id = client.post("/api/explain", json={"text": "负反馈"}).json()["session_id"]
    response = client.post("/api/chat", json={"session_id": session_id, "message": "还是不懂"})
    assert response.status_code == 200
    assert response.json()["attachment_notes"] == []
    assert graph.calls[-1]["user_input"] == "还是不懂"
    assert graph.calls[-1]["stored_input"] is None


# =============================== 匿名（游客）身份 ===============================


def guest(client) -> dict:
    """匿名建一个游客身份，返回 `{token, user}`。"""
    response = client.post("/api/auth/guest")
    assert response.status_code == 200, response.text
    return response.json()


def test_guest_endpoint_stays_anonymous(runtime) -> None:
    """免注册身份的意义就是「不用先登录」，所以它自己当然不能被登录挡住。"""
    client = anonymous(runtime[0])
    payload = guest(client)
    assert payload["user"]["guest"] is True
    assert payload["user"]["username"], "得有个能显示的称呼"
    assert "salt" not in payload["user"] and "passwordHash" not in payload["user"]

    # 拿到的 token 和正式账号同源：受保护的路由一视同仁地放行。
    client.headers["Authorization"] = f"Bearer {payload['token']}"
    for path in ("/api/auth/me", "/api/user/profile", "/api/notes", "/api/reviews", "/api/cards/due"):
        assert client.get(path).status_code == 200, path


def test_two_guests_are_different_accounts(runtime) -> None:
    """每个游客一个 uid，互不可见 —— 数据隔离这一层对匿名身份同样生效。"""
    client = anonymous(runtime[0])
    first = guest(client)
    second = guest(client)
    assert first["user"]["id"] != second["user"]["id"]

    client.headers["Authorization"] = f"Bearer {first['token']}"
    assert client.post("/api/user/points", json={"delta": 30, "reason": "test"}).status_code == 200

    client.headers["Authorization"] = f"Bearer {second['token']}"
    assert client.get("/api/auth/me").json()["profile"]["points"] == 0, "看不到别人的数据"


def test_guest_keeps_its_data_after_binding_an_account(runtime) -> None:
    """绑定的全部意义：uid 不变，所以攒下的数据一个都不丢。

    「换台设备继续用」就落在这一步 —— 绑定之后必须能用用户名密码登回**同一个**账号。
    """
    client = anonymous(runtime[0])
    created = guest(client)
    client.headers["Authorization"] = f"Bearer {created['token']}"
    assert client.post("/api/user/points", json={"delta": 30, "reason": "test"}).status_code == 200

    bound = client.post("/api/auth/upgrade", json={"username": "绑定后的我", "password": "hunter2"})
    assert bound.status_code == 200, bound.text
    assert bound.json()["user"]["id"] == created["user"]["id"], "uid 必须不变"
    assert "guest" not in bound.json()["user"], "绑定后不再是匿名身份"

    # 换一个干净客户端、走真正的登录接口 —— 模拟「另一台设备」。
    fresh = anonymous(runtime[0])
    logged_in = fresh.post("/api/auth/login", json={"username": "绑定后的我", "password": "hunter2"})
    assert logged_in.status_code == 200
    assert logged_in.json()["user"]["id"] == created["user"]["id"]

    fresh.headers["Authorization"] = f"Bearer {logged_in.json()['token']}"
    assert fresh.get("/api/auth/me").json()["profile"]["points"] == 30, "数据必须跟着账号"


def test_guest_display_name_is_not_a_login(runtime) -> None:
    """游客的显示名不是凭证：拿它配任何密码都登不进去。"""
    client = anonymous(runtime[0])
    created = guest(client)
    name = created["user"]["username"]
    assert client.post("/api/auth/login", json={"username": name, "password": ""}).status_code == 401
    assert client.post("/api/auth/login", json={"username": name, "password": "hunter2"}).status_code == 401


def test_upgrade_requires_a_token_and_reports_failures(runtime) -> None:
    client = anonymous(runtime[0])
    # 没登录当然不能认领任何身份。
    assert client.post("/api/auth/upgrade", json={"username": "x", "password": "hunter2"}).status_code == 401

    # 格式不合规 → 422。
    client.headers["Authorization"] = f"Bearer {guest(client)['token']}"
    assert client.post("/api/auth/upgrade", json={"username": "a", "password": "hunter2"}).status_code == 422
    assert client.post("/api/auth/upgrade", json={"username": "合法名字", "password": "123"}).status_code == 422

    # 撞上已注册的用户名 → 409。注意得用一个**游客**去撞：正式账号连第二步都到不了。
    register(client, "already-here")
    client.headers["Authorization"] = f"Bearer {guest(client)['token']}"
    assert client.post("/api/auth/upgrade", json={"username": "Already-Here", "password": "hunter2"}).status_code == 409

    # 已经是正式账号的，不能再绑一次 → 400（先于重名判断：它根本没有可绑的匿名身份）。
    client.headers["Authorization"] = f"Bearer {register(client, '已正式')}"
    assert client.post("/api/auth/upgrade", json={"username": "另一个", "password": "hunter2"}).status_code == 400


def test_real_account_projection_has_no_guest_key(runtime) -> None:
    """`guest` 只在是匿名身份时出现 —— 正式账号的投影保持原来那三个键。

    `public_user` 的注释里说明了为什么不做成恒定输出：这形状是两个测试钉住的
    凭证外泄护栏，不该为了一个布尔值去改。
    """
    client = anonymous(runtime[0])
    token = register(client, "普通人")
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert set(me.json()["user"]) == {"id", "username", "createdAt"}
    assert not me.json()["user"].get("guest")
