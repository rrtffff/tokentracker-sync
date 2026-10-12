"""TokenTracker Sync 命令行接口（与图形界面共用同一套核心逻辑）。"""

from __future__ import annotations

import os
import sys

from . import core


def out(msg=""):
    print(msg)


def _opt(args, name, default=""):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def cmd_name() -> str:
    """按实际调用方式显示命令名，避免写成"显示名"那样读起来不像命令。"""
    base = os.path.basename(sys.argv[0] or "")
    if not base:
        return "tokenrackersync"
    if base.lower().endswith(".exe"):
        return base[:-4]
    return f"python {base}"


def print_help():
    c = cmd_name()
    out(
        f"""
TokenTracker Sync — 跨机器用量合并 / 备份 / 还原（非官方小工具）

用法：
  {c}
                                 打开图形界面（推荐）
  {c} <数据包.json>
                                 直接导入该数据包（等同于拖到程序图标上）

  {c} export --to <目录> [--format json|csv]
                                 导出本机数据（目录可指向 U 盘）
  {c} import <数据包.json> [--dry-run] [--force] [--yes]
                                 把数据包合并进本机
  {c} status                     查看本机合计与已导入来源
  {c} doctor                     环境自检（报 bug 时贴这个输出）

  {c} backup [--full] [--note <备注>] [--out <目录>]
                                 完整备份（生成单个文件，可整体还原）
  {c} backups                    列出备份
  {c} restore-backup <序号|文件>
                                 整体还原某个备份
  {c} restore <序号>              回滚某次导入前的自动备份

两个不要混淆的概念：
  export / import     跨机器【合并】—— 两台的数据相加
  backup / restore    本机【整体覆盖还原】—— 回到某个时间点

数据目录：{core.TRACKER_DIR}
备份目录：{core.BACKUP_DIR}
"""
    )


def cmd_export(args):
    files = core.export_data(_opt(args, "--to") or None, _opt(args, "--format", "json") or "json", log=out)
    out("")
    out(f"合计：{core.fmt_num(files['tokens'])} tokens")
    for f in files["files"]:
        out(f"已写出：{f}")
    out("")
    if files["format"] == "json":
        out("这个 JSON 里带了 _spec 字段，写明了合并规则。")
        out("带到另一台电脑后选「导入并合并」即可（也可让 AI 按 _spec 处理）。")
    else:
        out("CSV 仅供查看，导入请用默认的 json 格式。")
    out("")


def cmd_import(args):
    path = next((a for a in args if not a.startswith("-")), "")
    if not path:
        raise core.SyncError("用法：tokentracker-sync.py import <数据包.json> [--dry-run] [--force] [--yes]")
    path = os.path.abspath(core.normalize_path_input(path))
    if not os.path.exists(path):
        raise core.SyncError(f"找不到数据包：{path}")

    dry = "--dry-run" in args
    force = "--force" in args

    snap = core.read_snapshot(path)
    planned = core.plan_import(snap)

    if core.cloud_sync_enabled() and not force:
        raise core.SyncError(
            "检测到云同步处于开启状态。导入会重写 queue.jsonl，云上传的字节偏移会失效。\n"
            "       建议先在 TokenTracker 设置里关闭云同步；确认要带着云同步一起做，请加 --force。"
        )

    out("")
    out("== 导入数据包 ==")
    info = planned["info"]
    out(f"来源机器：{info['hostname']}")
    out(f"导出时间：{snap.get('exportedAt')}")
    out("")
    out("合并结果预览：")
    out("")
    out("  " + _pad("数据部分", 12) + _pad("合并前", 22) + _pad("合并后", 22) + "变化")
    for p in planned["plan"]:
        if p["skip"]:
            out("  " + _pad(p["target"]["label"], 12) + f"({p['reason']})")
            continue
        diff = p["after"] - p["before"]
        out(
            "  "
            + _pad(p["target"]["label"], 12)
            + _pad(core.fmt_num(p["before"]) + " tokens", 22)
            + _pad(core.fmt_num(p["after"]) + " tokens", 22)
            + ("+" if diff >= 0 else "")
            + core.fmt_num(diff)
        )
    out("")

    dropped = snap.get("_dropped_fields") or {}
    if dropped:
        out("  已忽略数据包中本工具不认识的字段（不会写进你的数据文件）：")
        for qn, cnt in dropped.items():
            items = "、".join(f"{k}×{v}" for k, v in sorted(cnt.items()))
            out(f"    {qn}：{items}")
        out("")

    bad = sum(p.get("malformed", 0) for p in planned["plan"])
    if bad:
        out(f"  本机队列里有 {bad} 行无法解析（可能是上次写入被中断），")
        out("  它们不会被计入统计，但会原样保留在文件末尾，不会被删除。")
        out("")

    if dry:
        out("[dry-run] 未写入任何文件。")
        out("")
        return

    if not ("--yes" in args) and sys.stdin.isatty():
        if input("确认写入？会先自动备份 queue.jsonl。[y/N] ").strip().lower() not in ("y", "yes"):
            out("已取消，未做任何修改。")
            return

    applied = core.apply_import(planned, path)
    out("已写入：")
    for w in applied["written"]:
        out("  · " + w)
    out("")
    out("完成。打开 TokenTracker 面板即可看到合并后的数据（无需重启）。")
    out("")


