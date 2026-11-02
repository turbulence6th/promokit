"""`touches` komutu: kaydedilmiş dokunuşları incele, yönünü düzelt, senkronla.

Yön/senkron hatası en hızlı GÖZLE yakalanır: `--plot`, dokunuş izlerini kaydın bir
karesinin üstüne çizer (renk = zaman). Kaydırılan sayfa ile iz aynı yerde değilse
yön yanlıştır (`--rotation`); el içerikten önce/sonra hareket ediyorsa ofset (`--auto`).

Otomatik senkron (`--auto`): ekrandaki hareket enerjisi ile parmak hareket enerjisi
arasında çapraz-korelasyon; tepe gecikmesinden dokunuşun kayıtta görünen tipik tepki
gecikmesi düşülür (dokunuş, ekrandaki tepkiden ÖNCE gelir). Bu gecikme çekimin
KAYNAĞINA göre değişir (REACTION_LATENCY) — tek değer Mac çekimlerini kaydırıyordu.
"""
from __future__ import annotations

import json
import math
import os
import subprocess

from PIL import Image, ImageChops, ImageDraw, ImageStat

from .capture import build_touches
from .media import Probe, VideoSource
from .text import Fonts, render_text
from .touches import TouchLog
from .util import PromoError, ffmpeg_bin, log, mix, rgb

# Dokunuş → kayıtta görünen tepki (sn), çekim kaynağına göre; kaba tipik değerler:
#   tablet: kayıt tabletin KENDİ ekranı → tam tur (dokunuş→Mac→kodlama→tablet) görünür
#   mac:    kayıt Mac'te yakalanır → yalnız giriş yolu (tablet→Mac + bir ekran yenilemesi);
#           bu çekimin senkronu zaten kare-saatinden (SCK ilk kare) — --auto orada yalnız denetim
REACTION_LATENCY = {"tablet": 0.07, "mac": 0.03}
SYSTEM_LATENCY = REACTION_LATENCY["tablet"]  # kaynağı bilinmeyen çekim


def reaction_latency(take: dict) -> float:
    return REACTION_LATENCY.get(take.get("source"), SYSTEM_LATENCY)


def _load_take(path: str):
    d = path if os.path.isdir(path) else os.path.dirname(path)
    p = os.path.join(d, "take.json")
    if not os.path.exists(p):
        raise PromoError(f"take.json yok: {d}")
    with open(p, encoding="utf-8") as f:
        return d, json.load(f)


def _save_take(d: str, take: dict):
    with open(os.path.join(d, "take.json"), "w", encoding="utf-8") as f:
        json.dump(take, f, ensure_ascii=False, indent=1)


def motion_signal(video: str, fps: float = 30.0, width: int = 96):
    """Kareler arası ortalama mutlak fark (gri, küçük boyut)."""
    pr = Probe(video)
    h = max(2, int(width * pr.height / pr.width) // 2 * 2)
    cmd = [ffmpeg_bin(), "-v", "error", "-i", video, "-an", "-vf", f"fps={fps},scale={width}:{h},format=gray",
           "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    n = width * h
    prev = None
    sig = []
    while True:
        b = p.stdout.read(n)
        if len(b) < n:
            break
        im = Image.frombytes("L", (width, h), b)
        sig.append(0.0 if prev is None else ImageStat.Stat(ImageChops.difference(im, prev)).mean[0])
        prev = im
    p.wait()
    return sig


def touch_signal(tl: TouchLog, n: int, fps: float = 30.0):
    a = [0.0] * n
    for c in tl.contacts:
        for i in range(1, len(c.t)):
            k = int(c.t[i] * fps)
            if 0 <= k < n:
                a[k] += math.hypot(c.x[i] - c.x[i - 1], c.y[i] - c.y[i - 1])
        for tt in (c.t0, c.t1):  # bas/kaldır anları da ekranda bir şey değiştirir (tık)
            k = int(tt * fps)
            if 0 <= k < n:
                a[k] += 0.02
    return a


def _z(xs):
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)) or 1.0
    return [(x - m) / sd for x in xs]


def estimate_offset(video: str, tl: TouchLog, max_lag: float = 2.0, fps: float = 60.0,
                    latency: float = SYSTEM_LATENCY):
    """(önerilen touch_offset, güven, tepe) — güven = tepe korelasyonu / ikinci tepe."""
    m = motion_signal(video, fps)
    if len(m) < fps * 2:
        raise PromoError("video çok kısa")
    return offset_from_signals(m, touch_signal(tl, len(m), fps), fps, max_lag, latency)


