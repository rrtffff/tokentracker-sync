"""
TokenTracker Sync core —— TokenTracker 跨机器用量合并 / 备份 / 还原

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
import math
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

# 每个队列对应的磁盘文件名（真正与 TokenTracker 约定的部分，不要改）
_TARGET_FILENAMES = {
    "main": ("queue.jsonl", "queue.state.json"),
    "projects": ("project.queue.jsonl", "project.queue.state.json"),
}


def use_home(home: str) -> None:
    """把所有路径重新定位到指定的 home 目录。

    主要给测试用（可以在临时目录里跑完整流程，不碰真实数据），
    也方便需要多份独立数据的高级用法。
    """
    global HOME, TRACKER_DIR, TT_HOME, BACKUP_DIR
    global STATE_FILE, CONFIG_FILE, CLOUD_PREF_FILE

    HOME = home
    TRACKER_DIR = os.path.join(home, ".tokentracker", "tracker")
    TT_HOME = os.path.join(home, ".tokentracker", "tt-sync")
    BACKUP_DIR = os.path.join(TT_HOME, "backups")
    STATE_FILE = os.path.join(TRACKER_DIR, "tt-sync-state.json")
    CONFIG_FILE = os.path.join(TRACKER_DIR, "config.json")
    CLOUD_PREF_FILE = os.path.join(TRACKER_DIR, "cloud-sync-pref.json")

    for name, t in TARGETS.items():
        fn, sn = _TARGET_FILENAMES[name]
        t["file"] = os.path.join(TRACKER_DIR, fn)
        t["state_file"] = os.path.join(TRACKER_DIR, sn)

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

# ------------------------------------------------------------------ 安全边界

# 允许从「外部快照」写入本机队列的数值字段白名单。
# 依据：对真实 queue.jsonl 做字段普查得到的实际 schema（见 docs/SECURITY.md）。
# 不在名单里的字段一律丢弃并计数 —— 这样一份构造出来的快照就无法把任意字段
# 塞进用户的队列文件里（比如伪造的 kind 会改变 TokenTracker 对该行的解释）。
SAFE_NUMERIC_FIELDS = frozenset({
    "input_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
    "billable_total_tokens",
    "conversation_count",
    "total_cost_usd",
    "priority_input_tokens",
    "priority_output_tokens",
    "priority_cached_input_tokens",
    "priority_reasoning_output_tokens",
})

# 键字段（source / model / project_key / hour_start）的字符串长度上限
MAX_KEY_TEXT = 512
# 单个队列允许的行数上限
MAX_SNAPSHOT_ROWS = 300_000
# 快照文件大小上限
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
# 状态文件里允许记录的最大来源机器数（每台都会把其全部行存下来）
MAX_SOURCE_MACHINES = 64


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


def _reject_const(name):
    """JSON 标准里没有 NaN / Infinity，但 Python 的 json 默认接受。

    上游用 JavaScript 读这些文件，`JSON.parse` 遇到 NaN / Infinity 会直接抛错。
    所以把这类值当损坏数据处理，绝不静默吸收。
    """
    raise ValueError(f"非标准 JSON 常量：{name}")


def _dumps(obj, **kw):
    """统一序列化出口。

    关键：allow_nan=False。否则一旦某个数值变成 NaN / Infinity，Python 会
    写出字面量 `NaN`，而那是非法 JSON —— 上游将无法解析这一行，
    相当于本工具把用户的数据写坏了。
    """
    kw.setdefault("ensure_ascii", False)
    return json.dumps(obj, allow_nan=False, **kw)


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f, parse_constant=_reject_const)
    except Exception:
        return default


def write_json(path, obj, private=True):
    body = _dumps(obj) + "\n"          # 先整体序列化：失败就不落地，不留半个文件
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    os.replace(tmp, path)


def read_jsonl(path):
    """返回 (rows, malformed_lines, missing)。

    malformed_lines 是**原始文本行**，不是计数 —— 合并时会原样写回，
    保证本工具不会以「解析不了」为理由丢掉用户已有的数据。
    """
    if not os.path.exists(path):
        return [], [], True
    rows, malformed = [], []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line, parse_constant=_reject_const))
                except Exception:
                    malformed.append(line)
    except OSError:
        return [], [], True
    return rows, malformed, False


def write_jsonl_atomic(path, rows):
    """原子写入。rows 里可以是 dict，也可以是「原样保留的原始行」字符串。"""
    parts = []
    for r in rows:
        if isinstance(r, str):
            parts.append(r.replace("\n", " ").replace("\r", " ").strip() + "\n")
        else:
            parts.append(_dumps(r) + "\n")     # 先全部序列化，任何一行不合法就整体不写
    body = "".join(parts)
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
    """把键字段拼成一个字符串，仅用于做字典索引。

    注意：**不能用它反推出各字段**（字段值里可能含分隔符）。需要还原字段时
    必须用 key_parts()，它在同一轮扫描里顺带取到原始值。
    """
    return "|".join(str(row.get(f) or "") for f in key_fields)


def key_parts(row, key_fields) -> tuple:
    return tuple(row.get(f) if isinstance(row.get(f), str) else "" for f in key_fields)


def sanitize_row(row, key_fields):
    """清洗一行「外部数据」。

    返回 (clean_row, dropped_field_names)。规则：
      · 键字段必须是长度受限的非空字符串
      · 数值只允许已知字段，且必须是有限数（NaN / Infinity 直接报错中止）
      · 其他一切字段丢弃并计数，绝不写进用户的数据文件
    """
    clean, dropped = {}, []
    for f in key_fields:
        v = row.get(f)
        if not isinstance(v, str) or not v:
            raise SyncError(f"数据包里有一行缺少合法的 {f} 字段")
        if len(v) > MAX_KEY_TEXT:
            raise SyncError(f"数据包里 {f} 字段过长（上限 {MAX_KEY_TEXT} 字符）")
        clean[f] = v

    for k, v in row.items():
        if k in clean:
            continue
        if k not in SAFE_NUMERIC_FIELDS:
            dropped.append(k)          # kind / _ttsync / 未知字段，一律不带进来
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            dropped.append(k)
            continue
        if isinstance(v, float):
            if not math.isfinite(v):
                raise SyncError(f"数据包里 {k} 的值不是有限数（{v}），文件可能已损坏")
            if v.is_integer():
                v = int(v)
        clean[k] = v
    return clean, dropped


def totals_with_parts(rows, key_fields):
    """去重后同时返回 (总量表, 键字段原值表)。

    两边一次扫出来，合成行就不必再去「拆」键字符串，从根上避免
    字段值里含分隔符导致错位。
    """
    totals, parts = {}, {}
    for k, row in dedupe_last_wins(rows, key_fields).items():
        parts[k] = key_parts(row, key_fields)
        totals[k] = _add_into({}, row)
    return totals, parts


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
            "命令行实现：python tokentracker-sync.py import <本文件路径> --dry-run 先预览，去掉 --dry-run 正式执行。"
            "图形界面：双击 TokenTrackerSync.exe，选「导入并合并」。两者都已验证，优于手写合并。"
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
                "malformed": len(malformed),
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
            f.write(_dumps(snapshot, indent=2) + "\n")
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
    """读取并**校验**一份外部数据包。

    校验放在入口做一次，后续逻辑就可以放心地用这些行。
    校验不通过就直接拒绝，不做任何「尽力而为」的猜测。
    """
    p = normalize_path_input(path)
    if not os.path.exists(p):
        raise SyncError(f"找不到数据文件：{path}")
    try:
        size = os.path.getsize(p)
    except OSError as e:
        raise SyncError(f"无法读取文件大小：{e}")
    if size > MAX_SNAPSHOT_BYTES:
        raise SyncError(
            f"数据包过大（{size / 1048576:.0f} MB，上限 {MAX_SNAPSHOT_BYTES // 1048576} MB），已拒绝。"
        )

    s = read_json(p, None)
    if not isinstance(s, dict):
        raise SyncError(f"数据文件无法解析（或含非法数值）：{path}")
    if s.get("format") != SNAPSHOT_FORMAT:
        raise SyncError(f"这不是 TokenTracker Sync 数据包（format={s.get('format')}）：{path}")
    try:
        ver = int(s.get("version") or 0)
    except (TypeError, ValueError):
        ver = 0
    if ver > SNAPSHOT_VERSION:
        raise SyncError(f"数据包版本 {ver} 高于本工具支持的 {SNAPSHOT_VERSION}，请更新工具。")
    if not isinstance(s.get("queues"), dict):
        raise SyncError(f"数据包缺少 queues 字段：{path}")

    # 逐行清洗：只保留键字段与已知数值字段，其余丢弃并统计
    dropped = {}
    for name, t in TARGETS.items():
        q = (s.get("queues") or {}).get(name)
        if q is None:
            continue
        if not isinstance(q, dict) or not isinstance(q.get("rows"), list):
            raise SyncError(f"数据包的 queues.{name}.rows 格式不对：{path}")
        if len(q["rows"]) > MAX_SNAPSHOT_ROWS:
            raise SyncError(
                f"数据包 queues.{name} 有 {len(q['rows']):,} 行，超过上限 "
                f"{MAX_SNAPSHOT_ROWS:,} 行，已拒绝。"
            )
        clean_rows, cnt = [], {}
        for row in q["rows"]:
            if not isinstance(row, dict):
                continue
            c, dr = sanitize_row(row, t["key_fields"])
            clean_rows.append(c)
            for d in dr:
                cnt[d] = cnt.get(d, 0) + 1
        q["rows"] = clean_rows
        if cnt:
            dropped[name] = cnt

    s["_dropped_fields"] = dropped
    return s


def precheck_import(snapshot) -> dict:
    """导入前的硬性检查。返回 {'machine_key':..., 'hostname':...}。"""
    mine = own_machine()
    inc_id = (snapshot.get("machine") or {}).get("machineId") or ""
    hostname = (snapshot.get("machine") or {}).get("hostname") or "未知"
    if inc_id and mine["machineId"] and inc_id == mine["machineId"]:
        raise SyncError("这份数据就是本机自己导出的（machineId 相同），导入它会造成重复计数。")
    return {"machine_key": inc_id or f"host:{hostname}", "hostname": hostname, "mine": mine}


def plan_import(snapshot) -> dict:
    """只计算不写入，返回每个队列的合并计划。"""
    info = precheck_import(snapshot)
    incoming_key = info["machine_key"]
    state = load_state()
    # 深拷贝一份，避免下面改动污染真实状态
    imports = json.loads(_dumps(state.get("imports") or {}))

    known = set(imports.keys())
    if incoming_key not in known and len(known) >= MAX_SOURCE_MACHINES:
        raise SyncError(
            f"已记录 {len(known)} 台来源机器，达到上限 {MAX_SOURCE_MACHINES}。\n"
            "       每台来源的数据都会永久保存在 tt-sync-state.json 里，无上限增长会拖慢每次导入。\n"
            "       如确实需要，请先在 GUI 里「回滚导入」清理不再需要的来源。"
        )

    plan = []

    for name, t in TARGETS.items():
        snap_q = (snapshot.get("queues") or {}).get(name)
        if not snap_q or not isinstance(snap_q.get("rows"), list):
            plan.append({"name": name, "target": t, "skip": True, "reason": "数据包中没有这部分数据"})
            continue

        # 快照已在 read_snapshot() 里清洗过
        snap_totals, snap_parts = totals_with_parts(snap_q["rows"], t["key_fields"])

        own_all, malformed, _ = read_jsonl(t["file"])
        own_rows = [r for r in own_all if not is_synthesized(r)]
        own_totals, own_parts = totals_with_parts(own_rows, t["key_fields"])

        others, other_parts = {}, {}
        for mk, entry in imports.items():
            if mk == incoming_key:
                continue
            q = ((entry or {}).get("queues") or {}).get(name)
            if not q or not isinstance(q.get("rows"), list):
                continue
            ot, op = totals_with_parts(q["rows"], t["key_fields"])
            others[mk] = ot
            for k, v in op.items():
                other_parts.setdefault(k, v)

        contributors = dict(others)
        contributors[incoming_key] = snap_totals

        # 键字段原值汇总：本机优先，其次已导入来源，最后新快照。
        # 绝不从键字符串里拆 —— 字段值本身可能含分隔符。
        parts_by_key = {}
        for src in (snap_parts, other_parts, own_parts):
            for k, v in src.items():
                parts_by_key.setdefault(k, v)

        keys = set(own_totals.keys())
        for tt in contributors.values():
            keys |= set(tt.keys())

        synthesized = []
        for k in keys:
            parts = parts_by_key.get(k)
            if parts is None:
                # 理论上不会发生（k 一定来自某一份 rows）。真遇到就跳过，
                # 而不是硬拆字符串造出一个字段错位的行。
                continue

            base = dict(own_totals.get(k) or {})
            for i, f in enumerate(t["key_fields"]):
                base[f] = parts[i]

            has_import = False
            for tt in contributors.values():
                row = tt.get(k)
                if not row:
                    continue
                has_import = True
                for kk, vv in row.items():
                    if kk == MARK_FIELD or kk in t["key_fields"]:
                        continue
                    if isinstance(vv, bool) or not isinstance(vv, (int, float)):
                        continue
                    base[kk] = (base.get(kk) or 0) + vv
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
                # 原样保留的损坏行（不是计数），写回时附在文件末尾，确保不丢数据
                "malformed_lines": list(malformed),
                "malformed": len(malformed),
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
        # 已有的无法解析的行（截断等）原样附在末尾，不因「读不懂」就丢掉用户数据
        mal = p.get("malformed_lines") or []
        rows += mal
        write_jsonl_atomic(t["file"], rows)
        note = f"{os.path.basename(t['file'])} ← {len(rows)} 行（含 {len(p['synthesized'])} 行合成）"
        if mal:
            note += f"，另保留 {len(mal)} 行无法解析的原始数据"
        written.append(note)

        if os.path.exists(t["state_file"]):
            st = read_json(t["state_file"], {}) or {}
            st["offset"] = 0
            st["updatedAt"] = now_iso()
            st["note"] = f"TokenTracker Sync import {stamp}"
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
            "malformed": len(malformed),
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
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_dumps(bundle))

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
        raise SyncError(f"这不是 TokenTracker Sync 备份文件：{path}")

    stamp = now_stamp()
    safety = os.path.join(BACKUP_DIR, f"pre-restore-{stamp}")
    os.makedirs(safety, exist_ok=True)

    def _safe_target(raw_name) -> str:
        """把备份里的文件名解析成 TRACKER_DIR 内的一个确定路径。

        第一层：显式挡掉分隔符、`..`、驱动器号（`C:`）和 NUL —— 合法数据文件名里
                不该出现这些。
        第二层：再校验解析结果确实落在 TRACKER_DIR 之内（纵深防御，防止
                Windows 路径拼接的边角行为）。
        """
        name = str(raw_name or "")
        if not name or "/" in name or "\\" in name or ".." in name or ":" in name or "\x00" in name:
            return ""
        base = os.path.abspath(TRACKER_DIR)
        target = os.path.abspath(os.path.join(base, name))
        try:
            if os.path.commonpath([base, target]) != base:
                return ""
        except ValueError:
            return ""
        return target

    saved = []
    for f in bundle.get("files") or []:
        target = _safe_target(f.get("name"))
        if not target or not os.path.exists(target):
            continue
        shutil.copy2(target, os.path.join(safety, os.path.basename(target)))
        saved.append(os.path.basename(target))

    restored = 0
    for f in bundle.get("files") or []:
        target = _safe_target(f.get("name"))
        if not target:
            continue
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
