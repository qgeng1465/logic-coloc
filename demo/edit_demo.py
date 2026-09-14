#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 record_demo.py 打的时间戳剪辑原始录屏：
- accel=True 的段 8 倍速（LLM 等待）
- 其余原速
- 统一 30fps、860x1864(2x lanczos)+轻锐化、libx264，尾部 concat 成一条 mp4

想改语速、换加速倍率或挪裁剪点，改下面几个常量再从原始 webm 重跑即可，不用重新录屏：
    python3 edit_demo.py <原始 webm> [时间戳 jsonl]
不传参数则读同目录的 raw.video.path 和 steps.jsonl。
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE
ACCEL = 8.0            # 等待段加速倍率；改成 4 就是 4 倍速
W, H = 860, 1864

RAW = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    (HERE / "raw.video.path").read_text(encoding="utf-8").strip())
STEPS = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "steps.jsonl"
SEGS_DIR = OUT / "segs"
SEGS_DIR.mkdir(exist_ok=True)
FINAL = OUT / "logic_coloc_demo.mp4"


def find_ffmpeg() -> str:
    """PATH 里有就用 PATH 里的；没有就退回 imageio-ffmpeg 自带的那个二进制。"""
    found = shutil.which("ffmpeg") or os.environ.get("FFMPEG_BINARY")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise SystemExit("找不到 ffmpeg：apt install ffmpeg，或 pip install imageio-ffmpeg")


FF = find_ffmpeg()

marks = json.loads(STEPS.read_text(encoding="utf-8"))
t0 = marks[0]["t"]  # v0_start ≈ 视频起点（page 创建后立刻打点）


def run(cmd: list) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1800:], file=sys.stderr)
        raise SystemExit(f"ffmpeg failed: {' '.join(cmd[:6])}…")


segments = []
for i in range(len(marks) - 1):
    a, b = marks[i], marks[i + 1]
    start, end = a["t"] - t0, b["t"] - t0
    dur = end - start
    if dur < 0.2:
        continue
    segments.append({"name": a["name"], "start": max(start, 0.0), "dur": dur,
                     "accel": bool(a["accel"]), "idx": len(segments)})

total_wall = sum(s["dur"] for s in segments)
total_final = sum(s["dur"] / (ACCEL if s["accel"] else 1.0) for s in segments)
print(f"segments={len(segments)} wall={total_wall:.1f}s final≈{total_final:.1f}s")

concat_list = SEGS_DIR / "list.txt"
with concat_list.open("w", encoding="utf-8") as fh:
    for s in segments:
        out = SEGS_DIR / f"seg_{s['idx']:02d}_{s['name']}.mp4"
        speed = ACCEL if s["accel"] else 1.0
        vf = [f"fps=30", f"scale={W}:{H}:flags=lanczos", "unsharp=5:5:0.35:5:5:0.0", "format=yuv420p"]
        if speed != 1.0:
            vf.append(f"setpts=PTS/{speed:g}")
        if s["idx"] == 0:
            vf.append("fade=t=in:st=0:d=0.5")
        if s["idx"] == len(segments) - 1:
            vf.append(f"fade=t=out:st={max(s['dur'] / speed - 0.7, 0):.2f}:d=0.7")
        cmd = [FF, "-y", "-ss", f"{s['start']:.3f}", "-t", f"{s['dur']:.3f}", "-i", str(RAW),
               "-vf", ",".join(vf), "-r", "30", "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "19", "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", str(out)]
        print(f"  seg{s['idx']:02d} {s['name']:<14} {s['dur']:6.1f}s -> {s['dur']/speed:5.1f}s"
              f"{'  x8' if speed != 1 else ''}")
        run(cmd)
        fh.write(f"file '{out}'\n")

run([FF, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy",
     "-movflags", "+faststart", str(FINAL)])
print(f"final: {FINAL} ({FINAL.stat().st_size/1e6:.1f} MB)")

# 海报帧：取成片 1.5s 处（首页首屏）
run([FF, "-y", "-ss", "1.5", "-i", str(FINAL), "-frames:v", "1", "-q:v", "2",
     str(OUT / "poster.jpg")])
print(f"poster: {OUT/'poster.jpg'}")
