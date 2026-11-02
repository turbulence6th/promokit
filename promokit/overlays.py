"""Sahne katmanları: arka plan, dokunuş efektleri, başlık/altyazı, tuş çipleri.

Varsayılan görsel dil (`util.BRAND`): açık zemin #f6f8fc, koyu metin #0f1422, mavi→mor vurgu
gradyanı, Lexend. "Başlık + kısa gradyan çizgi" düzeni.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter, ImageMath

from .text import Fonts, render_text
from .util import bump, clamp, ease, lerp, mix, parse_color, rgb

SS = 4


# ── Arka plan ────────────────────────────────────────────────────────────────
THEMES = {
    "light": {"top": "#f8faff", "bottom": "#edf1f9", "glow1": ("#4f7cff", 0.075), "glow2": ("#8b5bff", 0.06),
              "text": "#0f1422", "muted": "#5b6678"},
    "dark": {"top": "#0d1119", "bottom": "#151b2b", "glow1": ("#4f7cff", 0.16), "glow2": ("#8b5bff", 0.13),
             "text": "#f1f4fa", "muted": "#9aa6bb"},
}


def _radial(W, H, cx, cy, R) -> Image.Image:
    """Merkezde 1, R uzaklıkta 0 olan yumuşak (kosinüs) F maskesi."""
    size = max(2, int(R * 2))
    # Pillow'un radial_gradient'i merkezde 0, iç teğet çemberde ~181 (köşede 255) — 181 = yarıçap
    g = Image.radial_gradient("L").resize((size, size), Image.Resampling.BILINEAR)
    canvas = Image.new("L", (W, H), 255)
    canvas.paste(g, (int(cx - R), int(cy - R)))
    return canvas.point(lambda v: int(255 * (0.5 + 0.5 * math.cos(math.pi * min(1.0, v / 181.0)))))


def _dither_noise(W: int, H: int, tile: int = 256) -> Image.Image:
    """Sabit tohumlu gürültü dokusu (döşenir). `Image.effect_noise` tohum almıyor → her Timeline
    farklı doku üretiyordu: paralel render'da her parçanın arka plan "tanesi" değişiyor (parça
    sınırında titreme) ve iki kareyi kıyaslayan testler anlamsızlaşıyordu (her yer "farklı")."""
    import random
    rnd = random.Random(20261019)
    t = Image.new("L", (tile, tile))
    t.putdata([max(0, min(255, int(rnd.gauss(128.0, 28.0)))) for _ in range(tile * tile)])
    out = Image.new("L", (W, H))
    for y in range(0, H, tile):
        for x in range(0, W, tile):
            out.paste(t, (x, y))
    return out


def background(W: int, H: int, theme: str = "light", color: str | None = None) -> Image.Image:
    """Hafif dikey gradyan + iki marka parıltısı; 8-bit kodlamada bant oluşmasın
    diye ±½ LSB gürültüyle titreştirilir (dithering)."""
    if color:
        return Image.new("RGB", (W, H), rgb(color))
    th = THEMES.get(theme, THEMES["light"])
    top, bot = rgb(th["top"]), rgb(th["bottom"])
    gy = Image.linear_gradient("L").resize((W, H), Image.Resampling.BILINEAR)
    m = max(W, H)
    g1 = _radial(W, H, W * 0.12, H * 0.05, m * 0.62)
    g2 = _radial(W, H, W * 0.92, H * 1.0, m * 0.58)
    c1, a1 = rgb(th["glow1"][0]), th["glow1"][1]
    c2, a2 = rgb(th["glow2"][0]), th["glow2"][1]
    noise = _dither_noise(W, H)  # ort. 128
    chans = []
    for i in range(3):
        ch = ImageMath.lambda_eval(
            lambda a: ((a["t"] + (a["b"] - a["t"]) * a["gy"] / 255.0) * (1 - a["k1"] * a["g1"] / 255.0 - a["k2"] * a["g2"] / 255.0)
                       + a["c1"] * a["k1"] * a["g1"] / 255.0 + a["c2"] * a["k2"] * a["g2"] / 255.0
                       + (a["n"] - 128.0) / 64.0 + 0.5),
            t=float(top[i]), b=float(bot[i]), gy=gy.convert("F"), g1=g1.convert("F"), g2=g2.convert("F"),
            k1=a1, k2=a2, c1=float(c1[i]), c2=float(c2[i]), n=noise.convert("F"))
        chans.append(ch.convert("L"))
    return Image.merge("RGB", chans)



def glow_background(W: int, H: int, cx: float, cy: float, R: float) -> Image.Image:
    """Reels kapağının zemini: koyu lacivert dikey gradyan + (cx, cy) çevresinde R yarıçaplı mavi parıltı (içeriğin
    arkasında). Mor YOK (kullanıcı, 2026-10-20: mor zemin "itici"). `background` gibi ±½ LSB titreştirilir."""
    top, bot, glow, k = rgb("#0a0f1f"), rgb("#141d3a"), rgb("#3557d8"), 0.55
    gy = Image.linear_gradient("L").resize((W, H), Image.Resampling.BILINEAR)
    g = _radial(W, H, cx, cy, R)
    noise = _dither_noise(W, H)
    chans = []
    for i in range(3):
        ch = ImageMath.lambda_eval(
            lambda a: ((a["t"] + (a["b"] - a["t"]) * a["gy"] / 255.0) * (1 - a["k"] * a["g"] / 255.0)
                       + a["c"] * a["k"] * a["g"] / 255.0 + (a["n"] - 128.0) / 64.0 + 0.5),
            t=float(top[i]), b=float(bot[i]), gy=gy.convert("F"), g=g.convert("F"), k=k, c=float(glow[i]),
            n=noise.convert("F"))
        chans.append(ch.convert("L"))
    return Image.merge("RGB", chans)


# ── Dokunuş efektleri ────────────────────────────────────────────────────────
def _ring(r: float, width: float, color, alpha: float, halo: float = 0.0) -> Image.Image:
    size = int(2 * (r + width + halo) + 6)
    S = SS
    im = Image.new("RGBA", (size * S, size * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = size * S / 2
    if halo > 0:
        rr = (r + width / 2 + halo / 2) * S
        d.ellipse((c - rr, c - rr, c + rr, c + rr), outline=(15, 20, 34, int(70 * alpha)), width=max(1, int(halo * S)))
    rr = (r + width / 2) * S
    d.ellipse((c - rr, c - rr, c + rr, c + rr), outline=tuple(color[:3]) + (int(255 * alpha),),
              width=max(1, int(width * S)))
    return im.reduce(S)


_DOTS: dict = {}


def _dot(r: float, alpha: float) -> Image.Image:
    """Dokunuş dairesi (el çizilmediğinde temas noktası): yarı saydam beyaz dolgu + ince koyu kenar +
    yumuşak gölge — açık sayfada da koyu ekranda (Mission Control, klavye) da seçilir."""
    key = (round(r * 2) / 2, round(alpha * 20))
    im = _DOTS.get(key)
    if im is not None:
        return im
    # Çizim de KUANTİZE değerlerle: önbelleği ilk dolduran r'yi kullanmak, aynı karede hangi parçanın
    # (paralel render) önce hangi r'yi gördüğüne göre farklı sprite üretiyordu (38↔39 px)
    r, a = key[0], key[1] / 20
    pad = int(r * 0.5) + 4
    size = int(2 * r) + 2 * pad
    S = SS
    sh = Image.new("L", (size, size), 0)
    c = size / 2
    ImageDraw.Draw(sh).ellipse((c - r, c - r + r * 0.12, c + r, c + r + r * 0.12), fill=int(80 * a))
    sh = sh.filter(ImageFilter.GaussianBlur(max(1.0, r * 0.22)))
    im = Image.new("RGBA", (size, size), (15, 20, 34, 0))
    im.putalpha(sh)
    top = Image.new("RGBA", (size * S, size * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(top)
    cs = size * S / 2
    d.ellipse((cs - r * S, cs - r * S, cs + r * S, cs + r * S), fill=(255, 255, 255, int(150 * a)),
              outline=(15, 20, 34, int(110 * a)), width=max(1, int(r * 0.08 * S)))
    im.alpha_composite(top.reduce(S))
    if len(_DOTS) > 256:
        _DOTS.clear()
    _DOTS[key] = im
    return im


def _paste_center(img, sprite, x, y):
    w, h = sprite.size
    img.paste(sprite, (int(round(x - w / 2)), int(round(y - h / 2))), sprite)


DOT_OUT = 0.18   # dokunuş dairesinin parmak kalktıktan sonra sönme süresi (sn)


def draw_touch_fx(img: Image.Image, contacts, t: float, to_stage, r0: float, dots: bool = False):
    """Dokunuş göstergesi: dokunulan yerde daire (el çizilmediğinde) + uzun basmada ürünün halkası.

    Başka süs YOK: temas anında yayılan halka ve kaydırma izi kaldırıldı — iz basamak basamak
    çiziliyordu (çentikli kule gibi), halkalar çok parmakta üst üste biniyordu (kullanıcı: "saçma
    sapan eklemeler olmasın", 2026-10-19). El çizilirse bu katmanın ÜSTÜNE çizilir.
    """
    for c in contacts:
        if c.on != "screen":
            continue
        # Uzun bas: üründe bir halka belirir, ~400 ms'de YEŞİLE döner ("tutuldu" — bırakınca sağ tık,
        # sürüklerse seçim/sürükleme).
        if c.t1 - c.t0 >= 0.4 and c.t0 + 0.12 <= t <= c.t1 + 0.2 and c.tool != "pen":
            p0 = c.pos(c.t0)
            still = all(math.dist(c.pos(c.t0 + k * 0.04), p0) < 0.012 for k in range(11))
            if still:
                x, y = to_stage(*c.pos(min(t, c.t1)))
                armed = t >= c.t0 + 0.4
                grow = ease((t - c.t0 - 0.12) / 0.28, "out")
                fade = 1.0 if t <= c.t1 else 1 - (t - c.t1) / 0.2
                col = (52, 199, 89) if armed else (255, 255, 255)
                _paste_center(img, _ring(r0 * (0.95 + 0.35 * grow), max(2.0, r0 * 0.16), col, 0.9 * fade, halo=r0 * 0.1),
                              x, y)
        if dots and c.t0 - 0.01 <= t <= c.t1 + DOT_OUT:
            # belirir (kısa basma çöküşüyle), parmağı izler, kalkınca hafif büyüyerek söner
            k_in = ease((t - c.t0) / 0.07, "out")
            k_out = 1.0 - ease((t - c.t1) / DOT_OUT, "out") if t > c.t1 else 1.0
            press = bump(t, c.t0, 0.05, 0.16)
            scale = (0.72 + 0.28 * k_in) * (1 - 0.08 * press) * (1 + 0.18 * (1 - k_out))
            r = r0 * (0.24 if c.tool == "pen" else 0.62) * scale
            x, y = to_stage(*c.pos(min(t, c.t1)))
            _paste_center(img, _dot(r, min(k_in, k_out) * (1.4 if c.tool == "pen" else 1.0)), x, y)


def accent_bar(w: int, h: int) -> Image.Image:
    S = SS
    m = Image.new("L", (w * S, h * S), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w * S - 1, h * S - 1), radius=h * S / 2, fill=255)
    m = m.reduce(S)
    g = Image.linear_gradient("L").rotate(90, expand=True).transpose(Image.Transpose.FLIP_LEFT_RIGHT).resize((w, h))
    col = Image.composite(Image.new("RGB", (w, h), rgb("accent2")), Image.new("RGB", (w, h), rgb("accent")), g)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(col, (0, 0), m)
    return out


def with_alpha(im: Image.Image, a: float) -> Image.Image:
    if a >= 0.999:
        return im
    out = im.copy()
    out.putalpha(im.getchannel("A").point(lambda v: int(v * clamp(a))))
    return out


def paste_rgba(img: Image.Image, sprite: Image.Image, x: float, y: float, alpha: float = 1.0):
    if alpha <= 0.001:
        return
    sp = with_alpha(sprite, alpha)
    img.paste(sp, (int(round(x)), int(round(y))), sp)


# ── Başlık / altyazı bloğu ───────────────────────────────────────────────────
class Caption:
    """Başlık + gradyan çizgi + (ops.) alt satır; sahne üst bandında ortalanır."""

    TITLE = 0.050    # min(W,H) oranı
    SUB = 0.026
    # 9:16 telefonda tam ekran izlenir: alt satır 16:9 oranıyla ~28 px kalıyor, okunmuyordu (inceleme, 2026-10-20)
    # → dikeyde başlık ×1,15, alt satır ×1,5 (bant da aynı oranla büyür, cihazlar binmez)
    TALL = (1.15, 1.5)

    @staticmethod
    def _k(W: int, H: int) -> tuple:
        return Caption.TALL if H >= 1.6 * W else (1.0, 1.0)

    def __init__(self, fonts: Fonts, W: int, H: int, title: str, subtitle: str = "", theme: str = "light",
                 size: float = 1.0, max_width: float | None = None):
        u = min(W, H)
        th = THEMES.get(theme, THEMES["light"])
        self.title_text, self.sub_text = title, subtitle
        kt, ks = Caption._k(W, H)
        ts, ss = u * self.TITLE * size * kt, u * self.SUB * size * ks
        self.title = render_text(fonts, title, ts, 700, th["text"], tracking=-0.02,
                                 max_width=max_width or W * 0.86) if title else None
        self.sub = render_text(fonts, subtitle, ss, 400, th["muted"],
                               max_width=max_width or W * 0.80) if subtitle else None
        self.tpad = int(ts * 0.25) + 2   # render_text'in kenar payı (taban hizası için korunuyor)
        self.spad = int(ss * 0.25) + 2
        self.bar = accent_bar(int(u * 0.075 * size), max(3, int(u * 0.0062 * size)))
        self.gap = u * 0.016 * size
        self.u = u
        self.size = size

    @staticmethod
    def band(W: int, H: int, size: float = 1.0) -> float:
        """Tek satırlık başlık + çizgi + alt satır bloğunun AYRILAN yüksekliği (en küçük bant; render,
        senaryodaki gerçek başlıkların `extent`iyle büyütür — sarılan başlık cihazların üstüne binmesin)."""
        u = min(W, H)
        kt, ks = Caption._k(W, H)
        return u * size * (Caption.TITLE * kt * 1.30 + 0.016 + 0.0062 + 0.016 * 1.2 + Caption.SUB * ks * 1.35)

    def extent(self) -> float:
        """Çizilen bloğun `top`tan alt satırın altına yüksekliği (sarılmış satırlar dahil)."""
        h = 0.0
        if self.title:
            h += self.title.size[1] - 2 * self.tpad
        h += self.gap + self.bar.size[1]
        if self.sub:
            h += self.gap * 1.2 + self.sub.size[1] - 2 * self.spad
        return h

    def draw(self, img: Image.Image, cx: float, top: float, t_in: float, t_out: float,
             alpha: float = 1.0, title_static: bool = False, sub_alpha: float | None = None, title_alpha: float = 1.0):
        """t_in: giriş ilerlemesi 0..1, t_out: çıkış ilerlemesi 0..1, alpha: dış saydamlık (geçiş).

        `title_static`: başlık önceki sahneyle aynı → başlık ve çizgi yerinde durur, yalnız alt satır
        girer. `sub_alpha`/`title_alpha`: geçişte iki parçayı ayrı karıştırmak için."""
        a = ease(t_in, "out") * (1 - ease(t_out, "in")) * clamp(alpha)
        a_title = (1.0 if title_static else a) * clamp(title_alpha)
        if title_static:
            t_in_title = 1.0
        else:
            t_in_title = t_in
        if max(a, a_title) <= 0.001:
            return
        rise = 0.0 if title_static else (1 - ease(t_in, "out")) * self.u * 0.018
        y = top + rise
        if self.title:
            paste_rgba(img, self.title, cx - self.title.size[0] / 2, y - self.tpad, a_title)
            y += self.title.size[1] - 2 * self.tpad
        y += self.gap
        bar_k = ease((t_in_title - 0.15) / 0.85, "out")
        if bar_k > 0.01:
            bw = max(1, int(self.bar.size[0] * bar_k))
            bar = self.bar.resize((bw, self.bar.size[1])) if bw != self.bar.size[0] else self.bar
            paste_rgba(img, bar, cx - bw / 2, y, a_title)
        y += self.bar.size[1]
        if self.sub:
            y += self.gap * 1.2
            sa = a * ease((t_in - 0.1) / 0.9, "out") if sub_alpha is None else a * clamp(sub_alpha)
            paste_rgba(img, self.sub, cx - self.sub.size[0] / 2, y - self.spad, sa)


# ── Tuş çipleri ──────────────────────────────────────────────────────────────
class KeyChips:
    def __init__(self, fonts: Fonts, u: float):
        self.fonts = fonts
        self.u = u
        self._cache = {}

    def sprite(self, label: str) -> Image.Image:
        im = self._cache.get(label)
        if im is None:
            parts = label.split()
            size = self.u * 0.030
            chips = []
            for p in parts:
                txt = render_text(self.fonts, p, size, 600, "#ffffff")
                tw, th = txt.size
                w = max(int(size * 1.9), tw + int(size * 0.5))
                h = int(size * 1.95)
                S = 3
                m = Image.new("L", (w * S, h * S), 0)
                ImageDraw.Draw(m).rounded_rectangle((0, 0, w * S - 1, h * S - 1), radius=size * 0.42 * S, fill=225)
                chip = Image.new("RGBA", (w, h), (15, 20, 34, 0))
                chip.putalpha(m.reduce(S))
                chip.alpha_composite(txt, (int((w - tw) / 2), int((h - th) / 2)))
                chips.append(chip)
            gap = int(size * 0.35)
            W = sum(c.size[0] for c in chips) + gap * (len(chips) - 1)
            H = max(c.size[1] for c in chips)
            im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            x = 0
            for c in chips:
                im.alpha_composite(c, (x, (H - c.size[1]) // 2))
                x += c.size[0] + gap
            self._cache[label] = im
        return im

    def draw(self, img: Image.Image, keys, t: float, cx: float, cy: float):
        live = [k for k in keys if k.t <= t <= k.t + k.dur]
        for i, k in enumerate(live[-1:]):  # aynı anda tek kombinasyon
            age = t - k.t
            a = ease(age / 0.18, "out") * (1 - ease((age - (k.dur - 0.25)) / 0.25, "in"))
            sp = self.sprite(k.label)
            sc = lerp(0.92, 1.0, ease(age / 0.22, "outback"))
            if abs(sc - 1) > 0.005:
                sp = sp.resize((max(1, int(sp.size[0] * sc)), max(1, int(sp.size[1] * sc))), Image.Resampling.BICUBIC)
            paste_rgba(img, sp, cx - sp.size[0] / 2, cy - sp.size[1] / 2, a)