def cmd_doctor(_args):
    """环境自检：报 bug 时把这个输出贴上去即可。"""
    import platform
    import socket

    shown = os.environ.get("TT_SYNC_REDACT", "") == "1"

    def hide(s):
        if not s:
            return "—"
        return ("…" + s[-6:]) if shown else s

    out("")
    out("== TokenTracker Sync 环境自检 ==")
    try:
        from . import __version__
        out(f"  TokenTracker Sync 版本    : {__version__}")
    except Exception:
        pass
    out(f"  Python          : {platform.python_version()}  ({platform.machine()})")
    out(f"  操作系统        : {platform.system()} {platform.release()}")
    out(f"  主机名          : {socket.gethostname()}")
    out(f"  打包运行        : {'是（PyInstaller）' if getattr(sys, '_MEIPASS', None) else '否（源码）'}")

    out("")
    out("== 数据目录 ==")
    out(f"  数据目录        : {core.TRACKER_DIR}")
    out(f"  目录存在        : {'是' if os.path.isdir(core.TRACKER_DIR) else '否 ← 请先运行一次 TokenTracker'}")
    for name in ("queue.jsonl", "project.queue.jsonl", "cursors.json", "config.json"):
        p = os.path.join(core.TRACKER_DIR, name)
        if os.path.exists(p):
            out(f"    {name:<22}: {os.path.getsize(p) / 1024:>10,.0f} KB")
        else:
            out(f"    {name:<22}: （不存在）")

    cfg = core.read_json(core.CONFIG_FILE, {}) or {}
    out(f"  machineId       : {hide(cfg.get('machineId', ''))}")
    out(f"  云同步          : {'开启（导入前建议关闭）' if core.cloud_sync_enabled() else '已关闭'}")

    out("")
    out("== 工具目录 / 资源 ==")
    out(f"  工具目录        : {core.TT_HOME}")
    out(f"  备份目录        : {core.BACKUP_DIR}")
    bl = core.list_backup_bundles()
    out(f"  已有备份        : {len(bl)} 个")
    out(f"  导入前自动备份  : {len(core.list_import_backups())} 个")

    try:
        from . import gui
        out(f"  拖放支持        : {'是（tkinterdnd2）' if gui.HAS_DND else '否（可把文件拖到 exe 图标上导入）'}")
        for a in ("bg.png", "mascot.png", "icon.png"):
            p = gui._asset(a)
            out(f"    {a:<14}: {'找到' if os.path.exists(p) else '缺失'}  {p}")
    except Exception as e:
        out(f"  图形界面模块    : 不可用（{e}）")

    out("")
    out("== 合并账目 ==")
    st = core.load_state()
    imports = st.get("imports") or {}
    out(f"  已导入来源机器  : {len(imports)}")
    for k, v in imports.items():
        m = (v or {}).get("machine") or {}
        out(f"    · {m.get('hostname', '?')}  {hide(k)}  快照 {(v or {}).get('snapshotExportedAt', '?')}")
    out("")


def _pad(s, n):
    s = str(s)
    width = sum(2 if ord(c) > 127 else 1 for c in s)
    return s + " " * max(0, n - width)


def cmd_status(_args):
    s = core.status()
    m = s["machine"]
    out("")
    out("== 本机 ==")
    out(f"  {m['hostname']}   ID {m['machineId'][:12]}…")
    out(f"  云同步：{'开启（会影响字节偏移，建议关闭）' if s['cloud_sync'] else '已关闭'}")
    out("")
    for q in s["queues"].values():
        out(f"  {q['label']}：")
        out(f"    本机自身   {core.fmt_num(q['own']['keys'])} 个桶，{core.fmt_num(q['own']['tokens'])} tokens")
        if q["synth_rows"]:
            out(f"    已含导入   {q['synth_rows']} 行合成，合并后合计 {core.fmt_num(q['merged']['tokens'])} tokens")
    out("")
    out(f"== 已导入的来源机器（{len(s['imports'])}）==")
    if not s["imports"]:
        out("  尚无。在另一台电脑导出，把数据包带过来导入即可。")
    for i in s["imports"]:
        out(f"  · {i['hostname']}  ID {i['key'][:12]}…")
        out(f"      快照时间 {i['snapshotAt']}，导入于 {i['importedAt']}")
        out(f"      贡献 {core.fmt_num(i['keys'])} 个桶，{core.fmt_num(i['tokens'])} tokens")
    out("")


