"""Yazı: Lexend (marka fontu) yükleme, harf aralığı (tracking), satır kaydırma.

Lexend sistemde değişken font olarak kurulu (`Lexend[wght].ttf`); ağırlık
`set_variation_by_axes` ile seçilir. ⌘ ⌥ ⇧ gibi tuş simgeleri Lexend'de YOK →
o karakterler sembol fontundan (SF / Apple Symbols) çizilir.
"""
from __future__ import annotations

import glob
import itertools
import os

from PIL import Image, ImageDraw, ImageFont

from .util import parse_color

_FONT_CANDIDATES = [
    "~/Library/Fonts/Lexend[wght].ttf",
    "/Library/Fonts/Lexend[wght].ttf",
    "~/Library/Fonts/Lexend-VariableFont_wght.ttf",
    "/Library/Fonts/Lexend-VariableFont_wght.ttf",
    "~/AppData/Local/Microsoft/Windows/Fonts/Lexend[wght].ttf",
    "C:/Windows/Fonts/Lexend[wght].ttf",
]
_FALLBACK_FONTS = [
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
_SYMBOL_FONTS = [
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Apple Symbols.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "C:/Windows/Fonts/seguisym.ttf",
]
# Lexend'de bulunmayan, sembol fontundan çizilecek karakterler.
SYMBOL_CHARS = set("⌘⌥⇧⌃⎋⏎⌫⌦⇥↩↵←→↑↓⏏⇪⌽␣•·…✓")


def _first_existing(paths):
    for p in paths:
        p = os.path.expanduser(p)
        if os.path.exists(p):
            return p
    return None


class Fonts:
    def __init__(self, path: str | None = None):
        if path:
            p = os.path.expanduser(path)
            if not os.path.exists(p):
                raise FileNotFoundError(f"font yok: {p}")
            self.path = p
        else:
            self.path = _first_existing(_FONT_CANDIDATES)
            if not self.path:
                # statik Lexend dosyaları da olabilir
                hits = sorted(glob.glob(os.path.expanduser("~/Library/Fonts/Lexend*.ttf")))
                self.path = hits[0] if hits else _first_existing(_FALLBACK_FONTS)
        self.symbol_path = _first_existing(_SYMBOL_FONTS) or self.path
        self._cache: dict = {}
        self.variable = False
        try:
            f = ImageFont.truetype(self.path, 20)
            self.variable = bool(f.get_variation_axes())
        except Exception:
            self.variable = False

    def get(self, size: float, weight: int = 600, symbol: bool = False):
        size = max(4, int(round(size)))
        key = (size, weight, symbol)
        f = self._cache.get(key)
        if f is None:
            f = ImageFont.truetype(self.symbol_path if symbol else self.path, size)
            if not symbol and self.variable:
                try:
                    f.set_variation_by_axes([weight])
                except Exception:
                    pass
            self._cache[key] = f
        return f


def _runs(text: str):
    """Metni (sembol mü, parça) koşularına böl."""
    runs = []
    for ch in text:
        sym = ch in SYMBOL_CHARS
        if runs and runs[-1][0] == sym:
            runs[-1][1] += ch
        else:
            runs.append([sym, ch])
    return runs


def text_width(fonts: Fonts, text: str, size: float, weight: int = 600, tracking: float = 0.0) -> float:
    w = 0.0
    for sym, part in _runs(text):
        f = fonts.get(size, weight, sym)
        w += f.getlength(part)
    if tracking and len(text) > 1:
        w += tracking * size * (len(text) - 1)
    return w


PHRASE_SEP = " · "               # bağımsız ifadeleri ayırır → satır sığmazsa ÖNCE buradan kırılır (nokta düşer)
GLUED = {"/", "·", "—", "–", "|"}  # tek başına ayraç satır BAŞLATMAZ: önceki kelimeye yapışır


def wrap(fonts: Fonts, text: str, size: float, weight: int, max_width: float | None, tracking: float = 0.0):
    """Satırlara böl: `\\n` kesin kırılır · sığmayan paragraf önce " · " ifade sınırlarından, ifade yine
    sığmazsa kelime kelime kırılır. Kelime-kelime tek başına "… Google Play" gibi bir kuyruk bırakıyordu."""
    fits = lambda s: text_width(fonts, s, size, weight, tracking) <= max_width   # noqa: E731
    lines = []
    for para in text.split("\n"):
        if max_width is None or fits(para):
            lines.append(para)
            continue
        cur = ""
        for seg in para.split(PHRASE_SEP):
            cand = seg if not cur else cur + PHRASE_SEP + seg
            if cur and not fits(cand):
                lines += _wrap_words(cur, fits)
                cur = seg
            else:
                cur = cand
        lines += _wrap_words(cur, fits)
    return lines


def balanced_wrap(fonts: Fonts, text: str, size: float, weight: int, max_width: float | None,
                  tracking: float = 0.0) -> str:
    """`wrap`la AYNI satır sayısı, ama satırlar dengeli: en geniş satırı en dar olan bölünüş seçilir — açgözlü
    kırılım tek kelimeyi alta bırakıyordu ("mikrofon yok / mu?" → "mikrofon / yok mu?"). Elle `\n` varsa dokunulmaz."""
    if max_width is None or "\n" in text:
        return text
    words = text.split(" ")
    n = min(len(wrap(fonts, text, size, weight, max_width, tracking)), len(words))
    if n < 2:
        return text
    best = None
    for cuts in itertools.combinations(range(1, len(words)), n - 1):
        parts = [" ".join(words[a:b]) for a, b in zip((0, *cuts), (*cuts, len(words)))]
        widest = max(text_width(fonts, p, size, weight, tracking) for p in parts)
        if widest <= max_width and (best is None or widest < best[0]):
            best = (widest, parts)
    return "\n".join(best[1]) if best else text


def _wrap_words(para: str, fits) -> list[str]:
    words = []
    for w in para.split(" "):
        if w in GLUED and words:
            words[-1] += " " + w
        else:
            words.append(w)
    out, cur = [], ""
    for w in words:
        cand = w if not cur else cur + " " + w
        if cur and not fits(cand):
            out.append(cur)
            cur = w
        else:
            cur = cand
    out.append(cur)
    return out


def render_text(fonts: Fonts, text: str, size: float, weight: int = 600, color="fg",
                tracking: float = 0.0, max_width: float | None = None, align: str = "center",
                line_height: float = 1.22, spans: list | None = None) -> Image.Image:
    """Metni sıkı kırpılmış RGBA görüntüye çiz.

    `spans`: tek satırlık çok-renkli metin için [(parça, renk, ağırlık), …] (wordmark).
    `tracking`: em cinsinden harf aralığı (ör. -0.04 = marka wordmark'ı).
    """
    if spans:
        lines = [spans]
    else:
        lines = [[(ln, color, weight)] for ln in wrap(fonts, text, size, weight, max_width, tracking)]
    f0 = fonts.get(size, weight)
    asc, desc = f0.getmetrics()
    lh = size * line_height
    widths = []
    for ln in lines:
        widths.append(sum(text_width(fonts, p, size, w, tracking) for p, _, w in ln)
                      + (tracking * size if tracking and len(ln) > 1 else 0) * (len(ln) - 1))
    pad = int(size * 0.25) + 2
    W = int(max(widths) if widths else 1) + 2 * pad
    H = int(lh * (len(lines) - 1) + asc + desc) + 2 * pad
    im = Image.new("RGBA", (max(1, W), max(1, H)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        lw = widths[i]
        if align == "center":
            x = pad + (W - 2 * pad - lw) / 2
        elif align == "right":
            x = W - pad - lw
        else:
            x = pad
        y = pad + asc + i * lh  # taban çizgisi
        for part, col, wgt in ln:
            fill = parse_color(col)
            for sym, run in _runs(part):
                f = fonts.get(size, wgt, sym)
                if tracking:
                    # Harf harf çizilir ama konum KERNING'Lİ önekten: i. harf = getlength(run[:i+1]) −
                    # getlength(harf) (+ aralık). Önceden harf genişlikleri toplanıyordu → ölçülen
                    # (kerning'li) genişlikten uzun çiziliyor, başlık sağa kayıyor, uç harf kırpılıyordu.
                    for i, ch in enumerate(run):
                        xi = x + f.getlength(run[:i + 1]) - f.getlength(ch) + tracking * size * i
                        d.text((xi, y), ch, font=f, fill=fill, anchor="ls")
                    x += f.getlength(run) + tracking * size * len(run)
                else:
                    d.text((x, y), run, font=f, fill=fill, anchor="ls")
                    x += f.getlength(run)
    # Pad bilerek korunur: aynı boyuttaki iki metnin taban çizgisi hizalı kalsın.
    return im
