"""Senaryoda elle yazılan jestler → Contact listesi.

⚠️ Jest ANLAMLARI "tablet ekranı = Mac'e dokunmatik giriş" sözleşmesini modeller (README → «Jest anlamları»):
tek parmak sürükle = KAYDIRMA · uzun bas = sağ tık (sürüklerse seçim/sürükleme) · iki parmak
sürükle = imleç (tıksız) · iki parmak dokun = sağ tık (iki kez = akıllı yakınlaştırma) · 3 parmak =
Mission Control/Space · 4 parmak dikey = tabletin ekran klavyesi · çimdik = yakınlaştırma · `back` =
tabletin KENDİ geri jesti (kenardan; Mac'te sayfa geri). Başka bir sözleşme istenirse eşikler ve
anlamlar `htmlscreen.py` + `runtime/promo.js` başındadır.

Konumlar ekran-normalize ([0..1, 0..1], sol üst = 0,0). Mesafeler (parmak aralığı,
çimdik açıklığı, döndürme yarıçapı) "ekran GENİŞLİĞİ" biriminde verilir — yatay ve
dikey eksen farklı ölçekli olduğu için en-boy oranı burada düzeltilir.

Çok parmaklı jestlerde parmak uçları el modelinin DOĞAL diziliminden türetilir;
böylece çizilen el parmakları zorlamadan o noktalara oturur.
"""
from __future__ import annotations

import difflib
import math

from . import hand
from .htmlscreen import LONG_PRESS_S
from .touches import Contact, KeyPress, TypeText
from .util import PromoError, catmull_rom, ease, point_along, rot

RATE = 120.0  # örnekleme (Hz)

# tür → varsayılan süre (sn)
TYPES = {
    "tap": 0.09, "double_tap": 0.08, "long_press": 0.75, "drag": 0.9, "swipe": 0.28,
    "scroll": 0.7, "swipe3": 0.45, "swipe4": 0.45, "pinch": 0.75, "rotate": 0.8,
    "two_finger_tap": 0.09, "pointer": 0.8, "pen": 1.0, "keys": 1.2, "type": 0.0, "keytaps": 0.0,
    "back": 0.32,
}
BACK_REACH = 0.11  # geri jesti: kenardan içeri ekran-genişliği payı (Android'in ok balonu bu kadar çekilince tetikler)
GRAB_HOLD = 0.45  # üründe uzun-bas eşiği ~400 ms: sürükleme önce "tutulur", sonra hareket eder
COMMON_KEYS = {"t", "dur", "ease", "fingers", "spacing", "from", "to", "angle", "by", "radius",
               "pressure", "smooth", "gap", "hold", "rate", "at", "on", "tool"}
TOOL_KINDS = ("tap", "double_tap", "long_press")  # `tool = "pen"`: kalemle dokunma (üründe tık)


class HandModel:
    """Jest derlemesinin el ölçüsü: parmak genişliği (ekran-genişliği birimi) + el açısı."""

    def __init__(self, fw: float, angle: float, aspect: float):
        self.fw = fw
        self.angle = angle
        self.aspect = aspect  # ekran genişlik / yükseklik

    def to_norm(self, dx: float, dy: float):
        """Genişlik birimindeki ofseti normalize (x, y) ofsetine çevir."""
        return (dx, dy * self.aspect)

    def tip_offsets(self, fingers):
        """Doğal duruştaki parmak uçlarının merkeze göre ofsetleri (genişlik birimi)."""
        pts = [rot(hand.natural_tip(f), self.angle) for f in fingers]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        return [((p[0] - cx) * self.fw, (p[1] - cy) * self.fw) for p in pts]


def _spec_type(spec: dict) -> str:
    found = [k for k in spec if k in TYPES]
    if len(found) != 1:
        unknown = [k for k in spec if k not in TYPES and k not in COMMON_KEYS]
        hint = ""
        for k in unknown:
            m = difflib.get_close_matches(k, list(TYPES) + list(COMMON_KEYS), 1)
            if m:
                hint += f" ('{k}' → '{m[0]}' mi?)"
        raise PromoError(f"jest türü belirsiz: {spec}{hint}\n  türler: {', '.join(TYPES)}")
    for k in spec:
        if k not in TYPES and k not in COMMON_KEYS:
            m = difflib.get_close_matches(k, list(COMMON_KEYS), 1)
            raise PromoError(f"jestte bilinmeyen alan '{k}' ({spec})" + (f" — '{m[0]}' mi?" if m else ""))
    if "t" not in spec:
        raise PromoError(f"jestte başlangıç zamanı 't' yok: {spec}")
    return found[0]


