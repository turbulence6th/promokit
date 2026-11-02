"""Sahne yerleşimi: cihazların DÜNYA koordinatındaki ekran dikdörtgenleri + kamera + cihaz duruşu.

Dünya = kamera yakınlaştırması 1 iken sahne pikseli. Cihazlar fiziksel oranlarıyla
(ekran köşegeni) yerleşir: 14" MacBook Pro (14,2") ya da Windows dizüstü (14", `[video] laptop_model`)
↔ MatePad 11,5". Yatay sahnede yan yana (tabanlar aynı masa çizgisinde), dikey sahnede üst üste.
Cihaz seçenekleri (`opts`: dizüstü modeli) yerleşimden duruşa kadar taşınır — gövde geometrisi modelin.

Başlık bandı HER sahnede ayrılır (başlık olmasa da) — sahneler arası geçişte
cihazlar yerinden zıplamasın. Bandın yüksekliği senaryodaki EN UZUN (sarılmış) başlıktan ve
`caption_size`tan gelir; yerleşim ve kamera AYNI içerik kutusunu kullanır.
"""
from __future__ import annotations

import math

from .devices import HINGE_SHOW, LAPTOP_MODELS, geometry, laptop_model, laptop_spec, lid_view
from .util import PromoError, clamp, ease, lerp, rot

# ekran köşegeni (inç): MatePad 11,5 · MacBook Pro 14 (14,2) — dizüstünün değeri MODELİN (`diag`)
DIAG = {"tablet": 11.5, "laptop": LAPTOP_MODELS["macbook"]["diag"]}
# Medyası olmayan cihazın ekran oranı = şablonların görünüm alanı (MatePad 2800×1840 · MBP 14 ≈ 1470×956;
# Windows dizüstü 1440×900 — `default_aspect`)
DEFAULT_ASPECT = {"tablet": 1400 / 920, "laptop": LAPTOP_MODELS["macbook"]["aspect"]}


def diag(kind: str, opts: dict | None = None) -> float:
    """Ekran köşegeni (inç); dizüstünde seçeneklerin modeli (`opts["model"]`)."""
    return laptop_model(opts)["diag"] if kind == "laptop" else DIAG[kind]


def default_aspect(kind: str, opts: dict | None = None) -> float:
    """Medyasız ekran oranı; dizüstünde modelin şablon görünüm alanı."""
    return laptop_model(opts)["aspect"] if kind == "laptop" else DEFAULT_ASPECT[kind]


def screen_dims(diag_px: float, aspect: float):
    w = diag_px * aspect / math.sqrt(1 + aspect * aspect)
    return (w, w / aspect)


# Dikey kısa video (9:16 — Reels/TikTok/Shorts): platform arayüzü videonun ÜSTÜNE biner — üstte durum
# çubuğu + sekmeler/ikonlar (~%10-13), altta açıklama + kullanıcı adı + ses (~%15). Başlık üst çizginin
# altından başlar, cihazlar alt çizginin üstünde biter (kullanıcı, 2026-10-20: "dikey videolarda yazı çok
# üstte … üstte şerit oluyor yazıyı blokluyor"). 4:5 / 1:1 / yatay akışlarda böyle bir örtü yok → eski yer.
# Üst pay %13 → %20 (kullanıcı, 2026-10-23): YouTube webde Shorts altyazısını ÜSTTE gösteriyor ve iki satırlık
# başlığın 1. satırına (%13,5-17) iniyordu. %20'de ilk satır %20,6'dan başlar → ~%3,6 boşluk (üç satıra bölünen
# altyazıya da yeter).
SAFE_TALL = (0.20, 0.15)


def tall(W: int, H: int) -> bool:
    return H >= 1.6 * W


def caption_top(W: int, H: int) -> float:
    if tall(W, H):
        return H * SAFE_TALL[0]
    return min(W, H) * (0.075 if H > W else 0.048)


