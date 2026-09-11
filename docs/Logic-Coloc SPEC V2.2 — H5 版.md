# Logic-Coloc

## SPEC V2.2 — H5 Web Application

> 版本：V2.2  
> 产品形态：H5 Web Application  
> 核心方向：AI 知识解释 + 多轮知识对话 + 跨学科逻辑同源发现  
> Agent：LangGraph Single Agent  
> RAG：正式纳入核心架构  
> Logic Engine：保留现有确定性同源判断核心  
> Multi-Agent：暂不实现，根据 Benchmark 决定是否进入后续版本

---

# 1. 产品定义

## 1.1 产品名称

Logic-Coloc

核心定位：

> 把复杂知识翻译成容易理解的语言，并帮助用户发现不同学科之间隐藏的底层逻辑联系。

产品不是简单的：

- AI 搜索
- AI 总结
- 向量相似度搜索
- ChatGPT 套壳

核心差异在于：

> 系统不仅解释“这个概念是什么意思”，还尝试回答“这个概念背后的机制是什么，以及其他学科是否存在结构上相似的逻辑”。

---

# 2. 产品形态

Logic-Coloc V2.2 第一阶段采用：

> **H5 Web Application**

而不是浏览器插件。

用户通过浏览器访问 H5 页面。

第一版不要求浏览器插件直接读取知乎页面。

---

# 3. H5 第一版核心用户流程

## 3.1 读懂它

```text
用户打开 H5
    ↓
粘贴知乎/网页/论文中的专业文本
    ↓
点击「读懂它」
    ↓
Agent 分析文本
    ↓
提取 Concept
    ↓
提取 Domain
    ↓
提取 Mechanism
    ↓
生成 Logic Profile
    ↓
生成通俗解释
    ↓
创建 Knowledge Session
    ↓
进入多轮知识对话
```

---

## 3.2 发现同源

```text
用户输入专业文本
        ↓
提取核心概念
        ↓
生成 Logic Profile
        ↓
RAG 检索候选概念
        ↓
Logic Engine 计算逻辑相似度
        ↓
Threshold 判断
        ↓
如果达到阈值
        ↓
Mapper 进行跨学科概念映射
        ↓
Critique 检查类比是否成立
        ↓
生成最终报告
```

---

# 4. H5 第一版页面

第一版 H5 至少包含以下页面/区域：

## 4.1 首页

```text
Logic-Coloc

把复杂知识，
翻译成你能理解的逻辑。

[ 粘贴专业文本 ]

[ 读懂它 ]

[ 发现同源 ]
```

---

## 4.2 知识解释页面

```text
原文

↓

核心概念

↓

一句话解释

↓

通俗解释

↓

机制拆解

↓

为什么？

↓

生活中的例子

↓

跨学科联系

↓

继续提问
```

---

## 4.3 Knowledge Session 页面

用户进入“读懂它”以后，不应该只是得到一次性答案。

必须形成一个持续的 Knowledge Session。

用户可以继续：

- 为什么？
- 我还是不懂
- 举个例子
- 换一种说法
- 这个和 XX 有什么区别？
- 还有其他领域类似吗？

系统需要保留当前知识上下文。

---

# 5. Knowledge Session

Knowledge Session 是 Logic-Coloc 的核心数据结构。

一个 Session 包含：

```text
source
knowledge
conversation
timestamps
```

---

## 5.1 Source

```text
platform
url
title
selected_text
context_before
context_after
```

对于 H5：

`platform` 和 `url` 可以为空。

例如：

```json
{
  "platform": "zhihu",
  "url": "",
  "title": "",
  "selected_text": "......"
}
```

---

## 5.2 Knowledge

```text
concept
domain
mechanism
logic_profile
key_terms
```

---

## 5.3 Conversation

```text
summary
understood_points
user_confusions
important_questions
important_answers
current_focus
recent_messages
```

---

# 6. Session Context 控制

不能无限把历史消息全部发送给 LLM。

Agent 每次获得：

```text
Stable Knowledge Context
+
Conversation Summary
+
Recent Messages
+
Current Question
```

而不是：

```text
全部历史消息
```

这样可以避免 Context 无限增长。

---

# 7. Intent Routing

Agent 需要识别用户当前意图。

第一版至少支持：

```text
EXPLAIN
SIMPLIFY
EXAMPLE
TERM
WHY
COMPARE
FOLLOW_UP
DISCOVER_HOMOLOGY
```

例如：

```text
“这是什么意思？”
→ EXPLAIN

“能不能说简单一点？”
→ SIMPLIFY

“举个例子”
→ EXAMPLE

“为什么会这样？”
→ WHY

“这个和量子力学有什么区别？”
→ COMPARE

“发现其他领域类似的东西”
→ DISCOVER_HOMOLOGY
```

---

# 8. Agent 架构

Logic-Coloc 使用：

> **LangGraph Single Agent**

作为第一阶段正式 Agent Framework。

第一阶段禁止直接引入 Multi-Agent。

---

# 9. Agent 的职责

Agent 可以：

- 理解用户问题
- 判断 Intent
- 提取 Concept
- 提取 Domain
- 提取 Mechanism
- 调用 RAG
- 调用 Logic Engine
- 调用 Mapper
- 调用 Critique
- 组织最终回答
- 管理 Knowledge Session
- 决定下一步调用什么 Tool