def offset_from_signals(m, a, fps: float, max_lag: float = 2.0, latency: float = SYSTEM_LATENCY):
    """Hareket (m) ve dokunuş (a) enerjileri → (touch_offset, güven, tepe). `latency`: beklenen tepki."""
    if sum(a) == 0:
        raise PromoError("kayıtta hareket eden dokunuş yok")
    mz, az = _z(m), _z(a)
    L = int(max_lag * fps)
    scores = []
    n = len(m)
    for lag in range(-L, L + 1):  # ekrandaki hareket = dokunuş + lag
        s = 0.0
        cnt = 0
        for k in range(max(0, -lag), min(n, n - lag)):
            s += az[k] * mz[k + lag]
            cnt += 1
        scores.append((s / max(1, cnt), lag))
    by_lag = {l2: sc for sc, l2 in scores}
    scores.sort(reverse=True)
    best, lag = scores[0]
    second = next((s for s, l2 in scores[1:] if abs(l2 - lag) > fps * 0.25), 1e-6)
    conf = best / max(second, 1e-6)
    # Tepeyi komşularıyla parabol oturtarak kutucuk-altı çözünürlüğe çıkar
    frac = 0.0
    if lag - 1 in by_lag and lag + 1 in by_lag:
        y0, y1, y2 = by_lag[lag - 1], best, by_lag[lag + 1]
        den = y0 - 2 * y1 + y2
        if den < 0:
            frac = max(-0.5, min(0.5, 0.5 * (y0 - y2) / den))
    return (lag + frac) / fps - latency, conf, best


def plot(d: str, take: dict, tl: TouchLog, out: str, at: float | None):
    video = os.path.join(d, take["screen"])
    pr = Probe(video)
    W = 1600
    H = int(W * pr.height / pr.width) // 2 * 2
    t = at if at is not None else ((pr.duration or 2) / 2)
    src = VideoSource(pr, (0, 0, 1, 1), (W, H), pr.fps or 30, t)
    base = src.get(t).copy()
    src.close()
    base = Image.blend(base, Image.new("RGB", base.size, (0, 0, 0)), 0.45)
    lay = Image.new("RGBA", (W, H + 84), (0, 0, 0, 0))
    canvas = Image.new("RGB", (W, H + 84), (15, 20, 34))
    canvas.paste(base, (0, 0))
    dr = ImageDraw.Draw(lay)
    total = max([c.t1 for c in tl.contacts] + [pr.duration or 1])
    c0, c1 = rgb("#4f7cff"), rgb("#ff8a3d")
    for c in tl.contacts:
        col = mix(c0, c1, c.t0 / total) + (255,)
        pts = [(x * W, y * H) for x, y in zip(c.x, c.y)]
        if len(pts) > 1:
            dr.line(pts, fill=col, width=4, joint="curve")
        x, y = pts[0]
        dr.ellipse((x - 9, y - 9, x + 9, y + 9), outline=col, width=3)
        if c.tool == "pen":
            dr.rectangle((x - 4, y - 4, x + 4, y + 4), fill=col)
        # zaman şeridi
        x0, x1 = c.t0 / total * W, max(c.t0 / total * W + 3, c.t1 / total * W)
        dr.rectangle((x0, H + 20, x1, H + 44), fill=col)
    if at is not None:
        dr.line(((at / total) * W, H + 12, (at / total) * W, H + 52), fill=(255, 255, 255, 255), width=2)
    canvas = canvas.convert("RGBA")
    canvas.alpha_composite(lay)
    fonts = Fonts()
    info = (f"{len(tl.contacts)} temas · yön {tl.meta.get('rotation')} · ofset {take.get('touch_offset', 0):+.3f} sn · "
            f"kare {t:.2f} sn")
    canvas.alpha_composite(render_text(fonts, info, 18, 600, "#ffffff", align="left"), (8, H + 50))
    canvas.convert("RGB").save(out)
    return out


