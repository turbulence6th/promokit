"""HTML ekran sayfaları: jest zaman çizelgesiyle kare kare çekilen "ekran içeriği".

Akış: temaslar (yazılmış ya da kaydedilmiş) → `classify` → yüksek düzey jestler (CSS piksel)
→ sayfaya `__promoInit` → her kare `__promoSeekFrame(k)` + ekran görüntüsü. Eli/daireyi süren
temaslarla sayfayı süren jestler AYNI listeden türediği için senkron yapısaldır.

Sayfa SABİT kare ızgarasında ilerler (k = ⌊t·fps⌋): bir kareye doğrudan atlamak ile kare kare gelmek
aynı adım dizisini koşar → paralel render parçalarının sınırında dikiş yok (promokit/runtime/promo.js).

Sayfa boyutu: `<meta name="promo-viewport" content="1400x920">` (CSS px; yoksa 1400×920 —
2800×1840 tabletin 2× HiDPI sanal ekranı). Çekim çözünürlüğü = sahnedeki en büyük görüntülenme
boyutu (cihaz piksel oranı ile) → yakınlaştırmada da yazı keskin.

Jest anlamları README'deki «Jest anlamları» tablosuyla birebir. Eşikler aşağıda; tests/test_gestures.py kilitler.
"""
from __future__ import annotations

import json
import math
import os
import re

from PIL import Image

from .cdp import Browser
from .hand import group_contacts
from .util import PromoError

RUNTIME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime", "promo.js")
DEFAULT_VIEWPORT = (1400, 920)
RATE = 120.0

# ── Jest eşikleri ──
# Mesafeler dp. Sayfa CSS px'i → dp: referans tablet (Huawei PCE-W29) 2800×1840 panel @ density 2.0 =
# 1400×920 dp ve sayfa tabletin ekranını TAMAMEN kaplar → 1 CSS px = TABLET_DP_W / sayfa genişliği dp.
# 1400 CSS genişliğindeki şablonlarda tam 1; başka genişlikte `dp_per_css` ölçekler (dp ≠ CSS px).
TABLET_DP_W = 1400.0
TAP_SLOP_DP = 12.0             # İNİŞ noktasından bu kadar yer değiştirme → dokunma değil, kaydırma
GRAB_DRAG_SLOP_DP = 4.0        # uzun bas (GRAB) sonrası sürükleme eşiği
LONG_PRESS_S = 0.400           # hareketsiz bu süre → GRAB (halka yeşil; bırakınca sağ tık)
TWO_TAP_MAX_S = 0.350          # iki parmak dokunma üst süresi (2. parmağın inişinden son kalkışa)
DOUBLE_TAP_S = 0.350           # çift-dokunma penceresi; iki parmak sağ tıkı bu kadar GECİKİR
DOUBLE_TAP_SLOP_DP = 30.0
CURSOR_ARM_DP = 6.0            # iki parmak: merkez bu kadar kayınca (yarıçap ~sabit) → imleç
CURSOR_SPAN_MAX_DP = 5.0
ZOOM_ARM_DP = 6.0              # iki parmak: YARIÇAP (parmak aralığı / 2) bu kadar değişince → çimdik
ZOOM_GAIN_PER_DP = 5.0 / 1000  # zoomScaleDp: yarıçap dp'si → magnification (Mac uygulaması ölçek ×(1+m))
KB_SWIPE_DP = 60.0             # 4 parmak dikey → ekran klavyesi (hareket SIRASINDA bir kez)
DOCK_ENGAGE_DP = 30.0          # 3 parmak: eksen kilidi eşiği
DOCK_DOMINANCE = 1.4           # baskın eksen diğerinin ≥ bu katı değilse kilitlenmez (diyagonal)
DOCK_FLING_GAP_S = 0.100       # kalkıştan önce bu kadar duran parmak → kalkış hızı 0
PEN_SLOP_DP = 8.0              # kalem tık-eşiği = Android ViewConfiguration dokunma slop'u (AOSP 8 dp)


