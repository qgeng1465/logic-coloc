# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

Logic-Coloc「跨学科知识同源翻译机」：判断两段不同领域的文本是否具有相同的**底层逻辑结构**（"同源"），同源度超过阈值时再为两侧的领域术语建立跨域一一映射。整个代码库注释与输出为中文。

两套入口，共用同一套业务内核：

- **Web 应用**（主交付物）：`api/` 的 FastAPI 服务 + `web/` 前端 + `agents/` 的 LangGraph 编排。
- **CLI 离线演示**：`run_demo.py` / `precompute.py` / `make_figures.py`，只跑 `demo_texts.py` 里预置的 3 组文本，走本地缓存。

## 目录性质与运行方式

**本目录就是 `logic_coloc` 包本身**（模块间用相对导入 `from . import …`），不是独立项目根。

**所有运行产出都落在包目录里面**：Web 端数据在 `logic_coloc/data/`（账号、笔记、卡片）与 `logic_coloc/uploads/`；CLI 演示的缓存在 `logic_coloc/data/demo_cache.json`、出图在 `logic_coloc/assets/`。**不要在包的上一级目录再建 `data/` 或 `assets/`** —— 2026-09-13 之前 `cache.py` / `precompute.py` / `make_figures.py` 取的是包的**上一级**目录（本机是 `E:\`，用户整个个人盘，里面还堆着论文、毕设），结果每跑一次 CLI 就在人家的盘根凭空多出一个 `data/`，还和 Web 端真正的 `logic_coloc/data/` 名字就差一层、极易删错。已改成取包目录本身。

三处路径常量是唯一的定义点，动它们之前先看这里：`cache.py` 的 `_DEFAULT_PATH`、`precompute.py` 与 `make_figures.py` 的 `ASSETS`。

包入口只能以 `python -m logic_coloc.<模块>` 执行，且必须把父目录 `E:\` 放到 sys.path（在 `E:\` 下运行，或设 `PYTHONPATH=E:\`）。直接在 `E:\logic_coloc` 内 `python -m` 或直接运行单个 `.py` 文件都会因找不到包/相对导入而失败。

```bash
cd /e          # 必须在包的父目录下运行，下同

# ---- Web 应用 ----
python -m uvicorn logic_coloc.api:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips='*'
# 浏览器开 http://127.0.0.1:8000/

# ---- 测试（245 个，全部离线、不碰网络）----
python -m pytest logic_coloc/tests/ -q
python -m pytest logic_coloc/tests/test_api.py -q                       # 单文件
python -m pytest logic_coloc/tests/test_api.py::test_ocr_returns_text   # 单用例

# ---- CLI 演示（走离线缓存，秒开；可用 demo1/demo2/demo3）----
python -m logic_coloc.run_demo --pair demo1
python -m logic_coloc.run_demo --a "文本A" --b "文本B"     # 自定义文本，需在线 LLM
python -m logic_coloc.run_demo --pair demo2 --png out.png --method wasserstein

# 预计算：把全部演示数据写入 data/demo_cache.json + 生成雷达图 PNG（首次需 LLM 在线，约 1 分钟）
python -m logic_coloc.precompute

