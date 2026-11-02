"""Prosedürel sağ el: dokunuş verisinden sürülen, ekrana dokunan animasyonlu el.

Model (yerel koordinat, birim = işaret parmağı genişliği "fw"; görüntü ekseni,
y aşağı; parmaklar yukarı; sağ elin SIRTI kameraya dönük → başparmak SOLDA):

    sırt poligonu + önkol + 4 parmak kapsülü + 2 parçalı başparmak

Kare başına akış:
  1. Pose      — hangi parmak nereye uzanıyor, ne kadar havada (lift), basma anı
  2. çözüm     — avuç konumu: uzanan parmak uçlarından geriye doğru ortalama
  3. yumuşatma — avuç/açı dizisi sıfır-fazlı süzgeçten geçer (gecikmesiz)
  4. çizim     — 3× süper-örnek maske + gölge + ışık + tırnak/boğum ayrıntısı

Dokunuşlar "gruplanır" (zamanda örtüşen temaslar = tek jest). Gruplar arasında el
havalanıp bir sonraki jestin parmak dizilimine geçer; uzun boşlukta çerçeveden
çıkıp geri girer.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .touches import Contact
from .util import (bump, catmull_rom, clamp, ease, lerp, lerp2, mix, rgb, rot,
                   smooth_series)

# ── Geometri (fw birimi) ──────────────────────────────────────────────────────
BACK = [
    (-2.05, -1.50), (-1.80, -2.08), (-1.05, -2.38), (-0.20, -2.52), (0.70, -2.40),
    (1.42, -2.06), (1.92, -1.52), (2.10, -0.40), (2.00, 0.90), (1.68, 2.00),
    (0.20, 2.40), (-1.22, 2.02), (-1.82, 1.00), (-2.22, -0.30),
]
FINGERS = ("index", "middle", "ring", "pinky")
MCP = {"index": (-1.42, -1.88), "middle": (-0.40, -2.10), "ring": (0.58, -1.98), "pinky": (1.44, -1.62)}
FW = {"index": 1.00, "middle": 1.05, "ring": 0.97, "pinky": 0.84}
FLEN = {"index": 3.75, "middle": 4.15, "ring": 3.90, "pinky": 3.10}
FDIR = {"index": -7.0, "middle": -1.0, "ring": 5.0, "pinky": 12.0}   # dikeyden derece (− = sola)
STUB = {"index": 1.02, "middle": 1.12, "ring": 1.04, "pinky": 0.88}  # kıvrık parmağın görünen boyu
THUMB_CMC = (-1.50, 0.85)
THUMB_MCP = (-2.50, -0.22)
THUMB_REST_TIP = (-2.70, -1.38)
THUMB_W = 1.16
THUMB_LEN = 2.55
THUMB_DIR = -42.0
REACH = 0.80          # uzanan parmak doğal boyunun bu oranında "rahat"
SLEEVE_START = 2.6    # kol yeni bilekten bu kadar (fw) sonra başlar
LIFT_SCALE = 0.12     # havadayken kameraya yaklaşma büyümesi


def _unit(deg: float):
    a = math.radians(deg)
    return (math.sin(a), -math.cos(a))


# Kalem tutuşu bu düzenekle ÇİZİLMEZ: yazarken parmaklar kıvrılır, buradaki parmaklar düz
# kapsül. Düzenekle iki, elle çizilmiş siluetle bir deneme yapıldı; hepsi reddedildi ("gerçekte
# böyle bir tutuş yok", "uzaylı eli gibi") → kalem pozunda FOTOĞRAFTAN dekupe gerçek el devralır
# (photohand.py); bu el yalnız dokunuş ↔ kalem geçişinde, çapraz sönerek görünür.


def finger_dir(f: str):
    return _unit(THUMB_DIR if f == "thumb" else FDIR[f])


def natural_tip(f: str):
    """Parmağın rahat uzanmış ucunun yerel konumu."""
    if f == "thumb":
        d = finger_dir("thumb")
        return (THUMB_MCP[0] + d[0] * THUMB_LEN * REACH, THUMB_MCP[1] + d[1] * THUMB_LEN * REACH)
    d = finger_dir(f)
    return (MCP[f][0] + d[0] * FLEN[f] * REACH, MCP[f][1] + d[1] * FLEN[f] * REACH)


# ── Stil ──────────────────────────────────────────────────────────────────────
@dataclass
class HandStyle:
    fill: tuple
    light: tuple
    shade: tuple
    nail: tuple
    line: tuple
    opacity: float = 1.0
    shadow: float = 1.0
    details: bool = True
    sleeve: tuple | None = None   # kol yeni rengi; None = çıplak önkol


STYLES = {
    "skin": HandStyle(rgb("#e2a882"), rgb("#f3c9a6"), rgb("#b9785a"), rgb("#f5d6c6"), rgb("#9a5f45"),
                      sleeve=rgb("#2b3242")),
    "skin-dark": HandStyle(rgb("#8d5a3c"), rgb("#ad7752"), rgb("#5e3824"), rgb("#c99a84"), rgb("#4a2a1a"),
                           sleeve=rgb("#2b3242")),
    # ghost: öğretici çizim tarzı yarı saydam beyaz el — parmak kenar çizgileri açık kalır ki
    # çok parmaklı jestlerde parmaklar tek bir lekeye karışmasın
    "ghost": HandStyle(rgb("#ffffff"), rgb("#ffffff"), rgb("#c9d1e0"), rgb("#f4f6fa"), rgb("#9aa8c0"),
                       opacity=0.9, shadow=0.7, details=True),
}


# ── Poz ───────────────────────────────────────────────────────────────────────
@dataclass
class Pose:
    visible: float = 0.0
    anchor: tuple = (0.0, 0.0)          # birincil parmak ucu hedefi (dünya px)
    tips: dict = field(default_factory=dict)   # parmak → hedef uç (dünya px)
    ext: dict = field(default_factory=dict)    # parmak → uzanma 0..1
    touching: dict = field(default_factory=dict)  # parmak → yüzeye değiyor mu
    lift: float = 1.0
    press: float = 0.0
    pen: float = 0.0                    # 0..1 kalem tutuşu
    reach: float = 0.0                  # ekran ortasına göre yatay konum (−0,5…0,5) — kalem eli döner
    # çözüm sonrası
    palm: tuple = (0.0, 0.0)
    angle: float = 0.0
    scale: float = 1.0                  # px / fw (dünya)
    solved_targets: dict = field(default_factory=dict)  # parmak → (hedef, ağırlık)


@dataclass
class Group:
    contacts: list
    names: dict = field(default_factory=dict)   # id(contact) → parmak adı

    @property
    def t0(self):
        return min(c.t0 for c in self.contacts)

    @property
    def t1(self):
        return max(c.t1 for c in self.contacts)

    @property
    def pen(self):
        return any(c.tool == "pen" for c in self.contacts)


class Surface:
    """Normalize ekran koordinatını dünya pikseline çevirir (ekran düz, kamera dik)."""

    def __init__(self, rect):
        self.x, self.y, self.w, self.h = rect

    def to_world(self, u, v):
        return (self.x + u * self.w, self.y + v * self.h)


def group_contacts(contacts):
    """Zamanda GERÇEKTEN örtüşen temaslar bir grup. Biri kalktığı AN öteki inen iki dokunuş eşzamanlı
    değildir (ürün: `onUp` sekansı kapatır, sonraki `onDown` yeni sekans) — eşitlik ayrı grup."""
    groups = []
    for c in sorted(contacts, key=lambda c: c.t0):
        if groups and c.t0 < groups[-1].t1 - 1e-9:
            groups[-1].contacts.append(c)
        else:
            groups.append(Group([c]))
    return groups


def _max_concurrency_time(contacts):
    ev = sorted([(c.t0, 1) for c in contacts] + [(c.t1, -1) for c in contacts], key=lambda e: (e[0], -e[1]))
    best, cur, tbest = 0, 0, contacts[0].t0
    for i, (t, d) in enumerate(ev):
        cur += d
        if cur > best:
            best = cur
            nxt = ev[i + 1][0] if i + 1 < len(ev) else t
            tbest = (t + nxt) / 2
    return tbest


def assign_fingers(group: Group, surface: Surface, angle: float, fw: float):
    """Gruptaki temaslara parmak adı ver (işaret/orta/…/başparmak/kalem)."""
    cs = group.contacts
    if group.pen:
        for c in cs:
            group.names[id(c)] = "pen" if c.tool == "pen" else "index"
        return
    tm = _max_concurrency_time(cs)
    snap = [c for c in cs if c.t0 <= tm <= c.t1] or cs[:1]
    pos = {id(c): surface.to_world(*c.pos(tm)) for c in snap}
    ax = rot((1.0, 0.0), angle)
    th = rot((-0.55, 0.83), angle)
    proj = lambda c, v: pos[id(c)][0] * v[0] + pos[id(c)][1] * v[1]
    names = {}
    n = len(snap)
    if n == 1:
        names[id(snap[0])] = "index"
    elif n == 2:
        a, b = snap
        ov0, ov1 = max(a.t0, b.t0), min(a.t1, b.t1)
        d0 = math.dist(surface.to_world(*a.pos(ov0)), surface.to_world(*b.pos(ov0)))
        d1 = math.dist(surface.to_world(*a.pos(ov1)), surface.to_world(*b.pos(ov1)))
        pinch = abs(d1 - d0) > max(0.25 * min(d0, d1), 0.6 * fw) or min(d0, d1) > 2.6 * fw
        if pinch:
            t_c = max(snap, key=lambda c: proj(c, th))
            names[id(t_c)] = "thumb"
            names[id(a if t_c is b else b)] = "index"
        else:
            left, right = sorted(snap, key=lambda c: proj(c, ax))
            names[id(left)], names[id(right)] = "index", "middle"
    else:
        rest = list(snap)
        if n >= 5:
            t_c = max(rest, key=lambda c: proj(c, th))
            names[id(t_c)] = "thumb"
            rest.remove(t_c)
        for c, f in zip(sorted(rest, key=lambda c: proj(c, ax)), FINGERS):
            names[id(c)] = f
    # Anlık görüntüde olmayan (önce/sonra gelen) temaslar: boştaki en yakın parmak
    used = set(names.values())
    for c in cs:
        if id(c) in names:
            continue
        free = [f for f in ("index",) + FINGERS[1:] if f not in used] or ["index"]
        names[id(c)] = free[0]
    group.names = names


@dataclass
class HandConfig:
    fw: float                 # parmak genişliği, dünya px
    angle: float = math.radians(-14)
    reach_k: float = math.radians(12)   # ekranın solunda el biraz daha döner
    long_gap: float = 2.2
    enter: float = 0.62
    exit: float = 0.55
    smooth: float = 0.35


class HandTrack:
    """Sahnenin tüm kareleri için el pozlarını ÖNCEDEN hesaplar (paralel render'da
    her parça aynı sonucu bağımsız üretir — durum taşımaz)."""

    def __init__(self, contacts: list[Contact], surface: Surface, fps: float, n_frames: int,
                 cfg: HandConfig):
        self.surface = surface
        self.cfg = cfg
        self.fps = fps
        cs = [c for c in contacts if c.on == "screen"]
        self.groups = group_contacts(cs)
        for g in self.groups:
            assign_fingers(g, surface, cfg.angle, cfg.fw)
        self.poses = [self._pose(i / fps) for i in range(n_frames)]
        self._solve_all()

    # ── poz örnekleme ──
    def _group_pose(self, g: Group, t: float) -> Pose:
        p = Pose(visible=1.0)
        touching_any = False
        for c in g.contacts:
            f = g.names[id(c)]
            tt = min(max(t, c.t0), c.t1)
            p.tips[f] = self.surface.to_world(*c.pos(tt))
            p.ext[f] = 1.0
            on = c.t0 <= t <= c.t1
            p.touching[f] = on
            touching_any |= on
            p.press = max(p.press, bump(t, c.t0, 0.05, 0.16))
        prim = "pen" if "pen" in p.tips else ("index" if "index" in p.tips else next(iter(p.tips)))
        p.anchor = p.tips[prim]
        p.pen = 1.0 if g.pen else 0.0
        # Grup içinde ardışık temaslar arası (ör. çift dokunuş) küçük havalanma
        p.lift = 0.0 if touching_any else 0.22
        return p

    def _rest_from(self, p: Pose) -> tuple:
        s = self.surface
        return (p.anchor[0] + s.w * 0.30, p.anchor[1] + s.h * 1.05)

    def _shift(self, p: Pose, new_anchor) -> Pose:
        dx, dy = new_anchor[0] - p.anchor[0], new_anchor[1] - p.anchor[1]
        q = Pose(visible=p.visible, anchor=new_anchor, ext=dict(p.ext), pen=p.pen,
                 touching={k: False for k in p.touching})
        q.tips = {k: (v[0] + dx, v[1] + dy) for k, v in p.tips.items()}
        return q

    def _blend(self, a: Pose, b: Pose, s: float, gap: float) -> Pose:
        e = ease(s, "inout")
        anchor = lerp2(a.anchor, b.anchor, e)
        q = Pose(visible=1.0, anchor=anchor, pen=lerp(a.pen, b.pen, e))
        for f in set(a.ext) | set(b.ext):
            if f in a.ext and f in b.ext:
                q.tips[f] = lerp2(a.tips[f], b.tips[f], e)
                q.ext[f] = 1.0
            elif f in a.ext:
                da = (a.tips[f][0] - a.anchor[0], a.tips[f][1] - a.anchor[1])
                q.tips[f] = (anchor[0] + da[0], anchor[1] + da[1])
                q.ext[f] = 1.0 - ease(s / 0.55, "inout")
            else:
                db = (b.tips[f][0] - b.anchor[0], b.tips[f][1] - b.anchor[1])
                q.tips[f] = (anchor[0] + db[0], anchor[1] + db[1])
                q.ext[f] = ease((s - 0.45) / 0.55, "inout")
            q.touching[f] = False
        q.lift = math.sin(math.pi * s) ** 0.85 * clamp(gap / 0.75, 0.18, 1.0)
        return q

    def _enter(self, b: Pose, s: float) -> Pose:
        rest = self._rest_from(b)
        q = self._shift(b, lerp2(rest, b.anchor, ease(s, "out")))
        q.lift = 1.0 - ease(s, "inout")
        q.visible = ease(s / 0.35, "out")
        return q

    def _exit(self, a: Pose, s: float) -> Pose:
        rest = self._rest_from(a)
        q = self._shift(a, lerp2(a.anchor, rest, ease(s, "in")))
        q.lift = ease(s / 0.6, "inout")
        q.visible = 1.0 - ease((s - 0.65) / 0.35, "in")
        return q

    def _pose(self, t: float) -> Pose:
        gs = self.groups
        if not gs:
            return Pose(visible=0.0)
        cfg = self.cfg
        for g in gs:
            if g.t0 <= t <= g.t1:
                return self._group_pose(g, t)
        prev = next((g for g in reversed(gs) if g.t1 < t), None)
        nxt = next((g for g in gs if g.t0 > t), None)
        if prev is None:
            s = (t - (nxt.t0 - cfg.enter)) / cfg.enter
            return Pose(visible=0.0) if s < 0 else self._enter(self._group_pose(nxt, nxt.t0), s)
        if nxt is None:
            s = (t - prev.t1) / cfg.exit
            return Pose(visible=0.0) if s > 1 else self._exit(self._group_pose(prev, prev.t1), s)
        gap = nxt.t0 - prev.t1
        if gap > cfg.long_gap:
            if t - prev.t1 <= cfg.exit:
                return self._exit(self._group_pose(prev, prev.t1), (t - prev.t1) / cfg.exit)
            if nxt.t0 - t <= cfg.enter:
                return self._enter(self._group_pose(nxt, nxt.t0), 1 - (nxt.t0 - t) / cfg.enter)
            return Pose(visible=0.0)
        return self._blend(self._group_pose(prev, prev.t1), self._group_pose(nxt, nxt.t0),
                           (t - prev.t1) / gap, gap)

    # ── avuç çözümü ──
    def _targets(self, p: Pose, s: float, ang: float, palm):
        """Parmak hedefleri. Kalem pozunda düzenek eli işaret parmağıyla kalem ucunu gösterir —
        kalem eline (photohand.py) çapraz sönerken aynı yerde dursun."""
        if p.pen <= 0 or "pen" not in p.tips:
            return {f: (v, p.ext.get(f, 0.0)) for f, v in p.tips.items() if f != "pen"}
        return {"index": (p.tips["pen"], 1.0)}

    def _solve(self, p: Pose):
        cfg = self.cfg
        s = cfg.fw * (1 + LIFT_SCALE * p.lift)
        cx = self.surface.x + self.surface.w / 2
        reach = (p.anchor[0] - cx) / max(1.0, self.surface.w)
        ang = cfg.angle + cfg.reach_k * reach
        p.reach = reach
        targets = self._targets(p, s, ang, None)
        ax = ay = wsum = 0.0
        for f, (T, w) in targets.items():
            if w <= 0:
                continue
            lp = natural_tip(f)
            off = rot((lp[0] * s, lp[1] * s), ang)
            ax += (T[0] - off[0]) * w
            ay += (T[1] - off[1]) * w
            wsum += w
        if wsum < 1.0:  # parmaklar kıvrıkken: işaret parmağı çapada gibi konumla
            lp = natural_tip("index")
            off = rot((lp[0] * s, lp[1] * s), ang)
            w = 1.0 - wsum
            ax += (p.anchor[0] - off[0]) * w
            ay += (p.anchor[1] - off[1]) * w
            wsum += w
        p.palm = (ax / wsum, ay / wsum)
        p.angle = ang
        p.scale = s
        p.solved_targets = targets

    def _solve_all(self):
        for p in self.poses:
            if p.visible > 0:
                self._solve(p)
        vis = [i for i, p in enumerate(self.poses) if p.visible > 0]
        if len(vis) < 3:
            return
        a = self.cfg.smooth * 60.0 / max(1.0, self.fps)
        a = clamp(a, 0.05, 1.0)
        # Görünür ardışık bölümleri ayrı ayrı yumuşat
        runs, cur = [], [vis[0]]
        for i in vis[1:]:
            if i == cur[-1] + 1:
                cur.append(i)
            else:
                runs.append(cur)
                cur = [i]
        runs.append(cur)
        for run in runs:
            xs = smooth_series([self.poses[i].palm[0] for i in run], a)
            ys = smooth_series([self.poses[i].palm[1] for i in run], a)
            ang = smooth_series([self.poses[i].angle for i in run], a)
            for k, i in enumerate(run):
                q = self.poses[i]
                q.palm = (xs[k], ys[k])
                q.angle = ang[k]
                if q.pen > 0 and "pen" in q.tips:
                    q.solved_targets = self._targets(q, q.scale, q.angle, q.palm)

    def at(self, i: int) -> Pose | None:
        if 0 <= i < len(self.poses):
            p = self.poses[i]
            return p if p.visible > 0.001 else None
        return None


# ── Çizim ─────────────────────────────────────────────────────────────────────
def _capsule(p0, p1, w0, w1, steps=10):
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    L = math.hypot(dx, dy) or 1e-6
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux
    pts = []
    base = math.atan2(ny, nx)
    for k in range(steps + 1):  # uç yarım daire (+n → +u → −n)
        a = base - math.pi * k / steps
        pts.append((p1[0] + math.cos(a) * w1 / 2, p1[1] + math.sin(a) * w1 / 2))
    for k in range(steps + 1):  # kök yarım daire (−n → −u → +n)
        a = base + math.pi - math.pi * k / steps
        pts.append((p0[0] + math.cos(a) * w0 / 2, p0[1] + math.sin(a) * w0 / 2))
    return pts


def _nail(tip, u, w, r_tip, steps=12):
    """Tırnak (sırttan bakış) uç falanksın ÖN yarısındadır: ön kenarı parmak ucunun yayına
    paralel, aradan ince bir deri payı kalır; arka kenarı (kütikül) daha düz. `tip` = kapsül
    ucunun MERKEZİ, `u` = parmak yönü, `r_tip` = uç yarıçapı → (gövde, serbest kenar şeridi)."""
    n = (-u[1], u[0])
    front = r_tip - 0.06 * w          # tırnağın ön kenarı, uç merkezinden u boyunca
    length, hw = 0.62 * w, 0.30 * w
    ef, eb = 0.17 * w, 0.09 * w       # ön / arka yay derinliği
    c1, c2 = front - ef, front - length + eb

    def P(a, b):
        return (tip[0] + u[0] * a + n[0] * b, tip[1] + u[1] * a + n[1] * b)
    body = [P(c1 + ef * math.cos(a), hw * math.sin(a))
            for a in (-math.pi / 2 + math.pi * k / steps for k in range(steps + 1))]
    body += [P(c2 + eb * math.cos(a), hw * 0.94 * math.sin(a))
             for a in (math.pi / 2 + math.pi * k / steps for k in range(steps + 1))]
    arc = [-0.42 * math.pi + 0.84 * math.pi * k / steps for k in range(steps + 1)]
    band = [P(c1 + ef * math.cos(a), hw * math.sin(a)) for a in arc]
    band += [P(c1 - 0.09 * w + ef * math.cos(a), hw * 0.97 * math.sin(a)) for a in reversed(arc)]
    return body, band


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


def hand_geometry(p: Pose, cam: Camera):
    """Pozdan sahne koordinatında şekiller: [(tür, nokta listesi, veri), …]."""
    z = cam.zoom
    s = p.scale * z
    P = cam.apply(p.palm)
    ang = p.angle

    def L(q):
        r = rot((q[0] * s, q[1] * s), ang)
        return (P[0] + r[0], P[1] + r[1])

    targets = {f: (cam.apply(T), w) for f, (T, w) in p.solved_targets.items()}
    shapes = []
    # Önkol
    wc = L((0.25, 2.0))
    d = rot((0.22, 1.0), ang)
    dl = math.hypot(*d)
    d = (d[0] / dl, d[1] / dl)
    n = (-d[1], d[0])
    far = (wc[0] + d[0] * 18 * s, wc[1] + d[1] * 18 * s)
    w0, w1 = 3.05 * s / 2, 3.7 * s / 2
    shapes.append(("arm", [(wc[0] + n[0] * w0, wc[1] + n[1] * w0), (far[0] + n[0] * w1, far[1] + n[1] * w1),
                           (far[0] - n[0] * w1, far[1] - n[1] * w1), (wc[0] - n[0] * w0, wc[1] - n[1] * w0)], None))
    # Kol yeni: manşet hafif kavisli (ele doğru şişkin), önkoldan biraz geniş
    c0 = (wc[0] + d[0] * SLEEVE_START * s, wc[1] + d[1] * SLEEVE_START * s)
    v0, v1 = 3.95 * s / 2, 4.5 * s / 2
    cuff = [(c0[0] + n[0] * v0 * k - d[0] * 0.38 * s * (1 - k * k), c0[1] + n[1] * v0 * k - d[1] * 0.38 * s * (1 - k * k))
            for k in [1 - 2 * i / 12 for i in range(13)]]
    shapes.append(("sleeve", cuff + [(far[0] - n[0] * v1, far[1] - n[1] * v1), (far[0] + n[0] * v1, far[1] + n[1] * v1)],
                   {"cuff": cuff}))
    shapes.append(("back", [L(q) for q in catmull_rom(BACK, 6, closed=True)], None))
    # Başparmak
    cmc, mcp = L(THUMB_CMC), L(THUMB_MCP)
    rest_tip = L(THUMB_REST_TIP)
    tip = rest_tip
    ext = 0.0
    if "thumb" in targets:
        T, ext = targets["thumb"]
        tip = lerp2(rest_tip, T, ext)
    shapes.append(("thumb_meta", _capsule(cmc, mcp, 1.34 * s, 1.22 * s), None))
    ud = (tip[0] - mcp[0], tip[1] - mcp[1])
    ul = math.hypot(*ud) or 1e-6
    tip_ext = (tip[0] + ud[0] / ul * 0.22 * s, tip[1] + ud[1] / ul * 0.22 * s) if ext > 0.5 else tip
    shapes.append(("finger", _capsule(mcp, tip_ext, THUMB_W * s, THUMB_W * 0.9 * s),
                   {"base": mcp, "tip": tip_ext, "w": THUMB_W * s, "ext": ext, "name": "thumb",
                    "r_tip": THUMB_W * 0.9 * s / 2}))
    # Dört parmak (pinkiden işarete: işaret en üstte çizilsin)
    for f in reversed(FINGERS):
        base = L(MCP[f])
        dloc = finger_dir(f)
        stub = L((MCP[f][0] + dloc[0] * STUB[f], MCP[f][1] + dloc[1] * STUB[f]))
        e = 0.0
        tip = stub
        if f in targets:
            T, e = targets[f]
            tip = lerp2(stub, T, e)
        w = FW[f] * s
        ud = (tip[0] - base[0], tip[1] - base[1])
        ul = math.hypot(*ud) or 1e-6
        if e > 0.5:  # temas noktası parmak YASTIĞININ altında kalsın
            tip = (tip[0] + ud[0] / ul * 0.24 * w, tip[1] + ud[1] / ul * 0.24 * w)
        w_tip = w * (0.86 if e > 0.5 else 1.0)
        shapes.append(("finger", _capsule(base, tip, w, w_tip),
                       {"base": base, "tip": tip, "w": w, "ext": e, "name": f, "r_tip": w_tip / 2}))
    return shapes, s


def _render(img, p: Pose, cam: Camera, style: HandStyle, shapes, s, receiver, shadow: bool, glow: bool = True):
    W, H = img.size
    xs = [q[0] for _, pts, _ in shapes for q in pts]
    ys = [q[1] for _, pts, _ in shapes for q in pts]
    pad = int(2.2 * s) + 6
    x0, y0 = max(int(min(xs)) - pad, 0), max(int(min(ys)) - pad, 0)
    x1, y1 = min(int(max(xs)) + pad, W), min(int(max(ys)) + pad, H)
    if x1 <= x0 or y1 <= y0:
        return
    bw, bh = x1 - x0, y1 - y0
    S = 3
    o = lambda q: ((q[0] - x0) * S, (q[1] - y0) * S)
    mask_ss = Image.new("L", (bw * S, bh * S), 0)
    md = ImageDraw.Draw(mask_ss)
    det_ss = Image.new("RGBA", (bw * S, bh * S), (0, 0, 0, 0))
    dd = ImageDraw.Draw(det_ss)
    hl_ss = Image.new("L", (bw * S, bh * S), 0)
    hd = ImageDraw.Draw(hl_ss)
    line = style.line
    sleeve = [(pts, data) for kind, pts, data in shapes if kind == "sleeve"] if style.sleeve else []
    for kind, pts, data in shapes:
        if kind != "sleeve":
            md.polygon([o(q) for q in pts], fill=255)
    sl_ss = None
    if sleeve:
        sl_ss = Image.new("L", (bw * S, bh * S), 0)
        for pts, _ in sleeve:
            ImageDraw.Draw(sl_ss).polygon([o(q) for q in pts], fill=255)
    if style.details:
        # Parmak kenar çizgileri ayrı katmanda: sırtın (+ başparmak tabanının) İÇİNE düşen kısmı
        # silinir — parmak sırttan doğar, kökü çizgiyle ayrılmaz (kıvrık/yana dönük parmakta
        # bu kısım sırtın üstünde çapraz çizgi gibi görünüyordu).
        edge_ss = Image.new("RGBA", (bw * S, bh * S), (0, 0, 0, 0))
        ed = ImageDraw.Draw(edge_ss)
        palm_ss = Image.new("L", (bw * S, bh * S), 0)
        for kind, pts, data in shapes:
            if kind in ("back", "thumb_meta"):
                ImageDraw.Draw(palm_ss).polygon([o(q) for q in pts], fill=255)
        for kind, pts, data in shapes:
            if kind != "finger":
                continue
            base, tip, w, e = data["base"], data["tip"], data["w"], data["ext"]
            dx, dy = tip[0] - base[0], tip[1] - base[1]
            L = math.hypot(dx, dy) or 1e-6
            ux, uy = dx / L, dy / L
            nx, ny = -uy, ux
            # kenar çizgisi: iki uzun kenar + uç yayı
            half = len(pts) // 2
            edge_pts = [pts[-1]] + pts[:half] + [pts[half]]
            ed.line([o(q) for q in edge_pts], fill=line + (70,), width=max(1, int(0.045 * s * S)), joint="curve")
            hd.line([o((base[0] + ux * w * 0.3, base[1] + uy * w * 0.3)),
                     o((tip[0] - ux * w * 0.55, tip[1] - uy * w * 0.55))], fill=255, width=max(1, int(w * 0.28 * S)))
            if e > 0.5 and L > 1.6 * w:
                for frac, wid in ((0.40, 0.46), (0.68, 0.40)):
                    c = (base[0] + dx * frac, base[1] + dy * frac)
                    arc = [o((c[0] + nx * w * wid * k + ux * w * 0.06 * (1 - k * k),
                              c[1] + ny * w * wid * k + uy * w * 0.06 * (1 - k * k))) for k in (-1, -0.5, 0, 0.5, 1)]
                    dd.line(arc, fill=line + (46,), width=max(1, int(0.035 * s * S)), joint="curve")
                body, band = _nail(tip, (ux, uy), w, data["r_tip"])
                dd.polygon([o(q) for q in body], fill=style.nail + (235,), outline=line + (60,))
                dd.polygon([o(q) for q in band], fill=mix(style.nail, (255, 255, 255), 0.5) + (190,))
        edge_ss.putalpha(ImageChops.multiply(edge_ss.getchannel("A"), ImageChops.invert(palm_ss)))
        det_ss = Image.alpha_composite(edge_ss, det_ss)
    mask = mask_ss.reduce(S)
    sl_mask = sl_ss.reduce(S) if sl_ss is not None else None
    vis = clamp(p.visible) * style.opacity
    if shadow and style.shadow > 0:
        lift = p.lift
        off = (0.22 + 1.5 * lift) * s
        dxs, dys = int(off * 0.55), int(off * 0.85)
        blur = (0.30 + 1.25 * lift) * s
        k = 3
        sh_src = mask if sl_mask is None else ImageChops.lighter(mask, sl_mask)
        sm = sh_src.resize((max(1, bw // k), max(1, bh // k)), Image.Resampling.BOX)
        sm = sm.filter(ImageFilter.GaussianBlur(max(0.6, blur / k)))
        sm = sm.resize((bw, bh), Image.Resampling.BILINEAR)
        a = (0.34 - 0.14 * lift) * vis * style.shadow
        sm = sm.point(lambda v: int(v * a))
        sx0, sy0 = x0 + dxs, y0 + dys
        if receiver is not None:
            sm = ImageChops.multiply(sm, receiver.crop((sx0, sy0, sx0 + bw, sy0 + bh)))
        img.paste((14, 18, 30), (sx0, sy0), sm)
        for kind, pts, data in shapes:  # temas gölgesi: yüzeye değen parmak ucunun altı
            if kind == "finger" and data["ext"] > 0.5 and p.touching.get(data["name"], False):
                tip = data["tip"]
                r = data["w"] * 0.5
                cm = Image.new("L", (bw, bh), 0)
                ImageDraw.Draw(cm).ellipse((tip[0] - x0 - r * 0.8, tip[1] - y0 - r * 0.45,
                                            tip[0] - x0 + r * 1.2, tip[1] - y0 + r * 0.95), fill=int(110 * vis))
                img.paste((14, 18, 30), (x0, y0), cm.filter(ImageFilter.GaussianBlur(r * 0.45)))
    col = Image.new("RGB", (bw, bh), style.fill)
    if style.details:
        # Parmak uçlarına doğru hafif açılan ton (bileğe doğru koyulaşır)
        g = Image.linear_gradient("L").resize((bw, bh), Image.Resampling.BILINEAR)
        col.paste(style.shade, (0, 0), g.point(lambda v: int(v * 0.16)))
        hl = hl_ss.reduce(S).filter(ImageFilter.GaussianBlur(max(1.0, 0.16 * s)))
        col.paste(style.light, (0, 0), hl.point(lambda v: int(v * 0.42)))
        if glow:
            blob = Image.new("L", (bw, bh), 0)
            bc = cam.apply(p.palm)
            r = 1.5 * s
            ImageDraw.Draw(blob).ellipse((bc[0] - x0 - r * 1.1, bc[1] - y0 - r * 1.25,
                                          bc[0] - x0 + r * 0.9, bc[1] - y0 + r * 0.6), fill=140)
            col.paste(style.light, (0, 0), blob.filter(ImageFilter.GaussianBlur(r * 0.8)))
    blur_m = mask.filter(ImageFilter.GaussianBlur(max(1.0, 0.32 * s)))
    rim = ImageChops.subtract(mask, blur_m).point(lambda v: min(255, int(v * 2.6)))
    col.paste(style.shade, (0, 0), rim)
    det = det_ss.reduce(S)
    col.paste(det, (0, 0), det)
    alpha = mask if vis >= 0.999 else mask.point(lambda v: int(v * vis))
    img.paste(col, (x0, y0), alpha)
    if sl_mask is not None:
        _paint_sleeve(img, x0, y0, sl_mask, sleeve, style, s, vis, o, S)


def _paint_sleeve(img, x0, y0, m, sleeve, style, s, vis, o, S):
    bw, bh = m.size
    base = style.sleeve
    col = Image.new("RGB", (bw, bh), base)
    # ışık: sol-üst kenarda açık şerit, gövdede hafif koyulaşma
    blur_m = m.filter(ImageFilter.GaussianBlur(max(1.0, 0.55 * s)))
    rim = ImageChops.subtract(m, blur_m).point(lambda v: min(255, int(v * 2.2)))
    col.paste(mix(base, (0, 0, 0), 0.45), (0, 0), rim)
    hl = Image.new("L", (bw * S, bh * S), 0)
    for pts, data in sleeve:
        cuff = data["cuff"]
        a, b = cuff[0], pts[-1]  # +n kenarı boyunca şerit
        ImageDraw.Draw(hl).line([o(a), o(b)], fill=255, width=max(1, int(0.9 * s * S)))
    hl = hl.reduce(S).filter(ImageFilter.GaussianBlur(max(1.0, 0.5 * s)))
    col.paste(mix(base, (255, 255, 255), 0.18), (0, 0), ImageChops.multiply(hl, m))
    det = Image.new("RGBA", (bw * S, bh * S), (0, 0, 0, 0))
    for pts, data in sleeve:  # manşet dikiş çizgisi
        cuff = data["cuff"]
        ImageDraw.Draw(det).line([o(q) for q in cuff], fill=mix(base, (0, 0, 0), 0.5) + (150,),
                                 width=max(1, int(0.07 * s * S)), joint="curve")
        inner = [(q[0] + (pts[-1][0] - cuff[0][0]) * 0.03, q[1] + (pts[-1][1] - cuff[0][1]) * 0.03) for q in cuff]
        ImageDraw.Draw(det).line([o(q) for q in inner], fill=mix(base, (255, 255, 255), 0.25) + (90,),
                                 width=max(1, int(0.04 * s * S)), joint="curve")
    det = det.reduce(S)
    col.paste(det, (0, 0), det)
    alpha = m if vis >= 0.999 else m.point(lambda v: int(v * vis))
    img.paste(col, (x0, y0), alpha)


def draw_hand(img: Image.Image, p: Pose, cam: Camera, style: HandStyle,
              receiver: Image.Image | None = None):
    """Eli (gölgesiyle) `img` (RGB, sahne boyutu) üstüne çiz.

    `receiver`: gölgenin düşebileceği yüzeyler (L, sahne boyutu) — arka plan
    uzakta sayıldığından gölge yalnız cihaz gövdelerine düşer.
    Kalem pozunda fotoğraftan dekupe GERÇEK el (photohand.py) çizilir; dokunuş ↔ kalem
    geçişinde iki el çapraz söner (ikisi de aynı uçta durur).
    """
    k = clamp((p.pen - 0.2) / 0.6) if "pen" in p.tips else 0.0   # yazı eli ağırlığı
    if k < 0.999:
        q = p if k <= 0.001 else replace(p, visible=p.visible * (1 - k))
        shapes, s = hand_geometry(q, cam)
        _render(img, q, cam, style, shapes, s, receiver, shadow=True)
    if k > 0.001:
        from . import photohand
        photohand.draw(img, cam.apply(p.tips["pen"]), p.scale * cam.zoom, p.reach, receiver,
                       clamp(p.visible) * style.opacity * k, p.lift, style.shadow)