def safe_bottom(W: int, H: int) -> float:
    """İçeriğin bitebileceği en alt y (9:16'da platformun alt şeridinin üstü; diğerlerinde kadrajın altı)."""
    return H * (1 - SAFE_TALL[1]) if tall(W, H) else float(H)


def content_box(W: int, H: int, caption_size: float = 1.0, band: float | None = None):
    """Cihazların sığdırıldığı kutu. `band`: ayrılan başlık bloğu yüksekliği (verilmezse tek satırlık
    başlık + alt satırın `caption_size`lı formülü; render, senaryodaki gerçek başlıklarla büyütür)."""
    from .overlays import Caption
    u = min(W, H)
    portrait = H > W
    mx = W * (0.05 if portrait else 0.06)
    band = Caption.band(W, H, caption_size) if band is None else band
    top = caption_top(W, H) + band + u * (0.05 if portrait else 0.035)
    bottom = H * SAFE_TALL[1] if tall(W, H) else u * (0.09 if portrait else 0.055)
    return (mx, top, W - mx, H - bottom)


def place(W: int, H: int, layout: str, aspects: dict, box=None, opts: dict | None = None) -> dict:
    """{'tablet': (x, y, w, h), 'laptop': (…)} — yalnız yerleşimdeki cihazlar. `box`: içerik kutusu
    (render'ın başlık bandını bilen kutusu; verilmezse varsayılan). `opts`:
    cihaz → seçenekler (`{"laptop": {"model": "windows"}}` — köşegen + gövde modelin; vars. MacBook)."""
    kinds = {"tablet": ["tablet"], "laptop": ["laptop"], "duo": ["laptop", "tablet"]}[layout]
    boxes = {}
    for k in kinds:
        o = (opts or {}).get(k)
        a = aspects.get(k) or default_aspect(k, o)
        sw, sh = screen_dims(diag(k, o) * 100.0, a)
        g = geometry(k, sw, sh, o)
        boxes[k] = (sw, sh, g)
    # Gövdeleri birim ölçekte diz (ekran sol-üstü konumları)
    pos = {}
    if len(kinds) == 1:
        k = kinds[0]
        sw, sh, g = boxes[k]
        pos[k] = (-g.left, -g.top)
    else:
        lw, lh, lg = boxes["laptop"]
        tw, th, tg = boxes["tablet"]
        if W >= H:
            gap = lg.width * 0.05
            pos["laptop"] = (-lg.left, -lg.top)
            desk = lg.height  # laptop gövde altı (y)
            tx = lg.width + gap - tg.left
            ty = desk - tg.bottom
            pos["tablet"] = (tx, ty)
        else:
            gap = lg.height * 0.10
            cx = max(lg.width, tg.width) / 2
            pos["laptop"] = (cx - lg.width / 2 - lg.left, -lg.top)
            pos["tablet"] = (cx - tg.width / 2 - tg.left, lg.height + gap - tg.top)
    # Grup kutusu
    x0 = min(pos[k][0] + boxes[k][2].left for k in kinds)
    y0 = min(pos[k][1] + boxes[k][2].top for k in kinds)
    x1 = max(pos[k][0] + boxes[k][2].right for k in kinds)
    y1 = max(pos[k][1] + boxes[k][2].bottom for k in kinds)
    cx0, cy0, cx1, cy1 = box if box is not None else content_box(W, H)
    s = min((cx1 - cx0) / (x1 - x0), (cy1 - cy0) / (y1 - y0))
    ox = cx0 + ((cx1 - cx0) - (x1 - x0) * s) / 2 - x0 * s
    oy = cy0 + ((cy1 - cy0) - (y1 - y0) * s) / 2 - y0 * s
    out = {}
    for k in kinds:
        sw, sh, _ = boxes[k]
        out[k] = (ox + pos[k][0] * s, oy + pos[k][1] * s, sw * s, sh * s)
    return out


