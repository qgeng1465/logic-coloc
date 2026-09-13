# Logic-Coloc · CloudBase 部署指南

> 给部署的同学：照这份做完就能上线。**先读第 1 节，它决定你选哪种服务。**

---

## 1. 先搞清楚这是个什么东西

**这不是一个静态网站。** `web/` 只是前端外壳，真正干活的是 `api/` 里的一个 Python FastAPI 服务。

这个服务同时干三件事：

| 路径 | 作用 |
|---|---|
| `/` | 返回 `web/index.html`（前端页面） |
| `/static/*` | 托管 `web/style.css`、`web/app.js` |
| `/api/*` | 业务接口（`discover` / `explain` / `chat` / `ocr` / `upload` / `notes` / `cards` / `health`） |

业务链路：FastAPI → LangGraph 编排 → 调用外部 LLM → 本地算分。

**结论：用「云托管」（容器），不要用「云函数」。**

理由（重要）：

- 会话存在进程内存里（`sessions/manager.py` 用的是 `dict`）。云函数每次调用都是全新实例，`/api/chat` 必然 404。
- `/api/discover` 单次请求要跑 74 秒左右，云函数默认超时通常不够。
- 有本地文件写入（笔记、卡片、上传附件），云函数文件系统不可靠。

---

## 2. 交付物清单

```
logic_coloc/
├── Dockerfile          ← 已备好，直接构建
├── .dockerignore       ← 已备好，会把 .env 挡在镜像外
├── requirements.txt    ← 已备好，版本是本地验证过的
├── api/                ← FastAPI 服务
├── agents/ rag/ sessions/   ← 业务逻辑
├── web/                ← 前端（index.html / app.js / style.css）
├── data/corpus/example.jsonl   ← ⚠️ RAG 语料库，必须随包上传，缺了 /api/discover 直接崩
└── config.py
```

**构建上下文就是仓库根目录本身**，不需要调整目录层级——`Dockerfile` 里已经处理好包路径问题（见第 4 节说明）。

---

## 3. 环境变量（最关键的一节）

线上靠环境变量配置，**不要**把 `.env` 传上去（`.dockerignore` 已经挡掉了）。

在云托管控制台的「环境变量」里配这几条：

| 变量 | 值 | 说明 |
|---|---|---|
| `LC_API_KEY` | **真实密钥** | 核心密钥，走私密渠道单独给，不要走 git |
| `LC_BRIDGE` | `https://api.openai-next.com` | **必须原样填**，见下方警告 |
| `LC_MODEL` | `gpt-4o-mini` | 照抄交付方 `.env` |
| `LC_TIMEOUT` | `180` | |
| `LC_DISCOVER_TIMEOUT` | `240` | discover 接口的内部上限 |
| `LC_THINKING` | `none` | **不要改**，见下方警告 |
| `LC_SECRET_KEY` | **一串随机字符，自己生成** | 账号登录态 token 的签名密钥。**强烈建议配**，见下方警告 |

可选（`config.py` 里有默认值，不配也行）：`LC_SAMPLES=3`、`LC_METHOD=cosine`、`LC_THRESHOLD=0.85`、`LC_GAMMA=0.06`。

`LC_SECRET_KEY` 随便给一串够长的随机字符串即可（例如 `python -c "import secrets;print(secrets.token_urlsafe(48))"` 的输出）。

### ⚠️ 关于 `LC_SECRET_KEY`

不配也能跑：容器第一次用到时会自己生成一份存到 `data/.secret_key`。但那个文件住在临时容器里，**每次重新部署都会换一把新密钥，于是所有人被踢回登录页**。配了它，密钥就由平台固定注入，重新部署不影响已登录的人（前提是账号数据本身没丢，见第 7 节第 1 条）。

镜像里**故意不带** `data/.secret_key`：带着等于把一个固定密钥烘进镜像，谁拿到镜像谁就能伪造任意账号的登录态。

### ⚠️ 两条硬规定

**1. `LC_BRIDGE` 必须一字不差地填 `https://api.openai-next.com`。**

不要加 `/v1`，不要加尾斜杠，不要换成别的域名（哪怕那个域名指向同一个服务）。

原因：`feature_extractor.py` 里是靠**域名后缀**来判断该用 OpenAI 协议还是 Anthropic 协议的：

```python
if config.BRIDGE.rstrip("/").endswith("api.openai-next.com") and ...
```

网址写法一变，就会**静默**走错协议，表现为「LLM 返回空内容」，而且报错信息**完全不会提示是这个原因**。