def dp_per_css(css) -> float:
    """Sayfanın 1 CSS px'i kaç dp (sayfa referans tabletin 1400 dp genişliğini kaplar)."""
    return TABLET_DP_W / float(css[0])


def viewport(path: str) -> tuple[int, int]:
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(4096)
    except OSError:
        return DEFAULT_VIEWPORT
    m = re.search(r'<meta[^>]+name=["\']promo-viewport["\'][^>]+content=["\'](\d+)\s*[x×]\s*(\d+)', head)
    return (int(m.group(1)), int(m.group(2))) if m else DEFAULT_VIEWPORT


def _concurrent(cs):
    """En çok eşzamanlı temasın olduğu andaki temaslar. Biri biterken öteki başlıyorsa eşzamanlı
    SAYILMAZ: aynı zamanda bitiş, başlangıçtan ÖNCE işlenir."""
    ev = sorted([(c.t0, 1, i) for i, c in enumerate(cs)] + [(c.t1, -1, i) for i, c in enumerate(cs)],
                key=lambda e: (e[0], e[1]))
    cur, best, act, snap = 0, 0, set(), []
    for t, d, i in ev:
        if d > 0:
            act.add(i)
        else:
            act.discard(i)
        cur += d
        if cur > best:
            best, snap = cur, list(act)
    return [cs[i] for i in snap] or cs[:1]


def _times(t0, t1):
    n = max(1, int(math.ceil((t1 - t0) * RATE)))
    return [t0 + (t1 - t0) * k / n for k in range(n + 1)]


def _centroid_rows(snap, P, t0, t1):
    rows = []
    for t in _times(t0, t1):
        pts = [P(*c.pos(t)) for c in snap]
        rows.append([t, sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)])
    return rows


def _one(c, P, px, st) -> dict:
    """Tek parmak = tek parmak jest tanıyıcısı: dokun · kaydır · GRAB → sağ tık / sürükle."""
    path = [[t, *P(*c.pos(t))] for t in _times(c.t0, c.t1)]
    x0, y0 = path[0][1], path[0][2]
    disp = [math.dist(r[1:3], (x0, y0)) for r in path]   # İNİŞ noktasından yer değiştirme (yol uzunluğu DEĞİL)
    last = st.get("tap")
    dbl = (last is not None and c.t0 - last[0] <= DOUBLE_TAP_S
           and math.dist((x0, y0), last[1:]) <= px(DOUBLE_TAP_SLOP_DP))
    arm = c.t0 + LONG_PRESS_S
    # eşiğin aşıldığı AN — örnekler arasında doğrusal ara değer (120 Hz örnek adımına yuvarlamasın)
    thr = px(TAP_SLOP_DP)
    j = next((i for i, d in enumerate(disp) if d > thr), None)
    ts = None if j is None else path[0][0] if j == 0 else (
        path[j - 1][0] + (thr - disp[j - 1]) / ((disp[j] - disp[j - 1]) or 1.0) * (path[j][0] - path[j - 1][0]))
    g = {"t0": c.t0, "t1": c.t1, "fingers": 1, "path": path}
    if ts is not None and (dbl or ts < arm):
        g.update(type="scroll", ts=ts)            # içerik eşik aşıldıktan SONRA kayar (ilk 12 dp Mac'e gitmez)
    elif dbl or c.t1 < arm:
        g["type"] = "tap"                         # bırakışta, İNİŞ noktasına atomik sol tık
        if dbl:                                   # çift-dokunmanın 2.'si: GRAB yok, süreden bağımsız tık,
            g["double"] = True                    # konum kilidi → tık İLK dokunuşun yerine iner
            for r in path:
                r[1], r[2] = last[1], last[2]
        st["tap"] = (c.t1, path[0][1], path[0][2])
    else:
        tb = next((r[0] for r, d in zip(path, disp) if r[0] > arm and d > px(GRAB_DRAG_SLOP_DP)), None)
        if tb is None:
            g.update(type="secondary", via="longpress", ta=arm)
        else:
            g.update(type="drag", ta=arm, tb=tb)  # sol tuş tb'de basılır, nesne parmağın inişten beri yolunu izler
    return g