class Camera:
    """Dünya → sahne dönüşümü: p' = A + (p − F) · z."""

    def __init__(self, zoom=1.0, focus=(0.0, 0.0), anchor=(0.0, 0.0)):
        self.zoom = zoom
        self.focus = focus
        self.anchor = anchor

    def apply(self, p):
        return (self.anchor[0] + (p[0] - self.focus[0]) * self.zoom,
                self.anchor[1] + (p[1] - self.focus[1]) * self.zoom)

    def rect(self, r):
        x, y = self.apply((r[0], r[1]))
        return (x, y, r[2] * self.zoom, r[3] * self.zoom)


class CameraPath:
    """Kamera hareketleri: [{t, zoom, at=[u,v], on='tablet', dur, ease}].

    Her hareket `t` anında BAŞLAR ve `dur` saniyede (vars. 0,9) hedefe varır; bir
    sonraki hareket, o an ulaşılmış durumdan devam eder. `at` yoksa sahne merkezi.
    """

    def __init__(self, keyframes, rects: dict, W: int, H: int, default_on: str, box=None):
        cx0, cy0, cx1, cy1 = box if box is not None else content_box(W, H)
        self.anchor = ((cx0 + cx1) / 2, (cy0 + cy1) / 2)
        self.kfs = []
        for kf in sorted(keyframes, key=lambda k: float(k.get("t", 0))):
            z = float(kf.get("zoom", 1.0))
            if z <= 0:
                raise PromoError("camera.zoom > 0 olmalı")
            on = kf.get("on", default_on)
            if "at" in kf:
                if on not in rects:
                    raise PromoError(f"camera.on '{on}' bu yerleşimde yok ({', '.join(rects)})")
                x, y, w, h = rects[on]
                u, v = kf["at"]
                F = (x + float(u) * w, y + float(v) * h)
            else:
                F = self.anchor
            self.kfs.append((float(kf.get("t", 0.0)), float(kf.get("dur", 0.9)), z, F, kf.get("ease", "inout")))

    def max_zoom(self) -> float:
        return max([1.0] + [k[2] for k in self.kfs])

    def at(self, t: float) -> Camera:
        z, F = 1.0, self.anchor
        for t0, dur, z1, F1, ez in self.kfs:
            if t < t0:
                break
            k = 1.0 if dur <= 0 else ease((t - t0) / dur, ez)
            z = math.exp(lerp(math.log(z), math.log(z1), k))
            F = (lerp(F[0], F1[0], k), lerp(F[1], F1[1], k))
        return Camera(z, F, self.anchor)


# ── Cihaz animasyonu ─────────────────────────────────────────────────────────
# Yeni bir anim alanı buraya (varsayılanıyla) eklenince durum sözlüğüyle hem normal kareye hem
# geçişe (morph: alan alan ara değer) KENDİLİĞİNDEN akar.
ANIM_DEFAULT = {"x": 0.0, "y": 0.0, "s": 1.0, "o": 1.0, "on": 1.0, "lid": 1.0, "rot": 0.0}


def anim_state(kfs, t: float | None) -> dict:
    """Cihaz anahtar kareleri → t anındaki durum. Her özellik yalnız TANIMLANDIĞI karelerden
    ara değerlenir (bir karede yazılmayan özellik bir öncekinden taşınır). `t=None` → varsayılan."""
    st = dict(ANIM_DEFAULT)
    if not kfs or t is None:
        return st
    kfs = sorted(kfs, key=lambda k: float(k.get("t", 0.0)))
    for prop in ANIM_DEFAULT:
        pts = [(float(k.get("t", 0.0)), float(k[prop]), k.get("ease", "inout")) for k in kfs if prop in k]
        if not pts:
            continue
        if t <= pts[0][0]:
            st[prop] = pts[0][1]
            continue
        st[prop] = pts[-1][1]
        for (ta, va, _), (tb, vb, ez) in zip(pts, pts[1:]):
            if ta <= t <= tb:
                st[prop] = va + (vb - va) * (ease((t - ta) / (tb - ta), ez) if tb > ta else 1.0)
                break
    return st


