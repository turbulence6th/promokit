"""HTML sayfaları: kare kare çekilen "ekran içeriği" (cihaz ekranı ya da tam ekran sahne).

Akış: sayfaya `__promoInit(zaman_çizelgesi)` → her kare `__promoSeekFrame(k)` + ekran görüntüsü.
Sayfa gerçek zamanda ÇALIŞMAZ: sabit bir kare ızgarasında (k = ⌊t·fps⌋) ilerler; bir kareye doğrudan
atlamak ile kare kare gelmek AYNI adım dizisini koşar → paralel render parçalarının sınırında dikiş yok
(promokit/runtime/promo.js).

Zaman çizelgesi (`HtmlSource(timeline=…)`):
    {"fps": 60, "params": {...}, "scroll": {"target": "#kap" | None, "moves": [{"t", "y"|"to", "dur", "ease", "offset"}]}}
`scroll` senaryodan gelir (`story.scroll_spec`) — sayfa, TOML'daki zaman çizelgesine göre GERÇEKTEN kaydırılır
(`window.scrollTo`/`scrollTop`) → `position: fixed|sticky`, `scroll-timeline` ve sitenin kendi `scroll`
dinleyicileri olduğu gibi çalışır.

Sayfa boyutu: `<meta name="promo-viewport" content="1400x920">` (CSS px; yoksa 1400×920). Çekim çözünürlüğü =
sahnedeki en büyük görüntülenme boyutu (cihaz piksel oranı ile) → yakınlaştırmada da yazı keskin.
"""
from __future__ import annotations

import json
import math
import os
import re

from PIL import Image

from .cdp import Browser

RUNTIME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime", "promo.js")
DEFAULT_VIEWPORT = (1400, 920)


def viewport(path: str) -> tuple[int, int]:
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(4096)
    except OSError:
        return DEFAULT_VIEWPORT
    m = re.search(r'<meta[^>]+name=["\']promo-viewport["\'][^>]+content=["\'](\d+)\s*[x×]\s*(\d+)', head)
    return (int(m.group(1)), int(m.group(2))) if m else DEFAULT_VIEWPORT


def frame_index(t: float, fps: float) -> int:
    """Sayfanın kare ızgarası: t'yi içeren son kare (yuvarlama kırıntısına toleranslı)."""
    return max(0, int(math.floor(t * fps + 1e-3)))


class HtmlSource:
    """Sayfayı başsız tarayıcıda açar; her `get(t)`'de o kareye sarar ve ekran görüntüsü döner."""

    def __init__(self, path: str, css: tuple[int, int], size: tuple[int, int], fps: float, timeline: dict | None = None):
        self.path = os.path.abspath(path)
        self.css = css
        self.size = size
        self.fps = fps
        self.timeline = timeline or {"fps": fps}
        self.b = None
        self.k = None
        self.img = None
        self._resized = {}
        with open(RUNTIME, encoding="utf-8") as f:
            self.runtime = f.read()

    @property
    def t(self):
        return None if self.k is None else self.k / self.fps

    def _boot(self):
        if self.b is None:
            scale = max(0.5, min(3.0, self.size[0] / self.css[0]))
            self.b = Browser(self.css[0], self.css[1], scale)
            # `promo.params`/`promo.fps` sayfa betikleri çalışmadan ÖNCE hazır olsun (sayfa yüklenirken okuyabilsin)
            boot = {"fps": self.fps, "params": self.timeline.get("params", {})}
            self.b.add_init_script("window.__promoBoot = " + json.dumps(boot) + ";\n" + self.runtime)
        self.b.open("file://" + self.path)
        self.b.eval("__promoInit(" + json.dumps(self.timeline) + ")")
        self.k = 0
        self.img = None

    def get(self, t_media: float, size=None) -> Image.Image:
        k = frame_index(max(0.0, t_media), self.fps)
        if self.b is None or (self.k is not None and k < self.k):  # geri sarma: sayfayı baştan kur
            self._boot()
        if self.img is None or k != self.k:
            self.b.eval(f"__promoSeekFrame({k})")
            self.img = self.b.screenshot()
            self.k = k
            self._resized.clear()
        if size is None or tuple(size) == self.img.size:
            return self.img
        im = self._resized.get(tuple(size))
        if im is None:
            im = self.img.resize(tuple(size), Image.Resampling.BICUBIC, reducing_gap=2.0)
            self._resized[tuple(size)] = im
        return im

    def close(self):
        if self.b is not None:
            self.b.close()
            self.b = None
