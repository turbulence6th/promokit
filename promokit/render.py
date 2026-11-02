"""Render hattı: senaryo → kare kare kompozisyon → ffmpeg.

Paralel render: zaman çizelgesi N parçaya bölünür, her süreç kendi parçasını kendi
ffmpeg'iyle kodlar, sonra parçalar kayıpsız birleştirilir (concat, `-c copy`). Her
şey zamanın SAF fonksiyonu (el pozları önceden hesaplanır, video okuyucu klip başından
sırayla okur) — parça sınırında dikiş oluşmaz.

Bir kare TEK yoldan çizilir (`_compose`): tek sahnenin karesi de iki sahne arası geçiş (morph)
karesi de cihazları `DevicePose` (kamera + anim durumunun tamamı) ile, kabloyu/Wi-Fi'ı/sesi ve
dokunuşları aynı fonksiyonlarla çizer. Geçişte ortak cihazın duruşu (dikdörtgen + TÜM anim
alanları) ara değerlenir — yeni bir anim alanı geçişe kendiliğinden akar.
"""
from __future__ import annotations

import copy
import math
from dataclasses import replace
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
import tempfile
import time

from PIL import Image, ImageChops, ImageDraw

from . import devices as dev
from . import layout as lay
from .gestures import HandModel, compile_gestures
from .hand import STYLES, Camera, HandConfig, HandTrack, Surface, draw_hand
from .layout import ANIM_DEFAULT, DevicePose, anim_state  # noqa: F401  (ANIM_DEFAULT/anim_state dışa açık)
from .media import Probe, SolidSource, StillSource, VideoSource, cover_crop
from .overlays import THEMES, Caption, KeyChips, background, draw_touch_fx, paste_rgba, render_text
from .story import Scene, Story, load
from .text import Fonts
from .touches import TouchLog
from .util import PromoError, clamp, ease, ffmpeg_bin, fmt_time, log, mix, rgb

HAND_FW = 0.045  # parmak genişliği / tablet ekran köşegeni (hand_scale=1; gerçek el ≈0.058)


class DeviceRT:
    """Bir cihazın ekran içeriği. Kaynak TEMBEL kurulur: HTML ekranın tarayıcısı ancak sahne
    gerçekten çizilince açılır (ve sayfanın jest zaman çizelgesi o ana kadar verilmiş olur)."""

    def __init__(self, kind: str, rect, ref, fps: float, max_zoom: float, opts: dict):
        self.kind = kind
        self.rect = rect  # dünya: ekran (x, y, w, h)
        self.ref = ref
        self.opts = opts
        self.fps = fps
        self.timeline = None
        self._source = None
        self.html = False
        self.probe = None
        self.crop = (0.0, 0.0, 1.0, 1.0)
        x, y, w, h = rect
        self.size = (max(2, int(w)), max(2, int(h)))
        if ref is None or ref.path is None:
            return
        pr = Probe(ref.path)
        self.probe = pr
        self.crop = cover_crop(pr.width, pr.height, w, h, ref.fit)
        cw, ch = pr.width * self.crop[2], pr.height * self.crop[3]
        if pr.kind == "html":
            # CSS boyutundan bağımsız: sahnedeki en büyük görüntülenme boyutunda çek
            self.html = True
            self.size = (max(2, int(w * max_zoom)), max(2, int(h * max_zoom)))
            return
        # Çözme boyutu: sahnedeki en büyük görüntülenme boyutu (yakınlaştırma dahil), kaynağı aşmadan
        k = min(1.0, max(w * max_zoom / cw, h * max_zoom / ch))
        self.size = (max(2, int(cw * k)), max(2, int(ch * k)))

    @property
    def source(self):
        if self._source is None:
            ref = self.ref
            if ref is None or (ref.path is None and ref.color is None):
                self._source = SolidSource((8, 9, 12), self.size)
            elif ref.color:
                self._source = SolidSource(rgb(ref.color), self.size)
            elif self.html:
                from .htmlscreen import HtmlSource
                pr = self.probe
                tl = dict(self.timeline or {"fps": self.fps, "gestures": [], "keys": [], "texts": []})
                tl["params"] = ref.params
                self._source = HtmlSource(ref.path, (pr.width, pr.height), self.size, self.fps, tl)
            elif self.probe.kind == "image":
                self._source = StillSource(ref.path, self.crop, self.size)
            else:
                self._source = VideoSource(self.probe, self.crop, self.size, self.fps, ref.clip_in, ref.speed,
                                           ref.loop, ref.clip_out)
        return self._source

    @property
    def still(self) -> bool:
        """İçerik zamandan bağımsız mı (durağan görüntü / düz renk)?"""
        return self.ref is None or self.ref.path is None or (self.probe is not None and self.probe.kind == "image")

    def media_to_screen(self, u: float, v: float):
        cx, cy, cw, ch = self.crop
        return ((u - cx) / cw, (v - cy) / ch)

    def close(self):
        if self._source is not None:
            self._source.close()
            self._source = None


# ── HTML sayfa zamanı ─────────────────────────────────────────────────────────
# Sayfa `in + t·hız` anında oynatılır (DeviceRT içeriği gibi) → sayfanın jestleri ve seçici çözümü de
# AYNI sayfa zamanında olmalı; daireler sahne zamanında kalır. Önceden jestler sahne zamanındaydı:
# `in = 1` ile t=2'deki dokunuş sayfada 1 sn ERKEN işliyordu.
def _page_contacts(cs, t_in: float, sp: float):
    # dataclasses.replace: temasın diğer alanları (araç, yüzey, yerel…) olduğu gibi taşınır
    return [replace(c, t=[t_in + tt * sp for tt in c.t], x=list(c.x), y=list(c.y), p=list(c.p)) for c in cs]


def _page_keys(ks, t_in: float, sp: float):
    return [replace(k, t=t_in + k.t * sp, dur=k.dur * sp) for k in ks]


def _page_texts(xs, t_in: float, sp: float):
    return [replace(x, t=t_in + x.t * sp, rate=x.rate / sp) for x in xs]


class _PageTimeResolver:
    """SelectorResolver'ı sahne zamanından sayfa zamanına çevirerek çağırır."""

    def __init__(self, inner, t_in: float, speed: float):
        self.inner, self.t_in, self.speed = inner, t_in, speed

    def __call__(self, selector, t, contacts, keys, texts, **kw):
        a, s = self.t_in, self.speed
        return self.inner(selector, a + t * s, _page_contacts(contacts, a, s), _page_keys(keys, a, s),
                          _page_texts(texts, a, s), **kw)

    def close(self):
        self.inner.close()


def _texts_at(sc: Scene, t: float) -> list:
    """Sahnenin `t` anında görünen başlıkları (SceneRT.caption_texts ile AYNI kural, Scene alanlarından)."""
    if sc.type not in ("device", "html"):
        return []
    out = [(sc.caption, sc.subcaption)] if (sc.caption or sc.subcaption) else []
    for c in sc.captions:
        t0 = float(c.get("t", 0.0))
        t1 = t0 + float(c.get("d", sc.duration - t0))
        if t0 - 0.01 <= t <= t1:
            out.append((str(c.get("text", "")), str(c.get("sub", ""))))
    return out


_SHADOWS: dict = {}   # dönmüş gövde gölgeleri (tür, boyut, açı, kapak) → (gölge, konum)