Agent 不可以：

- 自己修改 Homonomy Score
- 绕过 Threshold
- 用主观判断代替 Logic Engine
- 把 embedding similarity 当成最终同源度
- 强行制造跨学科类比
- 删除原有 deterministic core

核心原则：

> Agent 可以决定查谁、比较谁、怎么组织报告，但不能自由修改同源度分数。

---

# 10. Pydantic Schema

Agent、RAG、Session 和 API 数据必须尽可能使用 Pydantic Schema。

至少需要：

```text
LogicProfile
Concept
KnowledgeContext
Session
Message
Intent
CandidateConcept
HomologyResult
MappingResult
CritiqueResult
ExplainRequest
ExplainResponse
ChatRequest
ChatResponse
DiscoverRequest
DiscoverResponse
```

禁止让核心模块之间长期依赖没有结构约束的巨大 Dictionary。

---

# 11. Logic Profile

Logic-Coloc 使用五维 Logic Profile。

```text
system_closure
causal_chain_length
negative_feedback_strength
randomness_entropy
zero_sum_resource_level
```

每一个维度范围：

```text
0.0 ~ 1.0
```

---

# 12. 现有 Logic Engine

现有：

```text
homonomy.py
```

中的确定性同源计算必须保留。

核心流程：

```text
Logic Profile A
+
Logic Profile B
        ↓
Deterministic Homonomy Engine
        ↓
Homonomy Score
```

默认 Threshold：

```text
0.85
```

除非经过 Benchmark 验证，不得随意修改。

---

# 13. Homonomy 与 Retrieval 必须分离

系统中至少存在两个不同概念：

## Retrieval Score

用于回答：

> “这个候选值不值得进一步比较？”

来源可以包括：

- domain similarity
- keyword similarity
- embedding similarity
- logic profile similarity

---

## Homonomy Score

用于回答：

> “这两个概念的底层逻辑是否达到系统定义的同源标准？”

Homonomy Score 必须由：

> Deterministic Logic Engine

产生。

禁止：

```text
embedding cosine = homonomy score
```

---

# 14. RAG

RAG 是 V2.2 的正式架构组成部分。

RAG 的核心任务：

> **找谁。**

不是：

> 替代 Logic Engine。

---

# 15. RAG V0：Local Corpus

第一阶段不使用复杂向量数据库。

不要求：

- Chroma
- Milvus
- Pinecone
- Elasticsearch

第一版可以使用：

```text
JSONL / JSON
```

作为本地知识库。

目录：

```text
data/
└── corpus/
    ├── concepts.jsonl
    └── logic_profiles.jsonl
```

---

# 16. Corpus 数据结构

每个知识条目至少包含：

```text
id
concept
domain
description
mechanism
logic_profile
keywords
examples
```

例如：

```json
{
  "id": "immune_negative_feedback",
  "concept": "免疫负反馈",
  "domain": "免疫学",
  "description": "...",
  "mechanism": "...",
  "logic_profile": {
    "system_closure": 0.8,
    "causal_chain_length": 0.7,
    "negative_feedback_strength": 0.95,
    "randomness_entropy": 0.3,
    "zero_sum_resource_level": 0.2
  },
  "keywords": [
    "负反馈",
    "稳态",
    "免疫"
  ]
}
```

---

# 17. RAG V0 Retrieval

第一版采用：

```text
Query
 ↓
Concept / Domain / Keyword extraction
 ↓
Coarse Filtering
 ↓
Logic Profile similarity
 ↓
Top-K candidates
```

然后进入：

```text
Logic Engine
```

---

# 18. RAG V1

后续可以升级：

```text
Embedding Retrieval
+
Logic Profile Retrieval
+
Keyword Retrieval
```

形成 Hybrid Retrieval。

---

# 19. RAG V2

更成熟阶段：

```text
Query
 ↓
Embedding Retrieval
+
Keyword Retrieval
+
Domain Filtering
+
Logic Profile Filtering
 ↓
Candidate Fusion
 ↓
Top-K
 ↓
Logic Engine
```

但是 V2.2 不要求立即实现复杂向量数据库。

---

# 20. Concept Mapping

现有：

```text
mapper.py
```

继续保留。

Mapper 负责：

> 解释两个不同领域的概念在结构上如何对应。

例如：

```text
免疫系统负反馈
        ↕
Server Circuit Breaker
```

Mapper 应解释：

```text
触发条件
控制机制
反馈路径
系统目标
异常处理
```

---

# 21. Trust Boundary

系统必须严格区分：

> Logic Correspondence

和：

> Scientific Equivalence

禁止输出：

```text
免疫系统 = 服务器 Circuit Breaker
```

更推荐：

```text
二者并不属于同一科学机制，
但在“异常发生 → 抑制扩散 → 恢复系统稳定”
这一结构上存在逻辑对应关系。
```

系统必须优先使用：

- 底层逻辑相似
- 结构上的对应关系
- 可以类比理解
- 在某个机制层面存在相似性

避免：

- 完全一样
- 本质相同
- 就是同一个东西

---

# 22. Critique

在生成跨学科映射后，需要进行 Critique。

Critique 检查：

```text
是否存在真正的结构对应？
是否只是关键词相似？
是否存在明显的领域错误？
是否夸大了类比？
是否把类比说成科学等价？
是否应该拒绝该候选？
```

