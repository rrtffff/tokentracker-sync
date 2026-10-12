#!/usr/bin/env python3
"""
TokenTracker Sync —— TokenTracker 跨机器用量合并 / 备份 / 还原（非官方小工具）

入口规则：
  · 不带参数            → 打开图形界面
  · 第一个参数是 .json  → 打开图形界面并直接导入该数据包（= 把文件拖到本程序图标上）
  · 其他参数            → 走命令行（help 查看用法）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ttsync import core  # noqa: E402


def _fix_console():
    """Windows 控制台默认 GBK，中文会乱码；尽量切成 UTF-8。"""
    try:
        if sys.platform == "win32":
            os.system("chcp 65001 >nul 2>nul")
        for stream in (sys.stdout, sys.stderr):
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main(argv):
    # --noconsole 打包后 stdout/stderr 为 None，补上空设备以免库代码 print 崩溃
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    if not argv:
        from ttsync import gui

        gui.run()
        return 0

    first = core.normalize_path_input(argv[0])
    if len(argv) == 1 and first.lower().endswith(".json") and os.path.exists(first):
        from ttsync import gui

        gui.run(first)
        return 0

    if argv[0] in ("gui", "--gui"):
        from ttsync import gui

        gui.run(argv[1] if len(argv) > 1 else None)
        return 0

    _fix_console()
    from ttsync import cli

    try:
        return cli.run_cli(argv)
    except core.SyncError as e:
        sys.stderr.write(f"\n[错误] {e}\n")
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # 兜底，仍给出可读信息
        sys.stderr.write(f"\n[出错了] {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