def fit_scale(kind: str, rect, box, rot_deg: float, opts: dict | None = None) -> float:
    """Gövdenin `rot_deg` kadar dönmüş sınır kutusunu içerik kutusuna sığdıran ölçek (dünya
    dikdörtgenine göre; yerleşimin grubu sığdırdığı gibi). `opts`: cihaz seçenekleri (dizüstü modeli)."""
    g = geometry(kind, rect[2], rect[3], opts)
    cx0, cy0, cx1, cy1 = box
    c, s_ = abs(math.cos(math.radians(rot_deg))), abs(math.sin(math.radians(rot_deg)))
    rw, rh = g.width * c + g.height * s_, g.width * s_ + g.height * c
    return min((cx1 - cx0) / rw, (cy1 - cy0) / rh)


def in_fit_segment(kfs, t: float) -> bool:
    """`t`, uçlarından biri `s = "fit"` olan bir ölçek geçişinin İÇİNDE mi? O aralıkta ölçek, o anki
    dönüşün sığdırma ölçeğiyle sınırlanır: 0°→−90° dönerken 45°'de çapraz sınır kutusu en büyüktür ve
    düz ara değerli ölçek cihazı başlık bandına / kadraj dışına taşırıyordu (9:16 ve 16:9)."""
    pts = sorted((float(k.get("t", 0.0)), bool(k.get("_fit"))) for k in kfs if "s" in k)
    for (ta, fa), (tb, fb) in zip(pts, pts[1:]):
        if ta <= t <= tb:
            return fa or fb
    return False


def resolve_fit(kfs, kind: str, rect, box, opts: dict | None = None) -> list:
    """`s = "fit"` → sayı: o karedeki dönüşle (`rot`, yazılmadıysa önceki karelerden taşınan) gövdenin
    sınır kutusu içerik kutusuna yerleşimin yaptığı gibi SIĞAR. x/y yazılmadıysa kutuda ortalanır
    (eksen eksen). Dünya koordinatında hesaplanır → kamera yakınlaştırması üstüne biner. Çözülen
    kare `_fit` işaretini taşır (bkz. `in_fit_segment`). `opts`: cihaz seçenekleri (dizüstü modeli)."""
    if not kfs or not any(kf.get("s") == "fit" for kf in kfs):
        return list(kfs or [])
    x, y, w, h = rect
    g = geometry(kind, w, h, opts)
    cx0, cy0, cx1, cy1 = box
    rots = [kf for kf in kfs if "rot" in kf]
    out = []
    for kf in kfs:
        if kf.get("s") != "fit":
            out.append(kf)
            continue
        t = float(kf.get("t", 0.0))
        r = float(kf["rot"]) if "rot" in kf else anim_state(rots, t)["rot"]
        fit = fit_scale(kind, rect, box, r, opts)
        new = dict(kf)
        new["s"] = fit
        new["_fit"] = True
        # ölçek ekran merkezi etrafında; gövde merkezi ekran merkezinden (MacBook'ta) kayık olabilir
        dbx = (g.left + g.right) / 2 - w / 2
        dby = (g.top + g.bottom) / 2 - h / 2
        if "x" not in kf:
            new["x"] = ((cx0 + cx1) / 2 - (x + w / 2) - fit * dbx) / h
        if "y" not in kf:
            new["y"] = ((cy0 + cy1) / 2 - (y + h / 2) - fit * dby) / h
        out.append(new)
    return out