如果证据不足：

```text
不要强行生成同源结论。
```

---

# 23. Agent Tools

Agent 可以使用以下 Tools：

```text
extract_concept
extract_logic_profile
retrieve_candidates
calculate_homonomy
map_concepts
critique_mapping
get_session
update_session
generate_explanation
```

其中：

```text
calculate_homonomy
```

必须调用现有确定性 Logic Engine。

---

# 24. 推荐 Agent 流程

## 24.1 EXPLAIN

```text
User
 ↓
Intent Router
 ↓
Extract Concept
 ↓
Extract Mechanism
 ↓
Generate Explanation
 ↓
Create / Update Session
 ↓
Response
```

---

## 24.2 FOLLOW_UP

```text
User
 ↓
Intent Router
 ↓
Load Session
 ↓
Load Stable Knowledge Context
 ↓
Load Conversation Summary
 ↓
Load Recent Messages
 ↓
Answer
 ↓
Update Summary
```

---

## 24.3 DISCOVER_HOMOLOGY

```text
User
 ↓
Intent Router
 ↓
Extract Concept
 ↓
Extract Logic Profile
 ↓
RAG Retrieval
 ↓
Top-K Candidates
 ↓
Deterministic Homonomy
 ↓
Threshold
 ↓
Mapper
 ↓
Critique
 ↓
Final Report
```

---

# 25. LangGraph State

LangGraph State 至少需要能够表达：

```text
session_id

user_input

intent

concept

domain

mechanism

logic_profile

knowledge_context

conversation_summary

recent_messages

candidate_concepts

retrieval_scores

homonomy_results

mapping_results

critique_results

final_response
```

State 必须有明确结构。

---

# 26. H5 + API 架构

V2.2 新增正式 API 层。

整体架构：

```text
┌──────────────────────────┐
│        H5 Frontend       │
│                          │
│  输入专业文本             │
│  读懂它                   │
│  多轮对话                 │
│  发现同源                 │
└────────────┬─────────────┘
             │
             │ HTTP / REST API
             ↓
┌──────────────────────────┐
│       Backend API        │
│                          │
│ /api/explain             │
│ /api/chat                │
│ /api/discover            │
│ /api/session             │
└────────────┬─────────────┘
             ↓
┌──────────────────────────┐
│     LangGraph Agent      │
└────────────┬─────────────┘
             ↓
     ┌───────┼────────┐
     ↓       ↓        ↓
    RAG   Logic     Mapper
          Engine
             ↓
          Critique
             ↓
┌──────────────────────────┐
│    Knowledge Session     │
└──────────────────────────┘
```

---

# 27. API

第一版至少提供：

## POST /api/explain

用途：

> 创建知识解释 Session。

Request：

```json
{
  "text": "用户输入的专业文本",
  "title": "",
  "source": "zhihu"
}
```

Response：

```json
{
  "session_id": "xxx",
  "concept": "xxx",
  "domain": "xxx",
  "explanation": "xxx"
}
```

---

## POST /api/chat

用途：

> 在已有 Knowledge Session 中继续提问。

Request：

```json
{
  "session_id": "xxx",
  "message": "为什么？"
}
```

Response：

```json
{
  "session_id": "xxx",
  "answer": "xxx"
}
```

---

## POST /api/discover

用途：

> 发现跨学科同源概念。

Request：

```json
{
  "text": "专业概念或文本",
  "session_id": "xxx"
}
```

Response：

```json
{
  "concept": "xxx",
  "results": []
}
```

---

# 28. API 层原则

API 只负责：

```text
接收请求
 ↓
验证 Schema
 ↓
调用 Agent
 ↓
返回结构化结果
```

不能把业务逻辑全部塞进 API Route。

业务逻辑应该位于：

```text
Agent / Tools / Core
```

---

# 29. 项目目录

推荐结构：

```text
logic_coloc/
│
├── SPEC.md
│
├── feature_extractor.py
├── homonomy.py
├── mapper.py
├── config.py
├── cache.py
├── demo_texts.py
├── run_demo.py
├── precompute.py
├── make_figures.py
│
├── agents/
│   ├── __init__.py
│   ├── schemas.py
│   ├── state.py
│   ├── tools.py
│   ├── graph.py
│   ├── report.py
│   └── agent_cli.py
│
├── rag/
│   ├── __init__.py
│   ├── corpus.py
│   ├── retriever.py
│   └── schemas.py
│
├── sessions/
│   ├── __init__.py
│   └── manager.py
│
├── api/
│   ├── __init__.py
│   ├── routes.py
│   ├── schemas.py
│   └── service.py
│
├── web/
│   ├── index.html
│   ├── style.css
│   └── app.js
│
├── data/
│   └── corpus/
│       ├── concepts.jsonl
│       └── logic_profiles.jsonl
│
└── tests/
    ├── test_tools.py
    ├── test_graph.py
    ├── test_rag.py
    ├── test_session.py
    ├── test_api.py
    └── test_regression_demo_pairs.py
```

---

# 30. H5 技术选型

第一版前端不要求复杂框架。

优先：

```text
HTML
CSS
JavaScript
```

如果后续需要，可以升级为：

```text
React / Vue
```

但第一阶段不应该为了框架本身增加复杂度。

---

# 31. 后端技术

后端负责：