def _persp_coeffs(dst, src):
    """PERSPECTIVE dönüşüm katsayıları: ÇIKTI noktası (dst) → GİRDİ noktası (src); 4 eşleşme, 8 bilinmeyen."""
    A, bvec = [], []
    for (x, y), (u, v) in zip(dst, src):
        A.append([x, y, 1, 0, 0, 0, -x * u, -y * u])
        bvec.append(u)
        A.append([0, 0, 0, x, y, 1, -x * v, -y * v])
        bvec.append(v)
    n = 8
    M = [row[:] + [bv] for row, bv in zip(A, bvec)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        for r in range(n):
            if r != c and M[r][c] != 0.0:
                f = M[r][c] / M[c][c]
                M[r] = [a - f * b for a, b in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


def _aa_quad_mask(size, pts, ss: int = 4) -> Image.Image:
    m = Image.new("L", (size[0] * ss, size[1] * ss), 0)
    ImageDraw.Draw(m).polygon([(x * ss, y * ss) for x, y in pts], fill=255)
    return m.reduce(ss)


def _quad_box(quad):
    x0, y0 = math.floor(min(p[0] for p in quad)), math.floor(min(p[1] for p in quad))
    x1, y1 = math.ceil(max(p[0] for p in quad)), math.ceil(max(p[1] for p in quad))
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def _warp(img: Image.Image, src_box, quad):
    """`img`in `src_box` dikdörtgenini `quad`a (TL, TR, BR, BL) gerer → (RGBA parça, parçanın yeri).
    Ön-çarpımlı alfayla örneklenir (saydam kenarda koyu saçak olmasın); kenar süper-örnekli maskeyle."""
    x0, y0, w, h = _quad_box(quad)
    local = [(x - x0, y - y0) for x, y in quad]
    sx0, sy0, sx1, sy1 = src_box
    co = _persp_coeffs(local, [(sx0, sy0), (sx1, sy0), (sx1, sy1), (sx0, sy1)])
    out = img.convert("RGBa").transform((w, h), Image.Transform.PERSPECTIVE, co,
                                        resample=Image.Resampling.BICUBIC).convert("RGBA")
    out.putalpha(ImageChops.multiply(out.getchannel("A"), _aa_quad_mask((w, h), local)))
    return out, (x0, y0)


def _vgrad_rgb(size, top, bottom) -> Image.Image:
    g = Image.linear_gradient("L").resize(size, Image.Resampling.BILINEAR)
    return Image.composite(Image.new("RGB", size, bottom), Image.new("RGB", size, top), g)


def _rotate_layer(layer: Image.Image, deg: float) -> Image.Image:
    """Saat yönünde `deg` derece, merkez etrafında, genişleyerek. 90'ın katları kayıpsız (transpose):
    dönüp duran (ör. dikeye çevrilmiş) tabletin yazısı bulanıklaşmasın."""
    q = deg / 90.0
    if abs(q - round(q)) < 1e-6:
        n = int(round(q)) % 4
        T = Image.Transpose
        return layer if n == 0 else layer.transpose({1: T.ROTATE_270, 2: T.ROTATE_180, 3: T.ROTATE_90}[n])
    # önçarpımlı alfa: saydam kenarda koyu saçak kalmasın
    return layer.convert("RGBa").rotate(-deg, resample=Image.Resampling.BICUBIC, expand=True).convert("RGBA")


def _over(img: Image.Image, layer: Image.Image, pos, alpha: float = 1.0):
    """RGBA katmanı (düz alfa) kareye, ek saydamlıkla bindir."""
    if alpha <= 0.002:
        return
    if alpha < 0.999:
        layer = layer.copy()
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
    img.paste(layer, (int(round(pos[0])), int(round(pos[1]))), layer)


class SceneRT:
    """Bir sahnenin çalışma zamanı: kaynaklar, el izi, başlıklar."""

    def __init__(self, story: Story, sc: Scene, fonts: Fonts, bg: Image.Image, chips: KeyChips, ctx=None):
        self.story = story
        self.sc = sc
        self.fonts = fonts
        self.bg = bg
        self.chips = chips
        v = story.video
        self.W, self.H, self.fps = v.width, v.height, v.fps
        self.ctx = ctx if ctx is not None else _Layout(story, fonts)
        self.theme = v.theme
        self.devices: dict[str, DeviceRT] = {}
        self.track = None
        self.contacts = []
        self.keys = []
        self.captions = []
        self.card = None
        self.page = None
        self.anim_kfs: dict = {}
        if sc.type in ("title", "outro"):
            self._build_card()
            return
        if sc.type == "html":
            self._build_page()
            self._build_captions()
            return
        aspects = {}
        for kind, ref in (("tablet", sc.tablet), ("laptop", sc.laptop)):
            if ref and ref.path:
                pr = Probe(ref.path)
                aspects[kind] = pr.width / pr.height
        box = self.ctx.box
        opts = self.ctx.dev_opts
        self.rects = lay.place(self.W, self.H, sc.layout, aspects, box, self.ctx.cable_room, bool(sc.sound), opts)
        default_on = "tablet" if "tablet" in self.rects else "laptop"
        self.camera = lay.CameraPath(sc.camera, self.rects, self.W, self.H, default_on, box)
        # `s = "fit"` anahtar kareleri sayıya çevrilir (dönüş + içerik kutusu); en büyük ölçek, çözme
        # boyutuna da girer (büyüyen cihazın içeriği bulanıklaşmasın)
        self.anim_kfs = {k: lay.resolve_fit(kfs, k, self.rects[k], box, opts[k])
                         for k, kfs in sc.anim.items() if k in self.rects}
        for kind in ("laptop", "tablet"):
            if kind in self.rects:
                ref = sc.tablet if kind == "tablet" else sc.laptop
                smax = max([1.0] + [float(kf["s"]) for kf in self.anim_kfs.get(kind, []) if "s" in kf])
                self.devices[kind] = DeviceRT(kind, self.rects[kind], ref, self.fps, self.camera.max_zoom() * smax,
                                              opts[kind])
        self._build_touches()
        self._build_captions()

    # ── kurulum ──
    def _build_touches(self):
        sc, v = self.sc, self.story.video
        tab = self.devices.get("tablet")
        style = sc.hand or v.hand
        scale = sc.hand_scale if sc.hand_scale is not None else v.hand_scale
        if tab is None:
            if sc.gestures:
                log(f"⚠️ sahne #{sc.index + 1}: jestler tablet olmayan yerleşimde yok sayıldı")
            return
        x, y, w, h = tab.rect
        fw = HAND_FW * math.hypot(w, h) * scale
        cfg = HandConfig(fw=fw)
        t_in, sp = (tab.ref.clip_in, tab.ref.speed) if tab.ref is not None else (0.0, 1.0)
        resolver = None
        if tab.html and any(isinstance(g, dict) and ("on" in g or any(isinstance(v, str) for k, v in g.items()
                                                                        if k not in ("keys", "type", "ease")))
                            for g in sc.gestures):
            from .htmlscreen import SelectorResolver
            resolver = _PageTimeResolver(SelectorResolver(tab.ref.path, (tab.probe.width, tab.probe.height),
                                                          tab.crop, self.fps, v.latency * sp, tab.ref.params),
                                         t_in, sp)
        try:
            contacts, keys, texts = compile_gestures(sc.gestures, HandModel(fw / w, cfg.angle, w / h), resolver)
        finally:
            if resolver is not None:
                resolver.close()
        if sc.touches and sc.touches != "none":
            tl = TouchLog.load(sc.touches)
            win = tl.window(t_in, t_in + sc.duration * sp, sc.touch_offset)
            for c in win.contacts:
                c.t = [tt / sp for tt in c.t]
                pts = [tab.media_to_screen(a, b) for a, b in zip(c.x, c.y)]
                c.x = [p[0] for p in pts]
                c.y = [p[1] for p in pts]
                contacts.append(c)
            for k in win.keys:
                k.t /= sp
            keys.extend(win.keys)
        self.contacts = contacts
        self.keys = sorted(keys, key=lambda k: k.t)
        if tab.html:  # sayfa, daireleri süren AYNI temaslardan türeyen jestlerle — SAYFA zamanında — oynar
            from .htmlscreen import classify
            tab.timeline = classify(_page_contacts(contacts, t_in, sp), _page_keys(self.keys, t_in, sp),
                                    _page_texts(texts, t_in, sp), (tab.probe.width, tab.probe.height), tab.crop,
                                    self.fps, v.latency * sp)
            tab.timeline["params"] = tab.ref.params
        elif texts:
            log(f"⚠️ sahne #{sc.index + 1}: 'type' jesti yalnız HTML ekranlarda görünür")
        self.ripple = sc.ripple if sc.ripple is not None else v.ripple
        self.hand_style = None if style == "none" else STYLES[style]
        self.fw = fw
        if contacts and self.hand_style is not None:
            n = int(math.ceil(sc.duration * self.fps)) + 2
            self.track = HandTrack(contacts, Surface(tab.rect), self.fps, n, cfg)

    def _build_captions(self):
        sc, v = self.sc, self.story.video
        prev = self.story.scenes[sc.index - 1] if sc.index > 0 else None
        if sc.caption or sc.subcaption:
            # Süreklilik, önceki sahnenin geçiş başında GÖRÜNEN başlıklarına göre (zamanlı başlıkları
            # olan bir sahneden sonra aynı başlık sönüp yeniden girmesin — bulgu 10)
            pv = _texts_at(prev, prev.duration - self.story.overlap(sc)) if prev is not None else []
            cont = pv == [(sc.caption, sc.subcaption)]
            cont_title = bool(sc.caption) and bool(pv) and pv[0][0] == sc.caption
            cap = Caption(self.fonts, self.W, self.H, sc.caption, sc.subcaption, self.theme, v.caption_size)
            # (başlangıç, bitiş, başlık nesnesi, tamamen-sürüyor, başlık-sürüyor)
            self.captions.append((0.0, sc.duration + 1.0, cap, cont, cont_title))
        for c in sc.captions:
            t0 = float(c.get("t", 0.0))
            t1 = t0 + float(c.get("d", sc.duration - t0))
            cap = Caption(self.fonts, self.W, self.H, str(c.get("text", "")), str(c.get("sub", "")), self.theme,
                          v.caption_size)
            self.captions.append((t0, t1, cap, False, False))

    def _build_page(self):
        """Tam ekran HTML sahnesi: CSS görünüm alanının KISA kenarı 1080 px (sayfa en-boya göre
        yerleşir); sayfa, cihaz sahnelerinin yerleşimini `promo.params.layouts` (komşu sahnelerin
        GERÇEK medya oranlarıyla) ve içerik kutusunu `promo.params.content` ([x, y, w, h], CSS px —
        cihazların sığdırıldığı kutu; başlık bandı onun üstünde boş kalır) ile bilir — açılıştaki
        çizim cihazı TAM o yere bırakıp kesintisiz devredebilsin."""
        from .htmlscreen import HtmlSource
        W, H = self.W, self.H
        k = min(W, H) / 1080.0
        css = (int(round(W / k)), int(round(H / k)))
        aspects = self.ctx.page_aspects(self.sc.index)
        box = self.ctx.box
        layouts = {}
        for name in ("duo", "tablet", "laptop"):
            rects = lay.place(W, H, name, aspects, box, self.ctx.cable_room, opts=self.ctx.dev_opts)
            layouts[name] = {kd: [v / k for v in r] for kd, r in rects.items()}
        bx0, by0, bx1, by1 = box
        params = dict(self.sc.params)
        # captionTop/safeBottom: sayfanın KENDİ başlığı ve içeriği de platform şeritlerinden kaçsın (9:16)
        params.update({"layouts": layouts, "stage": [css[0], css[1]],
                       "content": [bx0 / k, by0 / k, (bx1 - bx0) / k, (by1 - by0) / k],
                       "captionTop": lay.caption_top(W, H) / k, "safeBottom": lay.safe_bottom(W, H) / k})
        tl = {"fps": self.fps, "gestures": [], "keys": [], "texts": [], "params": params}
        self.page = HtmlSource(self.sc.page, css, (W, H), self.fps, tl)

    def _build_card(self):
        sc, v = self.sc, self.story.video
        u = min(self.W, self.H)
        kt, ks = Caption._k(self.W, self.H)   # 9:16 telefonda: başlıkla aynı büyütme (alt satır en çok)
        th = THEMES.get(self.theme, THEMES["light"])
        items = []
        if sc.title:
            items.append(("title", render_text(self.fonts, sc.title, u * 0.052 * kt, 700, th["text"], tracking=-0.02,
                                               max_width=self.W * 0.84), u * 0.006 * kt))
        if sc.subtitle:
            items.append(("subtitle", render_text(self.fonts, sc.subtitle, u * 0.030 * ks, 400, th["muted"],
                                                  max_width=self.W * 0.84), u * 0.012 * kt))
        if sc.badge:
            items.append(("badge", _badge(self.fonts, sc.badge, u * kt), u * 0.01))
        total = sum(im.size[1] + gap for _, im, gap in items)
        self.card = (items, (self.H - total) / 2)

    # ── duruş ──
    def anim(self, kind: str, t: float | None) -> dict:
        return anim_state(self.anim_kfs.get(kind, []), t)

    def poses(self, cam, t: float | None = None) -> dict:
        """Cihaz → `DevicePose`: kamera + anim durumunun TAMAMI (x/y/s dikdörtgene, gerisi duruma)."""
        out = {}
        for k, d in self.devices.items():
            a = self.anim(k, t)
            x, y, w, h = cam.rect(d.rect)
            if t is not None and lay.in_fit_segment(self.anim_kfs.get(k, []), t):   # dönerken de sığsın
                a["s"] = min(a["s"], lay.fit_scale(k, d.rect, self.ctx.box, a["rot"], d.opts))
            s = a["s"]
            out[k] = DevicePose(k, (x + w * (1 - s) / 2 + a["x"] * h, y + h * (1 - s) / 2 + a["y"] * h, w * s, h * s), a,
                                d.opts)
        return out

    def camera_at(self, t: float) -> Camera:
        """Kamera + BANT KURALI: hiçbir cihazın görünen gövdesi başlık bandına (içerik kutusunun üstü)
        girmez — girecekse içerik aşağı kaydırılır. Kullanıcı (2026-10-20): "ekrana dikkat et, yazının
        altına getirme" — bir tur yakınlaşınca başlığın arkasına beyaz bir kutu giriyordu ve "bug gibi"
        duruyordu; kutu kaldırıldı, içerik banda hiç girmiyor. Kaydırma kamerayı bütünüyle taşır
        (duruşlar kamerada doğrusal) → normal kare ve geçiş aynı kuralla çizer."""
        cam = self.camera.at(t)
        poses = self.poses(cam, t)
        if not poses:
            return cam
        d = self.ctx.box[1] - min(p.visible_bbox()[1] for p in poses.values())
        if d <= 0:
            return cam
        return Camera(cam.zoom, cam.focus, (cam.anchor[0], cam.anchor[1] + d))

    def stage_rects(self, cam, t: float | None = None) -> dict:
        return {k: p.rect for k, p in self.poses(cam, t).items()}

    def content(self, kind: str, t: float, size) -> Image.Image:
        d = self.devices[kind]
        ref = d.ref
        t_media = (ref.clip_in if ref else 0.0) + t * (ref.speed if ref else 1.0)
        return d.source.get(t_media, size)

    def media_key(self, kind: str):
        ref = self.devices[kind].ref
        return None if ref is None else (ref.path, ref.color, ref.clip_in, ref.speed, repr(ref.params))

    # ── çizim ──
    def draw_device(self, img: Image.Image, kind: str, dp: DevicePose, content, t: float,
                    receiver: Image.Image | None = None, alpha: float = 1.0):
        """Cihazı duruşuyla çiz: dönüş (`rot`), ekran (`on`), kapak (`lid`), saydamlık (`o` × `alpha`).
        `content`: None (sahnenin kendi içeriği, `t` anında) · görüntü · `boyut → görüntü` çağrılabilir."""
        d = self.devices[kind]
        st = dp.state
        a = alpha * clamp(st.get("o", 1.0))
        on, lid, rot = clamp(st.get("on", 1.0)), clamp(st.get("lid", 1.0)), float(st.get("rot", 0.0))
        sx, sy, sw, sh = dp.rect
        isw, ish = max(2, int(round(sw))), max(2, int(round(sh)))
        X, Y = int(round(sx)), int(round(sy))
        W, H = img.size
        bx0, by0, bx1, by1 = dp.body_bbox()
        if bx0 > W or by0 > H or bx1 < 0 or by1 < 0 or a <= 0.002:
            return
        ch = dev.chrome(kind, isw, ish, d.opts)
        if callable(content):
            content = content((isw, ish)) if on > 0.001 else None
        elif content is None:
            content = self.content(kind, t, (isw, ish)) if on > 0.001 else None
        if content is not None and content.size != (isw, ish):
            content = content.resize((isw, ish), Image.Resampling.BICUBIC)
        if on < 0.999:  # ekran kapalı → açılıyor: siyahtan içeriğe
            black = Image.new("RGB", (isw, ish), (6, 7, 10))
            content = black if content is None else Image.blend(black, content, on)
        turned = abs(rot) >= 1e-3
        closing = kind == "laptop" and lid < 0.999
        if not turned and not closing and a >= 0.999:  # en sık yol: doğrudan yapıştır
            ox, oy = ch.origin
            sox, soy = ch.shadow_origin
            img.paste(ch.shadow, (X + sox, Y + soy), ch.shadow)
            img.paste(ch.body, (X + ox, Y + oy), ch.body)
            img.paste(content, (X, Y), ch.mask)
            if ch.glare is not None:
                img.paste(ch.glare, (X, Y), ch.glare)
            if ch.notch is not None:
                img.paste((10, 10, 12), (X, Y), ch.notch)
            if receiver is not None:
                receiver.paste(255, (X + ox, Y + oy), ch.body.getchannel("A"))
            return
        layer, (lx, ly) = self._device_layer(kind, ch, content, lid, shadow=not turned)
        if not turned:
            pos = (X + lx, Y + ly)
        else:
            # gövde katmanı gövde merkezinde döner; gölge DÖNMÜŞ silüetten, aşağı kayık yeniden kurulur
            layer = _rotate_layer(layer, rot)
            cx, cy = dp.pivot
            pos = (cx - layer.size[0] / 2, cy - layer.size[1] / 2)
            # silüet içerikten bağımsız (yalnız tür/boyut/açı/kapak) → dönüp duran cihazda gölge her kare
            # yeniden bulanıklaştırılmaz (saf işlev: önbellek parçalar arası dikiş yapmaz)
            key = (kind, isw, ish, rot, lid if closing else 1.0, tuple(sorted(d.opts.items())))
            hit = _SHADOWS.get(key)
            if hit is None:
                hit = dev.drop_shadow(layer.getchannel("A"), min(isw, ish))
                if len(_SHADOWS) > 16:
                    _SHADOWS.clear()
                _SHADOWS[key] = hit
            sh, (dx, dy) = hit
            _over(img, sh, (pos[0] + dx, pos[1] + dy), a)
        _over(img, layer, pos, a)
        if receiver is not None and a > 0.5:
            receiver.paste(255, (int(round(pos[0])), int(round(pos[1]))),
                           layer.getchannel("A").point(lambda v: 255 if v > 200 else 0))

    def _device_layer(self, kind, ch, content, lid, shadow: bool):
        """Cihazı (isteğe bağlı gölgesiyle) RGBA katmana çiz → (katman, katmanın ekran sol-üstüne göre yeri).
        Gölgesiz katman = gövde kutusu → dönüş merkezi gövde merkezi."""
        ox, oy = ch.origin
        bw, bh = ch.body.size
        sox, soy = ch.shadow_origin
        if shadow:
            x0, y0 = min(ox, sox), min(oy, soy)
            x1 = max(ox + bw, sox + ch.shadow.size[0])
            y1 = max(oy + bh, soy + ch.shadow.size[1])
        else:
            x0, y0, x1, y1 = ox, oy, ox + bw, oy + bh
        L = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
        X, Y = -x0, -y0  # ekran sol-üstü katmanda
        if kind == "laptop" and lid < 0.999:
            self._closing_laptop(L, ch, X, Y, content, lid, shadow)
        else:
            if shadow:
                L.alpha_composite(ch.shadow, (X + sox, Y + soy))
            L.alpha_composite(ch.body, (X + ox, Y + oy))
            L.paste(content, (X, Y), ch.mask)
            if ch.glare is not None:
                L.alpha_composite(ch.glare, (X, Y))
            if ch.notch is not None:
                L.paste((10, 10, 12, 255), (X, Y), ch.notch)
        return L, (x0, y0)

    def _closing_laptop(self, L, ch, X, Y, content, lid, shadow: bool):
        """Kapak kapanması TAM KARŞIDAN (`devices.lid_view`): kapak menteşe etrafında döner — yüzü L·sin φ
        boyunda kalır ve kameradan dönerken kararır, üst kenarı yaklaştıkça hafifçe genişler; kenarın
        kalınlığı yüzün üstünde belirir ve kapalıyken TABANLA AYNI genişlikte ince bir çizgi olarak kalır.
        Klavye ve kapağın sırtı görünmez. Önceki çizimde kapak k³ ile ezilip (ilk yarıda çöküş, sonra
        sürünme) kapalıyken tabandan DAR ayrı bir şerit bırakıyordu ("garip duruyor", kullanıcı
        2026-10-20). Gölge GÖRÜNEN kapaktan (bulgu 2: açık kapağın gölgesi havada asılı kalıyordu)."""
        ox, oy = ch.origin
        sox, soy = ch.shadow_origin
        bw, bh = ch.body.size
        split = ch.split
        sw = ch.screen[0]
        k = clamp(lid)
        spec = ch.spec or dev.LAPTOP            # dizüstü modelinin oranları (MacBook / Windows)
        face, edge, reach, sink = dev.lid_view(k, split, sw * spec["lid_t"], sw * spec["hinge"] * dev.HINGE_SHOW)
        lid_l = sw * spec["base_extra"]
        lid_w = bw - 2 * lid_l
        cx = bw / 2
        top_w = lid_w + (bw - lid_w) * reach    # kapağın yaklaşan üst kenarı — kapalıyken tabanın ön yüzü kadar
        bx, by = X + ox, Y + oy
        pad = ox - sox                          # sprite (0,0) → gölge tuvalinde (pad, pad)
        parts = []
        if face > 1.0:      # kapak yüzü: ekran + çerçeve, menteşede lid_w → üstte top_w
            lid_img = ch.body.crop((0, 0, bw, split)).copy()
            lid_img.paste(content, (-ox, -oy), ch.mask)
            if ch.glare is not None:
                lid_img.alpha_composite(ch.glare, (-ox, -oy))
            if ch.notch is not None:
                lid_img.paste((10, 10, 12, 255), (-ox, -oy), ch.notch)
            # önce dikeyde ALAN-ORTALAMALI küçült (perspektif dönüşümü süzgeçsiz örnekler: ezilen ekran içeriği
            # kapanış boyunca titreşirdi), sonra hafif eğ
            fh = max(2, int(round(face)))
            if fh < split:
                lid_img = lid_img.resize((bw, fh), Image.Resampling.BOX)
            quad = [(cx - top_w / 2, split - face), (cx + top_w / 2, split - face), (lid_l + lid_w, split), (lid_l, split)]
            part, (qx, qy) = _warp(lid_img, (lid_l, 0, lid_l + lid_w, lid_img.size[1]), quad)
            dark = Image.new("RGBA", part.size, (0, 0, 0, int(150 * (1 - k))))
            part.alpha_composite(Image.composite(dark, Image.new("RGBA", part.size, (0, 0, 0, 0)), part.getchannel("A")))
            parts.append((part, qx, qy))
        if edge > 0.5:      # kapağın üst kenarı (kalınlık): alüminyum; kapandıkça menteşe bandına iner (`sink`)
            rim = ch.rim or (200, 204, 210)
            x0, y0 = cx - top_w / 2, split - face - edge + sink
            w, h = max(1, int(math.ceil(top_w))), max(1, int(math.ceil(edge)))
            part = _vgrad_rgb((w, h), mix(rim, (255, 255, 255), 0.35), mix(rim, (0, 0, 0), 0.08)).convert("RGBA")
            m = Image.new("L", (w * 4, h * 4), 0)
            # üst köşeler yuvarlak, ALT köşeler kare: kapalıyken tabanla uçlarda da boşluksuz birleşir
            ImageDraw.Draw(m).rounded_rectangle((0, 0, w * 4 - 1, h * 4 - 1), radius=min(h * 2, sw * 0.03), fill=255,
                                                corners=(True, True, False, False))
            part.putalpha(m.reduce(4))
            parts.append((part, int(math.floor(x0)), int(math.floor(y0))))
        if shadow and ch.shadow_floor is not None:
            L.alpha_composite(ch.shadow_floor, (X + sox, Y + soy))
            if parts:
                lm = Image.new("L", ch.shadow_floor.size, 0)
                for part, qx, qy in parts:
                    lm.paste(part.getchannel("A"), (int(round(pad + qx)), int(round(pad + qy))), part.getchannel("A"))
                L.alpha_composite(dev.lid_shadow(lm, sw), (X + sox, Y + soy))
        L.alpha_composite(ch.body.crop((0, split, bw, bh)), (bx, by + split))   # menteşe şeridi (ek yeri) + taban
        for part, qx, qy in parts:
            L.alpha_composite(part, (int(round(bx + qx)), int(round(by + qy))))

    def draw_cable(self, img: Image.Image, poses: dict, t: float, fx: bool = False, alpha: float = 1.0):
        """USB-C kablo (cable.py): tabletin SAĞ kenarındaki yuvaya; kablo tabletin arkasından geçer.
        Tabletten ÖNCE çizilir (`fx=False`); "takıldı" vurgusu tabletten SONRA (`fx=True`)."""
        cb = self.sc.cable
        if not cb or "laptop" not in poses or "tablet" not in poses or alpha <= 0.002:
            return
        from . import cable
        an = cable.anchors(poses["laptop"], poses["tablet"], img.size)
        (cable.draw_fx if fx else cable.draw)(img, an, cb, t, alpha)

    def draw_wifi(self, img: Image.Image, poses: dict, t: float, alpha: float = 1.0):
        """Cihazlar arasında Wi-Fi simgesi: yaylar sırayla belirir, sonra yavaşça nabız atar. Yan yana
        yerleşimde kapağın kenarı ↔ tablet arasında, üst üste (dikey kadraj) yerleşimde MacBook'un altı
        ↔ tabletin üstü arasında."""
        wf = self.sc.wifi
        if not wf or "laptop" not in poses or "tablet" not in poses or alpha <= 0.002:
            return
        t0, dur = float(wf.get("t", 1.0)), float(wf.get("dur", 3.0))
        if t < t0:
            return
        k_out = 1.0 - ease((t - t0 - dur) / 0.5, "in")
        if k_out <= 0.01:
            return
        Lp, Tp = poses["laptop"], poses["tablet"]
        lw, lh = Lp.rect[2], Lp.rect[3]
        th = Tp.rect[3]
        lbb, tbb = Lp.body_bbox(), Tp.body_bbox()
        u = min(img.size)
        if tbb[1] >= lbb[3] - 0.02 * th:   # üst üste
            gap = tbb[1] - lbb[3]
            R = max(u * 0.012, min(gap * 0.5, th * 0.16))     # simge ~1,12R yüksek → aralığa sığar
            cx = (tbb[0] + tbb[2]) / 2
            cy = (lbb[3] + tbb[1]) / 2 + R * 0.44   # simge (yaylar + nokta) aralıkta ortalı
        else:
            # simge tabletin orta yüksekliğinde: orada boşluk KAPAĞIN kenarından başlar (alt gövdenin
            # taşan kenarından değil — öyle ölçülünce boşluk yarıya iniyor, simge seçilmiyordu)
            g = dev.geometry("laptop", lw, lh, Lp.opts)
            lr = dev.lid_marks(lw, Lp.opts)["lid_right"]
            lid_r = Lp.bbox(lw - lr, g.top, lr, lh + lw * dev.laptop_spec(Lp.opts)["chin"])[2]   # kapağın sağ kenarı
            gap = tbb[0] - lid_r
            R = max(u * 0.012, min(gap * 0.42, th * 0.16))
            cx = (lid_r + tbb[0]) / 2
            cy = (tbb[1] + tbb[3]) / 2
        S = 3
        size = int(R * 2.6)
        lay_ = Image.new("RGBA", (size * S, size * S), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay_)
        ox, oy = size * S / 2, size * S * 0.72          # simgenin dibi (nokta)
        col = rgb("accent")
        for i, rr in enumerate((0.0, 0.42, 0.72, 1.0)):
            a_in = ease((t - t0 - 0.12 * i) / 0.3, "out")
            pulse = 0.78 + 0.22 * math.sin((t - t0) * 4.2 - i * 0.9)
            a = int(255 * a_in * pulse * k_out * clamp(alpha))
            if a <= 2:
                continue
            if rr == 0.0:
                r0 = R * 0.12 * S
                d.ellipse((ox - r0, oy - r0, ox + r0, oy + r0), fill=col + (a,))
            else:
                r = R * rr * S
                d.arc((ox - r, oy - r, ox + r, oy + r), 225, 315, fill=col + (a,), width=max(2, int(R * 0.13 * S)))
        lay_ = lay_.reduce(S)
        img.paste(lay_, (int(cx - size / 2), int(cy - size * 0.72)), lay_)

    def draw_sound(self, img: Image.Image, poses: dict, t: float, alpha: float = 1.0):
        """Tabletin hoparlörleri (iki KISA kenar) dışında ses dalgaları: Wi-Fi simgesinin dilinde yaylar,
        sırayla belirir, hafifçe nabız atar, söner. Cihazın yerel çerçevesinde → `rot` ile döner."""
        sd = self.sc.sound
        if not sd or "tablet" not in poses or alpha <= 0.002:
            return
        t0, dur = float(sd.get("t", 0.5)), float(sd.get("dur", 3.0))
        if t < t0:
            return
        k_out = 1.0 - ease((t - t0 - dur) / 0.5, "in")
        if k_out <= 0.01:
            return
        Tp = poses["tablet"]
        w, h = Tp.rect[2], Tp.rect[3]
        g = dev.geometry("tablet", w, h)
        R = h * 0.11
        x0, y0, x1, y1 = Tp.body_bbox()
        m = R * 1.6 + 4
        W, H = img.size
        X0, Y0 = max(0, int(x0 - m)), max(0, int(y0 - m))
        X1, Y1 = min(W, int(x1 + m) + 1), min(H, int(y1 + m) + 1)
        if X1 <= X0 or Y1 <= Y0:
            return
        S = 3
        lay_ = Image.new("RGBA", ((X1 - X0) * S, (Y1 - Y0) * S), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay_)
        col = rgb("accent")
        width = max(2, int(R * 0.12 * S))
        drawn = False
        for side in (-1, 1):
            ex = g.left if side < 0 else g.right                 # gövdenin kısa kenarı (yerel)
            cxl, cyl = ex + side * R * 0.25, h / 2
            for i, rr in enumerate((0.45, 0.75, 1.05)):
                a_in = ease((t - t0 - 0.12 * i) / 0.3, "out")
                pulse = 0.78 + 0.22 * math.sin((t - t0) * 4.2 - i * 0.9)
                a = int(255 * a_in * pulse * k_out * clamp(alpha))
                if a <= 2:
                    continue
                pts = []
                for j in range(15):
                    ang = math.radians(-38 + 76 * j / 14)          # dışa doğru ±38°
                    px, py = Tp.local(cxl + side * R * rr * math.cos(ang), cyl + R * rr * math.sin(ang))
                    pts.append(((px - X0) * S, (py - Y0) * S))
                d.line(pts, fill=col + (a,), width=width, joint="curve")
                for p in (pts[0], pts[-1]):                      # yuvarlak uçlar
                    d.ellipse((p[0] - width / 2, p[1] - width / 2, p[0] + width / 2, p[1] + width / 2), fill=col + (a,))
                drawn = True
        if drawn:
            lay_ = lay_.reduce(S)
            img.paste(lay_, (X0, Y0), lay_)

    def draw_interaction(self, img: Image.Image, t: float, dp: DevicePose | None, receiver, alpha: float = 1.0):
        """Dokunuş efektleri + el + tuş çipleri, tabletin DURUŞUYLA (kamera + anim x/y/s/rot) — cihaz
        kayar, büyür, dönerse daireler ve çipler onunla gider."""
        tab = self.devices.get("tablet")
        if tab is None or dp is None or not (self.contacts or self.keys) or alpha <= 0.002:
            return
        x, y, w, h = tab.rect
        k = dp.rect[2] / w   # dünya → sahne ölçeği
        to_stage = dp.point
        r0 = self.fw * k * 0.62
        if self.ripple and alpha > 0.5:
            draw_touch_fx(img, self.contacts, t, to_stage, r0, dots=self.hand_style is None)
        if self.track is not None:
            pose = self.track.at(int(round(t * self.fps)))
            if pose is not None:
                if alpha < 0.999:
                    pose = copy.copy(pose)
                    pose.visible *= alpha
                cam = Camera(k, (x, y), (dp.rect[0], dp.rect[1]))   # isteğe bağlı el `rot`u izlemez
                draw_hand(img, pose, cam, self.hand_style, receiver)
        if self.keys and alpha > 0.5:
            kx, ky = self.sc.keys_at or (0.5, 0.86)
            cx, cy = to_stage(kx, ky)
            self.chips.draw(img, self.keys, t, cx, cy)

    def draw_captions(self, img: Image.Image, t: float, alpha: float = 1.0,
                      title_alpha: float | None = None, sub_alpha: float | None = None):
        """`title_alpha`/`sub_alpha` verilirse geçişte başlık ve alt satır ayrı karışır."""
        if not self.captions or alpha <= 0.002:
            return
        top = lay.caption_top(self.W, self.H)
        for t0, t1, cap, cont, cont_title in self.captions:
            if t0 - 0.01 <= t <= t1:
                t_in = 1.0 if cont else (t - t0 - 0.12) / 0.5
                t_out = 0.0 if t1 >= self.sc.duration else (t - (t1 - 0.3)) / 0.3
                if title_alpha is not None or sub_alpha is not None:
                    cap.draw(img, self.W / 2, top, 1.0, t_out, alpha, title_static=True,
                             title_alpha=1.0 if title_alpha is None else title_alpha, sub_alpha=sub_alpha)
                else:
                    cap.draw(img, self.W / 2, top, t_in, t_out, alpha, title_static=cont_title)

    def caption_texts(self, t: float):
        return [(c.title_text, c.sub_text) for t0, t1, c, *_ in self.captions if t0 - 0.01 <= t <= t1]

    def frame(self, t: float) -> Image.Image:
        if self.card is not None:
            return self._card_frame(t)
        if self.page is not None:
            img = self.page.get(t, (self.W, self.H)).copy()
            self.draw_captions(img, t)
            return img
        img = self.bg.copy()
        cam = self.camera_at(t)
        receiver = Image.new("L", img.size, 0) if self.track is not None else None
        poses = self.poses(cam, t)
        items = [(k, self, t, poses[k], None, 1.0) for k in ("laptop", "tablet") if k in poses]
        _compose(img, items, [(self, t, poses, 1.0, 1.0)], receiver)
        self.draw_captions(img, t)
        return img

    def _card_frame(self, t: float) -> Image.Image:
        img = self.bg.copy()
        items, y = self.card
        u = min(self.W, self.H)
        for i, (_, im, gap) in enumerate(items):
            k = (t - 0.15 - 0.11 * i) / 0.55
            a = ease(k, "out")
            rise = (1 - ease(k, "out")) * u * 0.022
            paste_rgba(img, im, (self.W - im.size[0]) / 2, y + rise, a)
            y += im.size[1] + gap
        return img

    def close(self):
        for d in self.devices.values():
            d.close()
        if self.page is not None:
            self.page.close()


def _compose(img: Image.Image, items, owners, receiver):
    """Bir karenin cihaz katmanı — TEK sahne karesi de geçiş (morph) karesi de buradan çizilir.

    items:  [(tür, sahne, t, duruş, içerik, saydamlık)] çizim sırasıyla (MacBook → tablet)
    owners: [(sahne, t, duruşlar, ağırlık, dokunuş_saydamlığı)] — her sahnenin kablo/Wi-Fi/ses/dokunuş
            katmanı, kendi zamanında ve bu karedeki cihaz duruşlarıyla (geçişte ağırlıkla söner)."""
    for kind, rt, t, pose, content, alpha in items:
        if kind == "tablet":  # kablo tabletin ARKASINDAN geçer
            for ort, ot, ops, wt, _ in owners:
                ort.draw_cable(img, ops, ot, alpha=wt)
        rt.draw_device(img, kind, pose, content, t, receiver, alpha)
    for ort, ot, ops, wt, _ in owners:
        ort.draw_cable(img, ops, ot, fx=True, alpha=wt)
        ort.draw_wifi(img, ops, ot, wt)
        ort.draw_sound(img, ops, ot, wt)
    for ort, ot, ops, wt, ta in owners:
        ort.draw_interaction(img, ot, ops.get("tablet"), receiver, ta)


def _badge(fonts: Fonts, text: str, u: float) -> Image.Image:
    size = u * 0.024
    txt = render_text(fonts, text, size, 600, "accent")
    tw, th = txt.size
    w, h = tw + int(size * 0.9), int(size * 2.1)
    S = 3
    m = Image.new("L", (w * S, h * S), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w * S - 1, h * S - 1), radius=h * S / 2, fill=255)
    out = Image.new("RGBA", (w, h), rgb("#e9efff") + (0,))
    out.putalpha(m.reduce(S))
    out.alpha_composite(txt, ((w - tw) // 2, (h - th) // 2))
    return out


class _Layout:
    """Senaryo düzeyinde yerleşim bağlamı (her parça sürecinde aynı → deterministik):
    - `box`: içerik kutusu — başlık bandı senaryodaki EN UZUN (sarılmış) başlıktan ve `caption_size`tan;
      cihaz yerleşimi, kamera, `s = "fit"` ve tam ekran sayfanın `promo.params.content`i HEP bunu kullanır
    - `cable_room`: senaryoda kablo varsa üst üste (dikey) MacBook+tablet yerleşimi kabloya iki yandan
      pay ayırır (bütün duo sahnelerinde → sahneler arasında cihazlar zıplamaz)
    - `dev_opts`: cihaz → çizim seçenekleri (dizüstü modeli + kaplaması, tablet rengi) — senaryo düzeyinde
      tek: yerleşim, duruş, gövde çizimi ve kablo yuvası hep aynı modeli görür"""

    def __init__(self, story: Story, fonts: Fonts):
        v = story.video
        W, H = v.width, v.height
        band = Caption.band(W, H, v.caption_size)
        for sc in story.scenes:
            if sc.type not in ("device", "html"):
                continue
            texts = _texts_at(sc, -1.0) + [(str(c.get("text", "")), str(c.get("sub", ""))) for c in sc.captions]
            for title, sub in dict.fromkeys(texts):
                band = max(band, Caption(fonts, W, H, title, sub, v.theme, v.caption_size).extent())
        self.box = lay.content_box(W, H, v.caption_size, band)
        self.cable_room = any(sc.cable for sc in story.scenes)
        self.dev_opts = {"laptop": {"finish": v.laptop_finish, "model": v.laptop_model}, "tablet": {"color": v.tablet_color}}
        self.story = story
        self._aspects = {}

    def page_aspects(self, i: int) -> dict:
        """Tam ekran sayfanın `layouts`u için cihaz ekran oranları: SONRAKİ (yoksa önceki) cihaz
        sahnesinin gerçek medyası; medya yoksa şablon görünüm alanı (1400/920 · dizüstü modelinin: 1470/956 ·
        1440/900)."""
        if i not in self._aspects:
            out = {}
            scenes = self.story.scenes
            for j in (i + 1, i - 1):
                if 0 <= j < len(scenes) and scenes[j].type == "device":
                    for kind in ("tablet", "laptop"):
                        ref = getattr(scenes[j], kind)
                        if kind not in out and ref is not None and ref.path:
                            pr = Probe(ref.path)
                            out[kind] = pr.width / pr.height
            self._aspects[i] = {k: out.get(k, lay.default_aspect(k, self.dev_opts.get(k))) for k in lay.DEFAULT_ASPECT}
        return self._aspects[i]


class Timeline:
    def __init__(self, story: Story):
        self.story = story
        self.fonts = Fonts(story.video.font)
        v = story.video
        self.bg = background(v.width, v.height, v.theme, v.background)
        self.chips = KeyChips(self.fonts, min(v.width, v.height))
        self.layout = _Layout(story, self.fonts)
        self.box = self.layout.box
        self.rts: dict[int, SceneRT] = {}

    def _rt(self, sc: Scene) -> SceneRT:
        rt = self.rts.get(sc.index)
        if rt is None:
            rt = SceneRT(self.story, sc, self.fonts, self.bg, self.chips, self.layout)
            self.rts[sc.index] = rt
        return rt

    def frame(self, t: float) -> Image.Image:
        st = self.story
        active = [sc for sc in st.scenes if sc.start - 1e-9 <= t < sc.start + sc.duration]
        if not active:
            active = [st.scenes[-1]]
        # Geride kalan sahnelerin kaynaklarını kapat
        for idx in list(self.rts):
            sc = st.scenes[idx]
            if sc.start + sc.duration < t - 0.05:
                self.rts.pop(idx).close()
        if len(active) == 1:
            sc = active[0]
            return self._rt(sc).frame(min(t - sc.start, sc.duration - 1e-6))
        a, b = active[-2], active[-1]
        ov = st.overlap(b)
        w = ease((t - b.start) / ov, "inout") if ov > 0 else 1.0
        A, B = self._rt(a), self._rt(b)
        kind = b.transition or st.video.transition
        if kind in ("auto", "morph") and A.devices and B.devices:
            return self._morph(A, t - a.start, B, t - b.start, w)
        fa = A.frame(t - a.start)
        fb = B.frame(t - b.start)
        if kind == "slide":
            W = st.video.width
            out = Image.new("RGB", fa.size)
            off = int(round(W * w))
            out.paste(fa, (-off, 0))
            out.paste(fb, (W - off, 0))
            return out
        return Image.blend(fa, fb, w)

    def _morph(self, A: SceneRT, ta: float, B: SceneRT, tb: float, w: float) -> Image.Image:
        """İki cihaz sahnesi arası, NORMAL karelerle aynı yoldan (`_compose`): ortak cihazın duruşu
        (dikdörtgen + tüm anim alanları: rot/on/lid/o/…) ara değerlenir, içerik çapraz geçer; yalnız
        birinde olan cihaz kendi duruşuyla söner; kablo/Wi-Fi/ses ve dokunuşlar sahneleriyle karışır."""
        img = self.bg.copy()
        ca, cb = A.camera_at(ta), B.camera_at(tb)
        pa, pb = A.poses(ca, ta), B.poses(cb, tb)
        receiver = Image.new("L", img.size, 0)
        own_a, own_b, items = {}, {}, []
        for kind in ("laptop", "tablet"):
            if kind in pa and kind in pb:
                p = DevicePose.lerp(pa[kind], pb[kind], w)
                own_a[kind] = own_b[kind] = p
                da, db = A.devices[kind], B.devices[kind]
                if da.still and db.still and A.media_key(kind) == B.media_key(kind):
                    content = None   # aynı durağan içerik: B'ninki
                else:   # HTML/video: aynı dosya bile olsa durum/zaman farklı → çapraz geçiş (sıçrama yok)
                    content = (lambda size, kind=kind: Image.blend(A.content(kind, ta, size), B.content(kind, tb, size), w))
                items.append((kind, B, tb, p, content, 1.0))
            elif kind in pa:
                p = pa[kind].scaled(1 - 0.05 * w)
                own_a[kind] = p
                items.append((kind, A, ta, p, None, 1 - w))
            elif kind in pb:
                p = pb[kind].scaled(0.95 + 0.05 * w)
                own_b[kind] = p
                items.append((kind, B, tb, p, None, w))
        _compose(img, items, [(A, ta, own_a, 1 - w, 1 - ease(w / 0.6, "inout")),
                              (B, tb, own_b, w, ease((w - 0.4) / 0.6, "inout"))], receiver)
        ca_, cb_ = A.caption_texts(ta), B.caption_texts(tb)
        if ca_ == cb_:
            B.draw_captions(img, tb)
        elif ca_ and cb_ and ca_[0][0] == cb_[0][0]:
            # aynı başlık: başlık yerinde durur; yalnız alt satır sırayla değişir
            A.draw_captions(img, ta, title_alpha=0.0, sub_alpha=1 - ease(w / 0.5, "inout"))
            B.draw_captions(img, tb, title_alpha=1.0, sub_alpha=ease((w - 0.5) / 0.5, "inout"))
        else:
            # sıralı: eski başlık ilk yarıda söner, yenisi ikinci yarıda belirir (üst üste binmez)
            A.draw_captions(img, ta, alpha=1 - ease(w / 0.5, "inout"))
            B.draw_captions(img, tb, alpha=ease((w - 0.5) / 0.5, "inout"))
        return img

    def close(self):
        for rt in self.rts.values():
            rt.close()
        self.rts.clear()


# ── Kodlama ───────────────────────────────────────────────────────────────────
CONTAINERS = {"prores": (".mov", ".mkv"), "h264": (".mp4", ".mov", ".m4v", ".mkv"),
              "hevc": (".mp4", ".mov", ".m4v", ".mkv")}


def check_output(out: str, codec: str):
    """Kodlayıcı ↔ kap uyumu, render'dan ÖNCE (ProRes .mp4'e yazılamaz — önceden tüm kareler
    kodlandıktan sonra birleştirmede düşüyordu ve parçalar siliniyordu)."""
    ext = os.path.splitext(out)[1].lower()
    ok = CONTAINERS.get(codec, ())
    if ext not in ok:
        raise PromoError(f"{codec} kodlaması '{ext or '(uzantısız)'}' çıktıya yazılamaz — {' | '.join(ok)} kullanın")


def resolve_output(story_path: str, cli_out: str | None, aspect: str | None, cli_codec: str | None,
                   preview: bool, out_dir: str) -> str:
    """Çıktı yolu: `-o` > senaryodaki `[video] out` (senaryoya göreli) > out_dir[/<dil>]/<ad>[-en-boy][-preview].
    Uzantı ETKİN kodlayıcıdan (CLI `--codec` yoksa senaryonun `codec`i)."""
    st = load(story_path, aspect)
    codec = cli_codec or st.video.codec
    tag = (f"-{aspect.replace(':', 'x')}" if aspect else "") + ("-preview" if preview else "")
    if cli_out:
        out = cli_out
    elif st.video.out:
        root, ext = os.path.splitext(st.video.out)
        out = root + tag + ext
    else:
        base = os.path.splitext(os.path.basename(story_path))[0]
        out = os.path.join(lang_dir(out_dir, st.video.lang), base + tag + (".mov" if codec == "prores" else ".mp4"))
    check_output(out, codec)
    return out


def lang_dir(out_dir: str, lang: str) -> str:
    """Türkçe (vars.) doğrudan out_dir'e, diğer diller `out_dir/<dil>/`e: aynı adlı iki dilli senaryo
    (ör. a/tr/demo.toml ve a/en/demo.toml) çıktıları birbirini ezmesin."""
    d = out_dir if lang == "tr" else os.path.join(out_dir, lang)
    os.makedirs(d, exist_ok=True)
    return d


def encoder_args(codec: str, crf: int, preview: bool):
    # BT.709 etiketi `setparams` ile KAREYE yazılır: `-color_primaries/-color_trc` çıkış
    # seçenekleri bu ffmpeg'de (8.1) libx264'e ulaşmıyor — ffprobe "unknown" gösterdi
    # (ölçüldü); kare özelliği ise VUI'ye iniyor ve concat `-c copy`de korunuyor.
    tag = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
    if codec == "prores":
        return ["-vf", f"scale=out_color_matrix=bt709:out_range=tv,format=yuv422p10le,{tag}",
                "-c:v", "prores_ks", "-profile:v", "3"]
    vf = ["-vf", f"scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,{tag}"]
    if codec == "hevc":
        return vf + ["-c:v", "libx265", "-preset", "fast" if preview else "medium", "-crf", str(crf + 2),
                     "-tag:v", "hvc1", "-x265-params", "log-level=error"]
    return vf + ["-c:v", "libx264", "-preset", "ultrafast" if preview else "medium", "-crf", str(crf),
                 "-profile:v", "high"]


def _opt(opts: dict, key: str, default):
    """CLI seçeneği: 0 da GEÇERLİ değerdir (`--crf 0`, `--to 0` sessizce yok sayılmasın)."""
    v = opts.get(key)
    return default if v is None else v


def _render_range(story_path: str, opts: dict, a: int, b: int, out: str, progress=None, slot: int = 0):
    story = load(story_path, opts.get("aspect"), opts.get("size"), opts.get("fps"))
    v = story.video
    tl = Timeline(story)
    cmd = [ffmpeg_bin(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{v.width}x{v.height}",
           "-r", f"{v.fps:g}", "-i", "-"] + encoder_args(_opt(opts, "codec", v.codec), _opt(opts, "crf", v.crf),
                                                          opts.get("preview", False)) + [out]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for n in range(a, b):
            im = tl.frame(n / v.fps)
            enc.stdin.write(im.tobytes())
            if progress is not None:
                progress[slot] = n - a + 1
    finally:
        tl.close()
        try:
            enc.stdin.close()
        except Exception:
            pass
        enc.wait()
    if enc.returncode != 0:
        raise PromoError(f"ffmpeg kodlama hatası (parça {slot})")


def _worker(story_path, opts, a, b, out, progress, slot, errors):
    try:
        _render_range(story_path, opts, a, b, out, progress, slot)
    except Exception as e:  # ana süreç raporlar
        import traceback
        errors[slot] = 1
        sys.stderr.write(f"\n[parça {slot}] {e}\n{traceback.format_exc()}\n")


def render(story_path: str, out: str, opts: dict) -> str:
    story = load(story_path, opts.get("aspect"), opts.get("size"), opts.get("fps"))
    v = story.video
    if opts.get("music") == "bed":   # o videoya özel fon müziği (giriş + karta denk gelen final)
        v.music = "bed"
    elif opts.get("music"):          # senaryoya dokunmadan parça dene (`render --music`)
        p = os.path.abspath(opts["music"])
        if not os.path.exists(p):
            raise PromoError(f"--music: dosya yok: {p}")
        v.music = p
    if v.music == "bed":
        from .musicbed import story_bed, story_params
        v.music = story_bed(story)
        v.music_final = story_params(story)[1]   # fon kendi finalini çözer → karıştırıcı sönmeyi ondan sonra başlatır
    if opts.get("music_volume") is not None:
        v.music_volume = float(opts["music_volume"])
    codec = _opt(opts, "codec", v.codec)
    check_output(out, codec)
    crf = int(_opt(opts, "crf", v.crf))
    if not 0 <= crf <= 51:
        raise PromoError(f"crf 0..51 olmalı (gelen: {crf})")
    total = int(round(story.duration * v.fps))
    t_from = float(_opt(opts, "from", 0.0))
    t_to = float(_opt(opts, "to", story.duration))
    n0, n1 = int(round(t_from * v.fps)), min(total, int(round(t_to * v.fps)))
    if n1 <= n0:
        raise PromoError("render aralığı boş")
    jobs = max(1, int(opts.get("jobs") or 1))
    jobs = min(jobs, max(1, (n1 - n0) // max(1, int(round(v.fps)))))  # parça başına en az ~1 sn
    # seslendirme kareler çizilmeden ÖNCE hazırlanır: anahtar yoksa / bir satır taşıyorsa hemen söylensin
    vlines = story.voice_lines()
    if vlines:
        from . import voice as vo
        vo.prepare(vlines, story.voice, opts.get("voice_engine"))
        for w in vo.check(vlines, story.duration):
            log(f"⚠️ seslendirme: {w}")
    sfx = []
    log(f"▶ {os.path.basename(story_path)} → {out}")
    log(f"  {v.width}×{v.height} @ {v.fps:g} fps · {fmt_time((n1 - n0) / v.fps)} · {len(story.scenes)} sahne · "
        f"{jobs} süreç")
    ext = ".mov" if codec == "prores" else ".mp4"
    tmp = tempfile.mkdtemp(prefix="promo-")
    t_start = time.time()
    try:
        bounds = [n0 + (n1 - n0) * k // jobs for k in range(jobs + 1)]
        parts = [os.path.join(tmp, f"part{k:02d}{ext}") for k in range(jobs)]
        if jobs == 1:
            prog = [0]
            import threading
            done = threading.Event()

            def report():
                while not done.wait(1.0):
                    _progress_line(prog[0], n1 - n0, t_start)
            th = threading.Thread(target=report, daemon=True)
            th.start()
            try:
                _render_range(story_path, opts, n0, n1, parts[0], prog, 0)
            finally:
                done.set()
        else:
            ctx = mp.get_context("spawn")
            progress = ctx.Array("i", jobs)
            errors = ctx.Array("i", jobs)
            procs = [ctx.Process(target=_worker, args=(story_path, opts, bounds[k], bounds[k + 1], parts[k],
                                                        progress, k, errors)) for k in range(jobs)]
            for p in procs:
                p.start()
            while any(p.is_alive() for p in procs):
                _progress_line(sum(progress[:]), n1 - n0, t_start)
                time.sleep(1.0)
            for p in procs:
                p.join()
            if any(errors[:]) or any(p.exitcode != 0 for p in procs):
                raise PromoError("render parçalarından biri başarısız oldu (ayrıntı yukarıda)")
        _progress_line(n1 - n0, n1 - n0, t_start)
        sys.stderr.write("\n")
        _finalize(parts, out, v, story, (n1 - n0) / v.fps, t_from, vlines, sfx)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if vlines:
        _write_subs(out, story, vlines)
    log(f"✓ {out}  ({time.time() - t_start:.1f} sn)")
    return out


def _write_subs(out: str, story, vlines):
    """Seslendirmeli videonun YANINA altyazılar: seslendirme dili + satırlarda çevirisi olan her dil
    (`video.mp4` → `video.en.srt`, `video.tr.srt`) — YouTube'a doğrudan yüklenir. Zamanlar karışımdakiyle aynı."""
    from .subs import srt
    if not any(ln.text or ln.subs for ln in vlines):
        return                                     # yalnız metinsiz kayıtlar: altyazılacak bir şey yok
    langs = [story.voice.lang] + sorted({k for ln in vlines for k in (ln.subs or {})} - {story.voice.lang})
    for lang in langs:
        try:
            text = srt(list(vlines), lang, story.voice.lang)
        except PromoError as e:
            log(f"⚠️ {lang} altyazısı yazılmadı: {e}")
            continue
        p = os.path.splitext(out)[0] + f".{lang}.srt"
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        log(f"  altyazı: {p}")


def _progress_line(done: int, total: int, t0: float):
    el = time.time() - t0
    fps = done / el if el > 0 else 0
    eta = (total - done) / fps if fps > 0 else 0
    sys.stderr.write(f"\r  {done}/{total} kare · {fps:5.1f} kare/sn · kalan ~{eta:4.0f} sn   ")
    sys.stderr.flush()


def _finalize(parts, out, v, story, dur, t_from, vlines=(), sfx=()):
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    tmpdir = os.path.dirname(parts[0])
    lst = os.path.join(tmpdir, "list.txt")
    with open(lst, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")
    cmd = [ffmpeg_bin(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst]
    g = None
    if vlines or sfx:
        from .voice import audio_graph, duration, loudness
        g = audio_graph(list(vlines), t_from, dur, v.music, v.music_volume, story.voice.volume, first_input=1,
                        music_lufs=loudness(v.music) if v.music else None,
                        sfx=[(t, p, duration(p), loudness(p)) for t, p in sfx], music_final=v.music_final)
    if g is not None and g.out:
        # konuşma (+ müzik, konuşurken kısılır) → eşitlenmiş tek ses akışı (promokit/voice.py)
        for inp in g.inputs:
            cmd += inp
        cmd += ["-filter_complex", g.filter, "-map", "0:v", "-map", g.out, "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest"]
    elif v.music:
        fade_out = max(0.0, dur - 1.5)
        # `apad`: müzik videodan KISAYSA sessizlikle uzar → `-shortest` videoyu değil sesi keser
        # (önceden 6 sn video + 2 sn müzik → 2 sn çıktı)
        cmd += ["-ss", f"{t_from:.3f}", "-i", v.music, "-map", "0:v", "-map", "1:a:0", "-c:v", "copy",
                "-af", f"afade=t=in:d=0.6,afade=t=out:st={fade_out:.2f}:d=1.5,volume={v.music_volume:.2f},apad",
                "-c:a", "aac", "-b:a", "192k", "-shortest"]
    else:
        cmd += ["-c", "copy"]
    if v.title:
        cmd += ["-metadata", f"title={v.title}"]
    if out.lower().endswith((".mp4", ".mov", ".m4v")):
        cmd += ["-movflags", "+faststart"]
    cmd += [out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise PromoError(f"birleştirme hatası:\n{r.stderr.strip()}")


# ── Önizleme ──────────────────────────────────────────────────────────────────
def still(story_path: str, t: float, out: str, opts: dict) -> str:
    story = load(story_path, opts.get("aspect"), opts.get("size"), opts.get("fps"))
    tl = Timeline(story)
    try:
        im = tl.frame(max(0.0, min(t, story.duration - 1e-3)))
    finally:
        tl.close()
    im.save(out)
    return out


def _check_still(story: Story, sc: Scene):
    """`still` anı geçişe denk gelmesin: o kare iki sahnenin karışımıdır (görsel olamaz)."""
    nxt = story.scenes[sc.index + 1] if sc.index + 1 < len(story.scenes) else None
    lo, hi = story.overlap(sc), sc.duration - (story.overlap(nxt) if nxt else 0.0)
    if not lo <= sc.still < hi:
        raise PromoError(f"[[scene]] #{sc.index + 1}.still = {sc.still:g} sn geçişe denk geliyor "
                         f"(temiz aralık {lo:g}–{hi:g} sn) — kare iki sahnenin karışımı olur")


def stills(story_path: str, out_dir: str, opts: dict, fmt: str = "png", max_kb: float | None = None) -> list[str]:
    """Sahne kareleri: `still` anahtarlı her sahneden BİR kare → `NN-<başlık>.png|jpg` (PNG alfasız;
    JPG'de `max_kb` tavanına inene dek JPEG kalitesi düşer — mağaza/CMS boyut sınırları için). Geçişe
    denk gelen an HATA: o kare iki sahnenin karışımıdır."""
    if fmt not in ("png", "jpg"):
        raise PromoError(f"biçim png | jpg olmalı (gelen: {fmt})")
    story = load(story_path, opts.get("aspect"), opts.get("size"), opts.get("fps"))
    shots = [(sc, sc.still) for sc in story.scenes if sc.still is not None]
    if not shots:
        raise PromoError("senaryoda still = <sn> anahtarlı sahne yok (görsel = sahnenin o anı)")
    for sc, _ in shots:
        _check_still(story, sc)
    os.makedirs(out_dir, exist_ok=True)
    tl = Timeline(story)
    out = []
    try:
        for n, (sc, at) in enumerate(shots, 1):
            im = tl.frame(sc.start + at).convert("RGB")
            texts = _texts_at(sc, at)
            path = os.path.join(out_dir, f"{n:02d}-{_slug(texts[-1][0] if texts else sc.title)}.{fmt}")
            if fmt == "png":
                im.save(path, optimize=True)
            else:
                _save_jpg(im, path, max_kb)
            out.append(path)
    finally:
        tl.close()
    return out


_ASCII = str.maketrans("çğıöşüÇĞİÖŞÜâîûÂÎÛ", "cgiosuCGIOSUaiuAIU")


def _slug(text: str) -> str:
    s = "".join(ch for ch in text.translate(_ASCII).lower() if ch.isascii())
    s = "-".join(w for w in "".join(ch if ch.isalnum() else " " for ch in s).split())
    return s[:48].rstrip("-") or "sahne"


def _save_jpg(im: Image.Image, path: str, max_kb: float | None):
    for q in (92, 88, 84, 80, 75, 70, 65, 60, 50, 40):
        im.save(path, "JPEG", quality=q, optimize=True, progressive=True)
        if max_kb is None or os.path.getsize(path) <= max_kb * 1024:
            return
    raise PromoError(f"{os.path.basename(path)}: kalite 40'ta bile {os.path.getsize(path) / 1024:.0f} KB > {max_kb:g} KB")


def sheet(story_path: str, out: str, opts: dict, every: float = 1.0, cols: int = 4) -> str:
    if every <= 0 or cols < 1:
        raise PromoError("--every > 0 ve --cols ≥ 1 olmalı")
    story = load(story_path, opts.get("aspect"), _opt(opts, "size", 540), opts.get("fps"))
    v = story.video
    times = [k * every for k in range(int(story.duration / every) + 1)]
    tl = Timeline(story)
    thumbs = []
    fonts = tl.fonts
    try:
        for t in times:
            im = tl.frame(min(t, story.duration - 1e-3))
            tw = 480
            th = int(tw * v.height / v.width)
            im = im.resize((tw, th), Image.Resampling.LANCZOS)
            label = render_text(fonts, f"{fmt_time(t)}", 18, 600, "#ffffff")
            pill = Image.new("RGBA", (label.size[0] + 8, label.size[1]), (15, 20, 34, 200))
            pill.alpha_composite(label, (4, 0))
            im.paste(pill, (6, 6), pill)
            thumbs.append(im)
    finally:
        tl.close()
    rows = math.ceil(len(thumbs) / cols)
    tw, th = thumbs[0].size
    g = 8
    sheet_im = Image.new("RGB", (cols * tw + (cols + 1) * g, rows * th + (rows + 1) * g), (255, 255, 255))
    for i, im in enumerate(thumbs):
        sheet_im.paste(im, (g + (i % cols) * (tw + g), g + (i // cols) * (th + g)))
    sheet_im.save(out)
    return out


def timeline_text(story_path: str, opts: dict) -> str:
    story = load(story_path, opts.get("aspect"), opts.get("size"), opts.get("fps"))
    v = story.video
    lines = [f"{os.path.basename(story_path)} — {v.width}×{v.height} @ {v.fps:g} fps · toplam {fmt_time(story.duration)}"]
    for sc in story.scenes:
        what = sc.type if sc.type != "device" else sc.layout
        cap = sc.caption or sc.title or ""
        extra = []
        if sc.gestures:
            extra.append(f"{len(sc.gestures)} jest")
        if sc.touches:
            extra.append("kayıtlı dokunuş")
        if sc.camera:
            extra.append("kamera")
        ov = story.overlap(sc)
        lines.append(f"  #{sc.index + 1:<2} {fmt_time(sc.start)} – {fmt_time(sc.start + sc.duration)}  "
                     f"{what:<7} {('geçiş %.2f sn' % ov) if ov else '':<14} {cap[:42]!s:<44} {', '.join(extra)}")
    return "\n".join(lines)