# 生成全部演示物料图（映射图/热图/度量对比/海报，纯本地渲染、可重复跑）
python -m logic_coloc.make_figures
```

依赖锁在 `requirements.txt`（本地已验证版本，Python 用 3.13；镜像也是 3.13，两侧一致）。`matplotlib` 只有 CLI 出图用、`pytest` 只有测试用，两者都**故意没列进去**——要跑 `make_figures` 得自己装 matplotlib。

Windows 控制台默认 GBK，中文/emoji 输出会乱码，跑 Python 前置 `PYTHONIOENCODING=utf-8`。

## Web 应用分层

自下而上四层，依赖单向：`api/` → `agents/` → `rag/` + `sessions/` → 根模块。

```
web/         单页前端。index.html 用绝对路径 /static/* 引资源
  └─ app.js  无框架，约 1400 行。两个「功能面板」(explainPanel / discoverPanel) +
             五个「应用页」(cardsPage / notesPage / reviewPage / petPage …) 两套切换机制。
             所有请求走 authFetch()（补 Authorization 头 + 401 统一处理：正式账号踢回
             登录页，游客则自动重建一个匿名身份，见 api/auth_store.py 的模块说明）
api/         HTTP 层
  ├─ __init__.py  app 装配：CORS 白名单写死 localhost:5500/8080；挂 /static → web/、/uploads
  ├─ routes.py    所有路由        ├─ service.py   LogicColocService：唯一持有 graph/corpus/retriever
  ├─ schemas.py   请求/响应模型    ├─ auth_store.py  账号：注册/登录/token/资料/能量
  └─ user_paths.py  唯一的路径推导层：DATA_DIR/USERS_DIR/UPLOAD_DIR + scoped()
     数据按账号分目录：data/users.json（凭证索引，含 hash）+
     data/users/<uid>/{notes,cards,review_records,profile}.json。上传落 uploads/<uid>/。
agents/      LangGraph 编排      ├─ graph.py 状态机   ├─ tools.py 所有 LLM 调用与报告生成
  └─ state.py AgentState（Pydantic，extra="forbid"）└─ schemas.py 领域模型
rag/         确定性检索          ├─ corpus.py 读 data/corpus/example.jsonl（11 条，人工标定）
  └─ retriever.py  词项/领域/画像加权打分
sessions/manager.py  纯内存会话（dict），有上限地暴露 recent_messages 给 Agent
```

**`LogicColocService` 是进程级单例**（`api/routes.py:_service`，模块级实例化）。它在 `__init__` 里一次性 `Corpus().load()` 并 `build_graph(...)`，所以：

- 改 `data/corpus/example.jsonl` 后**必须重启服务**才生效。
- `SessionManager` 的 `dict` 挂在这个单例上 → **会话是进程内存态**，多实例部署会让 `/api/chat` 随机 404，云上必须把实例数固定为 1。

### 路由表

**除下表标了「匿名」的六个之外，所有路由都挂了 `Depends(current_user)`，未带有效
`Authorization: Bearer <token>` 一律 401。** 加新路由时别忘了这个依赖 —— 漏掉就是
一个匿名可用的数据口子。前端对应的是 `authFetch`，新增请求不要直接调 `fetch`。

`/api/auth/guest` 是唯一一个**会写磁盘的匿名口**（每请求往 `data/users.json` 追加
一条），所以它单独有 `LC_MAX_GUESTS` 封顶；其余五个纯读或纯静态。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 返回 `web/index.html`（**匿名**） |
| GET | `/api/health` | 对 `config.BRIDGE` 做 1.5s TCP 探测；不通返回 `code:503`（**匿名**，平台探活用） |
| POST | `/api/auth/guest` | **匿名**。建一个免注册的匿名身份 → `{token, user}`，user 带 `guest: true`。前端首访静默调它，所以登录页不再是必经之路；超 `LC_MAX_GUESTS` 时淘汰最早的匿名记录 |
| POST | `/api/auth/upgrade` | 给匿名身份补用户名密码 → `{token, user}`。**uid 不变**，数据不丢。已经是正式账号 400、重名 409、格式不合规 422 |
| POST | `/api/auth/register` | → `{token, user}`；重名 409、格式不合规 422 |
| POST | `/api/auth/login` | → `{token, user}`；用户名或密码错都是 401 |
| GET | `/api/auth/me` | 校验 token + 回 `{user, profile}`，前端启动时用它决定进不进应用 |
| GET/POST | `/api/user/profile` | 读/写昵称、签名、头像 URL |
| POST | `/api/user/avatar` | 头像落盘到 `uploads/<uid>/` 并写进资料 |
| POST | `/api/user/points` | `{delta, reason}` → 服务端累加并返回权威值 |
| POST | `/api/user/update` | 兼容旧路径，同上（只覆盖非空字段） |
| POST | `/api/explain` | 建会话 → 跑 graph，返回概念 + 五维画像 + 通俗解释 |
| POST | `/api/chat` | 续用已有 `session_id` 追问；session 不存在 → 404 |
| POST | `/api/discover` | 跨学科同源发现，**单次约 74 秒**（见下） |
| GET | `/api/session/{id}` | 取会话快照（校验 `Session.owner_id`） |
| POST | `/api/ocr` | rapidocr 识图，限 image/*、≤10MB |
| POST | `/api/upload` | 附件落盘，URL 由 `request.base_url` 拼出 |
| GET/POST/PATCH/DELETE | `/api/notes*`、`/api/cards/*`、`/api/reviews*` | 笔记/卡片/复盘，写该账号自己的 JSON |

`/static/*` 与 `/uploads/*` 是**匿名**的。`/uploads` 不挂认证是**故意的**：`<img src>`
不会带 `Authorization` 头，挂了整个附件预览就全裂。改为按 uid 分子目录
（`uploads/<32位uid>/<uuid>_<名字>`），靠「随机 uid + 随机文件名」保护，与改动前同级。
StaticFiles 的 `directory` 是 **import 期快照**（`api/__init__.py` 里
`from .user_paths import UPLOAD_DIR`），运行期改 `user_paths.UPLOAD_DIR` 不会影响它。

### 账号体系的四个刻意决定（别"顺手改回去"）

1. **密码哈希只用标准库 PBKDF2-HMAC-SHA256**（`hashlib.pbkdf2_hmac`，每账号随机盐 +
   `hmac.compare_digest`）。`requirements.txt` 里**没有** passlib/bcrypt/argon2，而线上
   镜像只 `pip install -r requirements.txt` —— 本地恰好装着的 `bcrypt` 是别的包拖进来的，
   import 它线上就崩。轮数写在每条用户记录里，将来调高不会让老账号登不上。

2. **登录态是无状态 HMAC token**（`base64url(payload).base64url(HMAC-SHA256)`），不是内存
   dict。内存 dict 一重启就把所有人踢回登录页，而云托管每次重新部署都会重启。**代价是
   服务端无法吊销**：退出登录只能靠前端删掉本地那一份。要真吊销得再加一张黑名单表。
   密钥取 `config.SECRET_KEY`（环境变量 `LC_SECRET_KEY`），没配就自动生成一份存
   `data/.secret_key`。换成别人用不同密钥签的 token，`verify_token` 必然失败。

3. **按账号隔离靠「显式 `user_id` 参数 + 默认空串」，不靠全局状态。** 三个 store 的每个
   函数都有 `user_id: str = ""`，空串**原样走旧的全局路径**（`user_paths.scoped`）。
   这一条是既有 store 测试与离线脚本能零改动继续跑的原因。uid 由服务端
   `uuid4().hex` 生成，`user_paths.is_valid_user_id` 用 `\A[0-9a-f]{32}\Z` 卡死 ——
   **这是唯一一处外部输入直连文件路径的地方**，目录穿越必须在这里就被拦下。

4. **匿名身份是一等公民，不是「没登录」。** `auth_store.create_guest()` 产出的记录和
   正式账号**共用同一张表、同一种 token、同一套按 uid 分目录的隔离**，差别只有两点：
   没有密码哈希（带 `guest: True`）、`usernameLower` 带 `guest:` 前缀。因此：

   - 所有业务路由**一行都不用为游客特判**，`Depends(current_user)` 照常工作；
   - 登录接口永远匹配不到匿名记录（`_USERNAME_RE` 不许出现 `:`），且 `authenticate`
     里还有一道 `GUEST_FLAG` 显式拦截 —— 它没有密码，绝不能被比中；
   - `bind_credentials()` 把用户名密码补到**同一个 uid** 上，这就是「保存我的知识 /
     换台设备继续用」的全部实现：数据一个都不丢。

   **`public_user` 里的 `guest` 键只在是匿名身份时出现**，别改成恒定输出、也别为它
   去改 `test_public_user_never_exposes_credentials` / `test_register_login_and_me`
   —— 那两个测试用 `set(user) == {…}` 钉住的是**防凭证字段外泄**的护栏，
   消费端按「缺省即非匿名」读即可。

   ⚠️ `create_guest` 是唯一会被陌生人反复写入的落盘路径，所以有 `MAX_GUEST_ACCOUNTS`
   （`LC_MAX_GUESTS`，默认 2000）封顶：超限**只从 `users.json` 里摘掉最早那条记录**，
   磁盘上的 `data/users/<uid>/` 与 `uploads/<uid>/` 不删（删文件不可逆）。被摘掉的那个
   token 校验不过，等价于「很久没来的匿名身份过期了」。正式账号永不参与淘汰。
   同理 `_adopt_legacy_data` 的「第一个账号」只数正式账号，游客不继承旧全局数据。

`_adopt_legacy_data` 让**第一个注册的账号**继承旧的全局 `notes.json`/`cards.json`/
`review_records.json`（只读不删，判断失误时还能人工抢救）。线上镜像里那三个文件被
`.dockerignore` 排除、永远是空的，所以这一步只对本地开发有意义。

### 关于 `/api/discover` 的几个要点

它是唯一被 `asyncio.wait_for` 包住的路由（`LC_DISCOVER_TIMEOUT`，默认 240s），且**失败时不抛异常**——超时/LLM 异常/任何意外都返回 HTTP 200 + `code:500` + 「模型服务异常，请稍后重试」，靠 `errors: ["MODEL_SERVICE_UNAVAILABLE"]` 区分。改这段时不要"顺手"改成抛 4xx/5xx，前端依赖这个约定。

四个阶段的产物各自并存、供前端分别渲染：`candidates`（原始检索结果）、`homonomy_results`、`mapping_results`、`critique_results`、`learning_reports`。`service.discover()` 末尾会把 learning_reports 摘要成一条 SYSTEM 消息塞回 session，让后续 `/api/chat` 能引用——这是两条链路唯一的耦合点。

`web/app.js` 里 `top_k` 写死为 3；服务端再 `min(top_k, 5)` 夹一层。

## 处理管线（三阶段，分属三个模块）

整个系统的核心思想：**用 LLM 把文本压成低维"逻辑画像"向量，之后的一切判定都在本地确定性完成，尽量切断对 LLM 的依赖**。

1. **Step 1 特征提取 — `feature_extractor.py`**
   把 LLM 当作"高维逻辑特征提取器"而非文本生成器：给定一段文本，强制它输出严格 JSON——在 5 个逻辑维度上各打 0–100 分 + Top3 术语。按 `config.SAMPLES`（默认 3）次采样后**逐维取均值**去噪（LLM 打分有 ±10~20 抖动，均值化后同文本两次采样余弦相似度 ≈0.99）。产物是 5 维浮点向量 `vec` + Top3 术语。

2. **Step 2 同源度计算 — `homonomy.py`（本地、确定性、无 LLM）**
   主评分 `homonomy_cosine` = 5 维**对齐**余弦相似度（已验证语义排序正确）；可选副视角 `homonomy_wasserstein` = 一维 EMD（把向量当 5 个无序样本，丢弃维度身份，只比分布形状）。⚠️ W1 语义区分弱、实测会误判（把异源对排到最高），仅作可视化对比用途，不要改默认方法。用 `config.METHOD` / `LC_METHOD` 切换。

3. **Step 3 跨域映射 — `mapper.py`**
   仅当同源度 ≥ `config.THRESHOLD`（默认 0.85）才调用：再次用 LLM 让两侧 Top3 术语建立跨域对等映射，输出 `{"A_terms":…, "B_terms":…, "mapping":{a1:b1,…}}`。

**阈值闸门只有一个生效点**：`agents/graph.py:87` 的 `if score < config.THRESHOLD: continue`。低于阈值的候选**仍然会进 `homonomy_results`**（分数公开可见），只是跳过映射/报告/评审。

`tools.map_entities()` 内部还写了一模一样的第二道检查（`limit = config.THRESHOLD if threshold is None else threshold`），但调用方 `tools.map_entities(user_input, candidate_text, score)` 把 `score` 传在 `homonomy_score` 位置上、`threshold` 留空，于是内层读的还是 `config.THRESHOLD`——和外层同值，永远通过。**是冗余，不是被绕过**；要改阈值改 `config.THRESHOLD`（或 `LC_THRESHOLD`）即可，两道门会同时跟着变。

## 检索层（`rag/`）

`Retriever` 是**纯确定性字符串+向量匹配，不调 LLM**，五路加权：`concept` .30 / `keyword` .30 / `text` .20 / `domain` .10 / `logic_profile` .10；未提供 `logic_profile` 时该路权重从分母剔除而非计 0（`active_weights`）。

`MIN_RETRIEVAL_SCORE = 0.12` 的判定用的是**去掉画像项的纯词项分**（`lexical_score`）：全部候选词项分都低于 0.12 就整体返回 `[]`。所以「输入一段和语料毫无字面重叠的文本」会拿到空候选列表——这是预期行为，不是 bug。

`data/corpus/example.jsonl` 里 11 条概念的 `logic_profile` 是**人工标定的数值，不是 LLM 产出的**。因此换模型不会让它失效，但这个文件缺失会让 `/api/discover` 直接崩，**必须随包上传**（`.dockerignore` 里特意没有排除它）。

已知校准问题（**故意未修**）：语料内的画像与 LLM 现算的画像分布不同（LLM 更"中庸"，标准差 0.203 vs 人工标定 0.331），而 Web 路径恰好是两者混算，导致同源排序在部分输入上会倒挂。语料×语料之间区分度很好（0.32 的跨度），LLM×LLM 之间几乎没有区分度（0.077）。评委演示不涉及，故搁置。

## 5 个逻辑维度

`feature_extractor.DIMS`（键 → 中文释义），中文短标签在 `DIMS_CN`（图表用，键一一对应）：

- `system_closure` 系统封闭性（自包含、闭环运行）
- `causal_chain_length` 因果链长度
- `negative_feedback_strength` 负反馈强度（自我抑制/纠错/防失控）
- `randomness_entropy` 随机性/熵值
- `zero_sum_resource_level` 资源零和性（竞争/守恒/此消彼长）

改维度定义时必须**同时更新 `DIMS`、`DIMS_CN`、`feature_extractor.EXTRACT_SYS` 的提示词示例 JSON、`make_figures.py` 中硬编码的行色列表**，且旧缓存（内容哈希）会失效需重新 `precompute`。

## LLM 接入与配置

所有默认参数集中在 `config.py`，**全部可用 `LC_*` 环境变量覆盖**：`LC_BRIDGE`、`LC_MODEL`、`LC_API_KEY`、`LC_TIMEOUT`、`LC_THINKING`、`LC_SAMPLES`、`LC_METHOD`、`LC_THRESHOLD`、`LC_GAMMA`、`LC_CJK_FONT`、`LC_MAX_GUESTS`。

`config._load_dotenv()` 在 import 时执行，依次找 `<包目录>/.env` 和 `<父目录>/.env`，**只填 `os.environ` 里还没有的键**——所以平台注入的环境变量永远优先于 `.env` 文件。

- **端点默认值是 `https://api.deepseek.com/anthropic`**（不是本机 bridge）。当前实际跑在 `https://api.openai-next.com` + `gpt-4o-mini`，由本地 `.env` 覆盖，`.env` 未纳入版本控制。
- 统一 LLM 调用入口 `feature_extractor.llm()`，内部**按域名后缀嗅探协议**：

  ```python
  if config.BRIDGE.rstrip("/").endswith("api.openai-next.com") and config.API_KEY.startswith("sk-"):
      # OpenAI 方言：POST {BRIDGE}/v1/chat/completions，Bearer 头，读 choices[0].message.content
  else:
      # Anthropic 方言：POST {BRIDGE}/v1/messages，x-api-key 头，读 content[] 里 type=="text" 的块
  ```

  ⚠️ **两个条件必须同时成立**才走 OpenAI。任一不满足就静默回落到 Anthropic 方言，打在一个 OpenAI 端点上——表现为「LLM 返回空内容」，且报错信息完全指不到原因。改动 `LC_BRIDGE`（加 `/v1`、加尾斜杠、换等价域名）或换一把不以 `sk-` 开头的 key 都会触发。`tests/test_llm_client.py` 钉住了 Anthropic 分支的行为，改嗅探逻辑会让它失败。
- 已知坑：兼容端点上 thinking 传 `"disabled"` + 低 `max_tokens` 会**偶发空响应**；默认 `LC_THINKING=none`（= 不传该字段）+ `max_tokens≥2048` 最稳，不要改回传 disabled。

## 测试

`tests/` 下 245 个用例，**全部通过（0 failed）**，**全部离线**——不碰网络、不打真实 LLM。每个测试文件顶部自己 `sys.path.insert(0, parents[2])` 把 `E:\` 塞进 path，所以在哪运行都能 import 到包。

`tests/test_api.py` 用 `FakeGraph` 替掉真实 graph（`LogicColocService(graph=...)` 支持注入）。落盘路径现在由 `user_root` fixture **统一**重定向到 `tmp_path`（它同时 patch `user_paths.DATA_DIR/USERS_DIR/UPLOAD_DIR` 与三个 store 的文件常量，并注册一个 `tester` 账号），`runtime` 依赖它、把 token 塞进 `client.headers`——所以绝大多数用例是匿名时代写的、现在一行没改也照跑。要给某个账号播种数据用 `user_root.seed("notes.json", [...])`，别自己 patch 常量、更别写进真实的 `data/`。

`tests/test_card_store.py` 曾有 **2 个长期失败的用例**（`test_mastered_review_uses_ebbinghaus_intervals`、`test_forgot_and_vague_review_schedule`）：它们按「天」断言间隔，而 store 当时按「分钟」算（5 分钟 / 30 分钟）。**已于 2026-09-13 结清** —— 产品口径定为「今天复习过就算已掌握、以天为粒度」，`EBBINGHAUS_INTERVALS` 改成 1/2/4/7/15 天 + 30/90/180/365 天，那两条用例**一行没改就自己转绿了**（当时的判断「是测试过时、不是实现有 bug」是对的：页面曲线图 x 轴、复习阶段筛选、`scheduleDaysForStage` 三处早就按天写死了）。`test_every_interval_is_a_whole_number_of_days` 现在钉住这条，别再让排期表漂回分钟级。

`tests/test_schedule_curve.py` 守的是学习日志页顶上那张**手写的**艾宾浩斯曲线 SVG：8 个圆点必须与 8 个横坐标一一对应且同 x、标签不互相压字、点落在曲线上，并且横坐标与卡片下方「复习节点：…」那行一字不差。⚠️ **曲线图的 8 个节点（含 5分钟/30分钟/12小时）和筛选栏那 6 个按钮（全部 / 1天 / 2天 / 4天 / 7天 / 15天，`全部` 排最前）不是一回事**：前者是理论曲线，后者是产品真正在用的排期（按天起）。2026-09-13 图上是 7 个点配 5 个坐标、两套 x 还各不相同，才补成现在这样。

`tests/test_schedule_page.py` 是唯一**用 node 跑真 JS** 的用例：它调起 `tests/schedule_page_check.js`，那个脚本按花括号配对从 `web/app.js` 里切出 `schedulePlanDate / renderSchedule / scheduleGroup` 等真函数，配一套假 DOM 跑。

**它守的是「复习计划表」的语义，这页被推翻过一次，别又改回去**：标签 `[1天][2天][4天][7天][15天]` 说的是第几个复习**节点**，节点日期从**建卡那天**往后推（建卡日 +1/+2/+4/+7/+15 天）—— 9/13 建的卡在 [1天] 里落在 9/14、[2天] 里落在 9/15、[4天] 里落在 9/17。节点日期过了也照列，标 `✓ 已完成` / `已逾期`，**不隐藏**（它是计划表，不是「今天该复习什么」的待办）。2026-09-13 上午那版按 `next_review_due` 分区（[1天]=今天到期、[2天]=明天到期），用户当场否掉。**判定「今天要不要复习」的唯一依据始终是 `next_review_due`**，那是复习页和右上角铃铛的事，与这一页无关。

**别把这条护栏改成在 Python 里重写一遍推算规则**：那是平行实现，抄错了照样绿。没装 node 的环境自动 skip。

同一个文件里另有一条只看 `web/style.css` 静态文本的断言：筛选栏必须是**一行横着滚**（`overflow-x: auto`）、不许出现 `flex-wrap`、也不许把滚动条藏掉。换行会让「全部」掉到第二行孤零零一个（2026-09-13 用户当场否掉过），藏滚动条则没人知道右边还有按钮。按钮顺序（`全部` 最前）由 `schedule_page_check.js` 直接读 index.html 断言。

历史遗留：Windows 上若 `%TEMP%\pytest-of-<user>` 被残留锁住，会看到一批 `PermissionError: [WinError 5] 拒绝访问` 的收集错误——与代码无关，加 `--basetemp=<一个新目录>` 即可绕过。Linux 容器里不会出现。

## 部署

交付形态是**云托管（容器）**，不是静态站、也不是云函数：会话在进程内存、`/api/discover` 要跑 74 秒、还有本地文件写入，三条都排除云函数。

`Dockerfile` 里 `COPY . /app/logic_coloc` 是关键——仓库目录本身是包，必须让 `/app` 成为它的父目录，`uvicorn logic_coloc.api:app` 才解析得到。`.dockerignore` 挡掉 `.env`（内含真实密钥，线上靠平台环境变量注入）和本地数据、测试、文档。完整参数、验证步骤、排错对照表见 `docs/部署指南-CloudBase.md`（同目录 `make_docx.py` 可把该 md 转成 Word）。

## 缓存 / 离线演示机制

`cache.DemoCache` 读写 `data/demo_cache.json`：

- 文本画像按**文本内容 SHA-1 前 16 位**作键存 `texts`，演示对按 key 存 `pairs`。改提示词/维度后旧缓存不失效（键只看文本），注意配合 `--force` 或删缓存。
- 设计目标：`precompute` 跑过后，现场演示即使 LLM 端点不可达也**全程离线秒开**。`run_demo` 命中缓存即跳过 LLM。

⚠️ **这套缓存只服务 CLI 演示，Web 端完全不经过它**——`/api/discover` 每次都实时调 LLM，没有离线兜底。`DemoCache` 也只被 CLI 脚本 import，所以线上缺 `data/demo_cache.json` 不影响网站运行。

## 其它注意点

- 演示文本库 `demo_texts.py`：5 段文本 + 3 个演示对（demo1 免疫×服务器同源、demo2 量子×期权同源、demo3 免疫×量子异源对照），全部经真实 LLM 端到端验证过分数语义。新增演示文本要保证 LLM 打分语义稳定。
- `plots.py` 的 matplotlib 中文显示靠逐个尝试注册 CJK 字体候选（找不到就退化英文标签）；Windows 上可用 `LC_CJK_FONT` 指向中文字体（如微软雅黑/思源黑体 ttc）避免标题出方框。emoji 会被 `_no_emoji` 剥掉（matplotlib 缺字形）。
- 演示/海报里的分数来自缓存结果，改动评分逻辑后记得重新 `precompute` + `make_figures`，否则物料图分数是旧的。
- 前端同源部署优先：`web/app.js` 的 `API_BASE = window.LC_API_BASE || ""` 默认为空即同源，此时不碰 CORS。若改分离部署，`api/__init__.py` 的 CORS 白名单要加域名，且 `/uploads/*` 必须公网可达（附件与头像靠它显示）。
- `web/index.html` 用绝对路径 `/static/*` 引资源，所以静态文件必须挂在后端 `/static` 下。
