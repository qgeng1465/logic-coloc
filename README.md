# Logic-Coloc · 跨学科知识同源翻译机

判断两段来自不同领域的文本，是否具有相同的**底层逻辑结构**（"同源"）；同源度超过阈值时，
再为两侧的领域术语建立跨域一一映射，并给出通俗解释与学习路径。

两套入口，共用同一套业务内核：

- **Web 应用**（主交付物）：FastAPI 服务 + 单页前端 + LangGraph 编排
- **CLI 离线演示**：只跑预置的 3 组文本，走本地缓存，秒开

---

## 一、本地跑起来（5 分钟）

### 1. 拿到代码 —— ⚠️ 目录名必须叫 `logic_coloc`

本仓库的目录**本身就是一个 Python 包**（模块之间用相对导入 `from . import …`），
所以目录名必须和包名一致。而 GitHub 上的仓库名是 `logic-coloc`（连字符），
Python 不认识这个名字 —— 直接 clone 会得到一个 `logic-coloc/`，启动时报
`ModuleNotFoundError: No module named 'logic_coloc'`。

**所以克隆时显式指定目标目录名：**

```bash
git clone https://github.com/Scarlett-yzy/logic-coloc.git logic_coloc
```

已经 clone 过了也不要紧，改个名即可：`mv logic-coloc logic_coloc`
（Windows：`ren logic-coloc logic_coloc`）

### 2. 装依赖

```bash
cd logic_coloc
pip install -r requirements.txt
```

Python 用 **3.13**（与 Docker 镜像的 `python:3.13-slim` 一致）。
别降到 3.11：`scipy==1.18.0` 没有 3.11 能用的包，`pip install` 会直接失败。
`matplotlib`（离线出图）和 `pytest`（测试）**故意没列进依赖**，需要时自己装。

### 3. 配 `.env`

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

然后把 `.env` 里的 `LC_API_KEY` 换成一把**真实可用的密钥**。

> **不填会怎样？** 服务照样能启动、能匿名使用、能看界面，但所有 AI 功能
> （提问解释、跨学科发现、笔记复盘）都会返回
> `{"code":"LLM_BACKEND_UNAVAILABLE"}` + HTTP 503 —— 因为每次都要实时调用大模型。
> 这是体面的降级，不是崩溃。
>
> `.env` 已被 `.gitignore` 排除，**永远不要提交它**；线上由平台环境变量注入。

`LC_BRIDGE` 和 `LC_API_KEY` 是要逐字盯的**两个**条件 —— `feature_extractor.py:55` 判定
该说 OpenAI 方言还是 Anthropic 方言时，要求**同时**满足：`LC_BRIDGE` 去掉尾斜杠后以
`api.openai-next.com` 结尾，**且** `LC_API_KEY` 以 `sk-` 开头。两个都满足才走
`POST {LC_BRIDGE}/v1/chat/completions`，否则一律走 `POST {LC_BRIDGE}/v1/messages`。

判定失败**不报错**，只会静默换协议，表现为「模型有响应但内容为空」：

- `LC_BRIDGE` 加了 `/v1`、或换成别的域名 → 后缀匹配不上
  （**尾斜杠本身没关系**，代码里有 `rstrip("/")` 兜着）
- `LC_API_KEY` 不以 `sk-` 开头 → 即使域名写对了也走错协议，**这条最容易漏**

### 4. 启动 —— ⚠️ 必须在**上一级**目录执行

因为该目录是包而不是项目根，运行命令得让父目录进 `sys.path`：

```bash
cd ..                                                        # 回到 logic_coloc 的上一级
PYTHONPATH=. python -m uvicorn logic_coloc.api:app --host 127.0.0.1 --port 8000
```

Windows PowerShell 下把 `PYTHONPATH=. ` 换成 `$env:PYTHONPATH="."; `。
浏览器打开 **http://127.0.0.1:8000/** 即可。

开发时想要改代码自动重启，加 `--reload --reload-dir <仓库的绝对路径>`：

```bash
python -m uvicorn logic_coloc.api:app --reload --reload-dir /绝对路径/logic_coloc
```

⚠️ `--reload-dir` **必须写绝对路径**。它相对当前工作目录解析，写成相对的
`logic_coloc` 时，如果 cwd 已经在仓库里就会指向一个不存在的目录，
**watchfiles 会静默地什么都不监视** —— 症状是「代码明明改了，接口还是旧行为」。