def add_parser(sub):
    p = sub.add_parser("touches", help="kaydedilmiş dokunuşları incele / yön ve senkron düzelt")
    p.add_argument("take", help="çekim dizini (work/takes/<ad>)")
    p.add_argument("--plot", nargs="?", const="", help="izleri bir karenin üstüne çiz (PNG)")
    p.add_argument("--at", type=float, help="--plot için kare zamanı (sn)")
    p.add_argument("--auto", action="store_true", help="hareket korelasyonuyla ofset öner")
    p.add_argument("--write", action="store_true", help="--auto sonucunu take.json'a yaz")
    p.add_argument("--offset", type=float, help="touch_offset'i elle ayarla (sn; + = dokunuşlar geç)")
    p.add_argument("--rotation", type=int, choices=[0, 1, 2, 3], help="yönü zorla ve dokunuşları yeniden üret")
    p.add_argument("--flip", choices=["", "x", "y", "xy"], help="ekseni ters çevir ve yeniden üret")
    p.add_argument("--keys", choices=["combos", "all", "none"], help="tuş çipleri: kombinasyon/hepsi/yok")


def run(a):
    d, take = _load_take(a.take)
    raw = os.path.exists(os.path.join(d, "getevent.log"))
    regen = a.rotation is not None or a.flip is not None or a.keys is not None
    if not take.get("touches") and not (regen and raw):
        # Kayıt anında çözüm başarısız olduysa take.json yine yazılır, ham günlük durur → yeniden üretilebilir
        raise PromoError("bu çekimde dokunuş kaydı yok" +
                         (" — ham günlük var: `--rotation N` ile üretin" if raw else ""))
    if regen:
        if not raw:
            raise PromoError("ham günlük (getevent.log) yok — yeniden üretilemez")
        rot = a.rotation if a.rotation is not None else take.get("rotation", 0)
        flip = a.flip if a.flip is not None else take.get("flip", "")
        tl = build_touches(d, take.get("device", {}), take["first_frame_wall"], rot, flip, a.keys or "combos")
        take["rotation"], take["flip"] = rot, flip
        take["touches"] = "touches.json"
        _save_take(d, take)
        log(f"✓ dokunuşlar yeniden üretildi (yön {rot}, ters {flip or '—'}): {len(tl.contacts)} temas")
    tl = TouchLog.load(os.path.join(d, take["touches"]))
    if not a.plot and not a.auto and a.offset is None and a.rotation is None and a.flip is None:
        dur = max([c.t1 for c in tl.contacts], default=0)
        log(f"{d}\n  {len(tl.contacts)} temas · {len(tl.keys)} tuş · {dur:.1f} sn · yön {take.get('rotation')} · "
            f"ofset {take.get('touch_offset', 0):+.3f} sn · senkron: {take.get('sync', '?')}")
        for c in tl.contacts[:40]:
            kind = "kalem" if c.tool == "pen" else ("dokunuş" if c.t1 - c.t0 < 0.25 and c.travel() < 0.02 else "sürükleme")
            log(f"   {c.t0:7.2f}–{c.t1:6.2f}  {kind:<9} ({c.x[0]:.2f},{c.y[0]:.2f}) → ({c.x[-1]:.2f},{c.y[-1]:.2f})")
        if len(tl.contacts) > 40:
            log(f"   … +{len(tl.contacts) - 40}")
    if a.auto:
        lat = reaction_latency(take)
        off, conf, peak = estimate_offset(os.path.join(d, take["screen"]), tl, latency=lat)
        verdict = "güvenilir" if conf > 1.3 and peak > 0.15 else "ZAYIF — gözle doğrulayın (--plot)"
        log(f"  önerilen touch_offset: {off:+.3f} sn  (güven {conf:.2f}, tepe {peak:.2f} → {verdict}; "
            f"varsayılan tepki {lat * 1000:.0f} ms, kaynak {take.get('source', '?')})")
        if take.get("source") == "mac":
            log("  ℹ️ Mac çekiminin senkronu zaten kare-saatinden (SCK ilk kare) — bu öneri yalnız denetim")
        if a.write:
            take["touch_offset"] = round(off, 3)
            _save_take(d, take)
            log("  ✓ take.json güncellendi")
    if a.offset is not None:
        take["touch_offset"] = a.offset
        _save_take(d, take)
        log(f"  ✓ touch_offset = {a.offset:+.3f} sn")
    if a.plot is not None:
        out = a.plot or os.path.join(d, "touches.png")
        print(plot(d, take, tl, out, a.at))