**2. `LC_THINKING` 保持 `none`。**

这是踩过的坑：在兼容端点上传 `disabled` + 低 `max_tokens` 会偶发空响应。

---

## 4. 部署步骤（云托管）

### 4.1 构建配置

| 项 | 值 |
|---|---|
| 运行时 | **Python 3.13**（由 Dockerfile 的 `python:3.13-slim` 决定，与本地开发一致） |
| 构建方式 | Dockerfile（用仓库根目录的 `Dockerfile`） |
| 容器端口 | `8000`（平台注入 `$PORT` 时会自动覆盖） |
| **请求超时** | **必须 ≥ 300 秒** |
| **最小实例数** | **`1`**（不要设 `0`，原因见下） |

**关于最小实例数，和 WorthBloom 相反：**

WorthBloom 当时设 `0`（可以缩到零省钱）是没问题的，因为它的会话存在**数据库**（`agent_sessions` 集合）里。
本项目不同：会话存在**进程内存**里（`sessions/manager.py` 用的是 `dict`）。设成 `0` 的话，实例一缩容，所有会话就丢了，用户下次点「继续对话」会拿到 `session not found`。所以**必须固定为 `1`**。

启动命令已经写在 `Dockerfile` 的 `CMD` 里，不用另外填：

```sh
uvicorn logic_coloc.api:app --host 0.0.0.0 --port ${PORT:-8000} \
        --proxy-headers --forwarded-allow-ips='*'
```

**为什么 `Dockerfile` 里有 `COPY . /app/logic_coloc` 这一行？**

因为这个仓库目录本身就是一个 Python 包（模块间用相对导入 `from . import …`），它不是项目根。必须让 `/app` 成为它的父目录，`uvicorn logic_coloc.api:app` 才解析得到。**这行不要改**，它替你规避了最容易踩的目录层级坑。

### 4.2 代码来源

和 WorthBloom 当年一样，走 **GitHub 仓库构建**：

| 项 | 值 |
|---|---|
| 仓库 | `https://github.com/Scarlett-yzy/logic-coloc` |
| 分支 | `main` |
| 构建方式 | `Dockerfile` |
| Dockerfile 位置 | 仓库根目录（无需填路径） |

**前提：本文档提到的所有文件必须先 commit 并 push 到 main**，否则云端拉到的代码里没有 `Dockerfile`。

如果改用「上传代码包」：压缩包的顶层就应该直接是包含 `Dockerfile` 的那一层，不要多套也不要少套。

### 4.3 出网

容器要能访问 `api.openai-next.com:443`。`/api/health` 会对这个地址做一次 TCP 探测，不通时前端会显示「模型服务未连接」。

---

## 5. 部署后怎么验证

按顺序做，每一步都过了再往下：

**1. 健康检查**

```bash
curl https://你的域名/api/health
```

期望看到：

```json
{"code":0,"api":"ok","llm_bridge":{"ok":true,"host":"api.openai-next.com","port":443},"model":"gpt-4o-mini"}
```

`code` 是 `0` 且 `llm_bridge.ok` 是 `true` 才算通。如果是 `503`，说明容器连不上 LLM 端点，检查出网和 `LC_API_KEY`。

**2. 静态资源**

```bash
curl -o /dev/null -w "%{http_code}\n" https://你的域名/
curl -o /dev/null -w "%{http_code}\n" https://你的域名/static/app.js
```

两个都应该是 `200`。

**3. 页面能打开且能连上后端**

浏览器打开域名，页面上不该出现「模型服务未连接」。如果出现，看第 6 节第 1 条。

**4. 跑一次真实业务**

在页面里输入一段文字，点「发现同源」。**第一次要等 70 秒左右**（这是正常的，要调多次 LLM），别以为卡死了。

---

## 6. 排错对照表

