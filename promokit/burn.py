"""Altyazıyı görüntüye YAKMA — `promo.py burn VIDEO [--lang tr]`.

Kullanıcı (2026-10-22): Instagram Reels altyazı dosyası kabul etmiyor; videolar İngilizce seslendirilir, Türkçe
izleyici için çeviri (`render`ın yanına yazdığı `video.tr.srt`) görüntüye basılır. YouTube'a yakılmamış video +
SRT gider (izleyen dilini seçer).

⛔ Bu makinedeki ffmpeg'de `drawtext` / `libass` YOK (yalnız `--enable-libx264 --enable-videotoolbox`) → her
altyazı satırı PIL ile şeffaf PNG'ye çizilir (sitenin/videonun Lexend'i), ffmpeg `overlay` ile KENDİ zaman
aralığında (`enable='between(t,…)'`) bindirilir. Ses akışı kopyalanır, görüntü bir kez yeniden kodlanır.

Yerleşim: 9:16'da platformun alt şeridinin (Reels'in yazı/kullanıcı adı/düğme alanı — `layout.safe_bottom`)
ÜSTÜNDE, yatayda ortalı; diğer en-boylarda kadrajın altına yakın. Görünüm: koyu yarı saydam kutuda beyaz yazı —
videoların zemini çoğunlukla açık (beyaz kart, açık kapanış), gölgeli beyaz yazı orada okunmuyordu.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

from PIL import Image, ImageDraw

from .layout import safe_bottom, tall
from .text import Fonts, render_text
from .util import PromoError, ffmpeg_bin, ffprobe_bin

PILL = (15, 20, 34, 200)        # koyu, ≈ %78 örtücü — açık ve koyu zeminde okunur
TEXT = "#ffffff"
_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})")


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


def _sec(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_srt(text: str) -> list:
    """SRT → [Cue]. Bir bloğun metin satırları BİRLEŞTİRİLİR: yakarken çerçeve genişliğine göre yeniden kırılır
    (SRT'nin 42 karakterlik kırılımı yayın içindi, dikey kadrajın genişliğine göre değil)."""
    blocks = [b for b in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()) if b.strip()]
    cues = []
    for b in blocks:
        lines = [ln.strip() for ln in b.split("\n") if ln.strip()]
        k = 1 if lines and lines[0].isdigit() else 0
        m = _TIME.search(lines[k]) if len(lines) > k else None
        if not m or len(lines) <= k + 1:
            raise PromoError(f"altyazı bloğu okunamadı: {b[:60]!r}")
        g = m.groups()
        cues.append(Cue(_sec(*g[:4]), _sec(*g[4:]), " ".join(lines[k + 1:])))
    if not cues:
        raise PromoError("altyazı dosyası boş")
    return cues


def _size(W: int, H: int) -> float:
    return H * 0.024 if tall(W, H) else H * 0.044


def cue_image(fonts: Fonts, text: str, W: int, H: int) -> Image.Image:
    """Bir altyazı satırı: koyu yuvarlak kutu + beyaz yazı (RGBA, sıkı). Uzun satır çerçeve genişliğine göre kırılır."""
    size = _size(W, H)
    padx, pady = size * 0.55, size * 0.22
    txt = render_text(fonts, text, size, 600, TEXT, max_width=W * 0.84 - 2 * padx, line_height=1.25)
    w, h = int(txt.width + 2 * padx), int(txt.height + 2 * pady)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, w - 1, h - 1), radius=size * 0.5, fill=PILL)
    im.alpha_composite(txt, (int(padx), int(pady)))
    return im


def placement(W: int, H: int, w: int, h: int) -> tuple:
    """Kutunun sol üst köşesi: yatayda ortalı; alt kenar 9:16'da platform şeridinin üstünde, diğerlerinde altta."""
    bottom = safe_bottom(W, H) - H * 0.02 if tall(W, H) else H * 0.94
    return round((W - w) / 2), round(bottom - h)


def filter_graph(items: list) -> str:
    """[(Cue, x, y)] → ffmpeg filter_complex: her PNG yalnız KENDİ aralığında görünür; zincir [vN] ile biter."""
    parts = []
    for i, (c, x, y) in enumerate(items, 1):
        src = "[0:v]" if i == 1 else f"[v{i - 1}]"
        parts.append(f"{src}[{i}:v]overlay=x={x}:y={y}:enable='between(t,{c.start:.3f},{c.end:.3f})'[v{i}]")
    return ";".join(parts)


def default_srt(video: str, lang: str) -> str:
    return os.path.splitext(video)[0] + f".{lang}.srt"


def default_out(video: str, lang: str) -> str:
    return os.path.splitext(video)[0] + f".{lang}-sub.mp4"


def _frame_size(video: str) -> tuple:
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0:s=x", video],
                         capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split("x")[:2]
    return int(w), int(h)


def burn(video: str, srt_path: str, out: str, crf: int = 16) -> str:
    """`video`a `srt_path`teki altyazıyı yakıp `out`a yazar (ses kopyalanır)."""
    if not os.path.exists(video):
        raise PromoError(f"video yok: {video}")
    if not os.path.exists(srt_path):
        raise PromoError(f"altyazı yok: {srt_path} (önce `promo.py render` ya da `promo.py subs`)")
    with open(srt_path, encoding="utf-8") as f:
        cues = parse_srt(f.read())
    W, H = _frame_size(video)
    fonts = Fonts()
    with tempfile.TemporaryDirectory(prefix="promo-burn-") as tmp:
        items, inputs = [], []
        for i, c in enumerate(cues, 1):
            img = cue_image(fonts, c.text, W, H)
            x, y = placement(W, H, img.width, img.height)
            p = os.path.join(tmp, f"cue{i:03d}.png")
            img.save(p)
            inputs += ["-i", p]
            items.append((c, x, y))
        cmd = [ffmpeg_bin(), "-v", "error", "-y", "-i", video, *inputs,
               "-filter_complex", filter_graph(items), "-map", f"[v{len(items)}]", "-map", "0:a?",
               "-c:v", "libx264", "-crf", str(crf), "-preset", "medium", "-pix_fmt", "yuv420p",
               "-c:a", "copy", "-movflags", "+faststart", out]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise PromoError(f"ffmpeg altyazı yakamadı: {r.stderr.strip()[-400:]}")
    return out
