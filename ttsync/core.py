"""
tt-sync core —— TokenTracker 跨机器用量合并 / 备份 / 还原

本模块只依赖标准库，可被 GUI 与 CLI 共用。

核心语义（与 TokenTracker 自身的读取逻辑严格对齐）：
    TokenTracker 读队列时按 `source | model | hour_start` 去重，同键只保留
    文件中最后一条，绝不自动相加。因此：
      · 同一台机器内部同键重复 = 该小时的累计更新 → 取最后一条（last-wins）
      · 两台机器同键         = 各自独立的用量     → 必须相加（sum）
    混淆这两者会造成「丢数据」或「重复计数」。

幂等性：
    合成行带 `_ttsync` 标记。每次运行先剔除全部合成行，再从「本机原始行 +
    各来源机器快照的绝对总量」重新计算，所以重复执行结果完全一致。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import socket
import sys
from datetime import datetime

# --------------------------------------------------------------------- 常量

SNAPSHOT_FORMAT = "tokentracker-tt-sync"
SNAPSHOT_VERSION = 1
BACKUP_FORMAT = "tokentracker-tt-backup"
BACKUP_VERSION = 1
MARK_FIELD = "_ttsync"

HOME = os.path.expanduser("~")
TRACKER_DIR = os.path.join(HOME, ".tokentracker", "tracker")
TT_HOME = os.path.join(HOME, ".tokentracker", "tt-sync")
BACKUP_DIR = os.path.join(TT_HOME, "backups")

STATE_FILE = os.path.join(TRACKER_DIR, "tt-sync-state.json")
CONFIG_FILE = os.path.join(TRACKER_DIR, "config.json")
CLOUD_PREF_FILE = os.path.join(TRACKER_DIR, "cloud-sync-pref.json")

TARGETS = {
    "main": {
        "label": "用量队列",
        "file": os.path.join(TRACKER_DIR, "queue.jsonl"),
        "state_file": os.path.join(TRACKER_DIR, "queue.state.json"),
        "key_fields": ["source", "model", "hour_start"],
        "key_names": ["数据源", "模型", "小时起点(UTC ISO)"],
    },
    "projects": {
        "label": "项目队列",
        "file": os.path.join(TRACKER_DIR, "project.queue.jsonl"),
        "state_file": os.path.join(TRACKER_DIR, "project.queue.state.json"),
        "key_fields": ["project_key", "source", "hour_start"],
        "key_names": ["项目路径", "数据源", "小时起点(UTC ISO)"],
    },
}

BACKUP_FILES_CORE = [
    "queue.jsonl",
    "queue.state.json",
    "project.queue.jsonl",
    "project.queue.state.json",
    "cursors.json",
    "config.json",
    "cloud-sync-pref.json",
    "tt-sync-state.json",
]
BACKUP_FILES_FULL = [
    "session.queue.jsonl",
    "session.queue.jsonl.meta.json",
    "cloud-device-token.json",
    "cloud-upload-owner.json",
]


class SyncError(Exception):
    """面向用户的错误，GUI / CLI 直接展示 message。"""


# ----------------------------------------------------------------- 小工具

def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def safe_name(s: str) -> str:
    out = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(s or "unknown"))
    return out[:40] or "unknown"


def fmt_num(n) -> str:
    try:
        return f"{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return "0"


def normalize_path_input(p) -> str:
    """去掉拖动文件时带上的引号；把 Git Bash 风格 /c/Users/x 还原成 C:\\Users\\x。"""
    if not isinstance(p, str):
        return p
    s = p.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    if len(s) > 2 and s[0] == "/" and s[1].isalpha() and s[2] == "/":
        s = s[1].upper() + ":\\" + s[3:].replace("/", "\\")
    return s


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, obj, private=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def read_jsonl(path):
    """返回 (rows, malformed_count, missing)。"""
    if not os.path.exists(path):
        return [], 0, True
    rows, malformed = [], 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    malformed += 1
    except OSError:
        return [], 0, True
    return rows, malformed, False


def write_jsonl_atomic(path, rows):
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    tmp = f"{path}.ttsync-tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    os.replace(tmp, path)


# ------------------------------------------------------------- 行分类

def is_control_row(row) -> bool:
    return isinstance(row, dict) and isinstance(row.get("kind"), str)


def is_usage_row(row, key_fields) -> bool:
    if not isinstance(row, dict) or is_control_row(row):
        return False
    last = key_fields[-1]
    v = row.get(last)
    return isinstance(v, str) and v != ""


def is_synthesized(row) -> bool:
    return isinstance(row, dict) and isinstance(row.get(MARK_FIELD), str) and row.get(MARK_FIELD) != ""


def key_of(row, key_fields) -> str:
    return "|".join(str(row.get(f) or "") for f in key_fields)


# --------------------------------------------------------- 聚合与合并

def _add_into(target: dict, row: dict) -> dict:
    """把 row 的数值字段累加进 target；非数值字段只在缺失时写入。"""
    for k, v in row.items():
        if k == MARK_FIELD:
            continue
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            target[k] = (target.get(k) or 0) + v
        elif k not in target:
            target[k] = v
    return target


def dedupe_last_wins(rows, key_fields) -> dict:
    """同键保留最后一条 —— 与 TokenTracker 读取语义一致。"""
    out = {}
    for row in rows:
        if not is_usage_row(row, key_fields):
            continue
        out[key_of(row, key_fields)] = row
    return out


def to_totals(deduped: dict) -> dict:
    return {k: _add_into({}, v) for k, v in deduped.items()}


def totals_summary(totals: dict) -> dict:
    tokens = sum(float(r.get("total_tokens") or 0) for r in totals.values())
    conv = sum(float(r.get("conversation_count") or 0) for r in totals.values())
    return {"keys": len(totals), "tokens": tokens, "conversations": conv}


def own_machine() -> dict:
    cfg = read_json(CONFIG_FILE, {}) or {}
    return {
        "hostname": socket.gethostname(),
        "machineId": cfg.get("machineId", "") if isinstance(cfg.get("machineId"), str) else "",
        "platform": sys.platform,
    }


def cloud_sync_enabled() -> bool:
    p = read_json(CLOUD_PREF_FILE, None)
    return bool(isinstance(p, dict) and p.get("enabled") is True)


# ------------------------------------------------------------- 合并说明书

def build_merge_spec() -> dict:
    """写进导出文件，让任何机器上的人 / AI 都能不靠外部文档正确合并。"""
    return {
        "title": "TokenTracker 用量合并包",
        "what": "这是从另一台电脑导出的 TokenTracker 用量数据。请把它合并进本机。",
        "dataDir": "~/.tokentracker/tracker",
        "main": {
            "file": "~/.tokentracker/tracker/queue.jsonl",
            "keyFields": TARGETS["main"]["key_fields"],
            "keyFieldNames": TARGETS["main"]["key_names"],
        },
        "projects": {
            "file": "~/.tokentracker/tracker/project.queue.jsonl",
            "keyFields": TARGETS["projects"]["key_fields"],
            "keyFieldNames": TARGETS["projects"]["key_names"],
        },
        "coreSemantics": [
            "TokenTracker 读队列时按 keyFields 去重，同键【只保留最后一条】，绝不自动相加。",
            "本机内部同键出现多次 = 该小时的累计更新 → 取最后一条（last-wins）。",
            "两台电脑同键 = 各自独立的用量 → 必须【相加】(sum)。",
            "这两种语义方向相反，混淆会造成「丢数据」或「重复计数」。",
        ],
        "steps": [
            "1. 合并前先把本机 queue.jsonl 备份一份。",
            "2. 读本包 queues.main.rows，按 keyFields 去重（同键取最后一条）→ imported。",
            "3. 读本机 queue.jsonl，剔除所有带 _ttsync 字段的行，其余按 keyFields 去重（最后一条）→ own。",
            "4. 对每个 key：数值字段 = own[key] + Σ(每个已导入来源机器的 imported[key])；非数值字段优先取 own 的。",
            "5. 输出顺序必须是：控制行(kind)与缺 hour_start 的行原样在前 → own 原始行 → 合成行放【最后】。",
            "6. 合成行加字段 _ttsync 记录来源 machineId，这是幂等重算的标记，不要删。",
            "7. 写回 queue.jsonl（原子写入：临时文件 + replace）。",
            "8. 把 queue.state.json 的 offset 置 0（文件被重写，云上传的字节偏移已失效）。",
            "9. 记录本包 machineId 到 tt-sync-state.json，用于下次幂等重算。",
            "10. project.queue.jsonl / project.queue.state.json 同样处理（若本包含有该项数据）。",
        ],
        "idempotency": [
            "每次合并都先剔除全部 _ttsync 行，再从「本机原始行 + 各来源机器快照的绝对总量」重算。",
            "因此同一份包重复合并任意多次，结果完全一致，不会叠加。",
        ],
        "guardrails": [
            "若本包 machineId 与本机 config.json 的 machineId 相同 → 这是本机自己导出的，必须拒绝合并。",
            "若本机开启了云同步（cloud-sync-pref.json 的 enabled 为 true）→ 先关闭再合并。",
            "不要合并本机导出的包；导出时已自动剔除历史合成行，A↔B 来回搬运不会循环重复。",
            "不要把 rows 直接拼接或覆盖 —— 一定会出错。",
        ],
        "automation": (
            "命令行实现：python tt-sync.py import <本文件路径> --dry-run 先预览，去掉 --dry-run 正式执行。"
            "图形界面：双击 TTSync.exe，选「导入并合并」。两者都已验证，优于手写合并。"
        ),
        "backupNote": (
            "本包只用于跨机器【合并】。若要整体备份 / 还原本机数据，"
            "请用备份功能（backup / restore-backup）。"
        ),
    }


# ------------------------------------------------------------------ 导出

def export_data(out_dir=None, fmt="json", log=lambda s: None) -> dict:
    out_dir = os.path.abspath(normalize_path_input(out_dir)) if out_dir else os.getcwd()
    fmt = (fmt or "json").lower()
    if fmt not in ("json", "csv"):
        raise SyncError("format 只支持 json 或 csv")
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as e:
        raise SyncError(f"无法创建/访问目标目录 {out_dir}：{e}\n请确认 U 盘已插好、盘符正确。")

    machine = own_machine()
    snapshot = {
        "format": SNAPSHOT_FORMAT,
        "version": SNAPSHOT_VERSION,
        "exportedAt": now_iso(),
        "machine": machine,
        "_spec": build_merge_spec(),
        "queues": {},
    }
    base = f"tt-usage-{safe_name(machine['hostname'])}-{now_stamp()}"
    files, grand_tokens, details = [], 0.0, []

    for name, t in TARGETS.items():
        rows, malformed, missing = read_jsonl(t["file"])
        own = [r for r in rows if not is_synthesized(r)]
        skipped = len(rows) - len(own)
        deduped = dedupe_last_wins(own, t["key_fields"])
        totals = to_totals(deduped)
        agg = totals_summary(totals)
        snapshot["queues"][name] = {
            "keyFields": t["key_fields"],
            "rows": list(deduped.values()),
            "totals": agg,
        }
        grand_tokens += agg["tokens"]
        details.append(
            {
                "label": t["label"],
                "keys": agg["keys"],
                "tokens": agg["tokens"],
                "malformed": malformed,
                "skipped_synth": skipped,
                "missing": missing,
            }
        )

    if fmt == "csv":
        for name, t in TARGETS.items():
            q = snapshot["queues"].get(name)
            if not q:
                continue
            suffix = "" if name == "main" else f"-{name}"
            path = os.path.join(out_dir, f"{base}{suffix}.csv")
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(_rows_to_csv(q["rows"], t["key_fields"]))
            files.append(path)
    else:
        path = os.path.join(out_dir, f"{base}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
            f.write("\n")
        files.append(path)

    for d in details:
        log(
            f"  {d['label']}：{fmt_num(d['keys'])} 个时间桶，{fmt_num(d['tokens'])} tokens"
            + ("（文件不存在，跳过）" if d["missing"] else "")
            + (f"，跳过 {d['malformed']} 行损坏数据" if d["malformed"] else "")
            + (f"，排除 {d['skipped_synth']} 行历史合成数据" if d["skipped_synth"] else "")
        )

    return {"files": files, "tokens": grand_tokens, "format": fmt, "details": details, "machine": machine}


def _csv_escape(v) -> str:
    s = "" if v is None else str(v)
    return '"' + s.replace('"', '""') + '"' if any(c in s for c in '",\n') else s


def _rows_to_csv(rows, key_fields) -> str:
    cols, seen = [], set()
    for f in key_fields:
        if f not in seen:
            seen.add(f)
            cols.append(f)
    for row in rows:
        for k in row.keys():
            if k == MARK_FIELD or k in seen:
                continue
            seen.add(k)
            cols.append(k)
    lines = [",".join(cols)]
    for row in rows:
        lines.append(",".join(_csv_escape(row.get(c)) for c in cols))
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ 状态

def load_state() -> dict:
    s = read_json(STATE_FILE, None)
    if not isinstance(s, dict):
        return {"version": SNAPSHOT_VERSION, "imports": {}}
    if not isinstance(s.get("imports"), dict):
        s["imports"] = {}
    return s


def save_state(state: dict):
    state["version"] = SNAPSHOT_VERSION
    state["updatedAt"] = now_iso()
    write_json(STATE_FILE, state)


# ------------------------------------------------------------------ 导入

def read_snapshot(path) -> dict:
    s = read_json(normalize_path_input(path), None)
    if not isinstance(s, dict):
        raise SyncError(f"数据文件无法解析：{path}")
    if s.get("format") != SNAPSHOT_FORMAT:
        raise SyncError(f"这不是 tt-sync 数据包（format={s.get('format')}）：{path}")
    try:
        ver = int(s.get("version") or 0)
    except (TypeError, ValueError):
        ver = 0
    if ver > SNAPSHOT_VERSION:
        raise SyncError(f"数据包版本 {ver} 高于本工具支持的 {SNAPSHOT_VERSION}，请更新工具。")
    if not isinstance(s.get("queues"), dict):
        raise SyncError(f"数据包缺少 queues 字段：{path}")
    return s


def precheck_import(snapshot) -> dict:
    """导入前的硬性检查。返回 {'machine_key':..., 'hostname':...}。"""
    mine = own_machine()
    inc_id = (snapshot.get("machine") or {}).get("machineId") or ""
    hostname = (snapshot.get("machine") or {}).get("hostname") or "未知"
    if inc_id and mine["machineId"] and inc_id == mine["machineId"]:
        raise SyncError("这份数据就是本机自己导出的（machineId 相同），导入它会造成重复计数。")
    return {"machine_key": inc_id or f"host:{hostname}", "hostname": hostname, "mine": mine}


def plan_import(snapshot) -> list:
    """只计算不写入，返回每个队列的合并计划。"""
    info = precheck_import(snapshot)
    incoming_key = info["machine_key"]
    state = load_state()
    imports = json.loads(json.dumps(state.get("imports") or {}))
    plan = []

    for name, t in TARGETS.items():
        snap_q = (snapshot.get("queues") or {}).get(name)
        if not snap_q or not isinstance(snap_q.get("rows"), list):
            plan.append({"name": name, "target": t, "skip": True, "reason": "数据包中没有这部分数据"})
            continue

        snap_totals = to_totals(dedupe_last_wins(snap_q["rows"], t["key_fields"]))
        own_rows, malformed, _ = read_jsonl(t["file"])
        own_rows = [r for r in own_rows if not is_synthesized(r)]
        own_totals = to_totals(dedupe_last_wins(own_rows, t["key_fields"]))

        others = {}
        for mk, entry in imports.items():
            if mk == incoming_key:
                continue
            q = ((entry or {}).get("queues") or {}).get(name)
            if not q or not isinstance(q.get("rows"), list):
                continue
            others[mk] = to_totals(dedupe_last_wins(q["rows"], t["key_fields"]))

        contributors = dict(others)
        contributors[incoming_key] = snap_totals

        keys = set(own_totals.keys())
        for tt in contributors.values():
            keys |= set(tt.keys())

        synthesized = []
        for k in keys:
            parts = k.split("|")
            base = dict(own_totals.get(k) or {})
            for i, f in enumerate(t["key_fields"]):
                base[f] = parts[i] if i < len(parts) else ""
            has_import = False
            for mk, tt in contributors.items():
                row = tt.get(k)
                if not row:
                    continue
                has_import = True
                for kk, vv in row.items():
                    if kk == MARK_FIELD:
                        continue
                    if isinstance(vv, bool):
                        continue
                    if isinstance(vv, (int, float)):
                        base[kk] = (base.get(kk) or 0) + vv
                    elif kk not in base or base[kk] == "":
                        base[kk] = vv
            if not has_import:
                continue
            base[MARK_FIELD] = "+".join(sorted(contributors.keys()))
            synthesized.append(base)

        after_tokens = 0.0
        for k in keys:
            for tt in [own_totals] + list(contributors.values()):
                r = tt.get(k)
                if r:
                    after_tokens += float(r.get("total_tokens") or 0)

        passthrough = [r for r in own_rows if not is_usage_row(r, t["key_fields"])]
        own_usage = [r for r in own_rows if is_usage_row(r, t["key_fields"])]

        before = totals_summary(own_totals)
        plan.append(
            {
                "name": name,
                "target": t,
                "skip": False,
                "passthrough": passthrough,
                "own_usage": own_usage,
                "synthesized": synthesized,
                "before": before["tokens"],
                "before_keys": before["keys"],
                "after": after_tokens,
                "after_keys": len(keys),
                "malformed": malformed,
                "snapshot_rows": snap_q["rows"],
            }
        )

    return {"plan": plan, "incoming_key": incoming_key, "info": info, "snapshot": snapshot}


def apply_import(planned: dict, source_file: str) -> dict:
    snapshot = planned["snapshot"]
    incoming_key = planned["incoming_key"]
    stamp = now_stamp()
    state = load_state()
    imports = state.get("imports") or {}
    prev = imports.get(incoming_key) or {}

    written = []
    for p in planned["plan"]:
        if p["skip"]:
            continue
        t = p["target"]

        if os.path.exists(t["file"]):
            bak = f"{t['file']}.bak-ttsync-{stamp}"
            shutil.copy2(t["file"], bak)
            written.append(f"备份 {os.path.basename(bak)}")

        rows = list(p["passthrough"]) + list(p["own_usage"]) + list(p["synthesized"])
        write_jsonl_atomic(t["file"], rows)
        written.append(f"{os.path.basename(t['file'])} ← {len(rows)} 行（含 {len(p['synthesized'])} 行合成）")

        if os.path.exists(t["state_file"]):
            st = read_json(t["state_file"], {}) or {}
            st["offset"] = 0
            st["updatedAt"] = now_iso()
            st["note"] = f"tt-sync import {stamp}"
            write_json(t["state_file"], st)
            written.append(f"{os.path.basename(t['state_file'])} offset→0")

    entry = {
        "machine": snapshot.get("machine") or None,
        "snapshotExportedAt": snapshot.get("exportedAt"),
        "importedAt": now_iso(),
        "sourceFile": os.path.basename(source_file),
        "queues": dict(prev.get("queues") or {}),
    }
    for name, t in TARGETS.items():
        sq = (snapshot.get("queues") or {}).get(name)
        if sq and isinstance(sq.get("rows"), list):
            entry["queues"][name] = {"keyFields": t["key_fields"], "rows": sq["rows"]}
    imports[incoming_key] = entry
    state["imports"] = imports
    save_state(state)
    written.append(f"{os.path.basename(STATE_FILE)}（记录来源机器，供下次幂等重算）")

    return {"written": written, "incoming_key": incoming_key}


# ------------------------------------------------------------------ 状态查询

def status() -> dict:
    state = load_state()
    mine = own_machine()
    out = {"machine": mine, "cloud_sync": cloud_sync_enabled(), "queues": {}, "imports": []}

    for name, t in TARGETS.items():
        rows, malformed, _ = read_jsonl(t["file"])
        own = [r for r in rows if not is_synthesized(r)]
        synth = [r for r in rows if is_synthesized(r)]
        own_agg = totals_summary(to_totals(dedupe_last_wins(own, t["key_fields"])))
        merged_agg = totals_summary(to_totals(dedupe_last_wins(rows, t["key_fields"])))
        out["queues"][name] = {
            "label": t["label"],
            "own": own_agg,
            "merged": merged_agg,
            "synth_rows": len(synth),
            "malformed": malformed,
        }

    for mk, entry in (state.get("imports") or {}).items():
        m = (entry or {}).get("machine") or {}
        rows = (((entry or {}).get("queues") or {}).get("main") or {}).get("rows") or []
        out["imports"].append(
            {
                "key": mk,
                "hostname": m.get("hostname") or "未知",
                "snapshotAt": entry.get("snapshotExportedAt"),
                "importedAt": entry.get("importedAt"),
                "tokens": totals_summary(to_totals(dedupe_last_wins(rows, TARGETS["main"]["key_fields"])))["tokens"],
                "keys": len(dedupe_last_wins(rows, TARGETS["main"]["key_fields"])),
            }
        )
    return out


# ------------------------------------------------------------------ 备份

def _collect_backup_files(full: bool) -> list:
    wanted = BACKUP_FILES_CORE + (BACKUP_FILES_FULL if full else [])
    out = []
    for name in wanted:
        p = os.path.join(TRACKER_DIR, name)
        if not os.path.exists(p):
            continue
        with open(p, "rb") as f:
            buf = f.read()
        out.append(
            {
                "name": name,
                "size": len(buf),
                "sha256": hashlib.sha256(buf).hexdigest(),
                "content": buf.decode("utf-8", errors="replace"),
            }
        )
    return out


def backup(note: str = "", full: bool = False, out_dir: str = None) -> dict:
    out_dir = os.path.abspath(normalize_path_input(out_dir)) if out_dir else BACKUP_DIR
    os.makedirs(out_dir, exist_ok=True)

    machine = own_machine()
    files = _collect_backup_files(full)
    if not files:
        raise SyncError(f"没有找到任何可备份的数据文件（目录：{TRACKER_DIR}）")

    bundle = {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "createdAt": now_iso(),
        "note": note or "",
        "scope": "full" if full else "core",
        "machine": machine,
        "trackerDir": TRACKER_DIR,
        "files": files,
    }
    name = f"tt-backup-{safe_name(machine['hostname'])}-{now_stamp()}{'-full' if full else ''}.json"
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False)

    return {
        "file": path,
        "files": [(x["name"], x["size"]) for x in files],
        "scope": bundle["scope"],
        "machine": machine,
        "total_bytes": sum(x["size"] for x in files),
    }


def list_backup_bundles() -> list:
    if not os.path.isdir(BACKUP_DIR):
        return []
    items = []
    for n in os.listdir(BACKUP_DIR):
        if not (n.startswith("tt-backup-") and n.endswith(".json")):
            continue
        p = os.path.join(BACKUP_DIR, n)
        meta = read_json(p, {}) or {}
        items.append(
            {
                "path": p,
                "name": n,
                "mtime": os.path.getmtime(p),
                "size": os.path.getsize(p),
                "createdAt": meta.get("createdAt"),
                "scope": meta.get("scope", "core"),
                "note": meta.get("note", ""),
            }
        )
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def restore_backup(which, log=lambda s: None) -> dict:
    """整体还原某个完整备份。which 可以是序号(int/str)或文件路径。"""
    if isinstance(which, int) or (isinstance(which, str) and which.isdigit()):
        lst = list_backup_bundles()
        idx = int(which)
        if idx < 0 or idx >= len(lst):
            raise SyncError(f"序号无效：{which}")
        path = lst[idx]["path"]
    else:
        path = os.path.abspath(normalize_path_input(str(which)))
    if not os.path.exists(path):
        raise SyncError(f"找不到备份文件：{path}")

    bundle = read_json(path, None)
    if not isinstance(bundle, dict) or bundle.get("format") != BACKUP_FORMAT:
        raise SyncError(f"这不是 tt-sync 备份文件：{path}")

    stamp = now_stamp()
    safety = os.path.join(BACKUP_DIR, f"pre-restore-{stamp}")
    os.makedirs(safety, exist_ok=True)
    saved = []
    for f in bundle.get("files") or []:
        cur = os.path.join(TRACKER_DIR, f["name"])
        if not os.path.exists(cur):
            continue
        shutil.copy2(cur, os.path.join(safety, f["name"]))
        saved.append(f["name"])

    restored = 0
    for f in bundle.get("files") or []:
        name = f.get("name") or ""
        if "/" in name or "\\" in name or ".." in name:
            continue
        target = os.path.join(TRACKER_DIR, name)
        tmp = f"{target}.ttsync-restore-{os.getpid()}"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f.get("content") or "")
        os.replace(tmp, target)
        restored += 1

    return {
        "path": path,
        "createdAt": bundle.get("createdAt"),
        "scope": bundle.get("scope", "core"),
        "safety_dir": safety,
        "saved": saved,
        "restored": restored,
        "names": [f.get("name") for f in (bundle.get("files") or [])],
    }


# ------------------------------------------- 回滚某次导入前的自动备份

def list_import_backups() -> list:
    items = []
    if not os.path.isdir(TRACKER_DIR):
        return items
    for n in os.listdir(TRACKER_DIR):
        if ".bak-ttsync-" not in n:
            continue
        p = os.path.join(TRACKER_DIR, n)
        if not os.path.isfile(p):
            continue
        base = n.split(".bak-ttsync-")[0]
        items.append({"path": p, "name": n, "target": os.path.join(TRACKER_DIR, base), "mtime": os.path.getmtime(p)})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def restore_import_backup(which, log=lambda s: None) -> dict:
    lst = list_import_backups()
    if not lst:
        raise SyncError("没有找到任何导入前的自动备份。")
    if isinstance(which, str) and which.isdigit():
        idx = int(which)
    elif isinstance(which, int):
        idx = which
    else:
        raise SyncError("请用序号选择要回滚的备份。")
    if idx < 0 or idx >= len(lst):
        raise SyncError(f"序号无效：{which}")
    item = lst[idx]
    safety = f"{item['target']}.pre-restore-{now_stamp()}"
    if os.path.exists(item["target"]):
        shutil.copy2(item["target"], safety)
    shutil.copy2(item["path"], item["target"])
    return {"restored": os.path.basename(item["target"]), "from": os.path.basename(item["path"]), "safety": safety}
