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

> **不填会怎样？** 服务照样能启动、能注册登录、能看界面，但所有 AI 功能
> （提问解释、跨学科发现、笔记复盘）都会返回
> `{"code":"LLM_BACKEND_UNAVAILABLE"}` + HTTP 503 —— 因为每次都要实时调用大模型。
> 这是体面的降级，不是崩溃。
>
> `.env` 已被 `.gitignore` 排除，**永远不要提交它**；线上由平台环境变量注入。

`LC_BRIDGE` 那几行**不要改**，特别是不要加 `/v1` 或尾斜杠 —— 代码靠域名后缀判断
该说 OpenAI 方言还是 Anthropic 方言，网址写法一变就会**静默**走错协议，
表现为「模型返回空内容」且报错完全指不到原因。

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

### 5. 直接用 —— 不需要注册

打开 **http://127.0.0.1:8000/** 就能用，**不会让你登录**。第一次访问时前端会静默向
`POST /api/auth/guest` 要一个**匿名身份**（服务端给你分配一个 uid），笔记、卡片、能量
都照常能用，各人数据互不可见。

想让数据跟着自己走（换台设备 / 清浏览器数据都还在），在**设置 → 保存我的知识（绑定账号）**
里补一个用户名密码即可：**uid 不变，所以已经攒下的东西一个都不会丢**。用户名 2–20 位、
密码 6–128 位；**不收集邮箱、不发验证码**（没有邮件服务，加了也只是个摆设）。

账号和数据都只存在运行目录的 `data/` 与 `uploads/` 里，**按账号分目录隔离**。

> 匿名身份也理解成一种账号：它和正式账号同一张表、同一种 token、同一套隔离，只差
> 「没有密码」。所以只有在你**主动**去绑定时才需要填东西；在那之前没有任何一道门。
> 公开部署时匿名记录由 `LC_MAX_GUESTS`（默认 2000）封顶，超限淘汰最早的那条。

---

## 二、跑测试

```bash
cd ..                                        # 同上，必须在包的上一级
python -m pytest logic_coloc/tests/ -q
```

全部离线：不碰网络、不打真实大模型，也不需要 `.env`。
`tests/test_card_store.py` 里有 **2 个既存失败**（测试按「天」断言间隔，
而实现早已按「分钟」计算）—— 是测试过时，不是功能有问题。

---

## 三、部署上线

完整参数清单、验证步骤与排错对照表见 **[`docs/部署指南-CloudBase.md`](docs/部署指南-CloudBase.md)**。
交付形态是**云托管（容器）**，不是静态站也不是云函数（会话在进程内存、单个请求要跑 70 秒、
还要写本地文件，三条都排除云函数）。Dockerfile 已就绪，`docker build -t logic-coloc .` 即可。

上线前请务必确认这三条（**漏了任何一条，队友/评委看到的都会是「坏的」**）：

| | 事项 | 不做的后果 |
|---|---|---|
| 1 | 在平台「环境变量」里配 `LC_API_KEY`（真实密钥，走私密渠道给，别走 git）<br>再配一把自己生成的 `LC_SECRET_KEY` | 所有 AI 功能 503；不配 `LC_SECRET_KEY` 则每次重新部署都把人踢回登录页 |
| 2 | **实例数固定为 1**（最小实例数不要设 0） | 会话存在内存里，多实例会让 `/api/chat` 随机 404 |
| 3 | **挂持久化卷 / CFS** 到 `/app/logic_coloc/data` 与 `/app/logic_coloc/uploads` | 容器重启或重新部署会清空**账号本身**与全部笔记、卡片、附件。本地不会遇到这个问题（磁盘一直在），所以特别容易忽略 |

---

## 四、出问题了先看这里

| 症状 | 多半是 |
|---|---|
| `ModuleNotFoundError: No module named 'logic_coloc'` | 目录名不是 `logic_coloc`（见第一节第 1 步） |
| 打开就弹登录页（本来应该免登录直接用） | 前端建匿名身份失败了，多半是**断网或后端没起来**，它会退回登录页并提示「无法连接服务器」；先修后端，别以为是登录逻辑坏了 |
| 匿名身份用着用着数据没了 | token 存在浏览器 localStorage，清了浏览器数据 / 换了设备就找不回那个 uid。**这正是「保存我的知识」要解决的问题** —— 绑定一次就不再怕 |
| 「我的能量 / 笔记」换了台设备看不到 | 同上：还是匿名身份。去设置里绑定账号，再用它登录 |
| 启动即崩，报 `Form data requires "python-multipart"` | 依赖没装全：`pip install -r requirements.txt` 里已有它，别单装 `fastapi`。它不是可选功能，缺了**应用起不来** |
| 怎么改代码都没反应 / 接口字段还是旧的 | `--reload-dir` 没写绝对路径而静默失效，或服务是改动之前启动的；直接重启 |
| 所有 AI 功能返回 503 `LLM_BACKEND_UNAVAILABLE` | `.env` 没配或 `LC_API_KEY` 无效；线上检查平台环境变量 |
| 模型有响应但内容是空的 | `LC_BRIDGE` 写法被改了（加了 `/v1`、尾斜杠或换了域名），静默走错协议 |
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
