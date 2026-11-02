"""Kalem tutan GERÇEK el: fotoğraftan dekupe edilmiş sprite (assets/hands/pen-hand.png).

Kalem pozunda dokunuş düzeneğinin yerine bu çizilir. Elle çizilen iki yazı eli denemesi reddedildi
("gerçekte böyle bir tutuş yok", "uzaylı eli gibi") → gerçek tutuşu taklit etmek yerine gerçeğini
kullanıyoruz. Kaynak ve dekupe adımları: assets/hands/build_pen_hand.py.

Sprite katı (rigid) taşınır: kalem ucu (meta "tip") daima temas noktasına oturur; ölçek dokunuş
elinin fw'siyle aynı (meta "fw" = fotoğraftaki işaret parmağı genişliği), açı ekranın neresine
uzanıldığına göre birkaç derece döner. Gölge alfadan üretilir ve yalnız cihaz yüzeyine düşer.
"""
from __future__ import annotations

import json
import math
import os

from PIL import Image, ImageChops, ImageFilter

ASSET = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "hands", "pen-hand")
REACH_DEG = 6.0        # ekranın solunda kol daha uzanır → el biraz saat yönünün tersine döner
_base = None
_cache: dict = {}


def _load():
    global _base
    if _base is None:
        with open(ASSET + ".json", encoding="utf-8") as f:
            meta = json.load(f)
        im = Image.open(ASSET + ".png").convert("RGBA").convert("RGBa")   # önçarpımlı: kenarda saçak yok
        _base = (im, meta)
    return _base


def _sprite(k: float, deg: float):
    """Ölçeklenmiş + döndürülmüş sprite ve uç noktasının sprite içindeki konumu."""
    key = (round(k, 3), round(deg * 4) / 4)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    k = key[0]  # çizim de kuantize ölçekle: sprite, önbelleği ilk dolduran çağrıya bağlı olmasın
    im, meta = _load()
    w, h = max(2, int(round(im.size[0] * k))), max(2, int(round(im.size[1] * k)))
    sm = im.resize((w, h), Image.Resampling.LANCZOS)
    tx, ty = meta["tip"][0] * k, meta["tip"][1] * k
    rd = sm.rotate(key[1], resample=Image.Resampling.BICUBIC, expand=True)
    a = math.radians(key[1])
    cx, cy = w / 2, h / 2
    dx, dy = tx - cx, ty - cy
    # PIL rotate: pozitif açı = görüntüde saat yönünün TERSİ (y aşağı) → (dx, dy) buna göre döner
    rx = dx * math.cos(a) + dy * math.sin(a)
    ry = -dx * math.sin(a) + dy * math.cos(a)
    tip = (rd.size[0] / 2 + rx, rd.size[1] / 2 + ry)
    out = (rd.convert("RGBA"), tip)
    if len(_cache) > 48:
        _cache.clear()
    _cache[key] = out
    return out


def place(T, s: float, reach: float):
    """(sprite, sol üst köşe) — sprite'ın kalem ucu `T`'ye oturur."""
    _, meta = _load()
    spr, tip = _sprite(s / float(meta["fw"]), REACH_DEG * -reach)
    return spr, (int(round(T[0] - tip[0])), int(round(T[1] - tip[1]))), tip


def draw(img: Image.Image, T, s: float, reach: float, receiver=None, vis: float = 1.0, lift: float = 0.0,
         shadow: float = 1.0):
    """Eli kalem ucu `T`'de olacak şekilde çiz. `s` = sahnedeki fw (px), `reach` = ekranın
    ortasına göre yatay konum (−0,5…0,5)."""
    if vis <= 0.002:
        return
    spr, (X, Y), _ = place(T, s, reach)
    a = spr.getchannel("A")
    if shadow > 0:
        off = (0.22 + 1.4 * lift) * s
        blur = (0.30 + 1.1 * lift) * s
        k = 4
        sm = a.resize((max(1, a.size[0] // k), max(1, a.size[1] // k)), Image.Resampling.BOX)
        sm = sm.filter(ImageFilter.GaussianBlur(max(0.6, blur / k))).resize(a.size, Image.Resampling.BILINEAR)
        al = (0.36 - 0.12 * lift) * vis * shadow
        sm = sm.point(lambda v: int(v * al))
        sx, sy = X + int(off * 0.5), Y + int(off * 0.9)
        if receiver is not None:
            sm = ImageChops.multiply(sm, receiver.crop((sx, sy, sx + sm.size[0], sy + sm.size[1])))
        img.paste((14, 18, 30), (sx, sy), sm)
    if vis < 0.999:
        a = a.point(lambda v: int(v * vis))
    img.paste(spr.convert("RGB"), (X, Y), a)
