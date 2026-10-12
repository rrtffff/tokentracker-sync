"""把发布用的文件收集到 release/ 目录，并生成 SHA256SUMS.txt。

用法：
    python packaging/prepare_release.py [版本号]

产出（release/ 目录）：
    TTSync.exe              图形界面版（双击即用）
    ttsync-cli.exe          命令行版
    SHA256SUMS.txt          校验值
    LICENSE                 MIT 许可证
    THIRD_PARTY_NOTICES.txt 第三方组件声明

把 release/ 里的文件逐个拖到 GitHub Release 的附件区即可。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, "dist")
OUT = os.path.join(ROOT, "release")

BINARIES = ["TTSync.exe", "ttsync-cli.exe"]
EXTRA = ["LICENSE", "THIRD_PARTY_NOTICES.txt"]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    version = sys.argv[1] if len(sys.argv) > 1 else ""
    missing = [b for b in BINARIES if not os.path.exists(os.path.join(DIST, b))]
    if missing:
        sys.exit(
            "找不到构建产物：" + ", ".join(missing) +
            "\n请先运行 packaging/build_windows.bat"
        )

    os.makedirs(OUT, exist_ok=True)
    copied = []
    for name in BINARIES:
        src = os.path.join(DIST, name)
        dst = os.path.join(OUT, name)
        shutil.copy2(src, dst)
        copied.append(name)
    for name in EXTRA:
        src = os.path.join(ROOT, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(OUT, name))
            copied.append(name)

    lines = []
    for name in copied:
        p = os.path.join(OUT, name)
        lines.append(f"{sha256(p)}  {name}")
    sums = os.path.join(OUT, "SHA256SUMS.txt")
    with open(sums, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n发布文件已收集到：{OUT}")
    print(f"版本：{version or '(未指定)'}\n")
    for name in copied:
        size = os.path.getsize(os.path.join(OUT, name))
        print(f"  {name:<26} {size / 1024:>9,.0f} KB")
    print(f"  {'SHA256SUMS.txt':<26}")
    print("\n下一步：把这 4~5 个文件拖到 GitHub Release 的附件区。")
    print("校验命令（Windows）：certutil -hashfile TTSync.exe SHA256")
    print()


if __name__ == "__main__":
    main()