### 5. 直接用 —— 全程没有账号界面

打开 **http://127.0.0.1:8000/** 就能用，**不会让你登录**。第一次访问时前端会静默向
`POST /api/auth/guest` 要一个**匿名身份**（服务端给你分配一个 uid），笔记、卡片、能量
都照常能用，各人数据互不可见。

**身份只存在你自己浏览器的 localStorage 里**，所以：

- 换设备 / 换浏览器 / 清浏览器数据 = 换了一个新身份，看不到之前的笔记；
- 服务端重新部署（或 `LC_SECRET_KEY` 变了）会让旧 token 当场作废，用户重新打开网站
  会拿到新 uid、看到空书架 —— 盘上的数据还在 `data/users/<uid>/` 里，只是不再属于新身份。

这是「点击即用」的刻意取舍，**不是 bug**。产品上**没有任何登录 / 注册 / 绑定入口**
（2026-09-13 起）：设置里只有「账户与资料」和「关于知源」两个菜单项。

数据和身份都只存在运行目录的 `data/` 与 `uploads/` 里，按 uid 分目录隔离。

> 后端其实保留了完整的注册 / 登录 / 绑定接口（`api/auth_store.py`），但**前端不暴露**，
> 属于备用能力，不用管它。公开部署时匿名记录由 `LC_MAX_GUESTS`（默认 2000）封顶，
> 超限淘汰最早的那条。

---

## 二、跑测试

```bash
cd ..                                        # 同上，必须在包的上一级
python -m pytest logic_coloc/tests/ -q
```

全部离线：不碰网络、不打真实大模型，也不需要 `.env`。
注意 CI 里**不跑 pytest**（只做容器冒烟，见第三节），所以测试要在本机自己跑。

---

## 三、部署上线

完整参数清单、验证步骤与排错对照表见 **[`docs/部署指南-CloudBase.md`](docs/部署指南-CloudBase.md)**。
交付形态是**云托管（容器）**，不是静态站也不是云函数（会话在进程内存、单个请求要跑 70 秒、
还要写本地文件，三条都排除云函数）。Dockerfile 已就绪，`docker build -t logic-coloc .` 即可。

上线前请务必确认这四条（**漏了任何一条，队友/评委看到的都会是「坏的」**）：

| | 事项 | 不做的后果 |
|---|---|---|
| 1 | 在平台「环境变量」里配 `LC_API_KEY`（真实密钥，走私密渠道给，别走 git）<br>再配一把自己生成的 `LC_SECRET_KEY` | 所有 AI 功能 503；不配 `LC_SECRET_KEY` 则每次重新部署都让所有人的身份当场作废 |
| 2 | **实例数固定为 1**（最小实例数不要设 0） | 会话存在内存里，多实例会让 `/api/chat` 随机 404 |
| 3 | **挂持久化卷 / CFS** 到 `/app/logic_coloc/data` 与 `/app/logic_coloc/uploads` | 容器重启或重新部署会清空**身份记录本身**与全部笔记、卡片、附件。本地不会遇到这个问题（磁盘一直在），所以特别容易忽略 |
| 4 | 把**请求超时**设为 **≥300 秒**（配 `LC_DISCOVER_TIMEOUT=240`，必须小于平台超时） | 「发现同源」单次要跑约 70 秒，平台默认超时会在半路掐断，表现为「模型服务异常」 |

### 不用等云平台：先跑容器冒烟

`.github/workflows/container-smoke.yml` 会在 GitHub 的真 Linux 机器上用**仓库根那份
Dockerfile** 构建镜像、起容器、跑一遍真实请求 —— 等价于云托管平台的构建步骤，但不用登任何
控制台。工作流**配置为**在 push 到 `main` 时自动运行，**部署前先看它是不是绿的**，
绿了说明云上构建也会成功。

> ⚠️ **推完别马上看 —— 运行记录的注册会滞后，别把「暂时没看到」当成「失败」。**
> 2026-09-13 实测：一次协作者（非仓库所有者）推送后，半小时内都查不到任何运行，一度以为
> 没触发；它是**在下一次推送时才一起补登记的**，两条最终都是 `success`。所以刷新没看到就
> 等几分钟再刷，或直接去 Actions 页面手动 **Run workflow** 拿一个确定的结果
> —— 该工作流带 `workflow_dispatch`，任何时候都能手动起一次。

失败日志需要登录才能看，所以每个失败点都额外输出一条 `::error::` 注解，而**注解是公开可读的**：

