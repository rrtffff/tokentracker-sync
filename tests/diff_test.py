"""差分验证：Python 核心 vs 已验证的 JS 实现。

用同一份起始数据 + 同一份数据包，分别跑两个实现，比较结果语义是否完全一致。
只在临时目录里操作，不触碰真实数据。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

NODE = r"C:\Users\13296\AppData\Local\Programs\TokenTracker\EmbeddedServer\node.exe"
PY = r"C:\Users\13296\.workbuddy\binaries\python\envs\ttsync-312\Scripts\python.exe"
JS_TOOL = r"C:\Users\13296\WorkBuddy\2026-10-12-13-22-26\tokentracker-sync\tt-sync.js"
REAL_TRACKER = os.path.join(os.path.expanduser("~"), ".tokentracker", "tracker")
PROJ = os.path.dirname(os.path.abspath(__file__))

RESULTS = []


def check(name, ok, extra=""):
    RESULTS.append((name, ok, extra))
    print(("  ✓ " if ok else "  ✗ ") + name + (("   " + extra) if extra else ""))


def make_snapshot(path):
    """构造一份'来自另一台电脑'的数据包：100 行与本机同键（+1000），另加 1 个新键。"""
    src = os.path.join(REAL_TRACKER, "queue.jsonl")
    seen = {}
    with open(src, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or '"kind"' in line:
                continue
            r = json.loads(line)
            seen[(r.get("source"), r.get("model"), r.get("hour_start"))] = r
    picked = []
    for r in list(seen.values())[:100]:
        r = dict(r)
        r["total_tokens"] = (r.get("total_tokens") or 0) + 1000
        r["billable_total_tokens"] = (r.get("billable_total_tokens") or 0) + 1000
        picked.append(r)
    picked.append({
        "source": "fromOther", "model": "zz", "hour_start": "2026-10-10T01:00:00.000Z",
        "input_tokens": 111, "cached_input_tokens": 0, "cache_creation_input_tokens": 0,
        "output_tokens": 0, "reasoning_output_tokens": 0, "total_tokens": 111,
        "billable_total_tokens": 111, "conversation_count": 1,
    })
    snap = {
        "format": "tokentracker-tt-sync", "version": 1,
        "exportedAt": "2026-10-12T06:00:00.000Z",
        "machine": {"hostname": "OTHER-PC", "machineId": "C" * 64, "platform": "win32"},
        "_spec": {"title": "test"},
        "queues": {
            "main": {"keyFields": ["source", "model", "hour_start"], "rows": picked},
            "projects": {"keyFields": ["project_key", "source", "hour_start"], "rows": []},
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    return len(picked)


def prepare(sandbox):
    tracker = os.path.join(sandbox, ".tokentracker", "tracker")
    os.makedirs(tracker, exist_ok=True)
    shutil.copy2(os.path.join(REAL_TRACKER, "queue.jsonl"), os.path.join(tracker, "queue.jsonl"))
    shutil.copy2(os.path.join(REAL_TRACKER, "config.json"), os.path.join(tracker, "config.json"))
    st = os.path.join(REAL_TRACKER, "queue.state.json")
    if os.path.exists(st):
        shutil.copy2(st, os.path.join(tracker, "queue.state.json"))
    with open(os.path.join(tracker, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["machineId"] = "B" * 64
    with open(os.path.join(tracker, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return tracker


def env_for(sandbox):
    e = dict(os.environ)
    e["USERPROFILE"] = sandbox
    e["HOME"] = sandbox
    return e


def digest(tracker_path):
    """把 queue.jsonl 归一化成可比较的结构。"""
    path = os.path.join(tracker_path, "queue.jsonl")
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    ctrl = sorted(json.dumps(r, sort_keys=True) for r in rows if isinstance(r.get("kind"), str))
    synth = [r for r in rows if r.get("_ttsync")]
    dedup = {}
    for r in rows:
        if r.get("kind"):
            continue
        k = (r.get("source"), r.get("model"), r.get("hour_start"))
        dedup[k] = r
    return {"rows": rows, "ctrl": ctrl, "synth": synth, "dedup": dedup, "total": len(rows)}


def numeric_view(row):
    return {k: v for k, v in row.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


def main():
    tmp = tempfile.mkdtemp(prefix="tts-diff-")
    print("临时目录:", tmp)
    snap = os.path.join(tmp, "fromOther.json")
    n = make_snapshot(snap)
    print(f"数据包: {n} 行（其中 100 行与本机同键，各 +1000）\n")

    # ---------- JS 实现 ----------
    js_sb = os.path.join(tmp, "js")
    js_tracker = prepare(js_sb)
    r = subprocess.run([NODE, JS_TOOL, "import", snap, "--yes"], env=env_for(js_sb),
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    check("JS 导入执行成功", r.returncode == 0, (r.stderr or "").strip()[:120])
    js = digest(js_tracker)
    # 幂等
    subprocess.run([NODE, JS_TOOL, "import", snap, "--yes"], env=env_for(js_sb),
                   capture_output=True, text=True, encoding="utf-8", timeout=120)
    js2 = digest(js_tracker)

    # ---------- Python 实现 ----------
    py_sb = os.path.join(tmp, "py")
    py_tracker = prepare(py_sb)
    code = (
        "import sys,json;sys.path.insert(0, r'%s');"
        "from ttsync import core;"
        "s=core.read_snapshot(r'%s');"
        "p=core.plan_import(s);"
        "core.apply_import(p, r'%s');"
        "print('OK')" % (PROJ, snap, snap)
    )
    r = subprocess.run([PY, "-c", code], env=env_for(py_sb), capture_output=True, text=True,
                       encoding="utf-8", timeout=120)
    check("Python 导入执行成功", r.returncode == 0, (r.stderr or "").strip()[:200])
    py = digest(py_tracker)
    subprocess.run([PY, "-c", code], env=env_for(py_sb), capture_output=True, text=True,
                   encoding="utf-8", timeout=120)
    py2 = digest(py_tracker)

    print()
    # ---------- 比较 ----------
    check("控制记录逐字节一致", js["ctrl"] == py["ctrl"],
          f"JS={len(js['ctrl'])} PY={len(py['ctrl'])}")
    check("合成行数量一致", len(js["synth"]) == len(py["synth"]),
          f"JS={len(js['synth'])} PY={len(py['synth'])}")

    keys_js, keys_py = set(js["dedup"]), set(py["dedup"])
    check("去重后键集合一致", keys_js == keys_py,
          f"JS={len(keys_js)} PY={len(keys_py)}")

    diff = []
    for k in keys_js & keys_py:
        a, b = numeric_view(js["dedup"][k]), numeric_view(py["dedup"][k])
        if a != b:
            diff.append((k, a, b))
    check("每个桶的数值完全一致", not diff, (f"{len(diff)} 个不同: {diff[:2]}" if diff else ""))

    t_js = sum(r.get("total_tokens", 0) for r in js["dedup"].values())
    t_py = sum(r.get("total_tokens", 0) for r in py["dedup"].values())
    check("面板口径合计一致", t_js == t_py, f"JS={t_js:,} PY={t_py:,}")

    check("JS 自身幂等", js["total"] == js2["total"], f"{js['total']} → {js2['total']}")
    check("Python 自身幂等", py["total"] == py2["total"], f"{py['total']} → {py2['total']}")

    # 状态文件
    js_state = json.load(open(os.path.join(js_tracker, "tt-sync-state.json"), encoding="utf-8"))
    py_state = json.load(open(os.path.join(py_tracker, "tt-sync-state.json"), encoding="utf-8"))
    jk = set(js_state["imports"].keys())
    pk = set(py_state["imports"].keys())
    check("状态文件记录的来源机器一致", jk == pk, f"{jk} vs {pk}")
    jr = js_state["imports"][list(jk)[0]]["queues"]["main"]["rows"]
    pr = py_state["imports"][list(pk)[0]]["queues"]["main"]["rows"]
    check("状态文件记录的行数一致", len(jr) == len(pr), f"{len(jr)} vs {len(pr)}")

    ok = sum(1 for _, o, _ in RESULTS if o)
    print(f"\n===== 通过 {ok}/{len(RESULTS)} =====")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
