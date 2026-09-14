# 演示视频

`logic_coloc_demo.mp4` —— 线上站点的完整走查，2 分 13 秒，860×1864（2× DPR），30fps。

走的是这一条主线：

> 首页 → **读懂它**（免疫负反馈，LLM）→ 存为卡片 → **跨学科理解**（排队系统，LLM）
> → 知乎搜索 → **看山**桌宠六个动作 → **复习**（掌握 +10 能量）→ 回求知首页

两处 LLM 等待（读懂它、跨学科理解）在成片里是 **8 倍速**的，其余原速。

---

## 文件

| 文件 | 说明 |
|---|---|
| `logic_coloc_demo.mp4` | 成片 |
| `poster.jpg` | 封面帧（成片 1.5s 处） |
| `record_demo.py` | 录屏脚本：跑一遍全流程，整段录成 webm，同时在 `steps.jsonl` 打时间戳 |
| `edit_demo.py` | 剪辑脚本：按时间戳切段、给等待段加速、拼成 mp4 |
| `steps.jsonl` | 本次录制的时间戳（每段的起点时刻 + 是否加速） |
| `raw.video.path` | 指向原始 webm；**原始文件不随仓库分发**，见下方「重新剪辑」 |

---

## 重新剪辑（不用重新录屏）

原始 webm 没有进仓库（15MB 的二进制放 git 里会一直跟着 clone 走）。
在 [Releases](../../releases) 里下载 `raw-demo-recording.webm`，放到本目录，然后：

```bash
pip install imageio-ffmpeg      # 或者系统里装了 ffmpeg 也行
python3 edit_demo.py raw-demo-recording.webm steps.jsonl
```

出来的还是 `logic_coloc_demo.mp4`，中间产物在 `segs/`。

常改的几个地方都在 `edit_demo.py` 顶部：

| 常量 | 作用 |
|---|---|
| `ACCEL` | 等待段加速倍率，默认 `8.0`。想让人看清模型在转就调小到 `4` |
| `W, H` | 输出尺寸，默认 `860×1864`（430×932 的 2 倍，竖屏） |
| `crf`（在命令里） | 画质，默认 `19`，越小越清楚、文件越大 |

想**改裁剪点**（比如掐掉某个动作），改 `steps.jsonl`：每段的时长就是“这一条与下一条的时间差”，
删掉某条即可把它的时长并进前一段。`accel` 为 `true` 的段会被加速。

---

## 重新录屏

```bash
pip install playwright && playwright install chromium
LC_DEMO_BASE=https://你的域名 python3 record_demo.py
```

- 默认录的是线上环境，`LC_DEMO_BASE` 可以换成任意部署地址。
- 脚本会在 `steps.jsonl` 写下新的时间戳，原始 webm 路径写进 `raw.video.path`，
  再跑一次 `edit_demo.py` 即可。
- 流程里点的是**真实按钮**，没有 mock；两处 LLM 调用是真调后端，所以单次录制
  约 5 分钟（其中 ~2.5 分钟在等模型）。
- 页面上那个橙色的圆点光标是脚本注入的（`CURSOR_JS`）—— 无头浏览器没有系统光标，
  不画一个就看不出来“手”在哪。不想要就删掉 `ctx.add_init_script(CURSOR_JS)` 那行。

### 两个已知的坑

1. **CloudBase 测试域名有人工确认页**。首次访问会先落到「风险提醒」页，
   点「确定访问」设下 cookie 才进真应用。脚本已经处理了（见 `main()` 里 S0 那段）。
2. **线上实例重启会丢掉匿名账号**。后端把匿名身份存在内存里，实例一重启，
   前端就弹「连接已中断，请刷新页面重试」，之后所有点击都点不动。脚本里
   `assert_alive()` 会在每条打点前探测这个状态，发现就整条作废重录 ——
   否则会剪出一条“每一下都点了但页面毫无反应”的废片。