class DevicePose:
    """Bir cihazın SAHNEDEKİ duruşu: ekran dikdörtgeni (dönüşten ÖNCE; kamera + anim x/y/s uygulanmış)
    + anim durumu (o/on/lid/rot/…). Dönüş gövde merkezi etrafında, + = saat yönü (ekranda).
    Normal kare ve geçiş (morph) aynı duruş nesnesiyle çizer; dokunuşlar/tuş çipleri/kablo/ses de
    konumlarını buradan (`point`/`local`) alır → cihazla birlikte kayar, ölçeklenir, döner. `opts`: cihaz
    seçenekleri (dizüstü modeli → gövde geometrisi; ara değerlenmez, sahnenin cihazından gelir)."""

    def __init__(self, kind: str, rect, state: dict | None = None, opts: dict | None = None):
        self.kind = kind
        self.rect = tuple(float(v) for v in rect)
        self.state = dict(ANIM_DEFAULT if state is None else state)
        self.opts = dict(opts or {})

    @property
    def rot(self) -> float:
        return float(self.state.get("rot", 0.0))

    def geometry(self):
        return geometry(self.kind, self.rect[2], self.rect[3], self.opts)

    @property
    def pivot(self):
        x, y, w, h = self.rect
        g = self.geometry()
        return (x + (g.left + g.right) / 2, y + (g.top + g.bottom) / 2)

    def local(self, px: float, py: float):
        """Ekran sol-üstüne göre piksel (dönmemiş çerçeve) → sahne noktası (dönüş uygulanmış)."""
        x, y = self.rect[0] + px, self.rect[1] + py
        r = self.rot
        if abs(r) < 1e-9:
            return (x, y)
        cx, cy = self.pivot
        dx, dy = rot((x - cx, y - cy), math.radians(r))
        return (cx + dx, cy + dy)

    def point(self, u: float, v: float):
        """Ekran-normalize (u, v) → sahne noktası."""
        return self.local(u * self.rect[2], v * self.rect[3])

    def bbox(self, x0, y0, x1, y1):
        """Yerel (ekran sol-üstüne göre) dikdörtgenin dönmüş hâlinin sınır kutusu."""
        pts = [self.local(a, b) for a in (x0, x1) for b in (y0, y1)]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def body_bbox(self):
        g = self.geometry()
        return self.bbox(g.left, g.top, g.right, g.bottom)

    def visible_bbox(self):
        """GÖRÜNEN gövdenin sınır kutusu: MacBook kapağı kapanırken menteşe etrafında döner (yüz L·sin φ + üst
        kenar t·cos φ) — `devices.lid_view`, `render._closing_laptop` ile AYNI formül
        (`test_visible_box_matches_the_drawing`)."""
        g = self.geometry()
        top = g.top
        if self.kind == "laptop":
            k = clamp(float(self.state.get("lid", 1.0)))
            L = laptop_spec(self.opts)
            if k < 0.999:
                hinge = self.rect[3] + self.rect[2] * L["chin"]    # kapağın alt kenarı (ekran + çene)
                face, edge, _, sink = lid_view(k, hinge - g.top, self.rect[2] * L["lid_t"],
                                               self.rect[2] * L["hinge"] * HINGE_SHOW)
                top = hinge - face - edge + sink
        return self.bbox(g.left, top, g.right, g.bottom)

    def scaled(self, k: float) -> "DevicePose":
        """Ekran merkezi etrafında ölçek (geçişte sönen cihaz hafifçe küçülür)."""
        x, y, w, h = self.rect
        return DevicePose(self.kind, (x + w * (1 - k) / 2, y + h * (1 - k) / 2, w * k, h * k), self.state, self.opts)

    @staticmethod
    def lerp(a: "DevicePose", b: "DevicePose", w: float) -> "DevicePose":
        """İki sahnenin ortak cihazı: dikdörtgen + TÜM durum alanları ara değerlenir (yeni anim alanları dahil)."""
        rect = tuple(a.rect[i] + (b.rect[i] - a.rect[i]) * w for i in range(4))
        keys = set(a.state) | set(b.state)
        st = {}
        for k in keys:
            va, vb = a.state.get(k, ANIM_DEFAULT.get(k, 0.0)), b.state.get(k, ANIM_DEFAULT.get(k, 0.0))
            st[k] = va + (vb - va) * w
        return DevicePose(b.kind, rect, st, b.opts)


def as_pose(kind: str, r) -> DevicePose:
    """Dikdörtgen (dönüşsüz) ya da duruş → duruş."""
    return r if isinstance(r, DevicePose) else DevicePose(kind, r)