def _two(snap, P, px, dpc) -> dict:
    """İki parmak = iki parmak jest tanıyıcısı: karar BİR KEZ — yarıçap 6 dp değişirse çimdik;
    merkez 6 dp kayar ve yarıçap < 5 dp değişirse imleç; ikisi de yokken hızlı kalkış = iki parmak dokunma."""
    a, b = sorted(snap, key=lambda c: c.t0)
    t0, t2, tboth, t1 = a.t0, b.t0, min(a.t1, b.t1), max(a.t1, b.t1)
    rows = []
    for t in _times(t0, t1):
        pa, pb = P(*a.pos(t)), P(*b.pos(t))
        rows.append([t, (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2, math.dist(pa, pb),
                     math.degrees(math.atan2(pb[1] - pa[1], pb[0] - pa[0]))])
    both = [r for r in rows if t2 <= r[0] <= tboth] or rows[-1:]
    c0, r0 = both[0][1:3], both[0][3] / 2
    g = {"t0": t0, "t1": t1, "fingers": 2, "path": rows}
    for r in both[1:]:
        span, moved = abs(r[3] / 2 - r0), math.dist(r[1:3], c0)
        if span >= px(ZOOM_ARM_DP):
            # magnification yalnız kol kurulduktan SONRAKİ yarıçap değişiminden: k = 1/CSS px parmak aralığı
            g.update(type="pinch", ta=r[0], da=r[3], k=ZOOM_GAIN_PER_DP * dpc / 2)
            return g
        if moved >= px(CURSOR_ARM_DP) and span < px(CURSOR_SPAN_MAX_DP):
            g.update(type="pointer", ta=r[0])
            return g
    if t1 - t2 < TWO_TAP_MAX_S and math.dist(both[-1][1:3], c0) <= px(TAP_SLOP_DP):
        g["type"] = "secondary"                   # iki parmağın ORTASINDA sağ tık — ateş anı `_two_taps`
    else:
        g["type"] = "pointer"                     # basılı tutuldu: imleç merkeze ışınlandı, başka bir şey yok
    return g


def _three(snap, P, px) -> dict:
    """Üç parmak = `DockSwipeStream`: 30 dp + 1,4× baskınlıkla eksen KİLİDİ (kilitsiz jest Mac'te hiçbir şey
    yapmaz); kalkışta hız birikmiş yolun TERSİNEyse İPTAL (geri yaslanır), değilse BİTİŞ."""
    t0, t3, t1 = min(c.t0 for c in snap), max(c.t0 for c in snap), max(c.t1 for c in snap)
    tall = min(c.t1 for c in snap)
    rows = _centroid_rows(snap, P, t0, t1)
    base = next(r for r in rows if r[0] >= t3 - 1e-9)
    g = {"type": "swipe", "t0": t0, "t1": t1, "fingers": 3, "path": rows, "axis": None, "c0": base[1:3]}
    for r in rows:
        if r[0] < t3 - 1e-9 or r[0] > tall + 1e-9:
            continue
        adx, ady = abs(r[1] - base[1]), abs(r[2] - base[2])
        if max(adx, ady) < px(DOCK_ENGAGE_DP) or (adx < ady * DOCK_DOMINANCE and ady < adx * DOCK_DOMINANCE):
            continue
        g["axis"], g["tl"] = ("h" if adx >= ady else "v"), r[0]
        break
    if g["axis"]:
        i = 1 if g["axis"] == "h" else 2
        moving = [j for j in range(1, len(rows)) if abs(rows[j][i] - rows[j - 1][i]) > 1e-6]
        vel = 0.0
        if moving and t1 - rows[moving[-1]][0] <= DOCK_FLING_GAP_S:
            j = moving[-1]
            k = max(0, j - 3)
            vel = (rows[j][i] - rows[k][i]) / max(1e-6, rows[j][0] - rows[k][0])
        cum = rows[-1][i] - base[i]
        g["cancel"] = vel != 0 and cum != 0 and (vel > 0) != (cum > 0)
    return g


def _four(snap, P, px) -> dict:
    """Dört parmak = dört parmak jest tanıyıcısı: merkez dikeyde 60 dp'yi aştığı AN bir kez → ekran
    klavyesi göster (yukarı) / gizle (aşağı). Tablette yutulur, Mac görmez."""
    t0, t4, t1 = min(c.t0 for c in snap), max(c.t0 for c in snap), max(c.t1 for c in snap)
    tall = min(c.t1 for c in snap)
    rows = _centroid_rows(snap, P, t0, t1)
    base = next(r for r in rows if r[0] >= t4 - 1e-9)
    g = {"type": "swipe", "t0": t0, "t1": t1, "fingers": len(snap), "path": rows, "c0": base[1:3], "kb": None}
    for r in rows:
        if t4 - 1e-9 <= r[0] <= tall + 1e-9 and abs(r[2] - base[2]) >= px(KB_SWIPE_DP):
            g["kb"] = {"t": r[0], "show": r[2] < base[2]}
            break
    return g


def _pen(c, P, px) -> dict:
    """Kalem ucu = SOL TUŞ, jest makinesine girmez: tık-eşiği aşılmazsa TIK (süresi ne olursa olsun),
    aşılırsa sürükleme (`canvas[data-ink]` üstünde mürekkep)."""
    path = [[t, *P(*c.pos(t)), c.pressure(t)] for t in _times(c.t0, c.t1)]
    moved = max(math.dist(r[1:3], path[0][1:3]) for r in path)
    if moved < px(PEN_SLOP_DP):
        return {"type": "tap", "t0": c.t0, "t1": c.t1, "fingers": 1, "tool": "pen", "path": path}
    return {"type": "pen", "t0": c.t0, "t1": c.t1, "fingers": 1, "path": path}


def _back(c, P) -> dict:
    """Tabletin KENDİ geri jesti (kenardan kaydırma, §4.3): OS'un jestidir, uygulamaya dokunuş olarak GELMEZ —
    ürün geri OLAYINI yakalayıp Mac'e sayfa geri gönderir (fare yan tuşu). Tablet-yerel: kaydırma sayılmaz,
    başka temasla birleşmez. Sayfaya kalkışta 'back' olayı gider."""
    path = [[t, *P(*c.pos(t))] for t in _times(c.t0, c.t1)]
    return {"type": "back", "t0": c.t0, "t1": c.t1, "fingers": 1, "local": True,
            "side": "left" if c.x[0] < 0.5 else "right", "path": path}


def _two_taps(out):
    """İki parmak dokunmanın sağ tıkı çift-dokunma penceresi kadar GECİKİR ve pencerede inen yeni bir
    dokunuş onu İPTAL eder (TouchscreenInputController `pendingRightTap`). İki parmak dokunmanın KALKIŞI bir
    öncekinin kalkışından ≤ pencere sonra gelirse akıllı yakınlaştırmadır (jest tanıyıcısının kalkış kuralı:
    `SMARTZOOM` — Mac'in kendi smart zoom'u, ikinci kalkışta; ardından sayaç sıfırlanır)."""
    keep = []
    last_two = None   # ürün `lastTwoTapMs`: son 2-parmak tap'ın KALKIŞI (0 = yok)
    for i, g in enumerate(out):
        if g["type"] == "secondary" and g["fingers"] == 2:
            if last_two is not None and g["t1"] - last_two <= DOUBLE_TAP_S:
                g.update(type="smartzoom", tf=g["t1"])
                last_two = None
                keep.append(g)
                continue
            last_two = g["t1"]
            nxt = next((h for h in out[i + 1:] if not h.get("local") and h["t0"] >= g["t1"] - 1e-9), None)
            if nxt is not None and nxt["t0"] - g["t1"] <= DOUBLE_TAP_S:
                continue                          # pencerede yeni iniş → sağ tık hiç gitmez
            g["tf"] = g["t1"] + DOUBLE_TAP_S
        keep.append(g)
    return keep


def classify(contacts, keys, texts, css, crop=(0.0, 0.0, 1.0, 1.0), fps=60.0, latency=0.0) -> dict:
    """Temaslar → sayfa jestleri (CSS px). `crop`: sayfanın cihaz ekranına sığdırma kırpması."""
    W, H = css
    cx, cy, cw, ch = crop
    dpc = dp_per_css(css)

    def P(u, v):
        return ((cx + u * cw) * W, (cy + v * ch) * H)

    def px(dp):
        return dp / dpc

    screen = [c for c in contacts if c.on == "screen"]
    out = [{"type": "tap", "t0": c.t0, "t1": c.t1, "fingers": 1, "local": True,
            "path": [[t, *P(*c.pos(t))] for t in _times(c.t0, c.t1)]} for c in screen if c.local]
    out += [_back(c, P) for c in screen if c.system == "back"]
    st = {}   # çift-dokunma konum kilidi (ürün `reset()`te SİLMEZ → sekanslar arası taşınır)
    for grp in group_contacts([c for c in screen if not c.local and not c.system]):
        cs = grp.contacts
        pens = [c for c in cs if c.tool == "pen"]
        if pens:   # avuç reddi: kalem sahnedeyken parmak yutulur
            out.extend(_pen(c, P, px) for c in pens)
            continue
        snap = _concurrent(cs)
        n = len(snap)
        if n == 1:
            out.append(_one(snap[0], P, px, st))
        elif n == 2:
            out.append(_two(snap, P, px, dpc))
        elif n == 3:
            out.append(_three(snap, P, px))
        else:
            out.append(_four(snap, P, px))
    out.sort(key=lambda g: g["t0"])
    out = _two_taps(out)
    if latency:
        for g in out:
            for k in ("t0", "t1", "ts", "ta", "tb", "tf", "tl"):
                if g.get(k) is not None:
                    g[k] += latency
            if g.get("kb"):
                g["kb"]["t"] += latency
            for r in g["path"]:
                r[0] += latency
    return {"fps": fps, "gestures": out,
            "keys": [{"t": k.t + latency, "label": k.label, "dur": k.dur} for k in keys],
            "texts": [{"t": x.t + latency, "text": x.text, "rate": x.rate} for x in texts]}


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
        self.timeline = timeline or {"fps": fps, "gestures": [], "keys": [], "texts": []}
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
            self.b.add_init_script(self.runtime)
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


class SelectorResolver:
    """`tap = "#oge"` gibi hedefleri, jestin başladığı andaki sayfa durumunda ölçer.

    Her sorgu sayfayı TAZE açar ve o ana kadarki jestleri yeniden oynatır (deterministik) — jestin
    çalışma zamanındaki isabet testinin gördüğü kare (başladığı karenin bir öncesi). Sonuç cihaz ekranına
    göre normalize (x, y, w, h): öğenin EN BÜYÜK görünür parçası (iki satıra bölünen bağlantı → tek satır).
    İstenen nokta (`probe` = parça içindeki kesir) GERÇEKTEN öğeye isabet etmeli: görünmez (opaklık ≈ 0),
    örtülü ya da `pointer-events: none` hedef hata verir — video "dokundu" deyip başka şeye basmasın."""

    JS = r"""((sel, fx, fy) => {
      let el; try { el = document.querySelector(sel); } catch (e) { return {bad: String(e)}; }
      if (!el) return {missing: true};
      const W = innerWidth, H = innerHeight;
      let best = null;
      for (const r of el.getClientRects()) {
        const x0 = Math.max(r.left, 0), y0 = Math.max(r.top, 0), x1 = Math.min(r.right, W), y1 = Math.min(r.bottom, H);
        const a = Math.max(0, x1 - x0) * Math.max(0, y1 - y0);
        if (!best || a > best.a) best = {x0, y0, x1, y1, a};
      }
      const f = el.getBoundingClientRect(), full = [f.left, f.top, f.width, f.height];
      if (!best || best.x1 - best.x0 < 2 || best.y1 - best.y0 < 2) return {offscreen: true, full};
      const x = best.x0 + fx * (best.x1 - best.x0), y = best.y0 + fy * (best.y1 - best.y0);
      let op = 1;
      for (let e = el; e && e.nodeType === 1; e = e.parentElement) op *= parseFloat(getComputedStyle(e).opacity);
      const h = document.elementFromPoint(x, y);
      const name = (e) => !e ? '—' : e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') +
        (typeof e.className === 'string' && e.className.trim() ? '.' + e.className.trim().split(/\s+/).join('.') : '');
      return {x0: best.x0, y0: best.y0, x1: best.x1, y1: best.y1, full, opacity: op,
              hit: !!h && (h === el || el.contains(h)), over: name(h)};
    })(%s, %r, %r)"""

    def __init__(self, path: str, css, crop, fps: float, latency: float = 0.0, params: dict | None = None):
        self.path = os.path.abspath(path)
        self.css, self.crop, self.fps, self.latency = css, crop, fps, latency
        self.params = params or {}
        self.b = None
        with open(RUNTIME, encoding="utf-8") as f:
            self.runtime = f.read()

    def __call__(self, selector: str, t: float, contacts, keys, texts, probe=(0.5, 0.5)):
        if self.b is None:
            self.b = Browser(self.css[0], self.css[1], 0.5)  # ekran görüntüsü alınmıyor → düşük ölçek yeter
            self.b.add_init_script(self.runtime)
        self.b.open("file://" + self.path)
        tl = classify(contacts, keys, texts, self.css, self.crop, self.fps, self.latency)
        tl["params"] = self.params
        self.b.eval("__promoInit(" + json.dumps(tl) + ")")
        # Jest çalışma zamanında t+gecikme'yi içeren adımda başlar; isabet testi bir önceki karenin durumunu görür
        k = max(0, math.ceil((t + self.latency) * self.fps - 1e-6) - 1)
        self.b.eval(f"__promoSeekFrame({k})")
        r = self.b.eval(self.JS % (json.dumps(selector), float(probe[0]), float(probe[1])))
        where = f"({os.path.basename(self.path)})"
        if not r or r.get("missing"):
            raise PromoError(f"seçici sayfada yok: {selector}  {where}")
        if r.get("bad"):
            raise PromoError(f"seçici geçersiz: {selector} — {r['bad']}  {where}")
        if r.get("offscreen"):
            raise PromoError(f"seçici t={t:g} anında ekranda görünmüyor: {selector} — önce kaydırın "
                             f"(öğe kutusu {[round(v) for v in r['full']]})")
        if r["opacity"] < 0.05:
            raise PromoError(f"seçici t={t:g} anında görünmez (opaklık {r['opacity']:.2f}): {selector}  {where}")
        if not r["hit"]:
            raise PromoError(f"seçici t={t:g} anında dokunulamıyor: {selector} — o noktada üstte '{r['over']}' "
                             f"var (örtülü ya da pointer-events kapalı)  {where}")
        W, H = self.css
        cx, cy, cw, ch = self.crop
        return ((r["x0"] / W - cx) / cw, (r["y0"] / H - cy) / ch,
                (r["x1"] - r["x0"]) / W / cw, (r["y1"] - r["y0"]) / H / ch)

    def close(self):
        if self.b is not None:
            self.b.close()
            self.b = None