```text
API
Agent
RAG
Session
Core
```

具体 Web Framework 可以根据当前环境选择轻量方案。

不要因为 H5 而重写现有 Python 核心。

---

# 32. LLM Client

必须继续复用现有：

```text
feature_extractor.llm()
```

以及现有配置：

```text
LC_BRIDGE
LC_MODEL
LC_TIMEOUT
LC_THINKING
LC_SAMPLES
LC_METHOD
LC_THRESHOLD
LC_GAMMA
LC_CJK_FONT
```

禁止创建第二套独立 LLM Client。

---

# 33. Cache

现有：

```text
cache.py
```

必须保留。

新增 Agent / RAG / Session 时，应考虑复用现有 Cache 机制。

不得为了新架构直接删除 Cache。

---

# 34. P0 第一阶段必须完成

## Product

- H5 首页
- 文本输入
- 「读懂它」
- 「发现同源」
- Knowledge Session 页面
- 多轮对话 UI

## Agent

- LangGraph Single Agent
- Intent Router
- Knowledge Session
- 多轮对话

## RAG

- Local Corpus
- JSONL
- Candidate Retrieval
- Top-K

## Logic

- Logic Profile
- Deterministic Homonomy
- Threshold
- Mapper
- Critique

## API

- `/api/explain`
- `/api/chat`
- `/api/discover`
- `/api/session`

## Testing

- Agent tests
- RAG tests
- Session tests
- API tests
- Existing regression tests

---

# 35. P0 不做

第一阶段禁止为了“看起来高级”加入：

- Multi-Agent
- 大型知识图谱
- 自动爬取整个互联网
- Elasticsearch
- Milvus
- Pinecone
- 复杂推荐系统
- 社交系统
- 用户账户系统
- 社区系统
- 大规模 Web Crawler

---

# 36. P1

当 P0 稳定以后：

```text
Embedding RAG
Vector Retrieval
Hybrid Retrieval
Larger Corpus
Web API
H5 UI refinement
Session persistence
Streaming Response
```

---

# 37. P2

只有 Benchmark 证明 Single Agent + RAG 不够时，才考虑：

```text
Multi-Agent
Domain Expert Agents
Logic Judge Agent
Knowledge Graph
Large-scale Corpus
Personal Knowledge Graph
Advanced Recommendation
Long-term Memory
```

未来架构：

```text
                 Orchestrator
                      ↓
        ┌─────────────┼─────────────┐
        ↓             ↓             ↓
   Domain Agent   Domain Agent   Domain Agent
        └─────────────┼─────────────┘
                      ↓
                 Logic Judge
                      ↓
             Deterministic Engine
                      ↓
                   Reporter
```

---

# 38. Benchmark

至少准备：

## Positive

```text
Immune Negative Feedback
×
Server Circuit Breaker
```

预期：

```text
高逻辑对应
```

---

## Candidate / Positive

```text
Quantum Superposition
×
Option Pricing
```

根据当前 Logic Profile 和映射结果进行人工验证。

---

## Negative

```text
Immune Negative Feedback
×
Quantum Superposition
```

预期：

```text
低逻辑对应
```

---

# 39. Evaluation Metrics

## Logic Engine

```text
Precision
Recall
F1
False Positive Rate
False Negative Rate
Human Agreement
```

---

## Agent

```text
Intent Accuracy
Tool Selection Accuracy
Report Consistency
Mapping Helpfulness
Critique Accuracy
```

---

# 40. 产品最重要的评价标准

不要只评价：

> “回答听起来像不像 AI。”

更重要的是：

```text
解释是否正确？
用户是否真正理解？
跨学科类比是否成立？
是否存在关键词导致的假相似？
是否过度类比？
是否能够明确拒绝错误类比？
```

---

# 41. Definition of Done

一个功能只有同时满足以下条件，才算完成：

```text
代码完成
+
可以运行
+
可以测试
+
Schema 正确
+
异常处理存在
+
旧功能没有被破坏
```

---

# 42. 对 AI Coding Agent 的开发要求

Codex / Claude Code 等 Coding Agent 不允许直接：

```text
“把整个项目全部重写。”
```

必须按照阶段开发。

---

## Stage 0：Scan

首先：

```text
扫描整个项目
```

确认：

```text
现有文件
现有依赖
现有入口
现有 LLM Client
homonomy.py
mapper.py
feature_extractor.py
cache.py
当前运行方式
当前测试
```

不得修改代码。

---

## Stage 1：Architecture

根据 SPEC：

```text
设计新增目录
确认模块边界
确认 API
确认 Agent State
确认 Pydantic Schema
```

不得删除旧核心。

---

## Stage 2：Pydantic

实现：

```text
agents/schemas.py
rag/schemas.py
api/schemas.py
```

运行测试。

---

## Stage 3：Knowledge Session

实现：

```text
sessions/
```

实现：

```text
create_session
get_session
update_session
summarize_session
```

运行测试。

---

## Stage 4：RAG V0

实现：

```text
rag/corpus.py
rag/retriever.py
```

使用 Local JSONL Corpus。

运行测试。

---

## Stage 5：LangGraph Agent

实现：

```text
agents/state.py
agents/tools.py
agents/graph.py
```

接入：

```text
feature_extractor
homonomy
mapper
rag
sessions
```

运行测试。

---

