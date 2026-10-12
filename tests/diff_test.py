"""tt-sync 测试：合并语义、幂等性、导出隔离、安全护栏。

设计目标：**不依赖任何本机真实数据，克隆仓库即可运行。**
所有样本都在脚本内合成，结果是确定的，可以直接手工验算。

用法：
    python tests/diff_test.py

可选：如果同时拥有最初的 JavaScript 原型，可以跑一次「对拍测试」，
逐桶比对两个实现的结果是否一致：
    set TT_SYNC_JS_TOOL=D:\\path\\to\\tt-sync.js
    set TT_SYNC_NODE=D:\\path\\to\\node.exe
    python tests/diff_test.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ttsync import core  # noqa: E402

JS_TOOL = os.environ.get("TT_SYNC_JS_TOOL", "")
NODE = os.environ.get("TT_SYNC_NODE", "node")

RESULTS = []


def check(name, ok, extra=""):
    RESULTS.append((name, bool(ok), extra))
    print(("  \u2713 " if ok else "  \u2717 ") + name + (("   " + extra) if extra else ""))


def row(source, model, hour, tokens, conv=1, **kw):
    r = {
        "source": source, "model": model, "hour_start": hour,
        "input_tokens": tokens, "cached_input_tokens": 0,
        "cache_creation_input_tokens": 0, "output_tokens": 0,
        "reasoning_output_tokens": 0, "total_tokens": tokens,
        "billable_total_tokens": tokens, "conversation_count": conv,
    }
    r.update(kw)
    return r


# 本机（B）的队列：claude 那条写了两次，模拟「同一小时的累计更新」
OWN_QUEUE = [
    row("claude", "m", "2026-10-01T10:00:00.000Z", 100),
    row("claude", "m", "2026-10-01T10:00:00.000Z", 120),   # 累计更新 → 应取 120 而不是 220
    row("codex", "x", "2026-10-01T11:00:00.000Z", 50),
    {"kind": "account_session_state", "source": "claude", "session_id": "sess-1",
     "total_tokens": 500, "bucket_start": "2026-10-01T10:00:00.000Z"},  # 控制记录须原样保留
    {"source": "broken", "model": "no-hour"},               # 缺 hour_start → 原样保留
]

# 另一台机器（A）的数据包：claude 与 B 同键（→相加），qoder 是新键
SNAP_ROWS = [
    row("claude", "m", "2026-10-01T10:00:00.000Z", 300, conv=2),
    row("qoder", "q", "2026-10-01T09:00:00.000Z", 70),
]

# 手工验算：claude = 120 + 300 = 420；codex = 50；qoder = 70
EXPECT_TOTAL = 420 + 50 + 70
EXPECT_KEYS = 3


def make_sandbox(home: str, own_queue=None, machine_id="B" * 64):
    """在指定 home 下造一份 TokenTracker 数据目录。"""
    tracker = os.path.join(home, ".tokentracker", "tracker")
    os.makedirs(tracker, exist_ok=True)
    with open(os.path.join(tracker, "queue.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for r in (own_queue if own_queue is not None else OWN_QUEUE):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(tracker, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"machineId": machine_id}, f)
    with open(os.path.join(tracker, "queue.state.json"), "w", encoding="utf-8") as f:
        json.dump({"offset": 12345, "updatedAt": "2026-10-01T00:00:00Z"}, f)
    return tracker


def write_snapshot(path, rows=None, machine_id="A" * 64, hostname="OTHER-PC"):
    snap = {
        "format": core.SNAPSHOT_FORMAT,
        "version": 1,
        "exportedAt": "2026-10-12T06:00:00.000Z",
        "machine": {"hostname": hostname, "machineId": machine_id, "platform": "win32"},
        "_spec": core.build_merge_spec(),
        "queues": {
            "main": {"keyFields": core.TARGETS["main"]["key_fields"],
                     "rows": rows if rows is not None else SNAP_ROWS},
            "projects": {"keyFields": core.TARGETS["projects"]["key_fields"], "rows": []},
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    return path


def panel_view(tracker):
    """按面板口径还原：同键取最后一条，再求和。

    与 TokenTracker 一致：只统计带 hour_start 的行（缺 hour_start 的行虽然
    留在文件里，但不会进入按天汇总，因此不计入面板桶数与合计）。
    """
    rows, _, _ = core.read_jsonl(os.path.join(tracker, "queue.jsonl"))
    dedup = {}
    for r in rows:
        if isinstance(r.get("kind"), str):
            continue
        if not r.get("hour_start"):
            continue
        dedup[(r.get("source"), r.get("model"), r.get("hour_start"))] = r
    return len(dedup), sum(r.get("total_tokens") or 0 for r in dedup.values()), rows


def main():
    tmp = tempfile.mkdtemp(prefix="ttsync-test-")
    print(f"临时目录：{tmp}\n")

    # ---------------- 1. 基线：机内 last-wins ----------------
    print("== 1. 合成样本基线 ==")
    home_b = os.path.join(tmp, "B")
    tracker_b = make_sandbox(home_b)
    core.use_home(home_b)
    k0, t0, rows0 = panel_view(tracker_b)
    check("同一台机器内同键取最后一条（claude 得 120 而非 220）",
          t0 == 120 + 50, f"合计 {t0:,}")

    # ---------------- 2. 导入合并 ----------------
    print("\n== 2. 跨机器合并 ==")
    snap = write_snapshot(os.path.join(tmp, "from-other.json"))
    res, _ = core.apply_import(core.plan_import(core.read_snapshot(snap)), snap)
    k1, t1, rows1 = panel_view(tracker_b)
    check("跨机器同键相加（120 + 300 = 420）", t1 == EXPECT_TOTAL,
          f"合计 {t1:,}（期望 {EXPECT_TOTAL:,}）")
    check("新键并入（qoder 的 70）", any(r.get("source") == "qoder" for r in rows1))
    check("键数正确", k1 == EXPECT_KEYS, f"{k1}")

    ctrl0 = sorted(json.dumps(r, sort_keys=True) for r in rows0 if isinstance(r.get("kind"), str))
    ctrl1 = sorted(json.dumps(r, sort_keys=True) for r in rows1 if isinstance(r.get("kind"), str))
    check("控制记录逐字节保留", ctrl1 == ctrl0, f"{len(ctrl1)} 条")
    check("缺 hour_start 的行原样保留", any(r.get("source") == "broken" for r in rows1))
    check("合成行带 _ttsync 标记", sum(1 for r in rows1 if r.get("_ttsync")) == 2)
    check("导入前自动备份已生成",
          any(".bak-ttsync-" in f for f in os.listdir(tracker_b)))
    st = json.load(open(os.path.join(tracker_b, "queue.state.json"), encoding="utf-8"))
    check("云上传字节偏移已重置为 0", st.get("offset") == 0, str(st.get("offset")))

    # ---------------- 3. 幂等 ----------------
    print("\n== 3. 幂等性（重复导入应完全不变）==")
    core.apply_import(core.plan_import(core.read_snapshot(snap)), snap)
    core.apply_import(core.plan_import(core.read_snapshot(snap)), snap)
    k2, t2, rows2 = panel_view(tracker_b)
    check("重复导入 3 次后合计不变", t2 == t1, f"{t1:,} → {t2:,}")
    check("重复导入 3 次后行数不变", len(rows2) == len(rows1), f"{len(rows1)} → {len(rows2)}")

    # ---------------- 4. 导出隔离 ----------------
    print("\n== 4. 导出隔离（不能把对方数据带出去）==")
    out = os.path.join(tmp, "usb")
    exp = core.export_data(out, "json")
    snap_out = json.load(open(exp["files"][0], encoding="utf-8"))
    exported = snap_out["queues"]["main"]["rows"]
    check("导出不含 _ttsync 标记行", not any("_ttsync" in r for r in exported))
    check("导出不含对方（qoder）的数据", not any(r.get("source") == "qoder" for r in exported))
    check("导出的是本机自身量",
          snap_out["queues"]["main"]["totals"]["tokens"] == 120 + 50,
          f"{snap_out['queues']['main']['totals']['tokens']:,}")
    check("导出包含 _spec 合并说明书", bool(snap_out.get("_spec")))

    # ---------------- 5. 安全护栏 ----------------
    print("\n== 5. 安全护栏 ==")
    selfsnap = os.path.join(tmp, "self.json")
    shutil.copy(exp["files"][0], selfsnap)
    s = json.load(open(selfsnap, encoding="utf-8"))
    s["machine"]["machineId"] = "B" * 64      # 伪装成本机自己导出的
    json.dump(s, open(selfsnap, "w", encoding="utf-8"))
    try:
        core.plan_import(core.read_snapshot(selfsnap))
        check("拒绝导入本机自己导出的包", False, "未拦截")
    except core.SyncError as e:
        check("拒绝导入本机自己导出的包", "自己导出" in str(e))

    bad = os.path.join(tmp, "bad.json")
    json.dump({"format": "something-else"}, open(bad, "w", encoding="utf-8"))
    try:
        core.read_snapshot(bad)
        check("拒绝非本工具的数据包", False, "未拦截")
    except core.SyncError:
        check("拒绝非本工具的数据包", True)

    try:
        core.read_snapshot(os.path.join(tmp, "does-not-exist.json"))
        check("不存在的文件应报错", False, "未报错")
    except core.SyncError:
        check("不存在的文件应报错", True)

    # ---------------- 6. 备份 / 还原 ----------------
    print("\n== 6. 完整备份与整体还原 ==")
    before = open(os.path.join(tracker_b, "queue.jsonl"), "rb").read()
    b = core.backup(note="test")
    check("备份生成单个文件", os.path.exists(b["file"]))
    with open(os.path.join(tracker_b, "queue.jsonl"), "w", encoding="utf-8") as f:
        f.write("CORRUPTED")
    r = core.restore_backup(0)
    after = open(os.path.join(tracker_b, "queue.jsonl"), "rb").read()
    check("还原为字节级一致", after == before, f"{len(after):,} 字节")
    check("还原前已把当前数据另存", os.path.isdir(r["safety_dir"]))

    # ---------------- 7. 可选对拍 ----------------
    if JS_TOOL and os.path.exists(JS_TOOL):
        print("\n== 7. 与 JavaScript 原型对拍 ==")
        differential(tmp, snap)
    else:
        print("\n== 7. 与 JavaScript 原型对拍（跳过）==")
        print("  未设置 TT_SYNC_JS_TOOL。这只影响追溯性验证，不影响本工具的正确性；")
        print("  上面 1~6 项已覆盖全部核心语义。")

    ok = sum(1 for _, o, _ in RESULTS if o)
    print(f"\n===== 通过 {ok}/{len(RESULTS)} =====")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok == len(RESULTS) else 1


def differential(tmp, snap_path):
    """同一份数据分别喂给 JS 原型和本实现，比较结果是否一致。"""
    js_home = os.path.join(tmp, "JS")
    js_tracker = make_sandbox(js_home)
    env = dict(os.environ, USERPROFILE=js_home, HOME=js_home)
    r = subprocess.run([NODE, JS_TOOL, "import", snap_path, "--yes"],
                       env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
    check("JS 原型执行成功", r.returncode == 0, (r.stderr or "").strip()[:100])

    py_home = os.path.join(tmp, "PY")
    py_tracker = make_sandbox(py_home)
    core.use_home(py_home)
    core.apply_import(core.plan_import(core.read_snapshot(snap_path)), snap_path)
    core.use_home(os.path.join(tmp, "B"))     # 还原，避免影响后续

    k_js, t_js, _ = panel_view(js_tracker)
    k_py, t_py, _ = panel_view(py_tracker)
    check("两实现面板合计一致", t_js == t_py, f"JS={t_js:,} PY={t_py:,}")
    check("两实现去重后桶数一致", k_js == k_py, f"JS={k_js} PY={k_py}")


if __name__ == "__main__":
    sys.exit(main())
