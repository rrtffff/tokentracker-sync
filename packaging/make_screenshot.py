"""生成 README 用的界面截图。

**关键点：全部使用合成数据。** 直接给真实界面截图会把维护者的主机名、
machineId 和真实用量数字一并发布到 README 里，这是隐私泄露。

做法：把 core 的路径切到临时目录、伪造机器身份、灌入合成队列并真的跑一次合并，
然后对窗口截图。所以截图里看到的是一个"确实合并过"的真实界面，但数字全是假的。

用法：
    python packaging/make_screenshot.py

输出的裁剪坐标是按 125% 显示缩放（逻辑 2048x1152）实测得到的。
换机器/换缩放后如果裁偏了，调 CROP_BORDER 里的几个偏移量即可。
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from ttsync import core, gui  # noqa: E402

OUT = os.path.join(ROOT, "docs", "screenshot.png")

# 窗口在屏幕上的位置（逻辑坐标）与裁剪微调
WIN_POS = "+30+20"
# （已改用角标自动定位，无需手动偏移；下面常量保留仅供兜底参考）
CROP_BORDER = {"left": 0, "top": 17, "right": 10, "bottom": 8}

# 伪造的机器身份 —— 不要用真实值
FAKE_HOST = "MY-LAPTOP"
FAKE_ID = "0123456789abcdef" * 4
OTHER_HOST = "DESKTOP-OLD"
OTHER_ID = "fedcba9876543210" * 4


MARKER = "#FF00FF"
MARKER_SIZE = 8


def add_corner_markers(root):
    """在窗口四角放纯色小方块。

    这样就不用猜显示缩放、也不用调 Win32 API 取窗口矩形了 ——
    直接在截屏里找这个颜色，包围盒就是窗口客户区的精确像素范围。
    """
    import tkinter as tk

    root.update()
    w = root.winfo_width()
    h = root.winfo_height()
    marks = []
    for x, y in ((0, 0), (w - MARKER_SIZE, 0),
                 (0, h - MARKER_SIZE), (w - MARKER_SIZE, h - MARKER_SIZE)):
        f = tk.Frame(root, bg=MARKER, highlightthickness=0, bd=0)
        f.place(x=x, y=y, width=MARKER_SIZE, height=MARKER_SIZE)
        marks.append(f)
    root.update()
    return marks


def find_window_box(img):
    """在截图里找四角标记的包围盒。"""
    w, h = img.size
    px = img.load()
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            if px[x, y][:3] == (255, 0, 255):
                xs.append(x)
                ys.append(y)
    if len(xs) < 20:
        return None
    box = (min(xs), min(ys), max(xs) + 1, max(ys) + 1)
    # 标记本身占了最外圈，向内收一点，把标记裁掉
    return (box[0] + MARKER_SIZE, box[1] + MARKER_SIZE,
            box[2] - MARKER_SIZE, box[3] - MARKER_SIZE)


def row(source, model, hour, tokens, conv=1):
    return {
        "source": source, "model": model, "hour_start": hour,
        "input_tokens": int(tokens * 0.72), "cached_input_tokens": int(tokens * 0.2),
        "cache_creation_input_tokens": 0, "output_tokens": int(tokens * 0.08),
        "reasoning_output_tokens": 0, "total_tokens": tokens,
        "billable_total_tokens": tokens, "conversation_count": conv,
    }


def build_own_rows():
    """合成"本机"的用量：3 个数据源 × 若干小时，数字固定便于复现。"""
    rows = []
    plan = [
        ("claude", "claude-sonnet-4.5", [412000, 388500, 521300, 297400, 634100], 3),
        ("codex", "gpt-5-codex", [180200, 245900, 152300, 209800], 2),
        ("qoder", "ultimate", [98100, 76300, 122400], 1),
    ]
    for source, model, toks, conv in plan:
        for i, t in enumerate(toks):
            rows.append(row(source, model, f"2026-10-0{i + 1}T0{(i + 9) % 9 + 1}:00:00.000Z", t, conv))
    return rows


def build_snapshot(path):
    """合成"另一台电脑"的数据包。"""
    rows = [
        row("claude", "claude-sonnet-4.5", "2026-10-01T10:00:00.000Z", 276500, 2),
        row("claude", "claude-sonnet-4.5", "2026-10-03T12:00:00.000Z", 331900, 2),
        row("codex", "gpt-5-codex", "2026-10-02T11:00:00.000Z", 149700, 1),
        row("cursor", "composer-1", "2026-10-04T09:00:00.000Z", 208400, 2),
        row("qoder", "ultimate", "2026-10-05T15:00:00.000Z", 87600, 1),
    ]
    import json

    snap = {
        "format": core.SNAPSHOT_FORMAT,
        "version": 1,
        "exportedAt": "2026-10-07T02:00:00.000Z",
        "machine": {"hostname": OTHER_HOST, "machineId": OTHER_ID, "platform": "win32"},
        "_spec": core.build_merge_spec(),
        "queues": {
            "main": {"keyFields": core.TARGETS["main"]["key_fields"], "rows": rows},
            "projects": {"keyFields": core.TARGETS["projects"]["key_fields"], "rows": []},
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    return path


def seed_backups():
    """造几个备份记录，让「备份」列表有内容可看。"""
    import json

    os.makedirs(core.BACKUP_DIR, exist_ok=True)
    items = [
        ("2026-10-07T09:12:00.000+08:00", "core", "导入 DESKTOP-OLD 之前"),
        ("2026-10-03T21:40:00.000+08:00", "core", "每周例行备份"),
        ("2026-09-28T10:05:00.000+08:00", "full", "换硬盘前完整备份"),
    ]
    for i, (ts, scope, note) in enumerate(items):
        p = os.path.join(core.BACKUP_DIR, f"tt-backup-{FAKE_HOST}-2026100{i + 1}-000000.json")
        pad = "x" * (60000 - i * 18000)      # 让文件有像样的体积，列表里的"大小"才不显示 0 KB
        with open(p, "w", encoding="utf-8") as f:
            json.dump(
                {"format": core.BACKUP_FORMAT, "version": 1, "createdAt": ts, "note": note,
                 "scope": scope, "machine": {"hostname": FAKE_HOST, "machineId": FAKE_ID},
                 "trackerDir": core.TRACKER_DIR, "padding": pad, "files": []},
                f, ensure_ascii=False,
            )
        os.utime(p, (time.time() + i, time.time() + i))


def main():
    tmp = tempfile.mkdtemp(prefix="ttsync-shot-")
    try:
        # ---- 切换到沙盒 ----
        core.use_home(tmp)
        os.makedirs(core.TRACKER_DIR, exist_ok=True)
        import json

        with open(core.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"machineId": FAKE_ID}, f)
        with open(core.TARGETS["main"]["file"], "w", encoding="utf-8", newline="\n") as f:
            for r in build_own_rows():
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(core.TARGETS["main"]["state_file"], "w", encoding="utf-8") as f:
            json.dump({"offset": 0, "updatedAt": "2026-10-07T00:00:00Z"}, f)

        # ---- 真的跑一次合并，让界面显示"已合并"的状态 ----
        snap = build_snapshot(os.path.join(tmp, "other.json"))
        core.apply_import(core.plan_import(core.read_snapshot(snap)), snap)
        seed_backups()

        # ---- 伪造身份，防止真实主机名出现在截图里 ----
        core.own_machine = lambda: {"hostname": FAKE_HOST, "machineId": FAKE_ID, "platform": "win32"}
        core.cloud_sync_enabled = lambda: False      # 显示成"已关闭"，也避免警告文字

        # ---- 渲染并截图 ----
        import tkinter as tk
        from PIL import ImageGrab

        root = gui.TkinterDnD.Tk() if gui.HAS_DND else tk.Tk()
        root.geometry(f"{gui.WIN_W}x{gui.WIN_H}{WIN_POS}")
        gui.App(root)
        root.lift()
        root.attributes("-topmost", True)
        root.update()
        time.sleep(2.0)
        root.update()

        marks = add_corner_markers(root)
        shot = ImageGrab.grab()
        for m in marks:
            m.destroy()
        root.update()

        box = find_window_box(shot)
        if box is None:
            # 兜底：按几何位置加固定偏移裁剪（适合 100% 缩放）
            x0 = int(WIN_POS.split("+")[1])
            y0 = int(WIN_POS.split("+")[2])
            box = (x0, y0, x0 + gui.WIN_W, y0 + gui.WIN_H)
            print("警告：未找到角标，使用兜底裁剪，请检查成图是否完整。")

        shot.crop(box).save(OUT)
        root.destroy()

        print(f"裁剪矩形 {box}  成图尺寸 {box[2] - box[0]}x{box[3] - box[1]}")
        print(f"已写出 {OUT}")
        print("注意：截图为合成数据，请勿改成读取真实数据。")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
