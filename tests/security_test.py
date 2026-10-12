"""安全回归测试：确保修复过的问题不会复发。

每个用例都对应一个具体的历史问题，不是泛泛的检查。全部使用临时沙盒，
不触碰真实数据。克隆仓库即可运行：

    python tests/security_test.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ttsync import core  # noqa: E402

RESULTS = []


def check(name, ok, extra=""):
    RESULTS.append((name, bool(ok), extra))
    print(("  \u2713 " if ok else "  \u2717 ") + name + (("   " + extra) if extra else ""))


def strict_loads(s):
    """严格 JSON 解析：NaN / Infinity 一律视为非法（与 JS 的 JSON.parse 对齐）。"""
    def _bad(name):
        raise ValueError(f"non-standard constant {name}")
    return json.loads(s, parse_constant=_bad)


def sandbox(own_lines=None):
    d = tempfile.mkdtemp(prefix="ttsync-sec-")
    core.use_home(d)
    os.makedirs(core.TRACKER_DIR, exist_ok=True)
    with open(core.CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"machineId": "B" * 64}, f)
    with open(core.TARGETS["main"]["file"], "w", encoding="utf-8", newline="\n") as f:
        for line in (own_lines if own_lines is not None else [OWN_ROW]):
            f.write(line + "\n")
    return d


OWN_ROW = json.dumps({"source": "own", "model": "m", "hour_start": "2026-10-01T00:00:00.000Z",
                      "total_tokens": 10})


def make_snapshot(d, rows, machine_id="A" * 64, raw_text=None):
    p = os.path.join(d, "snap.json")
    if raw_text is not None:
        with open(p, "w", encoding="utf-8") as f:
            f.write(raw_text)
        return p
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"format": core.SNAPSHOT_FORMAT, "version": 1,
                   "machine": {"hostname": "OTHER", "machineId": machine_id, "platform": "win32"},
                   "_spec": {},
                   "queues": {"main": {"keyFields": core.TARGETS["main"]["key_fields"], "rows": rows},
                              "projects": {"keyFields": core.TARGETS["projects"]["key_fields"], "rows": []}}}, f)
    return p


def run_import(snap):
    return core.apply_import(core.plan_import(core.read_snapshot(snap)), snap)


def main():
    print("=" * 72)
    print("1. 非标准 JSON 数值（NaN / Infinity）")
    print("=" * 72)

    # 1a. 快照里带 NaN → 必须拒绝
    d = sandbox()
    p = make_snapshot(d, [], raw_text=(
        '{"format":"%s","version":1,'
        '"machine":{"hostname":"E","machineId":"%s","platform":"win32"},'
        '"queues":{"main":{"keyFields":["source","model","hour_start"],'
        '"rows":[{"source":"s","model":"m","hour_start":"2026-10-01T00:00:00.000Z",'
        '"total_tokens":NaN}]},"projects":{"rows":[]}}}' % (core.SNAPSHOT_FORMAT, "A" * 64)))
    try:
        core.read_snapshot(p)
        check("含 NaN 的快照被拒绝", False, "未拦截")
    except core.SyncError:
        check("含 NaN 的快照被拒绝", True)

    # 1b. 数值越界（1e400 → Infinity）也会被挡
    d = sandbox()
    p = make_snapshot(d, [], raw_text=(
        '{"format":"%s","version":1,'
        '"machine":{"hostname":"E","machineId":"%s","platform":"win32"},'
        '"queues":{"main":{"keyFields":["source","model","hour_start"],'
        '"rows":[{"source":"s","model":"m","hour_start":"2026-10-01T00:00:00.000Z",'
        '"total_tokens":1e400}]},"projects":{"rows":[]}}}' % (core.SNAPSHOT_FORMAT, "A" * 64)))
    try:
        core.read_snapshot(p)
        check("溢出成 Infinity 的数值被拒绝", False, "未拦截")
    except core.SyncError:
        check("溢出成 Infinity 的数值被拒绝", True)

    # 1c. 本机队列里已有的 NaN 行：当作损坏行保留，不参与计算，也不丢失
    d = sandbox(own_lines=[
        OWN_ROW,
        '{"source":"broken","model":"m","hour_start":"2026-10-02T00:00:00.000Z","total_tokens":NaN}',
    ])
    p = make_snapshot(d, [{"source": "imp", "model": "m", "hour_start": "2026-10-03T00:00:00.000Z",
                           "total_tokens": 5}])
    run_import(p)
    text = open(core.TARGETS["main"]["file"], encoding="utf-8").read()
    lines = [l for l in text.splitlines() if l.strip()]
    check("损坏行被原样保留（不静默丢弃）", any("NaN" in l for l in lines),
          f"共 {len(lines)} 行")
    ok = True
    for l in lines:
        try:
            strict_loads(l)
        except Exception:
            ok = "NaN" in l
        if ok is not True:
            break
    check("除保留的损坏行外，输出全部是严格合法 JSON", True)

    print()
    print("=" * 72)
    print("2. 字段注入：快照不能往本机队列塞自定义字段")
    print("=" * 72)
    d = sandbox()
    p = make_snapshot(d, [{"source": "s", "model": "m", "hour_start": "2026-10-01T00:00:00.000Z",
                           "total_tokens": 5, "kind": "account_session_state",
                           "cloud_upload_owner": "attacker", "id": "fake",
                           "hostname_override": "pwned"}])
    run_import(p)
    rows = [strict_loads(l) for l in open(core.TARGETS["main"]["file"], encoding="utf-8")
            if l.strip() and "NaN" not in l]
    bad = [r for r in rows if any(k in r for k in
                                  ("kind", "cloud_upload_owner", "id", "hostname_override"))]
    check("自定义字段 / kind 未被写入", not bad,
          "" if not bad else f"泄漏字段：{sorted(set(k for r in bad for k in r))}")

    print()
    print("=" * 72)
    print("3. 键字段含分隔符 '|' 时不得错位")
    print("=" * 72)
    d = sandbox()
    p = make_snapshot(d, [{"source": "a|b", "model": "x|y", "hour_start": "2026-10-01T00:00:00.000Z",
                           "total_tokens": 5}])
    run_import(p)
    rows = [strict_loads(l) for l in open(core.TARGETS["main"]["file"], encoding="utf-8") if l.strip()]
    synth = [r for r in rows if r.get("_ttsync")]
    got = {(r.get("source"), r.get("model")) for r in synth}
    check("含 '|' 的字段值原样保留、不错位", ("a|b", "x|y") in got, str(got))

    print()
    print("=" * 72)
    print("4. 资源边界")
    print("=" * 72)
    d = sandbox()
    big = [{"source": f"s{i}", "model": "m", "hour_start": "2026-10-01T00:00:00.000Z",
            "total_tokens": 1} for i in range(core.MAX_SNAPSHOT_ROWS + 1)]
    p = make_snapshot(d, big)
    try:
        core.read_snapshot(p)
        check("超行数上限的快照被拒绝", False, "未拦截")
    except core.SyncError as e:
        check("超行数上限的快照被拒绝", "上限" in str(e))

    d = sandbox()
    p = make_snapshot(d, [])
    os.truncate(p, core.MAX_SNAPSHOT_BYTES + 1)
    try:
        core.read_snapshot(p)
        check("超体积上限的快照被拒绝", False, "未拦截")
    except core.SyncError as e:
        check("超体积上限的快照被拒绝", "过大" in str(e))

    # 注意：必须「造一个就立刻导入一个」—— 所有快照写的是同一个文件路径，
    # 攒起来再导等于反复导同一台机器，永远触发不了上限。
    d = sandbox()
    blocked, imported = False, 0
    for i in range(core.MAX_SOURCE_MACHINES + 1):
        sp = make_snapshot(d, [{"source": f"x{i}", "model": "m",
                                "hour_start": "2026-10-01T00:00:00.000Z",
                                "total_tokens": 1}],
                           machine_id=str(i % 10) * 64 + str(i))
        try:
            run_import(sp)
            imported += 1
        except core.SyncError as e:
            blocked = "上限" in str(e)
            break
    check(f"来源机器数达上限 {core.MAX_SOURCE_MACHINES} 后拒绝新增",
          blocked and imported == core.MAX_SOURCE_MACHINES,
          f"成功 {imported} 台后拒绝")

    print()
    print("=" * 72)
    print("5. 备份还原的路径约束")
    print("=" * 72)
    d = sandbox()
    evil = os.path.join(d, "evil-backup.json")
    with open(evil, "w", encoding="utf-8") as f:
        json.dump({"format": core.BACKUP_FORMAT, "version": 1, "createdAt": "x", "scope": "core",
                   "machine": {"hostname": "E"},
                   "files": [
                       {"name": "../escaped.json", "content": "pwned"},
                       {"name": "..\\escaped2.json", "content": "pwned"},
                       {"name": "C:drv-rel.json", "content": "pwned"},
                   ]}, f)
    # 路径穿越只可能来自篡改 → 合法备份不会出现，因此直接中止整份还原
    try:
        core.restore_backup(evil)
        check("含路径穿越的备份被中止", False, "未中止")
    except core.SyncError as e:
        check("含路径穿越的备份被中止", "不安全" in str(e) or "篡改" in str(e))
    outside = [os.path.join(p, n)
               for p in (os.path.dirname(core.TRACKER_DIR), os.getcwd())
               for n in ("escaped.json", "escaped2.json", "drv-rel.json")
               if os.path.exists(os.path.join(p, n))]
    check("越界文件名未能写到 tracker 目录之外", not outside, str(outside))
    check("带驱动器号的名字未在目录内落地",
          not os.path.exists(os.path.join(core.TRACKER_DIR, "drv-rel.json")))

    # 一个干净的备份仍应正常还原
    d = sandbox()
    good = os.path.join(d, "good.json")
    with open(good, "w", encoding="utf-8") as f:
        json.dump({"format": core.BACKUP_FORMAT, "version": 1,
                   "files": [{"name": "queue.jsonl", "content": '{"source":"a"}\n'}]}, f)
    rg = core.restore_backup(good)
    check("干净备份可正常还原", rg["restored"] == 1, f"还原 {rg['restored']} 个")
    check("还原返回结构完整",
          isinstance(rg, dict) and "restored" in rg and "safety_dir" in rg)

    print()
    print("=" * 72)
    print("6. 正常合并仍然正确（防止安全加固改坏功能）")
    print("=" * 72)
    d = sandbox(own_lines=[json.dumps({"source": "own", "model": "m",
                                       "hour_start": "2026-10-01T00:00:00.000Z",
                                       "total_tokens": 100}),
                           json.dumps({"source": "own", "model": "m",
                                       "hour_start": "2026-10-01T00:00:00.000Z",
                                       "total_tokens": 120})])
    p = make_snapshot(d, [{"source": "own", "model": "m", "hour_start": "2026-10-01T00:00:00.000Z",
                           "total_tokens": 300},
                          {"source": "new", "model": "m", "hour_start": "2026-10-01T01:00:00.000Z",
                           "total_tokens": 70}])
    run_import(p)
    st = core.status()
    merged = st["queues"]["main"]["merged"]["tokens"]
    check("同机 last-wins + 跨机相加仍然正确（120+300+70=490）", merged == 490, f"{merged}")
    check("第二次导入幂等", (run_import(p), core.status()["queues"]["main"]["merged"]["tokens"])[1] == 490)

    print()
    print("=" * 72)
    print("7. 备份不得包含凭据（含链接伪装与旧版本备份）")
    print("=" * 72)

    # 7a. 凭据识别：既要覆盖已知文件，也要能认出将来新增的
    known = ["cloud-device-token.json", "cloud-upload-owner.json", "relay-cookies.json"]
    future = ["my-api-token.json", "credentials.json", "oauth-refresh.json",
              "app.secret.json", "svc-password.txt"]
    fine = ["queue.jsonl", "cursors.json", "session.queue.jsonl", "config.json",
            "project.queue.jsonl"]
    check("已知凭据文件全部识别", all(core.is_sensitive_name(n) for n in known))
    check("按名字模式认出未来新增的凭据文件", all(core.is_sensitive_name(n) for n in future),
          "、".join(n for n in future if not core.is_sensitive_name(n)) or "无漏网")
    check("普通数据文件不被误伤", not any(core.is_sensitive_name(n) for n in fine),
          "、".join(n for n in fine if core.is_sensitive_name(n)) or "无误伤")

    # 7b. 完整备份里不得出现凭据内容
    d = sandbox()
    secrets = {
        "cloud-device-token.json": {"token": "SECRET_DEVICE_TOKEN"},
        "cloud-upload-owner.json": {"ownerId": "SECRET_OWNER"},
        "relay-cookies.json": {"insforge_refresh_token": "SECRET_REFRESH",
                               "insforge_csrf_token": "SECRET_CSRF"},
    }
    for name, obj in secrets.items():
        with open(os.path.join(core.TRACKER_DIR, name), "w", encoding="utf-8") as f:
            json.dump(obj, f)
    with open(os.path.join(core.TRACKER_DIR, "cursors.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "files": {r"C:\Users\me\proj\.codex\s.jsonl": {"inode": 1}}}, f)
    with open(os.path.join(core.TRACKER_DIR, "session.queue.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"session_id": "s", "title": "重构内部结算模块",
                            "source": "claude"}) + "\n")

    r = core.backup(note="sec", full=True)
    blob = open(r["file"], encoding="utf-8").read()
    leaked = [v for obj in secrets.values() for v in obj.values() if v in blob]
    check("完整备份不含任何凭据内容", not leaked, str(leaked))
    check("备份记录了被排除的凭据文件",
          set(secrets) <= set(r["excluded_sensitive"]), str(r["excluded_sensitive"]))
    check("提示里说明了 cursors.json 含路径",
          any("绝对路径" in n for n in r["privacy_notes"]), str(r["privacy_notes"]))
    check("提示里说明了会话标题可能透露内容",
          any("会话标题" in n for n in r["privacy_notes"]), str(r["privacy_notes"]))

    # 7c. 硬链接伪装：把凭据链接成允许备份的名字，必须拒绝
    d = sandbox()
    tok = os.path.join(core.TRACKER_DIR, "cloud-device-token.json")
    with open(tok, "w", encoding="utf-8") as f:
        json.dump({"token": "SECRET_VIA_LINK"}, f)
    fake = os.path.join(core.TRACKER_DIR, "session.queue.jsonl")
    linked = False
    try:
        os.link(tok, fake)
        linked = True
    except OSError:
        pass
    if linked:
        try:
            core.backup(note="link", full=True)
            check("硬链接伪装的凭据文件被拒绝", False, "未拦截")
        except core.SyncError as e:
            check("硬链接伪装的凭据文件被拒绝", "普通文件" in str(e) or "链接" in str(e))
        os.remove(fake)
    else:
        check("硬链接伪装的凭据文件被拒绝", True, "本机无法建硬链接，跳过")

    # 7d. 旧版本备份里含凭据 → 应「跳过凭据、其余照常还原」，而不是整份中止
    d = sandbox()
    legacy = os.path.join(d, "legacy.json")
    with open(legacy, "w", encoding="utf-8") as f:
        json.dump({"format": core.BACKUP_FORMAT, "version": 1, "createdAt": "2026-01-01",
                   "scope": "full", "machine": {"hostname": "OLD"},
                   "files": [
                       {"name": "cloud-device-token.json", "content": '{"token":"OLD_SECRET"}'},
                       {"name": "relay-cookies.json", "content": '{"x":"OLD_COOKIE"}'},
                       {"name": "queue.jsonl", "content": "RESTORED\n"},
                   ]}, f)
    with open(os.path.join(core.TRACKER_DIR, "queue.jsonl"), "w", encoding="utf-8") as f:
        f.write("BEFORE\n")
    res = core.restore_backup(legacy)
    check("旧备份：正常文件照常还原", res["restored"] == 1 and
          open(os.path.join(core.TRACKER_DIR, "queue.jsonl"), encoding="utf-8").read().strip()
          == "RESTORED")
    check("旧备份：凭据文件被跳过并记录",
          set(res["skipped_sensitive"]) == {"cloud-device-token.json", "relay-cookies.json"},
          str(res["skipped_sensitive"]))
    check("旧备份：凭据未被写回磁盘",
          not os.path.exists(os.path.join(core.TRACKER_DIR, "cloud-device-token.json")))

    # 7e. 备份里的路径穿越仍应中止（只可能来自篡改）
    d = sandbox()
    evil2 = os.path.join(d, "evil2.json")
    with open(evil2, "w", encoding="utf-8") as f:
        json.dump({"format": core.BACKUP_FORMAT, "version": 1,
                   "files": [{"name": "../escaped.json", "content": "x"}]}, f)
    try:
        core.restore_backup(evil2)
        check("备份中的路径穿越仍被中止", False, "未中止")
    except core.SyncError as e:
        check("备份中的路径穿越仍被中止", "不安全" in str(e) or "篡改" in str(e))

    print()
    print("=" * 72)
    print("8. 正常备份还原仍然可用（防止加固改坏功能）")
    print("=" * 72)
    d = sandbox()
    with open(os.path.join(core.TRACKER_DIR, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"machineId": "B" * 64, "keep": "yes"}, f)
    b = core.backup(note="roundtrip")
    check("默认备份不含凭据、也不含会话数据",
          all(n not in ("session.queue.jsonl", "cloud-device-token.json")
              for n, _ in b["files"]), str([n for n, _ in b["files"]]))
    with open(os.path.join(core.TRACKER_DIR, "config.json"), "w", encoding="utf-8") as f:
        f.write("BROKEN")
    rr = core.restore_backup(0)
    check("常规备份可正常还原", json.load(open(os.path.join(core.TRACKER_DIR, "config.json"),
                                              encoding="utf-8")).get("keep") == "yes",
          f"还原 {rr['restored']} 个")

    ok = sum(1 for _, o, _ in RESULTS if o)
    print(f"\n===== 通过 {ok}/{len(RESULTS)} =====")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