def _pt(v, what):
    try:
        x, y = float(v[0]), float(v[1])
    except Exception:
        raise PromoError(f"{what}: [x, y] bekleniyordu, gelen: {v!r}")
    return (x, y)


def _path(v, what, smooth=True):
    try:
        pts = [_pt(p, what) for p in v]
    except PromoError:
        raise PromoError(f"{what}: [[x, y], [x, y], …] yol bekleniyordu, gelen: {v!r}")
    if len(pts) < 2:
        raise PromoError(f"{what}: en az iki nokta gerekir")
    return catmull_rom(pts, 12) if smooth and len(pts) > 2 else pts


def _sample_path(pts, t0, dur, kind, offset=(0.0, 0.0), pressure=None):
    n = max(2, int(dur * RATE))
    ts, xs, ys, ps = [], [], [], []
    for k in range(n + 1):
        u = k / n
        p = point_along(pts, ease(u, kind))
        ts.append(t0 + dur * u)
        xs.append(p[0] + offset[0])
        ys.append(p[1] + offset[1])
        if pressure is not None:
            ps.append(_pressure_at(pressure, u))
    return Contact(ts, xs, ys, ps or None)


def _pressure_at(pressure, u: float) -> float:
    """Kalem basıncı, vuruşun zamanı u∈[0,1] boyunca: tek değer = sabit · iki = uçlar ince, orta kalın (√sin,
    hemen tavana çıkar) · üç+ = eşit aralıklı anahtarlar arası kosinüs geçişi (fırça kalemi gibi ince-kalın
    dalga — basınç videoda GÖRÜNSÜN)."""
    if len(pressure) == 1:
        return pressure[0]
    if len(pressure) == 2:
        return pressure[0] + (pressure[1] - pressure[0]) * math.sin(math.pi * u) ** 0.5
    x = u * (len(pressure) - 1)
    i = min(int(x), len(pressure) - 2)
    f = (1 - math.cos(math.pi * (x - i))) / 2
    return pressure[i] + (pressure[i + 1] - pressure[i]) * f


def _hold(p, t0, dur):
    ts = [t0, t0 + dur * 0.5, t0 + dur]
    return Contact(ts, [p[0]] * 3, [p[1]] * 3)


def _resolve(spec: dict, kind: str, t: float, resolver, contacts, keys, texts):
    """Seçicili hedefi ekran-normalize koordinata çevir (HTML ekran).

    `tap = "#oge"` (+ `at = [fx, fy]`, vars. orta) → öğenin görünür kutusundaki nokta;
    `on = "#oge"` → jestin koordinatları O ÖĞEYE göreli (0..1). Konum, jestin başladığı
    andaki sayfa durumundan ölçülür (önceki kaydırma/dokunuşlar uygulanmış hâlde)."""
    target, on = spec[kind], spec.get("on")
    if not isinstance(target, str) and on is None:
        return target
    sel = target if isinstance(target, str) else on
    if resolver is None:
        raise PromoError(f"{kind}: seçici ('{sel}') yalnız HTML ekranlarda kullanılabilir")
    # Çözücü, jestin İNDİĞİ noktanın öğeye gerçekten isabet ettiğini de doğrular (görünmez/örtülü → hata)
    if isinstance(target, str):
        fx, fy = _pt(spec.get("at", [0.5, 0.5]), "at")
    else:
        first = target[0] if target and isinstance(target[0], (list, tuple)) else target
        fx, fy = _pt(first, f"{kind} (on)")
    x, y, w, h = resolver(str(sel), t, contacts, keys, texts, probe=(fx, fy))
    if isinstance(target, str):
        return [x + fx * w, y + fy * h]
    if target and isinstance(target[0], (list, tuple)):
        return [[x + float(a) * w, y + float(b) * h] for a, b in target]
    return [x + float(target[0]) * w, y + float(target[1]) * h]


