# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

Logic-Coloc「跨学科知识同源翻译机」：判断两段不同领域的文本是否具有相同的**底层逻辑结构**（"同源"），同源度超过阈值时再为两侧的领域术语建立跨域一一映射。整个代码库注释与输出为中文。

## 目录性质与运行方式

**本目录就是 `logic_coloc` 包本身**（模块间用相对导入 `from . import …`），不是独立项目根。设计上它作为子包放在某个项目根下，运行输出（`data/demo_cache.json`、`assets/*.png`）写到**包目录的两级父目录**，即 `E:\data`、`E:\assets`——代码里 `ROOT` 就是这个父目录。修改缓存/输出路径前先确认这点。

包入口只能以 `python -m logic_coloc.<模块>` 执行，且必须把父目录 `E:\` 放到 sys.path（在 `E:\` 下运行，或设 `PYTHONPATH=E:\`）。直接在 `E:\logic_coloc` 内 `python -m` 或直接运行单个 `.py` 文件都会因找不到包/相对导入而失败。

```bash
cd /e

# 命令行演示（走离线缓存，秒开；可用 demo1/demo2/demo3）
python -m logic_coloc.run_demo --pair demo1
python -m logic_coloc.run_demo --a "文本A" --b "文本B"     # 自定义文本，需在线 LLM
python -m logic_coloc.run_demo --pair demo2 --png out.png --method wasserstein

# 预计算：把全部演示数据写入 data/demo_cache.json + 生成雷达图 PNG（首次需 LLM 在线，约 1 分钟）
python -m logic_coloc.precompute

# 生成全部演示物料图（映射图/热图/度量对比/海报，纯本地渲染、可重复跑）
python -m logic_coloc.make_figures
```

依赖（无 requirements.txt，从 import 推断）：`requests`、`scipy`、`numpy`、`matplotlib`，Python ≥3.10。

## 处理管线（三阶段，分属三个模块）

整个系统的核心思想：**用 LLM 把文本压成低维"逻辑画像"向量，之后的一切判定都在本地确定性完成，尽量切断对 LLM 的依赖**。

1. **Step 1 特征提取 — `feature_extractor.py`**
   把 LLM 当作"高维逻辑特征提取器"而非文本生成器：给定一段文本，强制它输出严格 JSON——在 5 个逻辑维度上各打 0–100 分 + Top3 术语。按 `config.SAMPLES`（默认 3）次采样后**逐维取均值**去噪（LLM 打分有 ±10~20 抖动，均值化后同文本两次采样余弦相似度 ≈0.99）。产物是 5 维浮点向量 `vec` + Top3 术语。

2. **Step 2 同源度计算 — `homonomy.py`（本地、确定性、无 LLM）**
   主评分 `homonomy_cosine` = 5 维**对齐**余弦相似度（已验证语义排序正确）；可选副视角 `homonomy_wasserstein` = 一维 EMD（把向量当 5 个无序样本，丢弃维度身份，只比分布形状）。⚠️ W1 语义区分弱、实测会误判（把异源对排到最高），仅作可视化对比用途，不要改默认方法。用 `config.METHOD` / `LC_METHOD` 切换。

3. **Step 3 跨域映射 — `mapper.py`**
   仅当同源度 ≥ `config.THRESHOLD`（默认 0.85）才调用：再次用 LLM 让两侧 Top3 术语建立跨域对等映射，输出 `{"A_terms":…, "B_terms":…, "mapping":{a1:b1,…}}`。

## 5 个逻辑维度

`feature_extractor.DIMS`（键 → 中文释义），中文短标签在 `DIMS_CN`（图表用，键一一对应）：

- `system_closure` 系统封闭性（自包含、闭环运行）
- `causal_chain_length` 因果链长度
- `negative_feedback_strength` 负反馈强度（自我抑制/纠错/防失控）
- `randomness_entropy` 随机性/熵值
- `zero_sum_resource_level` 资源零和性（竞争/守恒/此消彼长）

改维度定义时必须**同时更新 `DIMS`、`DIMS_CN`、`feature_extractor.EXTRACT_SYS` 的提示词示例 JSON、`make_figures.py` 中硬编码的行色列表**，且旧缓存（内容哈希）会失效需重新 `precompute`。

## LLM 接入与配置

- 端点：Anthropic 兼容 `/v1/messages`。默认指向本机 `claude-openai-bridge`（`http://127.0.0.1:8388`），可改 `LC_BRIDGE` 直连 DeepSeek 等。
- 所有默认参数集中在 `config.py`，**全部可用 `LC_*` 环境变量覆盖**：`LC_BRIDGE`、`LC_MODEL`、`LC_TIMEOUT`、`LC_THINKING`、`LC_SAMPLES`、`LC_METHOD`、`LC_THRESHOLD`、`LC_GAMMA`、`LC_CJK_FONT`。
- 统一 LLM 调用入口 `feature_extractor.llm()`（取 `type=="text"` 内容块、跳过 thinking、自动重试）。已知坑：DeepSeek 兼容端点上 thinking 传 `"disabled"` + 低 `max_tokens` 会**偶发空响应**；默认 `LC_THINKING=none`（= 不传该字段）+ `max_tokens≥2048` 最稳，不要改回传 disabled。

## 缓存 / 离线演示机制

`cache.DemoCache` 读写 `data/demo_cache.json`：
- 文本画像按**文本内容 SHA-1 前 16 位**作键存 `texts`，演示对按 key 存 `pairs`。改提示词/维度后旧缓存不失效（键只看文本），注意配合 `--force` 或删缓存。
- 设计目标：`precompute` 跑过后，现场演示即使 LLM 端点不可达也**全程离线秒开**。`run_demo` 命中缓存即跳过 LLM。

## 其它注意点

- 演示文本库 `demo_texts.py`：5 段文本 + 3 个演示对（demo1 免疫×服务器同源、demo2 量子×期权同源、demo3 免疫×量子异源对照），全部经真实 LLM 端到端验证过分数语义。新增演示文本要保证 LLM 打分语义稳定。
- `plots.py` 的 matplotlib 中文显示靠逐个尝试注册 CJK 字体候选（找不到就退化英文标签）；Windows 上可用 `LC_CJK_FONT` 指向中文字体（如微软雅黑/思源黑体 ttc）避免标题出方框。emoji 会被 `_no_emoji` 剥掉（matplotlib 缺字形）。
- 演示/海报里的分数来自缓存结果，改动评分逻辑后记得重新 `precompute` + `make_figures`，否则物料图分数是旧的。