## Stage 6：API

实现：

```text
api/routes.py
api/service.py
api/schemas.py
```

完成：

```text
/api/explain
/api/chat
/api/discover
/api/session
```

运行测试。

---

## Stage 7：H5

实现：

```text
web/index.html
web/style.css
web/app.js
```

实现：

```text
输入文本
 ↓
读懂它
 ↓
知识解释
 ↓
继续提问
 ↓
发现同源
```

---

## Stage 8：Integration

最终：

```text
H5
 ↓
API
 ↓
LangGraph
 ↓
RAG
 ↓
Logic Engine
 ↓
Mapper
 ↓
Critique
 ↓
H5
```

进行完整 Demo。

---

# 43. 每次 Coding Agent 工作结束必须报告

必须输出：

```text
1. Modified files
2. Why these files were modified
3. Additions
4. Deletions
5. Existing feature modifications
6. New dependencies
7. Run instructions
8. Test method
9. Test results
10. Current issues
11. Next recommended step
```

---

# 44. 禁止事项

未经用户明确批准，不得：

```text
删除旧模块
重写 homonomy.py
替换现有 LLM Client
修改 Threshold
让 LLM 直接决定 Homonomy Score
用 embedding similarity 替代 Homonomy
绕过 Logic Engine
删除 cache.py
删除现有 Demo
改变已有 Demo 的预期结果
直接引入 Multi-Agent
直接引入复杂 Vector Database
进行大型无必要重构
```

---

# 45. 核心架构原则

整个项目必须遵守：

```text
H5
 ↓
API
 ↓
Agent
 ↓
┌───────────────┐
│               │
RAG        Knowledge Session
│               │
└───────┬───────┘
        ↓
Logic Engine
        ↓
Mapper
        ↓
Critique
        ↓
Final Answer
```

其中：

```text
Agent = 怎么组织
RAG = 找谁
Logic Engine = 像不像
Mapper = 怎么对应
Critique = 类比是否可靠
Session = 记住用户正在理解什么
H5 = 用户如何使用系统
API = 前后端如何连接
```

---

# 46. 最终产品演进路线

Logic-Coloc 不追求一次完成所有复杂能力。

演进路线：

```text
理解能力
    ↓
多轮知识对话
    ↓
逻辑判断能力
    ↓
知识积累能力
    ↓
复杂跨学科推理能力
```

对应技术路线：

```text
LLM
 ↓
Knowledge Session
 ↓
LangGraph Agent
 ↓
RAG
 ↓
Deterministic Logic Engine
 ↓
Concept Mapping
 ↓
Critique
 ↓
Benchmark
 ↓
Multi-Agent（仅在必要时）
```

---

# 47. 当前 V2.2 的核心目标

最终 Demo 必须能够完成：

```text
用户打开 H5
      ↓
粘贴一段知乎专业内容
      ↓
点击「读懂它」
      ↓
系统解释专业概念
      ↓
用户继续追问
      ↓
系统记住当前知识上下文
      ↓
用户点击「发现同源」
      ↓
系统从 RAG 找跨学科候选
      ↓
Logic Engine 判断逻辑对应程度
      ↓
达到 Threshold 才继续
      ↓
Mapper 解释两者如何对应
      ↓
Critique 检查类比
      ↓
H5 展示最终结果
```

这个流程必须成为第一版最重要的 End-to-End Demo。

---

# 48. 最重要的设计原则

> **不要让 Agent 取代 Logic Engine。**

> **不要让 RAG 取代 Logic Engine。**

> **不要让 embedding similarity 冒充 Homonomy。**

> **不要为了“AI 味”加入不必要的 Multi-Agent。**

> **不要因为改成 H5 而重写已有核心算法。**

最终目标：

> **让 Agent 负责理解与调度，让 RAG 负责寻找候选，让确定性 Logic Engine 负责判断，让 Mapper 负责解释，让 Critique 负责防止过度类比，让 Knowledge Session 负责持续理解，让 H5 成为普通用户真正可以使用的入口。**

---

# 49. Stage 8 增补：可核验的跨学科学习桥梁

## 49.1 阶段结论

本方案现实，但产品承诺必须限定为：

> 在经过策展的知识范围内，为跨学科概念提供有来源、可复核、明确边界的结构对应分析与学习桥梁。

系统不能承诺：

- 仅凭五维相似度证明两个科学概念同源；
- 对任意输入都找到可靠类比；
- 用 LLM 生成的解释替代领域事实来源；
- 替代领域专家完成工程、科研或投资决策。

五维 Logic Profile 和 Homonomy Score 是本系统定义下的结构判断依据，不是科学等价证明。达到阈值只允许候选进入映射与审查，不自动等于“可靠”。

---

## 49.2 本阶段要解决的问题

当前 Discover 结果主要由以下内容组成：

```text
候选概念
+
Retrieval Score
+
Homonomy Score
+
少量 A 术语 → B 术语
+
固定可靠性说明
```

这不足以服务“借、学、讲”三类目标用户，原因是：

1. 用户不知道候选知识来自哪里；
2. 用户不知道五维分数为什么这样计算；
3. 术语映射没有说明两端分别承担什么机制角色；
4. 用户无法判断映射在哪些条件下成立；
5. 用户不知道类比在哪些地方失效；
6. “可靠”标签缺少可审计证据；
7. 结果不能直接用于精读、设计借鉴或内容写作。