| 现象 | 原因 | 怎么办 |
|---|---|---|
| 页面打开一片空白 / 资源 404 | 前端没和后端同源 | 见下方「关于前后端同源」 |
| 提示「模型服务未连接」 | ① `LC_API_KEY` 没配或配错 ② 容器出网被限 | 先 `curl /api/health` 看 `detail` 字段 |
| discover 转很久然后报「模型服务异常」 | 平台请求超时 < 240 秒 | 把平台超时调到 **≥300 秒** |
| discover 返回空内容 / 报错但信息很奇怪 | **`LC_BRIDGE` 写得不完全一样** | 改回 `https://api.openai-next.com`，一字不差 |
| `/api/chat` 报 session not found | 用了云函数，或多实例部署 | 换云托管，并**把实例数固定为 1** |
| 容器起不来，日志报 `ModuleNotFoundError: logic_coloc` | 包层级错了 | 确认用的是仓库自带的 `Dockerfile` |
| 上传的附件重启后打不开 | 容器文件系统是临时的 | 见下方「已知限制」 |
| `/api/ocr` 报 500 | 镜像里没装 `rapidocr_onnxruntime` | 确认 `requirements.txt` 里那行没被删 |
| 接口全返回 401 | 前端请求没带 token | 确认走的是 `authFetch`；登录页本身打不开的话看下面一条 |
| 登录/注册成功但刷新后又回到登录页 | 浏览器禁用了 `localStorage`（隐私模式、部分内嵌 WebView） | token 存在 `localStorage` 里，禁用就没法保持登录 |
| 每次重新部署所有人都要重新登录 | 没配 `LC_SECRET_KEY` | 见第 3 节；不过更要紧的是第 7 节第 1 条 —— 账号数据本身也会一起丢 |

### 关于前后端同源（推荐）

**推荐把前端和后端部署在同一个域名下**（就用这个容器的地址）。这样：

- 静态资源（`/static/*`）和接口（`/api/*`）天然打通
- 不存在跨域问题，**不用改 CORS**
- 不存在 HTTPS 混合内容问题

前端代码已经改成默认同源（`web/app.js` 里 `API_BASE = window.LC_API_BASE || ""`），所以同源部署**不需要改任何前端代码**。

如果你一定要前后端分离部署（比如前端放静态托管、后端放云托管），需要：

1. 在 `index.html` 的 `<script src="/static/app.js">` **之前**插入：
   ```html
   <script>window.LC_API_BASE = "https://你的后端域名";</script>
   ```
2. 改 `api/__init__.py` 里的 CORS 白名单（现在只有 `localhost:5500` 和 `8080`），把前端域名加进去。
3. 附件和头像的图片必须能直接打开：`/uploads/*` 是**匿名可访问**的（`<img>` 不会带 `Authorization` 头，挂了鉴权整个预览就全裂）。它靠「随机 uid + 随机文件名」保护，和改动前同级。分离部署时这个前缀也要能公网访问。
4. 所有前端请求都走 `authFetch`（自动补 `Authorization` 头、统一处理 401）。**新加接口时别直接调 `fetch`**，否则会拿到 401 且不跳登录页。

---

## 7. 已知限制（提前知道，别当成 bug）

1. **账号和全部数据重启就丢 —— 这是现在最需要注意的一条。**
   笔记、卡片、复盘记录、头像上传，以及**账号本身**（`data/users.json` + `data/users/<uid>/`），全都存在容器本地文件里。容器重启或重新部署会清空，多实例之间也不共享。

   以前丢的只是数据，重新注册一下还能用；**现在有了账号体系，丢的是账号本身** —— 用户重新打开网站会发现自己「不存在了」，得重新注册，之前的东西也一起没了。

   二选一：
   - **挂一个持久化卷 / CFS** 到 `/app/logic_coloc/data` 和 `/app/logic_coloc/uploads`（改动最小，本方案直接受益，也让上面那条 `LC_SECRET_KEY` 的收益真正落地）；
   - 或者接受「演示够用」，但**要在交付时口头说清楚**，别让人以为注册了就存住了。

2. **实例数要固定为 1。**
   会话存在内存里，多实例会导致 `/api/chat` 随机 404。

3. **没有离线兜底。**
   网站核心功能每次都要实时调 LLM。LLM 端点不可达 = 网站不可用。演示现场务必确认出网和额度。

4. **discover 很慢。**
   单次 70 秒左右是正常的（要跑多次 LLM 采样 + 生成学习报告）。这是设计如此，不是性能问题。

5. **LLM 端点暂时不可用时**，`/api/discover` 会返回 `code:500` 和「模型服务异常，请稍后重试」，不会崩。

---

## 8. 本地怎么先验证一遍

在交付方机器上（Windows，`E:\` 下）：

```bash
cd /e
python -m uvicorn logic_coloc.api:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips='*'
```

然后浏览器开 `http://127.0.0.1:8000/`。

**注意必须从 `E:\` 下启动**（不是从 `E:\logic_coloc` 里），因为 `logic_coloc` 是包名，父目录要在 `sys.path` 上。
