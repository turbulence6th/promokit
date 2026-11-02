"""USB-C kablo: MacBook ↔ tablet (önden görünüm) + takma / çıkarma animasyonu.

Yerleşim gerçeğe uygun: tabletin USB-C yuvası SAĞ kısa kenarda (yatay tutuşta, ön kamera üstte —
MatePad / Galaxy Tab böyle), dizüstününki alt gövdenin sağ yanında (MacBook Pro'nun sağ
Thunderbolt'u; Windows dizüstünde de sağ yan) — nokta MODELİN geometrisinden (`devices.laptop_port`):
taban yüksekliği/çene modele göre değişir, fiş gövdeye oturmaya devam eder. Kablo iki cihaz arasında
masaya iner ve TABLETİN ARKASINDAN geçer: görünen yalnız
Mac'teki fiş ile tabletin sağından yuvaya kıvrılan kısım. Bu yüzden kablo tabletten ÖNCE çizilir
(`draw`) — takılan fişin metal ucunu ve arkadan geçen kısmı tabletin gövdesi doğal olarak örter;
"bağlandı" ışıması tabletten SONRA (`draw_fx`). Dikey kadrajda (tablet MacBook'un ALTINDA) masa
yoktur: kablo Mac'in sağından aşağı iner ve yuvaya sağdan girer (yerleşim kabloya iki yandan pay
ayırır, kadrajdan taşmaz). Yuvalar cihazın yerel çerçevesinde → cihaz kayar/döner, kablo izler.

Önceki taslak (kullanıcı: "kablo animasyonu çok kötü"): kablo tabletin SOL kenarına giriyordu,
Mac'in altından çengel gibi kıvrılan ince beyaz bir çizgiydi ve fiş yuvaya çapraz kayıyordu.

Ölçüler tabletin ekran yüksekliğine (th) oranlı — MatePad 11,5" ölçeğinde kablo ~3,5 mm,
fiş kalıbı ~13 × 7 mm, metal uç ~6,5 mm.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

from . import devices as dev
from .layout import as_pose
from .util import clamp, ease, lerp, lerp2, rgb, rot

CW = 0.021          # kablo kalınlığı / th
PLUG_H = 0.042      # fiş kalıbı yüksekliği
PLUG_L = 0.080      # kalıp boyu
METAL_L = 0.036     # metal uç boyu
METAL_H = 0.024
RELIEF_L = 0.032    # kalıp → kablo geçişi (gerilim giderici)
CABLE = (52, 56, 65)
CABLE_HI = (150, 156, 168)
PLUG = (60, 64, 73)
PLUG_HI = (128, 134, 146)
METAL = (196, 200, 208)
LIGHT = (-0.6, -0.8)  # ışık sol üstten (sahnenin geri kalanıyla aynı)


def anchors(lpose, tpose, size=None) -> dict:
    """Cihaz duruşlarından (ya da dönüşsüz dikdörtgenlerden) yuva konumları ve masa çizgisi.
    Yuvalar cihazın YEREL çerçevesinde tanımlı → cihaz kayar/ölçeklenir/dönerse yuva da onunla gider.
    `size`: kare boyutu (dikey yerleşimde kablo kadrajdan taşmasın)."""
    L, T = as_pose("laptop", lpose), as_pose("tablet", tpose)
    lw, lh = L.rect[2], L.rect[3]
    tw, th = T.rect[2], T.rect[3]
    tb = dev.geometry("tablet", tw, th)
    lbb, tbb = L.body_bbox(), T.body_bbox()
    return {
        "mac": L.local(*dev.laptop_port(lw, lh, L.opts)),      # dizüstü yuvası: modelin alt gövdesinin sağ yüzü
        "mac_rot": L.rot,
        "port": T.local(tb.right, th / 2),                     # tabletin sağ kısa kenarı, ortası
        "rot": T.rot,
        "desk": tbb[3],                                        # tabletin durduğu masa çizgisi
        "tab_x": (tbb[0], tbb[2]),
        "th": th,
        # üst üste (dikey kadraj): tablet MacBook'un ALTINDA → kablo arkadan değil sağdan iner
        "stacked": tbb[1] >= lbb[3] - 0.02 * th,
        "size": size,
    }


def plug_pose(spec: dict, t: float, port, th: float, rot_deg: float = 0.0):
    """Tablet fişinin pozu → (F, φ, görünürlük). F = metal ucun önü; a = (cos φ, sin φ) fişten
    kabloya doğru eksen. Takma: elde (yuvanın sağ altı, hafif eğik) → hizalan → it. Çıkarma: çek →
    aşağı sark + kablo söner. `rot_deg`: tabletin dönüşü — poz tabletin YEREL çerçevesinde
    hesaplanıp yuva etrafında döndürülür."""
    F, phi, vis = _plug_pose_local(spec, t, port, th)
    if abs(rot_deg) > 1e-9:
        a = math.radians(rot_deg)
        d = rot((F[0] - port[0], F[1] - port[1]), a)
        F, phi = (port[0] + d[0], port[1] + d[1]), phi + a
    return F, phi, vis


def _plug_pose_local(spec: dict, t: float, port, th: float):
    t0 = float(spec.get("t", 0.8))
    dur = max(0.05, float(spec.get("dur", 0.8)))
    x, y = port
    R0 = ((x + 0.035 * th, y + 0.12 * th), math.radians(16))     # elde, yuvanın sağ altında
    R1 = ((x + 0.040 * th, y), 0.0)                               # hizalı, yuvanın hemen önünde
    R2 = ((x - METAL_L * th + 0.002 * th, y), 0.0)                # takılı: metal uç gövdenin içinde
    Rd = ((x + 0.050 * th, y + 0.15 * th), math.radians(24))      # çıkarılınca aşağı sarkar

    def mix(A, B, k):
        return (lerp2(A[0], B[0], k), lerp(A[1], B[1], k))
    s = (t - t0) / dur
    if not spec.get("out", False):
        if s < 0.5:
            P = mix(R0, R1, ease(s / 0.5, "inout"))
        else:
            P = mix(R1, R2, ease((s - 0.5) / 0.5, "inout"))
        return P[0], P[1], 1.0
    if s < 0.45:
        P = mix(R2, R1, ease(s / 0.45, "inout"))
    else:
        P = mix(R1, Rd, ease((s - 0.45) / 0.55, "inout"))
    fade = 1.0 - ease((t - t0 - 0.45 * dur) / (0.55 * dur + 0.25), "in")
    return P[0], P[1], clamp(fade)


def _bez(p0, p1, p2, p3, n=18):
    out = []
    for i in range(n + 1):
        q = i / n
        a, b, c, d = (1 - q) ** 3, 3 * (1 - q) ** 2 * q, 3 * (1 - q) * q * q, q ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


def cable_path(an: dict, F, phi: float):
    """Mac fişinden tablet fişine kablo ekseni (sahne px). Yan yana: masaya iner, tabletin ARKASINDAN
    geçer, sağda yukarı kıvrılıp fişe yatay girer. Üst üste (dikey kadraj): Mac'in sağ yanından
    aşağı iner, tabletin yuvası hizasında sola kıvrılıp fişe girer (kadrajın sağ kenarını aşmaz)."""
    th = an["th"]
    mac, port, desk = an["mac"], an["port"], an["desk"]
    tl, tr = an["tab_x"]
    a = (math.cos(phi), math.sin(phi))
    L = (METAL_L + PLUG_L + RELIEF_L) * th
    D = (F[0] + a[0] * L, F[1] + a[1] * L)               # tablet fişinin kablo ucu
    am = (math.cos(math.radians(an.get("mac_rot", 0.0))), math.sin(math.radians(an.get("mac_rot", 0.0))))
    A = (mac[0] + am[0] * (PLUG_L + RELIEF_L) * th, mac[1] + am[1] * (PLUG_L + RELIEF_L) * th)   # Mac fişinin kablo ucu
    if an.get("stacked"):
        r = 0.035 * th
        xc = max(A[0], D[0]) + r
        if an.get("size"):
            xc = min(xc, an["size"][0] - 2 - CW * th)
        y_top, y_bot = A[1] + r, max(A[1] + r, D[1] - r)
        pts = _bez(A, (A[0] + am[0] * r * 0.55, A[1] + am[1] * r * 0.55), (xc, y_top - r * 0.45), (xc, y_top))
        pts += [(xc, y_bot)]
        pts += _bez((xc, y_bot), (xc, y_bot + r * 0.45), (D[0] + a[0] * r * 0.55, D[1] + a[1] * r * 0.55), D)[1:]
        return pts
    behind = desk - 0.03 * th                            # tabletin arkasında, masada
    G = (tl + (tr - tl) * 0.30, behind)
    E = (tl + (tr - tl) * 0.88, behind)
    M = (D[0] + 0.012 * th, (max(D[1], port[1]) + desk) / 2 + 0.03 * th)   # sağ yanda sarkan kısmın ortası
    pts = _bez(A, (A[0] + 0.10 * th, A[1]), (G[0] - 0.18 * th, G[1]), G)
    pts += [E]
    pts += _bez(E, (E[0] + 0.14 * th, E[1]), (M[0], M[1] + 0.10 * th), M)[1:]
    pts += _bez(M, (M[0], M[1] - 0.08 * th), (D[0] + a[0] * 0.05 * th, D[1] + a[1] * 0.05 * th), D)[1:]
    return pts


def _plug_poly(F, a, x0, x1, h0, h1=None, r=0.0, steps=4):
    """Fiş ekseninde [x0, x1] boyunca yüksekliği h0 → h1 olan (köşeleri r yuvarlak) dörtgen."""
    h1 = h0 if h1 is None else h1
    n = (-a[1], a[0])

    def P(x, y):
        return (F[0] + a[0] * x + n[0] * y, F[1] + a[1] * x + n[1] * y)
    if r <= 0:
        return [P(x0, -h0 / 2), P(x1, -h1 / 2), P(x1, h1 / 2), P(x0, h0 / 2)]
    pts = []
    for cx, cy, a0 in ((x1 - r, -h1 / 2 + r, -90), (x1 - r, h1 / 2 - r, 0), (x0 + r, h0 / 2 - r, 90),
                       (x0 + r, -h0 / 2 + r, 180)):
        for k in range(steps + 1):
            ang = math.radians(a0 + 90 * k / steps)
            pts.append(P(cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


def _plug(d, o, F, a, th, metal: bool):
    """Fişi çiz (o: sahne → katman). Işık tarafındaki kenara parlama, karşı kenara koyu şerit."""
    n = (-a[1], a[0])
    side = 1.0 if n[0] * LIGHT[0] + n[1] * LIGHT[1] < 0 else -1.0   # +n ışığa bakmıyorsa ışık −n'de
    mL, pL, rL = METAL_L * th, PLUG_L * th, RELIEF_L * th
    if metal:
        d.polygon([o(q) for q in _plug_poly(F, a, 0, mL + 0.004 * th, METAL_H * th, r=METAL_H * th * 0.32)],
                  fill=METAL + (255,), outline=(120, 126, 136, 255))
        d.line([o(q) for q in _plug_poly(F, a, mL * 0.15, mL * 0.85, METAL_H * th * 0.25)][:2],
               fill=(150, 155, 165, 255), width=1)
    body = _plug_poly(F, a, mL, mL + pL, PLUG_H * th, r=PLUG_H * th * 0.34)
    d.polygon([o(q) for q in body], fill=PLUG + (255,))
    hi = _plug_poly((F[0] - n[0] * side * PLUG_H * th * 0.24, F[1] - n[1] * side * PLUG_H * th * 0.24), a,
                    mL + pL * 0.10, mL + pL * 0.88, PLUG_H * th * 0.16, r=PLUG_H * th * 0.08)
    d.polygon([o(q) for q in hi], fill=PLUG_HI + (130,))
    d.polygon([o(q) for q in _plug_poly(F, a, mL + pL - 0.002 * th, mL + pL + rL, PLUG_H * th * 0.66, CW * th)],
              fill=PLUG + (255,))


def draw(img: Image.Image, an: dict, spec: dict, t: float, alpha: float = 1.0):
    """Kabloyu + fişleri çiz (tabletten ÖNCE). `alpha`: geçişte sahnesiyle birlikte söner."""
    th = an["th"]
    F, phi, vis = plug_pose(spec, t, an["port"], th, an.get("rot", 0.0))
    vis *= clamp(alpha)
    if vis <= 0.01:
        return
    a = (math.cos(phi), math.sin(phi))
    pts = cable_path(an, F, phi)
    mac = an["mac"]
    am = (math.cos(math.radians(an.get("mac_rot", 0.0))), math.sin(math.radians(an.get("mac_rot", 0.0))))
    F_mac = (mac[0] - am[0] * METAL_L * th, mac[1] - am[1] * METAL_L * th)
    W, H = img.size
    xs = [p[0] for p in pts] + [F[0], mac[0]]
    ys = [p[1] for p in pts] + [F[1], mac[1]]
    pad = int(0.08 * th) + 6
    x0, y0 = max(0, int(min(xs)) - pad), max(0, int(min(ys)) - pad)
    x1, y1 = min(W, int(max(xs)) + pad), min(H, int(max(ys)) + pad)
    if x1 <= x0 or y1 <= y0:
        return
    S = 3
    lay = Image.new("RGBA", ((x1 - x0) * S, (y1 - y0) * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)

    def o(p):
        return ((p[0] - x0) * S, (p[1] - y0) * S)
    cw = CW * th
    d.line([o(p) for p in pts], fill=(34, 37, 44, 255), width=max(2, int(round(cw * S))), joint="curve")
    d.line([o(p) for p in pts], fill=CABLE + (255,), width=max(2, int(round(cw * 0.80 * S))), joint="curve")
    # parlama: ışığa bakan yana kayık ince çizgi; kayma = normal · ışık (işaretli → kıvrımda zıplamaz)
    hi = []
    for i, p in enumerate(pts):
        q0, q1 = pts[max(0, i - 1)], pts[min(len(pts) - 1, i + 1)]
        tx_, ty_ = q1[0] - q0[0], q1[1] - q0[1]
        tl_ = math.hypot(tx_, ty_) or 1.0
        nx, ny = -ty_ / tl_, tx_ / tl_
        k = (nx * LIGHT[0] + ny * LIGHT[1]) * 0.24 * cw
        hi.append((p[0] + nx * k, p[1] + ny * k))
    d.line([o(p) for p in hi], fill=CABLE_HI + (70,), width=max(1, int(round(cw * 0.18 * S))), joint="curve")
    _plug(d, o, F_mac, am, th, metal=False)   # Mac tarafı hep takılı (metal gövdede)
    _plug(d, o, F, a, th, metal=True)
    lay = lay.reduce(S)
    alpha = lay.getchannel("A")
    if vis < 0.999:
        alpha = alpha.point(lambda v: int(v * vis))
        lay.putalpha(alpha)
    sh = alpha.filter(ImageFilter.GaussianBlur(max(1.0, 0.012 * th))).point(lambda v: int(v * 0.24))
    img.paste((20, 24, 36), (x0 + int(0.006 * th), y0 + int(0.016 * th)), sh)
    img.paste(lay, (x0, y0), lay)


def draw_fx(img: Image.Image, an: dict, spec: dict, t: float, alpha: float = 1.0):
    """Takıldı anı: yuvadan yayılan kısa vurgu halkası (tabletten SONRA çizilir)."""
    if spec.get("out", False) or alpha <= 0.01:
        return
    t1 = float(spec.get("t", 0.8)) + max(0.05, float(spec.get("dur", 0.8)))
    e = (t - t1) / 0.6
    if not 0.0 <= e <= 1.0:
        return
    th = an["th"]
    px, py = an["port"]
    r = (0.03 + 0.10 * ease(e, "out")) * th
    S = 3
    size = int(2 * r + 8)
    ring = Image.new("L", (size * S, size * S), 0)
    c = size * S / 2
    ImageDraw.Draw(ring).ellipse((c - r * S, c - r * S, c + r * S, c + r * S), outline=int(210 * (1 - e) ** 1.4 * clamp(alpha)),
                                 width=max(2, int(0.006 * th * S)))
    ring = ring.reduce(S)
    img.paste(rgb("accent"), (int(px - size / 2), int(py - size / 2)), ring)