Stage 8 的目标不是增加更多漂亮文字，而是把 Discover 从“词语配对”升级为“有证据的跨学科学习报告”。

---

# 50. 产品输出原则

## 50.1 一把尺子和一册词典

Discover 必须同时提供：

```text
尺子：这个跨域对应是否站得住，以及依据和置信边界是什么

词典：两边的实体、过程、信号、目标和约束如何对应
```

只有词典没有尺子，会产生看似漂亮但无法验证的类比。

只有尺子没有词典，用户知道“像”，但无法学习或使用另一领域的知识。

---

## 50.2 一个结果，三种使用方式

同一份结构化结果应支持三类目标用户，不建立三套分析引擎：

### 借

重点展示：

- 对方领域的机制解决了什么问题；
- 哪些机制可以迁移；
- 迁移前提和风险；
- 哪些部分禁止直接照搬。

### 学

重点展示：

- A、B 两个概念各自的基础知识；
- 用用户熟悉的 A 术语理解 B；
- 逐项机制对照；
- 容易误解的地方；
- 推荐继续学习的问题和关键词。

### 讲

重点展示：

- 这个类比能不能公开使用；
- 可用于讲解的共同结构；
- 必须附带的限定语；
- 不能使用的夸张说法；
- 可核验的来源。

第一版 H5 默认采用“学”的呈现顺序，同时保留“借”和“讲”所需字段。

---

# 51. 证据模型

每个可靠候选必须同时包含四层证据。

## 51.1 来源证据

说明候选概念、机制描述和关键事实来自哪里。

每条 Corpus 记录后续至少需要增加：

```text
sources[]
```

每个 Source 至少包含：

```text
id
title
publisher_or_author
url_or_identifier
locator
supports
```

其中：

- `locator` 表示章节、页码、条目或其他可定位信息；
- `supports` 说明该来源支持哪一项机制事实；
- 没有可核验来源的内容不能包装成确定事实；
- LLM 不得编造标题、作者、URL、DOI 或页码。

第一版允许使用教材、官方文档、同行评议综述和权威百科条目，不要求在线实时搜索。

---

## 51.2 结构判定证据

不能只展示一个总分。必须展示五个维度：

```text
dimension
source_value
candidate_value
similarity_or_difference
plain_language_reason
```

用户必须看见：

- 哪些维度支持对应；
- 哪些维度存在明显差异；
- 总分由什么结构特征产生；
- Retrieval Score 只表示“值得比较”，不表示对应成立；
- Homonomy Score 只由确定性 Logic Engine 产生。

LLM 可以解释分数，但不得修改分数。

---

## 51.3 机制映射证据

禁止只输出：

```text
A 词 → B 词
```

每条映射至少包含：

```text
source_term
source_type
source_role
target_term
target_type
target_role
correspondence_reason
evidence_refs
limitations
```

`source_type` 和 `target_type` 第一版使用受控类型：

```text
entity
process
signal
state
objective
constraint
failure_mode
```

默认只允许同类型映射。跨类型映射必须明确说明理由并降低可靠性。

例如：

```text
机器学习 → 神经元
```

属于“领域/方法集合 → 实体”的类型错配，不能因为词语相关就标记为可靠。

---

## 51.4 反证与边界证据

每个通过阈值的候选仍必须回答：

```text
相似性在哪个抽象层成立？
哪些机制没有对应物？
哪些条件变化后类比会失效？
是否存在领域中特别容易误导的说法？
```

至少输出：

```text
valid_conditions[]
failure_boundaries[]
known_differences[]
prohibited_claims[]
```

如果无法给出边界，候选不得标记为“可靠”，最多标记为“待核验”。

---

# 52. Discover Report 数据契约

Stage 8 应新增结构化报告 Schema。命名可以根据现有代码调整，但语义不得缺失。

```text
DiscoverLearningReport
├── source_concept
├── source_primer
├── candidate_concept
├── candidate_primer
├── retrieval_evidence
├── homonomy_result
├── dimension_comparisons[]
├── mechanism_summary
├── mapping_evidence[]
├── valid_conditions[]
├── failure_boundaries[]
├── known_differences[]
├── prohibited_claims[]
├── source_references[]
├── critique
├── verdict
└── learning_next_steps[]
```

## 52.1 Verdict

`verdict` 只能是：

```text
RELIABLE_WITH_LIMITS
NEEDS_REVIEW
REJECTED
INSUFFICIENT_EVIDENCE
```

禁止只用一个没有解释的“可靠”标签。

## 52.2 Critique

Critique 不能只检查“分数是否过阈值、mapping 是否非空”，至少需要验证：

1. 映射两端是否为相同机制层级；
2. 是否存在实体、过程、目标之间的类型错配；
3. 映射理由是否由 Corpus 事实支持；
4. 是否写明成立条件和失效边界；
5. 是否把结构相似夸大成科学等价；
6. 是否存在相互矛盾的映射；
7. 是否有足够来源支持关键结论。

Critique 可以使用 LLM 做语义审查，但最终结果必须保留确定性分数、结构化理由和可复核输入。LLM 不能改写 Homonomy Score。

---

# 53. Stage 8 Agent 工作流

