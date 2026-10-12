"""从 art/mascot.jpg 生成界面资源。

产出（全部为 Tk 8.6 可直接读取的 PNG，无需运行时依赖 Pillow）：
  assets/bg.png     窗口背景：柔和蓝色渐变 + 右侧角色（乘法混合，柔和融入）
  assets/mascot.png 单独的角色图（白底），用于白色卡片上
  assets/icon.png   64x64 头部裁切，用作窗口图标
  assets/icon.ico   多尺寸图标，打包 exe 用

用法：python packaging/make_assets.py
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageChops, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ART = os.path.join(ROOT, "art", "mascot.jpg")
OUT = os.path.join(ROOT, "assets")

# 窗口尺寸（与 gui.py 保持一致）
WIN_W, WIN_H = 980, 780

# 配色
WASH_TOP = (245, 249, 255)
WASH_BOT = (216, 231, 248)
LEFT_COL = 596  # 左侧 UI 区域宽度，右侧留给角色


def gradient(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        c = tuple(int(WASH_TOP[i] + (WASH_BOT[i] - WASH_TOP[i]) * t) for i in range(3))
        d.line([(0, y), (w, y)], fill=c)
    return img


def normalize_white(im: Image.Image, threshold: int = 246) -> Image.Image:
    """把接近纯白的像素压成纯白，避免在卡片上出现淡淡的灰边。"""
    g = im.convert("L")
    mask = g.point(lambda v: 255 if v >= threshold else 0)
    white = Image.new("RGB", im.size, (255, 255, 255))
    return Image.composite(white, im, mask)


def main():
    if not os.path.exists(ART):
        sys.exit(f"找不到素材：{ART}")

    os.makedirs(OUT, exist_ok=True)
    art = Image.open(ART).convert("RGB")
    aw, ah = art.size
    ratio = aw / ah
    print(f"素材 {aw}x{ah}  宽高比 {ratio:.3f}")

    # ---------------- 背景：渐变 + 右侧角色（乘法混合） ----------------
    bg = gradient(WIN_W, WIN_H)
    right_w = WIN_W - LEFT_COL
    ch = int(WIN_H * 0.94)                       # 角色高度
    cw = int(ch * ratio)
    if cw > right_w - 20:                        # 保证不压到左侧 UI
        cw = right_w - 20
        ch = int(cw / ratio)
    char = art.resize((cw, ch), Image.LANCZOS)
    x0 = LEFT_COL + (right_w - cw) // 2
    y0 = (WIN_H - ch) // 2

    region = bg.crop((x0, y0, x0 + cw, y0 + ch))
    blended = Image.blend(region, ImageChops.multiply(region, char), 0.92)
    bg.paste(blended, (x0, y0))
    bg.save(os.path.join(OUT, "bg.png"), optimize=True)
    print(f"assets/bg.png         {bg.size}  角色置于 x={x0} y={y0} {cw}x{ch}")

    # ---------------- 单独角色图（白底） ----------------
    mh = 120
    mw = int(mh * ratio)
    mascot = normalize_white(art.resize((mw, mh), Image.LANCZOS))
    mascot.save(os.path.join(OUT, "mascot.png"), optimize=True)
    print(f"assets/mascot.png     {mascot.size}")

    # ---------------- 图标：头部裁切 ----------------
    head = art.crop((int(aw * 0.30), int(ah * 0.13), int(aw * 0.72), int(ah * 0.55)))
    head = normalize_white(head)

    side = max(head.size)
    canvas = Image.new("RGB", (side, side), (255, 255, 255))
    canvas.paste(head, ((side - head.width) // 2, (side - head.height) // 2))
    icon = canvas.resize((64, 64), Image.LANCZOS)
    icon.save(os.path.join(OUT, "icon.png"), optimize=True)
    print(f"assets/icon.png       {icon.size}")

    canvas.resize((256, 256), Image.LANCZOS).save(
        os.path.join(OUT, "icon.ico"),
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("assets/icon.ico       多尺寸 (16/32/48/64/128/256)")


if __name__ == "__main__":
    main()
