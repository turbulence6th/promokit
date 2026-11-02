"""Koordinat ızgarası: bir ekran görüntüsü/video karesinin üstüne normalize ızgara çizer.

Kamera `at = [0.42, 0.31]` gibi ekran-normalize noktaları gözle okumak için.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from .media import Probe, StillSource, VideoSource
from .text import Fonts, render_text


def grid(media: str, t: float, out: str, max_w: int = 1800) -> str:
    pr = Probe(media)
    scale = min(1.0, max_w / pr.width)
    size = (int(pr.width * scale) // 2 * 2, int(pr.height * scale) // 2 * 2)
    if pr.kind == "image":
        im = StillSource(media, (0, 0, 1, 1), size).get(0).copy()
    else:
        src = VideoSource(pr, (0, 0, 1, 1), size, pr.fps or 30, max(0.0, t))
        im = src.get(max(0.0, t)).copy()
        src.close()
    W, H = im.size
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for k in range(1, 20):
        major = k % 2 == 0
        col = (255, 214, 10, 200) if major else (255, 214, 10, 90)
        w = 2 if major else 1
        d.line([(W * k / 20, 0), (W * k / 20, H)], fill=col, width=w)
        d.line([(0, H * k / 20), (W, H * k / 20)], fill=col, width=w)
    im = im.convert("RGBA")
    im.alpha_composite(ov)
    fonts = Fonts()
    for k in range(1, 10):
        lab = render_text(fonts, f"{k / 10:.1f}", max(12, H * 0.018), 700, "#ffd60a")
        im.alpha_composite(lab, (int(W * k / 10) - lab.size[0] // 2, 2))
        im.alpha_composite(lab, (2, int(H * k / 10) - lab.size[1] // 2))
    im.convert("RGB").save(out)
    return out