```text
User Input
    ↓
extract_source_knowledge
    ↓
extract_logic_profile
    ↓
retrieve_candidates
    ↓
Deterministic Homonomy Engine
    ↓
threshold_gate
    ↓
load_candidate_evidence
    ↓
generate_structured_mapping
    ↓
validate_mapping_types
    ↓
critique_correspondence
    ↓
build_learning_report
    ↓
save_session
```

节点职责：

- `extract_source_knowledge`：提取概念、领域、机制、关键术语；
- `retrieve_candidates`：只负责找值得比较的候选；
- `Homonomy Engine`：继续使用现有确定性 Core；
- `threshold_gate`：未过阈值不得进入可靠映射；
- `load_candidate_evidence`：从 Corpus 加载来源和机制事实；
- `generate_structured_mapping`：生成带角色与依据的结构化映射；
- `validate_mapping_types`：拒绝明显层级和类型错配；
- `critique_correspondence`：审查事实依据、边界和夸大风险；
- `build_learning_report`：把通过审查的数据组织成面向用户的报告；
- `save_session`：允许用户围绕报告继续追问。

---

# 54. 单文本发现与双文本验证

本项目仍以主 SPEC 的单文本 Discover 为主要入口，但必须明确能力范围。

## 54.1 单文本发现

用途：

> 用户只有一段文本，系统从已策展 Corpus 中寻找值得比较的候选。

约束：

- 只能声称“在当前知识库范围内发现”；
- Corpus 没有覆盖时返回 `INSUFFICIENT_EVIDENCE`；
- 不允许为了保证有结果而强行返回候选；
- 页面必须显示当前知识库覆盖范围。

## 54.2 双文本验证

作为 Stage 8 的补充入口：

> 用户同时提供 A、B 两段文本，系统判断用户已有的跨域猜想是否成立。

双文本验证复用同一套 Logic Engine、Mapper、Critique 和 Learning Report，不创建第二套判断逻辑。

它适合“借”和“讲”的高价值场景，也可作为单文本检索质量不足时的可靠备用入口。

---

# 55. 神经网络示例的最低输出标准

输入人工神经网络或反向传播文本时，如果候选为“生物神经网络与突触可塑性”，报告至少需要说明：

## 55.1 可以建立的有限对应

```text
人工神经元 ↔ 生物神经元的抽象信号处理单元
连接权重 ↔ 突触效能
权重更新 ↔ 突触可塑性引起的连接强度变化
网络表征形成 ↔ 神经回路对输入模式形成选择性响应
```

每条对应都必须包含角色解释和来源引用。

## 55.2 必须说明的差异

至少包括：

- 标准反向传播依赖明确目标函数、全局误差信号和可微计算图；
- 生物学习通常包含局部活动、时序、神经调质、稳态调节等多种机制；
- “人工神经网络受生物神经系统启发”不等于二者采用相同学习算法；
- 不能把反向传播直接宣称为大脑已经被证实采用的机制。

## 55.3 禁止出现的映射

```text
机器学习 → 神经元
深度学习 = 突触可塑性
反向传播 = 大脑学习机制
```

除非报告明确把它作为错误示例或已否定说法。

---

# 56. H5 Learning Report 页面

Discover 结果不能只显示卡片标题、百分比和三组词。第一版至少按以下顺序展示：

```text
1. 判定结论
2. 为什么值得比较
3. A 概念快速入门
4. B 概念快速入门
5. 两边共同的机制骨架
6. 五维结构证据
7. 术语与机制角色对照表
8. 成立条件
9. 失效边界与关键差异
10. 来源与可核验依据
11. 下一步学习建议
12. 围绕本报告继续追问
```

页面要求：

- Retrieval Score 与 Homonomy Score 必须分开解释；
- 百分比不能成为页面唯一或最醒目的可信依据；
- “底层逻辑相似 ≠ 科学等价”持续可见；
- 每项关键事实可以定位到来源；
- `REJECTED` 和 `INSUFFICIENT_EVIDENCE` 也必须给出有学习价值的原因；
- 不允许通过隐藏低分候选制造“总能发现”的假象。

---

# 57. Corpus 升级要求

当前十余条 Corpus 只适合链路演示，不能支持开放领域发现。

Stage 8 Corpus 每条记录必须经过人工策展，至少包含：

```text
概念定义
领域
机制
五维 Logic Profile
关键术语
机制角色
成立条件
常见误解
来源
```

扩充原则：

1. 先覆盖目标用户文档中的邻接领域，不追求全学科；
2. 优先建设可形成正例、反例和边界例的概念组；
3. 每条事实有来源，Logic Profile 有打分说明；
4. 数量不能替代质量；
5. 未经过审核的 LLM 自动生成条目不得直接进入正式 Corpus。

首批重点领域：

```text
生物与免疫
神经科学
控制与系统工程
分布式系统与网络
AI 与机器学习
生态与复杂系统
金融与风险管理
统计物理
```

---

# 58. Benchmark 与发布门槛

在继续宣传“可靠”之前，必须建立人工标注 Benchmark。

## 58.1 Case 类型

```text
Positive：结构对应成立
Negative：关键词相似但机制不同
Boundary：部分成立但存在重要失效条件
Type Mismatch：实体、过程、目标或领域层级错配
No Coverage：Corpus 无足够证据
```

## 58.2 必备案例

至少包含：

