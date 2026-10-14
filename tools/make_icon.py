# -*- coding: utf-8 -*-
"""生成软件图标：深色圆角方块 + 渐变 π + 光晕。

π 字形用几何图形绘制（圆角矩形拼），不依赖任何字体 ——
避免换台机器字体缺失时图标变成豆腐块。

输出：
  assets/icon.ico   多尺寸图标（16/24/32/48/64/128/256），供 exe 使用
  assets/icon.png   512px PNG，供网页 favicon 使用
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(_ROOT, "assets")

# 与应用同一套配色
C_BG_TOP = (18, 18, 34)
C_BG_BOT = (6, 6, 12)
C1 = (34, 211, 238)     # 青
C2 = (139, 92, 246)     # 紫
C3 = (244, 114, 182)    # 粉

SS = 2                  # 超采样倍数（2 倍即可获得平滑边缘，兼顾内存）
N = 1024                # 主尺寸
S = N * SS


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def grad_h(stops, w, h):
    """水平渐变图。stops = [(位置0..1, 颜色), ...]"""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for x in range(w):
        t = x / max(1, w - 1)
        # 找到所在区间
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                k = (t - p0) / max(1e-6, p1 - p0)
                d.line([(x, 0), (x, h)], fill=lerp(c0, c1, k))
                break
        else:
            d.line([(x, 0), (x, h)], fill=stops[-1][1])
    return img


def grad_v(top, bot, w, h):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=lerp(top, bot, y / max(1, h - 1)))
    return img


def radial(size, color, center, radius, strength=1.0):
    """平滑的径向光斑。

    早期版本用画同心圆的办法近似，结果出现明显的同心边纹；
    改成在 256×256 网格上逐像素算好衰减，再用 LANCZOS 放大 —— 完全平滑。
    """
    G = 256
    small = Image.new("L", (G, G), 0)
    px = small.load()
    cx, cy = center
    inv = 1.0 / max(1.0, radius)
    for y in range(G):
        dy = (y / (G - 1) * size - cy) * inv
        for x in range(G):
            dx = (x / (G - 1) * size - cx) * inv
            d = (dx * dx + dy * dy) ** 0.5
            if d < 1.0:
                t = 1.0 - d
                px[x, y] = int(255 * (t * t * (3 - 2 * t)) * strength)   # smoothstep
    small = small.resize((size, size), Image.LANCZOS)
    layer = Image.new("RGB", (size, size), color)
    return layer, small


def add_glow(base, color, center, radius, strength):
    layer, m = radial(base.width, color, center, radius, strength)
    from PIL import ImageChops
    return ImageChops.add(base, Image.composite(
        layer, Image.new("RGB", base.size, (0, 0, 0)), m))


def add(a, b):
    """两张 RGB 相加（加色混合）。"""
    from PIL import ImageChops
    return ImageChops.add(a, b)


def main():
    os.makedirs(OUT, exist_ok=True)

    # ---------------- 背景 ----------------
    bg = grad_v(C_BG_TOP, C_BG_BOT, S, S)

    # 角落彩光，呼应应用里的极光背景
    bg = add_glow(bg, (22, 78, 96), (int(S * 0.08), int(S * 0.10)), int(S * 0.72), 0.90)
    bg = add_glow(bg, (66, 38, 116), (int(S * 0.98), int(S * 0.06)), int(S * 0.74), 0.95)
    bg = add_glow(bg, (74, 26, 62), (int(S * 0.50), int(S * 1.06)), int(S * 0.78), 0.62)

    # 圆角遮罩
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.222), fill=255)
    bg = Image.composite(bg, Image.new("RGB", (S, S), (0, 0, 0)), mask)

    canvas = bg.convert("RGBA")

    # ---------------- π 字形 ----------------
    # 用一个独立的 L 通道当字形遮罩，再把渐变贴上去
    glyph = Image.new("L", (S, S), 0)
    gd = ImageDraw.Draw(glyph)

    u = S / 1024.0            # 以 1024 设计稿为基准换算

    def rr(x0, y0, x1, y1, r):
        gd.rounded_rectangle([x0 * u, y0 * u, x1 * u, y1 * u], radius=r * u, fill=255)

    # 字号放大到约占画布宽度的 67%，小尺寸下才看得清
    rr(168, 298, 856, 396, 49)      # 顶横
    rr(354, 366, 470, 744, 58)      # 左腿
    rr(554, 366, 670, 816, 58)      # 右腿（略长，更有动势）

    # 外发光：把字形放大模糊后叠在下面
    glow = glyph.filter(ImageFilter.GaussianBlur(int(40 * u)))
    gmask = glow.point(lambda v: int(v * 0.55))
    grad_glow = grad_h([(0.0, C1), (0.5, C2), (1.0, C3)], S, S).convert("RGBA")
    glow_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    glow_layer.paste(grad_glow, (0, 0), gmask)
    canvas = Image.alpha_composite(canvas, glow_layer)

    # 主体：渐变填充
    grad = grad_h([(0.0, C1), (0.46, C2), (1.0, C3)], S, S).convert("RGBA")
    body = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    body.paste(grad, (0, 0), glyph)

    # 顶部高光，让字更有立体感
    hi = Image.new("L", (S, S), 0)
    ImageDraw.Draw(hi).rounded_rectangle(
        [228 * u, 296 * u, 796 * u, 330 * u], radius=20 * u, fill=70)
    body.paste(Image.new("RGBA", (S, S), (255, 255, 255, 255)), (0, 0), hi)

    canvas = Image.alpha_composite(canvas, body)

    # ---------------- 内描边 ----------------
    edge = Image.new("L", (S, S), 0)
    ImageDraw.Draw(edge).rounded_rectangle(
        [3 * u, 3 * u, S - 3 * u, S - 3 * u], radius=int(S * 0.222), outline=255,
        width=max(1, int(3 * u)))
    canvas = Image.alpha_composite(
        canvas, Image.merge("RGBA", (
            Image.new("L", (S, S), 255), Image.new("L", (S, S), 255),
            Image.new("L", (S, S), 255), edge.point(lambda v: int(v * 0.16)))))

    # ---------------- 输出 ----------------
    master = canvas.resize((N, N), Image.LANCZOS)

    png = os.path.join(OUT, "icon.png")
    master.resize((512, 512), Image.LANCZOS).save(png, "PNG")
    print(f"已生成 {png}")

    ico = os.path.join(OUT, "icon.ico")
    master.resize((256, 256), Image.LANCZOS).save(
        ico, "ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"已生成 {ico}  ({os.path.getsize(ico) / 1024:.1f} KB)")

    # 放大预览图，方便肉眼检查
    prev = os.path.join(OUT, "icon_preview.png")
    tiles = [master.resize((s, s), Image.LANCZOS) for s in (256, 128, 64, 48, 32, 16)]
    W = sum(t.width for t in tiles) + 20 * (len(tiles) + 1)
    H = 256 + 40
    sheet = Image.new("RGBA", (W, H), (30, 30, 38, 255))
    x = 20
    for t in tiles:
        sheet.alpha_composite(t, (x, 20 + (256 - t.height)))
        x += t.width + 20
    sheet.save(prev, "PNG")
    print(f"已生成 {prev}（多尺寸预览）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
