"""Ortak yardımcılar: easing, renk, geometri, ffmpeg/adb yolları.

Tüm modüller yalnız stdlib + Pillow kullanır; numpy YOK (Windows kutusunda da
`pip` gerektirmeden koşsun diye).
"""
from __future__ import annotations

import math
import os
import shutil
import sys

# ── Varsayılan renk paleti (kendi markanız için burayı değiştirin) ──
BRAND = {
    "bg": "#f6f8fc",
    "fg": "#0f1422",
    "muted": "#5b6678",
    "accent": "#4f7cff",
    "accent2": "#8b5bff",
    "border": "#e2e7f1",
    "soft": "#f1f4fa",
    "frame": "#0b0d12",
    "white": "#ffffff",
    "black": "#000000",
}


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp2(p, q, t):
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def dist(p, q) -> float:
    return math.hypot(q[0] - p[0], q[1] - p[1])


def ease(t: float, kind: str = "inout") -> float:
    """0..1 → 0..1 easing eğrileri (kübik aile + outback + sine)."""
    t = clamp(t)
    if kind == "linear":
        return t
    if kind == "in":
        return t * t * t
    if kind == "out":
        return 1 - (1 - t) ** 3
    if kind == "inout":
        return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2
    if kind == "sine":
        return -(math.cos(math.pi * t) - 1) / 2
    if kind == "outback":
        c1 = 1.70158
        c3 = c1 + 1
        return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2
    if kind == "quart":  # sert başlayıp yumuşak biten (flick)
        return 1 - (1 - t) ** 4
    raise ValueError(f"bilinmeyen easing: {kind}")


EASINGS = ("linear", "in", "out", "inout", "sine", "outback", "quart")


def bump(t: float, t0: float, rise: float, fall: float) -> float:
    """t0'da başlayan, `rise` sn'de 1'e çıkıp `fall` sn'de 0'a inen yumuşak tümsek."""
    if t < t0:
        return 0.0
    if t < t0 + rise:
        return ease((t - t0) / rise, "sine")
    if t < t0 + rise + fall:
        return 1 - ease((t - t0 - rise) / fall, "sine")
    return 0.0


def parse_color(spec, alpha: float | None = None) -> tuple[int, int, int, int]:
    """'#rgb' / '#rrggbb' / '#rrggbbaa' / marka adı ('accent', 'fg' …) / (r,g,b[,a])."""
    if isinstance(spec, (tuple, list)):
        r, g, b = (int(v) for v in spec[:3])
        a = int(spec[3]) if len(spec) > 3 else 255
    else:
        s = str(spec).strip()
        s = BRAND.get(s, s)
        if not s.startswith("#"):
            raise ValueError(f"renk anlaşılamadı: {spec!r} (ör. '#4f7cff' ya da 'accent')")
        h = s[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            raise ValueError(f"renk anlaşılamadı: {spec!r}")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = int(h[6:8], 16) if len(h) == 8 else 255
    if alpha is not None:
        a = int(round(clamp(alpha) * 255))
    return (r, g, b, a)


def rgb(spec) -> tuple[int, int, int]:
    return parse_color(spec)[:3]


def mix(c1, c2, t):
    """İki RGB(A) rengi karıştır."""
    return tuple(int(round(lerp(a, b, t))) for a, b in zip(c1, c2))


def rot(p, ang: float):
    """(x, y) noktasını görüntü koordinatlarında (y aşağı) `ang` radyan döndür."""
    c, s = math.cos(ang), math.sin(ang)
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


def catmull_rom(points, samples: int = 8, closed: bool = False):
    """Noktalardan geçen yumuşak eğri (centripetal değil, uniform — kısa yollar için yeterli)."""
    pts = list(points)
    n = len(pts)
    if n < 3:
        return pts
    out = []
    rng = range(n) if closed else range(n - 1)
    for i in rng:
        p0 = pts[(i - 1) % n] if closed or i > 0 else pts[0]
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        p3 = pts[(i + 2) % n] if closed or i + 2 < n else pts[-1]
        for k in range(samples):
            t = k / samples
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
    if not closed:
        out.append(pts[-1])
    return out


def polyline_length(pts) -> float:
    return sum(dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def point_along(pts, s: float):
    """Polyline üzerinde 0..1 yay-uzunluğu oranındaki nokta."""
    if len(pts) == 1:
        return pts[0]
    total = polyline_length(pts)
    if total <= 1e-9:
        return pts[0]
    target = clamp(s) * total
    acc = 0.0
    for i in range(len(pts) - 1):
        seg = dist(pts[i], pts[i + 1])
        if acc + seg >= target:
            k = (target - acc) / seg if seg > 0 else 0
            return lerp2(pts[i], pts[i + 1], k)
        acc += seg
    return pts[-1]


def smooth_series(values, alpha: float):
    """Sıfır-fazlı (ileri+geri) üstel yumuşatma — gecikmesiz. values: float listesi."""
    if not values:
        return values
    fwd = [values[0]]
    for v in values[1:]:
        fwd.append(fwd[-1] + alpha * (v - fwd[-1]))
    out = [fwd[-1]] * len(fwd)
    acc = fwd[-1]
    for i in range(len(fwd) - 1, -1, -1):
        acc = acc + alpha * (fwd[i] - acc)
        out[i] = acc
    return out


def find_tool(name: str, extra: list[str] | None = None) -> str | None:
    """PATH + bilinen Homebrew/SDK yollarında bir aracı bul."""
    env = os.environ.get(name.upper())
    if env and os.path.exists(env):
        return env
    p = shutil.which(name)
    if p:
        return p
    for c in (extra or []):
        c = os.path.expanduser(c)
        if os.path.exists(c):
            return c
    return None


def ffmpeg_bin() -> str:
    p = find_tool("ffmpeg", ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"])
    if not p:
        die("ffmpeg bulunamadı (brew install ffmpeg)")
    return p


def ffprobe_bin() -> str:
    p = find_tool("ffprobe", ["/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"])
    if not p:
        die("ffprobe bulunamadı (brew install ffmpeg)")
    return p


def adb_bin() -> str | None:
    # macOS'ta adb çoğu kurulumda PATH'te DEĞİLDİR (Android Studio SDK'sı); `ADB` ortam değişkeni de kabul edilir.
    return find_tool("adb", [
        "~/Library/Android/sdk/platform-tools/adb",
        "~/AppData/Local/Android/Sdk/platform-tools/adb.exe",
    ])


class PromoError(Exception):
    """Kullanıcıya yığın izi olmadan gösterilecek hata."""


def die(msg: str):
    raise PromoError(msg)


def log(msg: str):
    print(msg, file=sys.stderr, flush=True)


def fmt_time(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:05.2f}"