def compile_gestures(specs, model: HandModel, resolver=None):
    """→ (temaslar, tuş kombinasyonları, yazılan metinler).

    Jestler zamana göre sıralı derlenir: seçici çözümü, ÖNCEKİ jestlerin sayfayı getirdiği
    durumu bilmeli (ör. kaydırmadan sonra konumu değişen bir bağlantı)."""
    contacts: list[Contact] = []
    keys: list[KeyPress] = []
    texts: list[TypeText] = []
    for spec in specs or []:
        if not isinstance(spec, dict):
            raise PromoError(f"jest bir tablo olmalı: {spec!r}")
    expanded = []   # (jest, tablet-yerel mi)
    for spec in specs or []:
        if "keytaps" in spec:  # tabletin ekran klavyesinde harf harf dokunma (HTML: .osk [data-key])
            _spec_type(spec)
            rate = float(spec.get("rate", 3.2))
            if rate <= 0:
                raise PromoError(f"keytaps: rate > 0 olmalı ({spec})")
            for i, ch in enumerate(str(spec["keytaps"]).lower()):
                key = "space" if ch == " " else ch
                expanded.append(({"t": float(spec["t"]) + i / rate, "tap": f'.osk [data-key="{key}"]'}, True))
        else:
            expanded.append((spec, False))
    for spec, local in sorted(expanded, key=lambda e: float(e[0].get("t", 0.0))):
        kind = _spec_type(spec)
        t = float(spec["t"])
        dur = float(spec.get("dur", TYPES[kind]))
        ez = spec.get("ease")
        if kind not in ("keys", "type"):
            spec = dict(spec)
            spec[kind] = _resolve(spec, kind, t, resolver, contacts, keys, texts)
        tool = spec.get("tool")
        if tool is not None and (tool not in ("finger", "pen") or kind not in TOOL_KINDS):
            raise PromoError(f"tool = {tool!r}: 'finger' | 'pen' ve yalnız {', '.join(TOOL_KINDS)} ile "
                             "(kalemle çizgi için 'pen' jesti)")
        n0 = len(contacts)
        if kind == "tap":
            contacts.append(_hold(_pt(spec[kind], kind), t, float(spec.get("hold", dur))))
        elif kind == "double_tap":
            p = _pt(spec[kind], kind)
            gap = float(spec.get("gap", 0.13))
            contacts.append(_hold(p, t, dur))
            contacts.append(_hold(p, t + dur + gap, dur))
        elif kind == "long_press":
            contacts.append(_hold(_pt(spec[kind], kind), t, dur))
        elif kind == "drag":  # uzun-bas → sürükle: önce tut (kol yeşile döner), sonra taşı
            pts = _path(spec[kind], kind, spec.get("smooth", True))
            hold = float(spec.get("hold", GRAB_HOLD))
            if hold < LONG_PRESS_S:   # üründe sürükleme ancak 400 ms hareketsiz basıştan (GRAB) sonra başlar
                raise PromoError(f"drag: hold ≥ {LONG_PRESS_S:g} sn olmalı (gelen {hold:g}) — daha kısa tutup "
                                 "sürüklemek üründe KAYDIRMADIR ('scroll' jesti)")
            mv = _sample_path(pts, t + hold, dur, ez or "inout")
            mv.t = [t] + mv.t
            mv.x = [mv.x[0]] + mv.x
            mv.y = [mv.y[0]] + mv.y
            mv.p = [1.0] + mv.p
            contacts.append(mv)
        elif kind == "swipe":
            pts = _path(spec[kind], kind, spec.get("smooth", True))
            contacts.append(_sample_path(pts, t, dur, ez or "quart"))
        elif kind == "two_finger_tap":  # sağ tık
            c = _pt(spec[kind], kind)
            for off in model.tip_offsets(hand.FINGERS[:2]):
                o = model.to_norm(*off)
                contacts.append(_hold((c[0] + o[0], c[1] + o[1]), t, dur))
        elif kind in ("scroll", "swipe3", "swipe4", "pointer"):
            n = int(spec.get("fingers", {"scroll": 1, "swipe3": 3, "swipe4": 4, "pointer": 2}[kind]))
            if not 1 <= n <= 4:
                raise PromoError(f"{kind}: fingers 1..4 olmalı")
            pts = _path(spec[kind], kind, spec.get("smooth", True))
            fingers = hand.FINGERS[:n]
            offs = model.tip_offsets(fingers)
            if "spacing" in spec:  # elle aralık: parmakları el ekseninde eşit diz
                sp = float(spec["spacing"])
                ax = rot((1.0, 0.0), model.angle)
                offs = [((i - (n - 1) / 2) * sp * ax[0], (i - (n - 1) / 2) * sp * ax[1]) for i in range(n)]
            for i, off in enumerate(offs):
                o = model.to_norm(*off)
                c = _sample_path(pts, t + 0.018 * i, dur, ez or "sine", o)
                contacts.append(c)
        elif kind == "pinch":
            c = _pt(spec[kind], kind)
            th = rot(hand.natural_tip("thumb"), model.angle)
            ix = rot(hand.natural_tip("index"), model.angle)
            nat = math.hypot(ix[0] - th[0], ix[1] - th[1]) * model.fw
            if "angle" in spec:
                a = math.radians(float(spec["angle"]))
                axis = (math.cos(a), -math.sin(a))
            else:
                L = math.hypot(ix[0] - th[0], ix[1] - th[1]) or 1.0
                axis = ((ix[0] - th[0]) / L, (ix[1] - th[1]) / L)
            d0 = float(spec.get("from", nat * 0.55))
            d1 = float(spec.get("to", nat * 1.15))
            n = max(2, int(dur * RATE))
            for sign, who in ((1, "index"), (-1, "thumb")):
                ts, xs, ys = [], [], []
                for k in range(n + 1):
                    u = k / n
                    d = d0 + (d1 - d0) * ease(u, ez or "inout")
                    off = model.to_norm(axis[0] * d / 2 * sign, axis[1] * d / 2 * sign)
                    ts.append(t + dur * u)
                    xs.append(c[0] + off[0])
                    ys.append(c[1] + off[1])
                contacts.append(Contact(ts, xs, ys))
        elif kind == "rotate":
            c = _pt(spec[kind], kind)
            r = float(spec.get("radius", 0.07))
            by = math.radians(float(spec.get("by", 45)))
            th = rot(hand.natural_tip("thumb"), model.angle)
            ix = rot(hand.natural_tip("index"), model.angle)
            a0 = math.atan2(ix[1] - th[1], ix[0] - th[0])
            n = max(2, int(dur * RATE))
            for base in (a0, a0 + math.pi):
                ts, xs, ys = [], [], []
                for k in range(n + 1):
                    u = k / n
                    a = base - by * ease(u, ez or "inout")  # pozitif = saat yönünün tersi
                    off = model.to_norm(math.cos(a) * r, math.sin(a) * r)
                    ts.append(t + dur * u)
                    xs.append(c[0] + off[0])
                    ys.append(c[1] + off[1])
                contacts.append(Contact(ts, xs, ys))
        elif kind == "pen":
            pts = _path(spec[kind], kind, spec.get("smooth", True))
            pr = spec.get("pressure", [0.35, 0.9])
            pr = [float(pr)] if isinstance(pr, (int, float)) else [float(v) for v in pr]
            c = _sample_path(pts, t, dur, ez or "sine", pressure=pr)
            c.tool = "pen"
            contacts.append(c)
        elif kind == "back":  # tabletin KENDİ geri jesti (§4.3): kenardan içeri kısa kaydırma → Mac'te sayfa geri
            x, y = _pt(spec[kind], kind)
            if 0.03 < x < 0.97:
                raise PromoError(f"back: kenardan başlamalı (x ≤ 0.03 ya da ≥ 0.97), gelen x = {x:g} — ortadan tek "
                                 "parmak sürükleme üründe KAYDIRMADIR ('scroll' jesti)")
            x0, sign = (0.0, 1.0) if x < 0.5 else (1.0, -1.0)
            c = _sample_path([(x0, y), (x0 + sign * BACK_REACH, y)], t, dur, ez or "out")
            c.system = "back"
            contacts.append(c)
        elif kind == "keys":
            keys.append(KeyPress(t, str(spec[kind]), dur))
        elif kind == "type":
            texts.append(TypeText(t, str(spec[kind]), float(spec.get("rate", 14.0))))
        if tool is not None:
            for c in contacts[n0:]:
                c.tool = tool
        if local:   # tabletin KENDİ klavyesi: dokunuşlar Mac jesti değil, birbirleriyle birleşmez
            for c in contacts[n0:]:
                c.local = True
    return contacts, keys, texts