```bash
curl -s -H "Authorization: Bearer <你的 token>" \
  https://api.github.com/repos/Scarlett-yzy/logic-coloc/actions/runs/<run-id>/annotations
```

> 一定带上 `Authorization` 头：不带就是匿名额度，**每小时只有 60 次且按出口 IP 计**，
> 很容易先被别的排查用光，报 `API rate limit exceeded` 而不是你想要的结果。

它只回答「镜像能不能构建、服务能不能起来、静态资源和鉴权闸门对不对」——
**不跑 pytest**（见第二节），也**不验真调大模型**（CI 里不配 `LC_API_KEY`，密钥绝不进仓库）。

---

## 四、出问题了先看这里

| 症状 | 多半是 |
|---|---|
| `ModuleNotFoundError: No module named 'logic_coloc'` | 目录名不是 `logic_coloc`（见第一节第 1 步） |
| 打开网站看到一层「无法连接服务器 / 刷新重试」 | 前端建匿名身份失败了，多半是**断网或后端没起来**。它**不是登录页**（产品上没有登录页），先修后端再刷新 |
| 匿名身份用着用着数据没了 | token 存在浏览器 localStorage，清了浏览器数据 / 换了设备就找不回那个 uid。**这是设计取舍，产品上没有找回入口**（见第一节第 5 步） |
| 「我的能量 / 笔记」换了台设备看不到 | 同上：身份只在本机浏览器里，换设备就是一个新身份 |
| 启动即崩，报 `Form data requires "python-multipart"` | 依赖没装全：`pip install -r requirements.txt` 里已有它，别单装 `fastapi`。它不是可选功能，缺了**应用起不来** |
| 怎么改代码都没反应 / 接口字段还是旧的 | `--reload-dir` 没写绝对路径而静默失效，或服务是改动之前启动的；直接重启 |
| 所有 AI 功能返回 503 `LLM_BACKEND_UNAVAILABLE` | `.env` 没配或 `LC_API_KEY` 无效；线上检查平台环境变量 |
| 模型有响应但内容是空的 | 协议判定没通过、静默走了另一边。两个条件要**同时**满足：`LC_BRIDGE` 去掉尾斜杠后以 `api.openai-next.com` 结尾、**且** `LC_API_KEY` 以 `sk-` 开头（见第一节第 3 步） |
| 传图片报错「模型服务暂时不可用」 | 容器缺 OpenCV 的系统库（Dockerfile 已装 `libgl1` + glib）；本地则看 `pip install -r requirements.txt` 是否完整 |
| `/api/chat` 报 session not found | 多实例部署，或用了云函数 —— 实例数必须固定为 1 |
| 重新部署后「我的账号不存在了」 | 没挂持久化卷（见第三节第 3 条） |
| 页面没样式、logo 裂图 | 前端资源走绝对路径 `/static/*`，必须由后端托管访问；直接双击打开 `index.html` 不行 |
| 跨学科发现很慢（约 70 秒） | 正常。它要跑多次大模型采样并生成学习报告 |
| 上传的 PDF 复盘时读不出内容 | 扫描件（没有文字层）读不了，页面会如实提示；有文字层的 PDF 正常 |

---

## 五、命令行离线演示（不依赖网络）

在包的**上一级**目录执行：

```bash
python -m logic_coloc.run_demo --pair demo1        # 演示对 demo1 / demo2 / demo3
python -m logic_coloc.run_demo --pair demo2 --png out.png
```

演示文本与演示对预置在 `demo_texts.py`，结果走本地缓存 `data/demo_cache.json`。
缓存只服务 CLI，**Web 端每次都实时调用大模型、没有离线兜底**。

---

## 六、目录结构

```
logic_coloc/            ← 仓库根 = Python 包本身
├── api/                HTTP 层：路由 / 服务 / 账号 / 路径推导 / 附件抽取 / OCR 单例
├── agents/             LangGraph 编排：状态机、工具、领域模型
├── rag/                确定性检索：语料库（人工标定）+ 加权打分
├── sessions/           纯内存会话
├── web/                单页前端（无框架）
├── tests/              全部离线
├── data/corpus/        RAG 语料库，必须随包分发
├── docs/               部署指南、产品文档
└── Dockerfile
```

更细的分层约定、设计取舍与「别顺手改回去」的坑，见 [`CLAUDE.md`](CLAUDE.md)。
