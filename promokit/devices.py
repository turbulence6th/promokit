"""Cihaz mockup'ları: tablet ve dizüstü (önden görünüm), prosedürel çizim.

Her cihaz bir EKRAN boyutundan türetilir (ekran px → gövde geometrisi); çizim
2× süper-örnekle yapılıp `reduce` ile kenar yumuşatılır. Sonuç boyuta göre
önbelleklenir — kamera sabitken her kare aynı sprite'ı kullanır.

Dizüstünün İKİ modeli var (`[video] laptop_model`, seçenekte `model`): 14" MacBook Pro (varsayılan) ve
genel bir Windows dizüstü. İkisi AYNI geometri sözleşmesini paylaşır (ekran genişliğine oranlı aynı
anahtarlar: `LAPTOP` ↔ `WIN_LAPTOP`) → yerleşim, kapak kapanması, başlık bandı kuralı ve kablo yuvası
modelden bağımsız tek formülle çalışır; değişen yalnız oranlar ve çizim.

Renkler: bezel #0b0d12 (util.BRAND["frame"]).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .util import mix, parse_color, rgb

SS = 2  # süper-örnek katsayısı

LAPTOP_FINISH = {
    # 14" MacBook Pro renkleri — kapak çerçevesi, alt gövde üst, alt gövde alt
    "silver": ("#c9cdd3", "#e6e8ec", "#aeb3ba"),
    "spaceblack": ("#2c2d31", "#3b3c41", "#232427"),
    "spacegray": ("#7e8289", "#a9adb4", "#6f737a"),
}

# 14" MacBook Pro (M4 nesli): 312,6 mm genişlik · 14,2" ekran 3024×1964 (302,4 × 196,4 mm). Kablo
# sahnesi Mac'in SAĞ yanına takılır — Pro'nun sağında USB-C (Thunderbolt) var, Air'de YOK.
# Oranlar ekran GENİŞLİĞİNE göre. ⚠️ Cihazı HTML'de kendisi çizen bir sayfa (tam ekran `html` sahnesi)
# bu oranları yeniden kullanıyorsa, burada bir oran değişince sayfadaki sabitler de değişmeli.
LAPTOP = {
    "side": 0.017,       # yan çerçeve ~5 mm (eski 0.024 = 7,3 mm)
    "top": 0.017,        # üst çerçeve ~5 mm; çentik ekranın İÇİNE oyulur (eski 0.036 = 10,9 mm)
    "chin": 0.042,       # alt çerçeve
    "base_extra": 0.06,  # alt gövdenin önden görünen taşması (perspektif)
    "base_h": 0.034,     # alt gövde ön yüzü (açıkken yalnız alt kasa görünür, ~11 mm)
    "hinge": 0.010,      # menteşe şeridi
    "lid_t": 0.016,      # kapak kalınlığı (~5 mm): kapalıyken önden görünen ince kenar
}
HINGE_SHOW = 0.55        # menteşe şeridinin tabanın ÜSTÜNDE görünen payı (alt kısmını taban örter)

# Genel Windows dizüstü (`[video] laptop_model = "windows"`) — HİÇBİR markanın tasarımı değil, logo yok:
# çentiksiz ekran (kamera üst çerçevenin İÇİNDE → üst çerçeve yan çerçeveden kalın), düz köşeli mat siyah
# çerçeveli panel, ortada menteşe silindiri, iki parçalı (üst kasa + alt kapak) biraz kalın taban. Ekran
# 14" 16:10 (yaygın Windows paneli; şablon 1440×900). Anahtarlar `LAPTOP`la aynı → aynı formüller.
WIN_LAPTOP = {
    "side": 0.015,       # yan çerçeve ~4,5 mm
    "top": 0.026,        # üst çerçeve ~8 mm (kamera)
    "chin": 0.034,       # alt çerçeve (logo yok)
    "base_extra": 0.05,
    "base_h": 0.040,     # alt gövde ön yüzü ~12 mm
    "hinge": 0.012,
    "lid_t": 0.018,
}
WIN_LAPTOP_FINISH = {
    # kapak çerçevesi, alt gövde üst, alt gövde alt
    "graphite": ("#3b3e44", "#56595f", "#2b2d32"),
    "silver": ("#c3c7cd", "#dcdfe3", "#a6aab1"),
}
# Model kaydı: oranlar · kaplamalar + varsayılanı · ekran köşegeni (inç; layout.DIAG) · medyasız ekran oranı
# (= şablonun görünüm alanı; layout.DEFAULT_ASPECT). Varsayılan model MacBook — mevcut senaryolar değişmez.
LAPTOP_MODELS = {
    "macbook": {"ratios": LAPTOP, "finishes": LAPTOP_FINISH, "finish": "silver", "diag": 14.2, "aspect": 1470 / 956},
    "windows": {"ratios": WIN_LAPTOP, "finishes": WIN_LAPTOP_FINISH, "finish": "graphite", "diag": 14.0,
                "aspect": 1440 / 900},
}
DEFAULT_LAPTOP = "macbook"


def laptop_model(options: dict | None = None) -> dict:
    """Seçeneklerin dizüstü modeli (`options["model"]`; yoksa MacBook)."""
    return LAPTOP_MODELS[(options or {}).get("model") or DEFAULT_LAPTOP]


def laptop_spec(options: dict | None = None) -> dict:
    """Modelin geometri oranları (`LAPTOP` ya da `WIN_LAPTOP`)."""
    return laptop_model(options)["ratios"]


# Tablet çerçevesi: ekranın KISA kenarına oranla. %5,2 (~8,5 mm) kalın duruyordu (kullanıcı, 2026-10-20:
# "tabletin çerçevesi de çok kalın") → ~5,5 mm (güncel tabletler 5-6 mm). Dış köşe, ekran köşesi aynı
# kalacak şekilde orantılı. ⚠️ Tableti HTML'de kendisi çizen sayfalar aynı oranları kullanmalı.
TABLET = {"bezel": 0.034, "radius": 0.062}
def lid_view(k: float, L: float, t: float, seam: float = 0.0) -> tuple[float, float, float, float]:
    """Kapak kapanması TAM KARŞIDAN (göz gövde hizasında): kapak açısı φ = 90°·k (1 açık → 0 kapalı), kapak
    boyu L, kalınlığı t, menteşe bandı `seam` (px; kapak altı ↔ taban üstü) → (`face`, `edge`, `reach`, `sink`):
    `face` = L·sin φ — kapak YÜZÜNÜN menteşe üstündeki görünen yüksekliği (gerçek dönüş; eski k³ ezilmesi
    ilk yarıda çöküp sonra sürünüyordu) · `edge` = t·cos φ — kapağın ÜST KENARI yüzün üstünde belirir,
    kapalıyken tabanın üstündeki ince çizgi olur · `reach` = cos φ — kenarın derinliği (0 menteşe … 1 ön
    kenar): yaklaşan kenar perspektifte genişler, kapalıyken tabanın ön yüzüyle AYNI genişlikte (eskiden
    tabandan dar, ayrı bir şerit gibi duruyordu) · `sink` = seam·cos φ — kenar kapandıkça menteşe bandına
    iner, kapalıyken tabanın üstüne OTURUR (arada koyu bant/zemin kalmıyor — kullanıcı: "arada hafif boşluk
    kalmış"). Tam karşıdan klavye ve kapağın sırtı GÖRÜNMEZ
    (kullanıcı, 2026-10-20: yükseltilmiş bakış denemesi geri alındı — "ince çizgi kalacak kapanınca").
    Çizim (`render._closing_laptop`) ve başlık-bandı kutusu (`layout.visible_bbox`) bunu paylaşır."""
    k = min(1.0, max(0.0, k))
    phi = math.pi / 2 * k
    return L * math.sin(phi), t * math.cos(phi), math.cos(phi), seam * math.cos(phi)


@dataclass
class Geometry:
    """Ekran (0,0)-(sw,sh) koordinatında gövde kutusu + yardımcı ölçüler."""
    kind: str
    sw: float
    sh: float
    left: float    # gövdenin ekran sol kenarına göre uzantısı (negatif)
    top: float
    right: float   # gövde sağ kenarı (sw + ...)
    bottom: float

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top


def geometry(kind: str, sw: float, sh: float, options: dict | None = None) -> Geometry:
    o = options or {}
    if kind == "tablet":
        u = min(sw, sh)
        b = u * o.get("bezel", TABLET["bezel"])
        return Geometry(kind, sw, sh, -b, -b, sw + b, sh + b)
    if kind == "laptop":
        L = laptop_spec(o)
        side, top, chin = sw * L["side"], sw * L["top"], sw * L["chin"]
        base_extra = sw * L["base_extra"]
        base_h = sw * L["base_h"]
        return Geometry(kind, sw, sh, -side - base_extra, -top, sw + side + base_extra,
                        sh + chin + base_h)
    raise ValueError(kind)


@dataclass
class Chrome:
    body: Image.Image          # RGBA gövde (gölgesiz); ekran alanı opak siyah
    origin: tuple[int, int]    # gövde sprite'ının ekran-sol-üstüne göre konumu (≤0)
    shadow: Image.Image        # RGBA gölge
    shadow_origin: tuple[int, int]
    screen: tuple[int, int]    # ekran boyutu (tamsayı)
    mask: Image.Image          # L — ekran köşe maskesi
    glare: Image.Image | None  # RGBA — cam yansıması (ekranın üstüne)
    notch: Image.Image | None = None  # L — ekran koordinatında, içeriğin ÜSTÜNE (MacBook)
    split: int | None = None          # MacBook: gövdede kapağın bittiği y (kapak animasyonu için)
    rim: tuple | None = None          # MacBook: kapak kenar rengi (kapalı kapağın ön kenarı)
    shadow_floor: Image.Image | None = None  # MacBook: yalnız zemin gölgesi (kapak kapalıyken)
    spec: dict | None = None          # dizüstü: modelin oranları (kapak kapanması bunlarla çizer)


def _rrect(draw, box, radius, **kw):
    """rounded_rectangle + yarıçap kırpma: Pillow kısmi köşelerde r > min(w,h)/2 iken
    ValueError atıyor (küçük çıkış boyutlarında, ör. 540p önizlemede)."""
    x0, y0, x1, y1 = box
    r = max(0.0, min(radius, (x1 - x0) / 2 - 0.5, (y1 - y0) / 2 - 0.5))
    draw.rounded_rectangle(box, radius=r, **kw)


def _rr_mask(size, box, radius, corners=None) -> Image.Image:
    m = Image.new("L", size, 0)
    _rrect(ImageDraw.Draw(m), box, radius, fill=255, corners=corners)
    return m


def _vgrad(size, top_rgb, bottom_rgb) -> Image.Image:
    g = Image.linear_gradient("L").resize(size, Image.Resampling.BILINEAR)
    a = Image.new("RGB", size, top_rgb)
    b = Image.new("RGB", size, bottom_rgb)
    return Image.composite(b, a, g)


def _shadow(mask_full: Image.Image, blur: float, alpha: float, color=(16, 22, 40)) -> Image.Image:
    """Gölgeyi 1/4 çözünürlükte bulanıklaştır (hızlı), sonra büyüt."""
    W, H = mask_full.size
    k = 4
    small = mask_full.resize((max(1, W // k), max(1, H // k)), Image.Resampling.BOX)
    small = small.filter(ImageFilter.GaussianBlur(max(0.5, blur / k)))
    m = small.resize((W, H), Image.Resampling.BILINEAR).point(lambda v: int(v * alpha))
    out = Image.new("RGBA", (W, H), color + (0,))
    out.putalpha(m)
    return out


_cache: dict = {}


def chrome(kind: str, sw: int, sh: int, options: dict | None = None) -> Chrome:
    o = options or {}
    key = (kind, sw, sh, tuple(sorted(o.items())))
    c = _cache.get(key)
    if c is None:
        if kind == "tablet":
            c = _tablet(sw, sh, o)
        else:
            c = _winbook(sw, sh, o) if laptop_model(o) is LAPTOP_MODELS["windows"] else _laptop(sw, sh, o)
        if len(_cache) > 24:
            _cache.clear()
        _cache[key] = c
    return c


def _glare(sw, sh, strength=0.06) -> Image.Image:
    """Sol üstten gelen çok hafif diyagonal cam yansıması (köşegen = (x/W + y/H)/2)."""
    gy = Image.linear_gradient("L").resize((sw, sh), Image.Resampling.BILINEAR)
    gx = Image.linear_gradient("L").rotate(90, expand=True).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    gx = gx.resize((sw, sh), Image.Resampling.BILINEAR)
    diag = ImageChops.add(gx, gy, scale=2.0)
    a = diag.point(lambda v: int(max(0.0, 1.0 - v / 120.0) ** 1.6 * 255 * strength))
    out = Image.new("RGBA", (sw, sh), (255, 255, 255, 0))
    out.putalpha(a)
    return out


def _tablet(sw: int, sh: int, o: dict) -> Chrome:
    g = geometry("tablet", sw, sh, o)
    u = min(sw, sh)
    b = -g.left
    R = u * o.get("radius", TABLET["radius"])
    r_scr = max(u * 0.022, R - b * 1.15)
    body_c = rgb(o.get("color", "frame"))
    W, H = int(round(g.width)), int(round(g.height))
    bx, by = int(round(b)), int(round(b))
    S = SS
    # Gövde maskesi + kenar (metal çerçeve) halkası
    m_outer = _rr_mask((W * S, H * S), (0, 0, W * S - 1, H * S - 1), R * S)
    rim = max(1.0, u * 0.0055)
    m_inner = _rr_mask((W * S, H * S), (rim * S, rim * S, W * S - 1 - rim * S, H * S - 1 - rim * S),
                       (R - rim) * S)
    m_outer = m_outer.reduce(S)
    m_inner = m_inner.reduce(S)
    rim_col = _vgrad((W, H), mix(body_c, (150, 158, 172), 0.32), mix(body_c, (60, 66, 78), 0.25))
    body = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    body.paste(rim_col, (0, 0), m_outer)
    face = _vgrad((W, H), mix(body_c, (40, 44, 54), 0.35), body_c)
    body.paste(face, (0, 0), m_inner)
    # Üst kenarda ince parlama çizgisi (aşağı doğru sönerek; yarıda kesik köşe bırakmaz)
    hl = Image.new("L", (W, H), 0)
    _rrect(ImageDraw.Draw(hl), (rim * 0.6, rim * 0.6, W - 1 - rim * 0.6, H - 1 - rim * 0.6),
           R, outline=40, width=max(1, int(rim * 0.5)))
    fade = Image.linear_gradient("L").resize((W, H), Image.Resampling.BILINEAR).point(
        lambda v: int(max(0.0, 1.0 - v / 110.0) * 255))
    hl = ImageChops.multiply(ImageChops.multiply(hl, m_outer), fade)
    body.paste((255, 255, 255), (0, 0), hl)
    # Ekran deliği (opak siyah; içerik üstüne yapıştırılır)
    scr_mask_full = _rr_mask((sw * S, sh * S), (0, 0, sw * S - 1, sh * S - 1), r_scr * S).reduce(S)
    body.paste((0, 0, 0), (bx, by), scr_mask_full)
    # Ön kamera: yatayda uzun kenarın ortası (MatePad), dikeyde sağ kenar
    cam_r = u * 0.0068
    if sw >= sh:
        cx, cy = W / 2, b / 2
    else:
        cx, cy = W - b / 2, H / 2
    cm = Image.new("L", (W * S, H * S), 0)
    ImageDraw.Draw(cm).ellipse(((cx - cam_r) * S, (cy - cam_r) * S, (cx + cam_r) * S, (cy + cam_r) * S), fill=255)
    cm = cm.reduce(S)
    body.paste((22, 26, 36), (0, 0), cm)
    lens = Image.new("L", (W * S, H * S), 0)
    lr = cam_r * 0.45
    ImageDraw.Draw(lens).ellipse(((cx - lr - cam_r * 0.15) * S, (cy - lr - cam_r * 0.15) * S,
                                  (cx + lr - cam_r * 0.15) * S, (cy + lr - cam_r * 0.15) * S), fill=255)
    body.paste((48, 62, 92), (0, 0), lens.reduce(S))
    # Gölge: geniş ortam gölgesi + dar temas gölgesi
    pad = int(u * 0.16)
    sm = Image.new("L", (W + 2 * pad, H + 2 * pad), 0)
    sm.paste(m_outer, (pad, pad))
    amb = _shadow(sm, u * 0.07, 0.30)
    con = _shadow(sm, u * 0.018, 0.22)
    shadow = Image.new("RGBA", (sm.size[0], sm.size[1] + int(u * 0.05)), (0, 0, 0, 0))
    shadow.alpha_composite(amb, (0, int(u * 0.04)))
    shadow.alpha_composite(con, (0, int(u * 0.008)))
    return Chrome(body=body, origin=(-bx, -by), shadow=shadow,
                  shadow_origin=(-bx - pad, -by - pad), screen=(sw, sh),
                  mask=scr_mask_full, glare=_glare(sw, sh) if o.get("glare", True) else None)


def _laptop(sw: int, sh: int, o: dict) -> Chrome:
    g = geometry("laptop", sw, sh, o)
    fin = LAPTOP_FINISH.get(o.get("finish", "silver"), LAPTOP_FINISH["silver"])
    rim_c, base_top, base_bot = rgb(fin[0]), rgb(fin[1]), rgb(fin[2])
    side, top, chin = sw * LAPTOP["side"], sw * LAPTOP["top"], sw * LAPTOP["chin"]
    base_extra = sw * LAPTOP["base_extra"]
    W, H = int(round(g.width)), int(round(g.height))
    ox = int(round(side + base_extra))  # ekran sol üstünün sprite içindeki konumu
    oy = int(round(top))
    S = SS
    lid_l, lid_t = base_extra, 0.0
    lid_r, lid_b = W - base_extra, oy + sh + chin
    R = sw * 0.032
    body = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    # Kapak: dış alüminyum halka + siyah cam
    lid_mask = _rr_mask((W * S, H * S), (lid_l * S, lid_t * S, lid_r * S - 1, lid_b * S),
                        R * S, corners=(True, True, False, False)).reduce(S)
    body.paste(_vgrad((W, H), mix(rim_c, (255, 255, 255), 0.25), rim_c), (0, 0), lid_mask)
    ring = sw * 0.0042
    glass = _rr_mask((W * S, H * S), ((lid_l + ring) * S, (lid_t + ring) * S, (lid_r - ring) * S - 1, lid_b * S),
                     (R - ring) * S, corners=(True, True, False, False)).reduce(S)
    body.paste((10, 10, 12), (0, 0), glass)
    # Ekran (üst köşeler yuvarlak, alt köşeler neredeyse düz)
    r_scr = sw * 0.011
    scr_mask = _rr_mask((sw * S, sh * S), (0, 0, sw * S - 1, sh * S - 1), r_scr * S,
                        corners=(True, True, False, False)).reduce(S)
    body.paste((0, 0, 0), (ox, oy), scr_mask)
    # Çentik (notch)
    nw, nh = sw * 0.105, sw * 0.022
    nx0 = ox + sw / 2 - nw / 2
    notch = Image.new("L", (W * S, H * S), 0)
    _rrect(ImageDraw.Draw(notch), (nx0 * S, (oy - 2) * S, (nx0 + nw) * S, (oy + nh) * S),
           sw * 0.008 * S, fill=255, corners=(False, False, True, True))
    notch = notch.reduce(S)
    # Çentik ekran içeriğinin ÜSTÜNE çizilmeli → maske olarak döndür; burada gövdeye de koy
    body.paste((10, 10, 12), (0, 0), notch)
    # Menteşe şeridi + alt gövde (önden ince dilim)
    hinge_t = lid_b
    hinge_h = sw * LAPTOP["hinge"]
    hinge = Image.new("L", (W, H), 0)
    ImageDraw.Draw(hinge).rectangle((lid_l + sw * 0.02, hinge_t, lid_r - sw * 0.02, hinge_t + hinge_h), fill=255)
    body.paste(_vgrad((W, H), (28, 30, 34), (70, 74, 80)), (0, 0), hinge)
    base_t = hinge_t + hinge_h * HINGE_SHOW
    base_b = H - 1
    bm = Image.new("L", (W * S, H * S), 0)
    bd = ImageDraw.Draw(bm)
    br = (base_b - base_t) * 0.55
    _rrect(bd, (0, base_t * S, W * S - 1, base_b * S), br * S, fill=255, corners=(False, False, True, True))
    bm = bm.reduce(S)
    # Gradyan alt gövde yüksekliğine oturt
    bg = Image.new("RGB", (W, H), base_top)
    span = max(1, int(base_b - base_t))
    bg.paste(_vgrad((W, span), base_top, base_bot), (0, int(base_t)))
    body.paste(bg, (0, 0), bm)
    # Ön kenarda başparmak oyuğu: delik değil, hafif koyu iç yuva
    sc_w, sc_d = sw * 0.15, (base_b - base_t) * 0.38
    sc = Image.new("L", (W * S, H * S), 0)
    _rrect(ImageDraw.Draw(sc), ((W / 2 - sc_w / 2) * S, base_t * S, (W / 2 + sc_w / 2) * S,
                                (base_t + sc_d) * S), sc_d * S, fill=70, corners=(False, False, True, True))
    body.paste(mix(base_bot, (40, 44, 50), 0.35), (0, 0), sc.reduce(S))
    # Üst kenar parlaklığı
    edge = Image.new("L", (W, H), 0)
    ImageDraw.Draw(edge).line((sw * 0.01, base_t + 0.5, W - sw * 0.01, base_t + 0.5), fill=110, width=max(1, int(sw * 0.0015)))
    body.paste((255, 255, 255), (0, 0), ImageChops.multiply(edge, bm))
    # Gölge: altta yassı geniş elips + kapak arkası hafif gölge
    pad = int(sw * 0.12)
    sm = Image.new("L", (W + 2 * pad, H + 2 * pad), 0)
    ImageDraw.Draw(sm).ellipse((pad - sw * 0.01, pad + base_b - sw * 0.03, pad + W + sw * 0.01, pad + base_b + sw * 0.03), fill=255)
    floor = _shadow(sm, sw * 0.03, 0.42)
    lm = Image.new("L", (W + 2 * pad, H + 2 * pad), 0)
    lm.paste(lid_mask, (pad, pad))
    lid_sh = lid_shadow(lm, sw)
    shadow = Image.new("RGBA", sm.size, (0, 0, 0, 0))
    shadow.alpha_composite(lid_sh, (0, 0))
    shadow.alpha_composite(floor, (0, 0))
    shadow_floor = floor
    return Chrome(body=body, origin=(-ox, -oy), shadow=shadow, shadow_origin=(-ox - pad, -oy - pad),
                  screen=(sw, sh), mask=scr_mask,
                  glare=_glare(sw, sh, 0.05) if o.get("glare", True) else None,
                  notch=notch.crop((ox, oy, ox + sw, oy + sh)), split=int(round(lid_b)), rim=rim_c,
                  shadow_floor=shadow_floor, spec=LAPTOP)


def lid_shadow(mask: Image.Image, sw: float) -> Image.Image:
    """Kapağın arka gölgesi (`mask`: gölge tuvalindeki kapak silüeti). Kapanırken GÖRÜNEN (ezilmiş)
    kapaktan yeniden hesaplanır — açık kapağın gölgesi havada asılı kalmasın."""
    return _shadow(mask, sw * 0.05, 0.16)


def lid_marks(sw: float, options: dict | None = None) -> dict:
    """Dizüstü ölçü noktaları (ekran sol-üstüne göre px): kapağın sağ kenarı + alt gövdenin üst kenarı
    (`options`: model — vars. MacBook)."""
    L = laptop_spec(options)
    return {"lid_right": sw * (1 + L["side"]),
            "base_top_below_screen": sw * (L["chin"] + L["hinge"] * 0.55)}


def laptop_port(sw: float, sh: float, options: dict | None = None) -> tuple[float, float]:
    """Dizüstünün USB-C yuvası (ekran sol-üstüne göre px): alt gövdenin SAĞ yüzü, ön yüzün yükseklik ortası —
    MODELİN geometrisinden (taban yüksekliği, çene, menteşe). Kablo (`cable.anchors`) fişi buraya takar;
    modele göre sabit bir nokta yazmak gövde değişince fişi havada bırakırdı."""
    g = geometry("laptop", sw, sh, options)
    base_t = sh + lid_marks(sw, options)["base_top_below_screen"]
    return g.right, (base_t + g.bottom) / 2


def _winbook(sw: int, sh: int, o: dict) -> Chrome:
    """Genel Windows dizüstü (önden; `WIN_LAPTOP`). Sprite düzeni MacBook'la (`_laptop`) AYNI — kapak
    [base_extra, W − base_extra] × [0, split], taban tüm genişlik, ekran sol-üstü (ox, oy) — ki kapak kapanması
    (`render._closing_laptop`), gölge ve kablo yuvası aynı yoldan çalışsın. MacBook'tan ayıran (ve hiçbir
    markayı taklit etmeyen) çizgiler: çentik YOK, kamera üst çerçevede · panel DÜZ köşeli, çevresi mat siyah
    çerçeve (cam değil) · kapak köşesi keskin · menteşe ortada kısa bir silindir · taban iki parça (üst kasa +
    alt kapak arasında ek yeri), başparmak oyuğu yok, altta iki lastik ayak · logo yok."""
    L = WIN_LAPTOP
    g = geometry("laptop", sw, sh, o)
    fin = WIN_LAPTOP_FINISH.get(o.get("finish") or "graphite", WIN_LAPTOP_FINISH["graphite"])
    rim_c, base_top, base_bot = rgb(fin[0]), rgb(fin[1]), rgb(fin[2])
    side, top, chin = sw * L["side"], sw * L["top"], sw * L["chin"]
    base_extra = sw * L["base_extra"]
    W, H = int(round(g.width)), int(round(g.height))
    ox = int(round(side + base_extra))  # ekran sol üstünün sprite içindeki konumu
    oy = int(round(top))
    S = SS
    lid_l, lid_r, lid_b = base_extra, W - base_extra, oy + sh + chin
    R = sw * 0.014                      # kapak köşesi (MacBook 0.032): daha keskin
    body = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    # Kapak: dış kasa halkası + MAT siyah çerçeve
    lid_mask = _rr_mask((W * S, H * S), (lid_l * S, 0, lid_r * S - 1, lid_b * S), R * S,
                        corners=(True, True, False, False)).reduce(S)
    body.paste(_vgrad((W, H), mix(rim_c, (255, 255, 255), 0.16), rim_c), (0, 0), lid_mask)
    ring = sw * 0.0028
    frame = _rr_mask((W * S, H * S), ((lid_l + ring) * S, ring * S, (lid_r - ring) * S - 1, lid_b * S),
                     (R - ring) * S, corners=(True, True, False, False)).reduce(S)
    body.paste((19, 20, 24), (0, 0), frame)
    # Panel: düz köşeli; çerçeveyle arasında ince, daha koyu panel kenarı
    lip = max(1, int(round(sw * 0.0018)))
    ImageDraw.Draw(body).rectangle((ox - lip, oy - lip, ox + sw - 1 + lip, oy + sh - 1 + lip), fill=(9, 9, 11, 255))
    scr_mask = Image.new("L", (sw, sh), 255)
    body.paste((0, 0, 0), (ox, oy), scr_mask)
    # Kamera (üst çerçevenin ortası) + yanında kapalı durum ışığı
    cx, cy = W / 2, (ring + oy - lip) / 2
    cam_r = sw * 0.0030
    cm = Image.new("L", (W * S, H * S), 0)
    ImageDraw.Draw(cm).ellipse(((cx - cam_r) * S, (cy - cam_r) * S, (cx + cam_r) * S, (cy + cam_r) * S), fill=255)
    body.paste((34, 37, 46), (0, 0), cm.reduce(S))
    lens = Image.new("L", (W * S, H * S), 0)
    lr = cam_r * 0.5
    ImageDraw.Draw(lens).ellipse(((cx - lr - cam_r * 0.12) * S, (cy - lr - cam_r * 0.12) * S,
                                  (cx + lr - cam_r * 0.12) * S, (cy + lr - cam_r * 0.12) * S), fill=255)
    body.paste((52, 66, 98), (0, 0), lens.reduce(S))
    led = Image.new("L", (W * S, H * S), 0)
    lx, led_r = cx + cam_r * 3.2, cam_r * 0.42
    ImageDraw.Draw(led).ellipse(((lx - led_r) * S, (cy - led_r) * S, (lx + led_r) * S, (cy + led_r) * S), fill=255)
    body.paste((30, 32, 38), (0, 0), led.reduce(S))
    # Menteşe: ortada kısa silindir (MacBook'un tam boy şeridi değil), uçları yuvarlak
    hinge_t = lid_b
    hinge_h = sw * L["hinge"]
    hw = (lid_r - lid_l) * 0.62
    hinge = Image.new("L", (W * S, H * S), 0)
    _rrect(ImageDraw.Draw(hinge), ((W / 2 - hw / 2) * S, hinge_t * S, (W / 2 + hw / 2) * S, (hinge_t + hinge_h) * S),
           hinge_h * 0.5 * S, fill=255)
    body.paste(_vgrad((W, H), (24, 25, 29), (64, 67, 73)), (0, 0), hinge.reduce(S))
    # Taban: ön yüz, alt köşeler yuvarlak
    base_t = hinge_t + hinge_h * HINGE_SHOW
    base_b = H - 1
    bm = Image.new("L", (W * S, H * S), 0)
    br = (base_b - base_t) * 0.42
    _rrect(ImageDraw.Draw(bm), (0, base_t * S, W * S - 1, base_b * S), br * S, fill=255,
           corners=(False, False, True, True))
    bm = bm.reduce(S)
    bg = Image.new("RGB", (W, H), base_top)
    span = max(1, int(base_b - base_t))
    bg.paste(_vgrad((W, span), base_top, base_bot), (0, int(base_t)))
    body.paste(bg, (0, 0), bm)
    # Ek yeri: üst kasa ↔ alt kapak (ön yüzün ~%58'inde ince koyu çizgi, altı bir ton koyu)
    seam_y = base_t + (base_b - base_t) * 0.58
    low = Image.new("L", (W, H), 0)
    ImageDraw.Draw(low).rectangle((0, seam_y, W, H), fill=60)
    body.paste(mix(base_bot, (0, 0, 0), 0.30), (0, 0), ImageChops.multiply(low, bm))
    seam = Image.new("L", (W, H), 0)
    ImageDraw.Draw(seam).line((0, seam_y, W, seam_y), fill=150, width=max(1, int(round(sw * 0.0012))))
    body.paste(mix(base_bot, (0, 0, 0), 0.45), (0, 0), ImageChops.multiply(seam, bm))
    # Lastik ayaklar: alt kenarda iki koyu şerit
    fh = max(1.0, (base_b - base_t) * 0.16)
    feet = Image.new("L", (W * S, H * S), 0)
    fd = ImageDraw.Draw(feet)
    for x0 in (W * 0.09, W * 0.83):
        _rrect(fd, (x0 * S, (base_b - fh) * S, (x0 + W * 0.08) * S, base_b * S), fh * 0.5 * S, fill=255)
    body.paste((22, 23, 26), (0, 0), ImageChops.multiply(feet.reduce(S), bm))
    # Üst kenar parlaklığı
    edge = Image.new("L", (W, H), 0)
    ImageDraw.Draw(edge).line((sw * 0.01, base_t + 0.5, W - sw * 0.01, base_t + 0.5), fill=90,
                              width=max(1, int(sw * 0.0015)))
    body.paste((255, 255, 255), (0, 0), ImageChops.multiply(edge, bm))
    # Gölge: MacBook'la aynı reçete (altta yassı geniş elips + kapak arkası hafif gölge)
    pad = int(sw * 0.12)
    sm = Image.new("L", (W + 2 * pad, H + 2 * pad), 0)
    ImageDraw.Draw(sm).ellipse((pad - sw * 0.01, pad + base_b - sw * 0.03, pad + W + sw * 0.01, pad + base_b + sw * 0.03),
                               fill=255)
    floor = _shadow(sm, sw * 0.03, 0.42)
    lm = Image.new("L", (W + 2 * pad, H + 2 * pad), 0)
    lm.paste(lid_mask, (pad, pad))
    shadow = Image.new("RGBA", sm.size, (0, 0, 0, 0))
    shadow.alpha_composite(lid_shadow(lm, sw), (0, 0))
    shadow.alpha_composite(floor, (0, 0))
    return Chrome(body=body, origin=(-ox, -oy), shadow=shadow, shadow_origin=(-ox - pad, -oy - pad),
                  screen=(sw, sh), mask=scr_mask,
                  glare=_glare(sw, sh, 0.035) if o.get("glare", True) else None,   # mat panel: az yansıma
                  notch=None, split=int(round(lid_b)), rim=rim_c, shadow_floor=floor, spec=WIN_LAPTOP)


def drop_shadow(alpha: Image.Image, u: float) -> tuple[Image.Image, tuple[int, int]]:
    """DÖNDÜRÜLMÜŞ gövdenin gölgesi: tabletin gölge reçetesi (geniş ortam + dar temas) dönmüş silüete
    uygulanır ve AŞAĞI kaydırılır — gölge sprite'ını gövdeyle döndürmek onu yana kaydırırdı.
    → (RGBA gölge, silüet sol-üstüne göre konum)."""
    pad = int(u * 0.16)
    sm = Image.new("L", (alpha.size[0] + 2 * pad, alpha.size[1] + 2 * pad), 0)
    sm.paste(alpha, (pad, pad))
    amb = _shadow(sm, u * 0.07, 0.30)
    con = _shadow(sm, u * 0.018, 0.22)
    out = Image.new("RGBA", (sm.size[0], sm.size[1] + int(u * 0.05)), (0, 0, 0, 0))
    out.alpha_composite(amb, (0, int(u * 0.04)))
    out.alpha_composite(con, (0, int(u * 0.008)))
    return out, (-pad, -pad)