def cmd_backup(args):
    r = core.backup(_opt(args, "--note"), "--full" in args, _opt(args, "--out") or None)
    out("")
    out("== 完整备份 ==")
    out(f"  机器：{r['machine']['hostname']}")
    out(f"  范围：{r['scope']}")
    out(f"  文件：{len(r['files'])} 个，共 {core.fmt_num(r['total_bytes'] / 1024)} KB")
    for name, size in r["files"]:
        out(f"    · {name}  ({size / 1024:.0f} KB)")
    out("")
    out(f"  已写出：{r['file']}")

    if r.get("excluded_sensitive"):
        out("")
        out("  已排除的凭据文件（不会写进备份，避免分享/上传时泄露）：")
        for n in r["excluded_sensitive"]:
            out(f"    × {n}")
    if r.get("privacy_notes"):
        out("")
        out("  隐私提示 —— 这份备份里除了数字，还有：")
        for n in r["privacy_notes"]:
            out(f"    · {n}")
        out("    分享给他人前请先确认这些内容可以外传。")

    out("")
    out("注意：这是「整体覆盖式还原」用的备份，不是跨机器合并包。")
    out("跨机器合并请用 export / import。")
    out("")


def cmd_backups(_args):
    lst = core.list_backup_bundles()
    out("")
    out("== 备份列表 ==")
    out(f"  目录：{core.BACKUP_DIR}")
    out("")
    if not lst:
        out("  暂无备份。用 backup 命令创建。")
        out("")
        return
    for i, b in enumerate(lst):
        out(f"  [{i}] {b['name']}")
        out(f"      {b['createdAt']}   {b['scope']}   {b['size'] / 1024:.0f} KB"
            + (f"   备注：{b['note']}" if b["note"] else ""))
    out("")
    out("还原：tokentracker-sync.py restore-backup <序号或文件路径>")
    out("")


def cmd_restore_backup(args):
    which = next((a for a in args if not a.startswith("-")), "")
    if not which:
        lst = core.list_backup_bundles()
        if not lst:
            raise core.SyncError("没有可用备份。先运行 backup。")
        out("")
        out("可用备份：")
        for i, b in enumerate(lst[:15]):
            out(f"  [{i}] {b['name']}")
        out("")
        out("用法：tokentracker-sync.py restore-backup <序号>")
        out("")
        return
    r = core.restore_backup(which, log=out)
    out("")
    out("== 还原完整备份 ==")
    out(f"  备份时间：{r['createdAt']}")
    out(f"  范围：{r['scope']}")
    out(f"  当前数据已另存：{r['safety_dir']}（{len(r['saved'])} 个文件）")
    out(f"  已还原 {r['restored']} 个文件。")
    out("")


def cmd_restore(args):
    which = next((a for a in args if not a.startswith("-")), "")
    lst = core.list_import_backups()
    if not which:
        if not lst:
            raise core.SyncError("没有找到任何导入前的自动备份。")
        out("")
        out("可用备份（新 → 旧）：")
        for i, b in enumerate(lst[:15]):
            out(f"  [{i}] {b['name']}")
        out("")
        out("用法：tokentracker-sync.py restore <序号>")
        out("")
        return
    r = core.restore_import_backup(which, log=out)
    out("")
    out(f"已回滚 {r['restored']}  ←  {r['from']}")
    out(f"当前数据已另存：{r['safety']}")
    out("")


def run_cli(argv):
    cmd = argv[0]
    args = argv[1:]
    if cmd in ("-h", "--help", "help"):
        print_help()
        return 0
    if cmd in ("-v", "--version"):
        from . import __version__

        out(f"TokenTracker Sync v{__version__}")
        return 0

    table = {
        "export": cmd_export,
        "import": cmd_import,
        "status": cmd_status,
        "doctor": cmd_doctor,
        "backup": cmd_backup,
        "backups": cmd_backups,
        "restore-backup": cmd_restore_backup,
        "restore": cmd_restore,
    }
    fn = table.get(cmd)
    if not fn:
        raise core.SyncError(f"未知命令：{cmd}（用 help 查看用法）")
    fn(args)
    return 0