```text
免疫负反馈 × Circuit Breaker
量子叠加 × 期权定价
人工神经网络 × 生物神经网络
免疫负反馈 × 量子叠加
机器学习 × 神经元（类型错配）
反向传播 × 生物突触可塑性（边界例）
```

## 58.3 评估指标

```text
Retrieval Recall@K
Homonomy Precision / Recall / F1
False Positive Rate
Mapping Type Accuracy
Mapping Evidence Coverage
Boundary Recall
Critique Rejection Accuracy
Source Grounding Accuracy
Human Agreement
Learning Helpfulness
```

阈值不得凭单个示例修改。只有 Benchmark 显示当前阈值或五维设计存在系统性问题时，才能提出版本化调整方案。

发布前至少满足：

- 所有关键映射都有依据和边界；
- 所有来源可定位，不存在虚构引用；
- Type Mismatch 测试可以稳定拒绝；
- Negative Case 不因关键词或普遍高余弦分数被标记为可靠；
- H5 能完整显示证据，而不是只显示结论；
- 人工评审确认示例对“借、学、讲”至少一种任务有实际帮助。

---

# 59. Stage 8 分阶段实施

## Stage 8A：Report Contract + Benchmark Baseline

目标：

- 定义 Evidence、Dimension Comparison、Mapping Evidence、Boundary、Verdict 和 Learning Report Schema；
- 建立 Positive、Negative、Boundary、Type Mismatch、No Coverage 测试集；
- 记录当前 Retriever、Homonomy、Mapper 和 Critique 基线结果；
- 暂不修改 Threshold。

完成标准：

- Schema 严格校验；
- Benchmark 可重复运行；
- 可以明确看见当前假阳性、假阴性和类型错配。

## Stage 8B：Curated Corpus + Provenance

目标：

- 为 Corpus 增加可核验来源、机制角色、成立条件和常见误解；
- 优先完善 Benchmark 涉及的概念组；
- 建立人工审核流程；
- 不引入 Vector DB。

完成标准：

- Benchmark 使用的每个候选都有完整来源；
- 每个关键事实可以追溯；
- Corpus 非覆盖输入能明确拒绝。

## Stage 8C：Evidence-grounded Mapping + Critique

目标：

- Mapper 输出结构化角色映射与依据；
- 增加映射类型校验；
- Critique 检查事实、层级、边界和夸大风险；
- 生成 `DiscoverLearningReport`；
- LLM 只负责基于证据组织语言。

完成标准：

- 不再出现无解释的三个词对；
- `机器学习 → 神经元` 等类型错配被拒绝；
- 可靠结论同时具备来源、维度、映射和边界证据；
- Homonomy Score 仍只来自现有确定性 Core。

## Stage 8D：H5 Learning Report

目标：

- 实现完整 Learning Report 页面；
- 默认服务“学”，同时提供“借”和“讲”所需信息；
- 支持围绕报告继续追问；
- 展示知识库覆盖范围和证据状态。

完成标准：

- 用户不看原始 JSON 也能理解两边知识；
- 用户能回答“为什么像、哪里不像、依据是什么”；
- 移动端和桌面端都能完整阅读；
- 拒绝结果仍提供清楚原因。

## Stage 8E：Calibration + End-to-End Acceptance

目标：

- 根据 Benchmark 评估 Retriever、五维画像、Homonomy、Mapper 和 Critique；
- 只有证据支持时才提出阈值或画像版本升级；
- 完成真实 LLM、API、Session、H5 端到端验收；
- 固化三个正式 Demo：正例、边界例、反例。

完成标准：

- 旧 Core regression 继续通过；
- 报告结果可复现；
- 正例能解释，边界例有限推荐，反例能拒绝；
- 不泄露内部错误、密钥或未审查来源；
- 目标用户能够使用报告完成一次“借、学或讲”任务。

---

# 60. Stage 8 严格禁止

1. 让 LLM 直接生成或修改 Homonomy Score；
2. 把 Retrieval Score 当作同源结论；
3. 只要分数过阈值就自动标记“可靠”；
4. 输出没有角色说明和依据的词语配对；
5. 编造来源、论文、作者、URL、DOI 或页码；
6. 为了总有结果而绕过拒绝机制；
7. 未经 Benchmark 直接修改 Threshold；
8. 用 Embedding 相似度替代 Logic Engine；
9. 创建第二套 Agent、Session、LLM Client 或 Logic Engine；
10. 在 Corpus 质量不足时宣称开放领域科学发现；
11. 引入 Multi-Agent、Vector DB 或大型知识图谱来掩盖基础证据不足；
12. 删除现有 Core、RAG、Session、API、H5 或 Demo 行为。

---

# 61. Stage 8 Definition of Done

Stage 8 只有在以下条件同时满足时才完成：

```text
候选有来源
+
总分有五维解释
+
映射有机制角色和依据
+
Critique 能拒绝类型错配
+
报告包含成立条件和失效边界
+
H5 能让用户学到 A、B 两边知识
+
Session 支持围绕报告继续追问
+
Benchmark 与全部旧测试通过
+
核心算法和阈值没有被 LLM 绕过
```

最终验收问题不是：

> 页面有没有显示一个 99% 和三组箭头？

而是：

> 用户能否说明两边各自是什么、为什么可以比较、证据来自哪里、能借什么、不能借什么，以及这个类比在哪一步会失效？
