"""Ekran içeriği kaynakları: durağan görüntü ve video (ffmpeg borusu).

Video okuma tek yönlü ve sıralıdır: render zaman çizelgesi ileri aktığı için her
kaynak kendi ffmpeg sürecinden ham RGB kare çeker; geriye sarma gerekirse süreç
yeniden başlatılır. Kareler çıkış fps'ine ffmpeg'in `fps` süzgeciyle oturtulur —
`adb screenrecord` ve ScreenCaptureKit kayıtları DEĞİŞKEN kare hızlıdır (VFR).
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile

from PIL import Image, ImageCms

from .util import PromoError, ffmpeg_bin, ffprobe_bin

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
HTML_EXT = {".html", ".htm"}
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mkv", ".webm", ".avi"}


def to_srgb(im: Image.Image) -> Image.Image:
    """Gömülü ICC profili varsa (Mac ekran görüntüleri Display P3 gelir) sRGB'ye çevir."""
    icc = im.info.get("icc_profile")
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
    if not icc:
        return im
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        dst = ImageCms.createProfile("sRGB")
        alpha = im.getchannel("A") if im.mode == "RGBA" else None
        out = ImageCms.profileToProfile(im.convert("RGB"), src, dst, outputMode="RGB")
        if alpha is not None:
            out.putalpha(alpha)
        return out
    except Exception:
        return im


class Probe:
    def __init__(self, path: str):
        self.path = path
        ext = os.path.splitext(path)[1].lower()
        if not os.path.exists(path):
            raise PromoError(f"medya dosyası yok: {path}")
        if ext in HTML_EXT:  # canlı HTML ekran: boyut = sayfanın CSS görünüm alanı
            from .htmlscreen import viewport
            self.kind = "html"
            self.width, self.height = viewport(path)
            self.duration = None
            self.fps = None
            self.matrix = None
            return
        if ext in IMAGE_EXT:
            self.kind = "image"
            with Image.open(path) as im:
                self.width, self.height = im.size
            self.duration = None
            self.fps = None
            self.matrix = None
            return
        self.kind = "video"
        out = subprocess.run(
            [ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,avg_frame_rate,r_frame_rate,duration,color_space,color_transfer:"
             "stream_side_data=rotation:format=duration", "-of", "json", path],
            capture_output=True, text=True)
        if out.returncode != 0:
            raise PromoError(f"ffprobe okuyamadı: {path}\n{out.stderr.strip()}")
        info = json.loads(out.stdout or "{}")
        streams = info.get("streams") or []
        if not streams:
            raise PromoError(f"video akışı yok: {path}")
        st = streams[0]
        w, h = int(st["width"]), int(st["height"])
        rot = 0
        for sd in st.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
        if abs(rot) % 180 == 90:  # ffmpeg otomatik döndürür → boyutlar yer değiştirir
            w, h = h, w
        self.width, self.height = w, h
        dur = st.get("duration") or (info.get("format") or {}).get("duration")
        self.duration = float(dur) if dur not in (None, "N/A") else None
        self.fps = _rate(st.get("avg_frame_rate")) or _rate(st.get("r_frame_rate"))
        cs = (st.get("color_space") or "").lower()
        self.matrix = cs if cs and cs not in ("unknown", "reserved") else None
        self.transfer = (st.get("color_transfer") or "").lower()


def _rate(s):
    try:
        n, d = s.split("/")
        n, d = float(n), float(d)
        return n / d if d else None
    except Exception:
        return None


def cover_crop(media_w: float, media_h: float, box_w: float, box_h: float, fit: str = "cover"):
    """Medyanın kutuya oturması için normalize kırpma (x, y, w, h). 'contain' → kırpma yok."""
    if fit != "cover":
        return (0.0, 0.0, 1.0, 1.0)
    ma = media_w / media_h
    ba = box_w / box_h
    if abs(ma - ba) < 1e-3:
        return (0.0, 0.0, 1.0, 1.0)
    if ma > ba:  # medya daha geniş → yanlardan kırp
        w = ba / ma
        return ((1 - w) / 2, 0.0, w, 1.0)
    h = ma / ba
    return (0.0, (1 - h) / 2, 1.0, h)


class StillSource:
    def __init__(self, path: str, crop, size):
        im = Image.open(path)
        im.load()
        im = to_srgb(im).convert("RGB")
        cx, cy, cw, ch = crop
        W, H = im.size
        box = (round(cx * W), round(cy * H), round((cx + cw) * W), round((cy + ch) * H))
        self.base = im.crop(box) if box != (0, 0, W, H) else im
        self.size = size
        self._cache = {}

    def get(self, t_media: float, size=None) -> Image.Image:
        size = size or self.size
        im = self._cache.get(size)
        if im is None:
            im = self.base.resize(size, Image.Resampling.LANCZOS, reducing_gap=3.0)
            if len(self._cache) > 6:
                self._cache.clear()
            self._cache[size] = im
        return im

    def close(self):
        pass


class VideoSource:
    """ffmpeg'den ham RGB karelerini sırayla okuyan kaynak. `end` (medya `out`u): oynatma orada durur
    (son karede kalır), `loop`ta [start, end] aralığı döner."""

    def __init__(self, probe: Probe, crop, size, fps: float, start: float,
                 speed: float = 1.0, loop: bool = False, end: float | None = None):
        self.probe = probe
        self.crop = crop
        self.size = (max(2, int(size[0])) // 2 * 2, max(2, int(size[1])) // 2 * 2)
        self.fps = fps
        self.start = max(0.0, start)
        self.end = end if end is None else max(self.start + 1e-3, float(end))
        self.speed = speed
        self.loop = loop
        self.proc = None
        self.err = None
        self.index = -1
        self.frame = None
        self.eof = False
        self._resized = {}

    def _spawn(self, start: float):
        self.close()
        if self.end is not None:
            start = min(start, max(self.start, self.end - 1.0 / max(1.0, self.fps)))
        w, h = self.size
        cx, cy, cw, ch = self.crop
        vf = []
        if (cx, cy, cw, ch) != (0.0, 0.0, 1.0, 1.0):
            vf.append(f"crop=trunc(iw*{cw:.6f}):trunc(ih*{ch:.6f}):trunc(iw*{cx:.6f}):trunc(ih*{cy:.6f})")
        vf.append(f"setpts=(PTS-STARTPTS)/{self.speed:.6f}")
        vf.append(f"fps={self.fps:.6f}:round=near")
        matrix = self.probe.matrix or ("bt709" if self.probe.height >= 720 else "bt601")
        if matrix.startswith("bt2020"):
            matrix = "bt2020"
        elif matrix not in ("bt709", "bt601", "fcc", "smpte240m", "bt470bg", "smpte170m"):
            matrix = "bt709"
        vf.append(f"scale={w}:{h}:flags=lanczos:in_color_matrix={matrix}")
        cmd = [ffmpeg_bin(), "-v", "error", "-nostdin"]
        if start > 0:
            cmd += ["-ss", f"{start:.4f}"]
        if self.end is not None:
            cmd += ["-t", f"{self.end - start:.4f}"]
        cmd += ["-i", self.probe.path, "-an", "-sn", "-vf", ",".join(vf),
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        # stderr BORUYA DEĞİL dosyaya: okunmayan boru ~64 KB'ta dolunca ffmpeg yazarken, biz de
        # stdout'u okurken bekliyorduk → render sonsuza dek kilitleniyordu (bozuk/gürültülü kayıt)
        self.err = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=self.err, bufsize=w * h * 3)
        self.index = -1
        self.eof = False
        self._seg_start = start

    def _read_one(self) -> bool:
        n = self.size[0] * self.size[1] * 3
        data = self.proc.stdout.read(n)
        if not data or len(data) < n:
            if self.frame is None:
                raise PromoError(f"video okunamadı: {self.probe.path}\n{self._err_tail()}")
            self.eof = True
            return False
        self.frame = Image.frombuffer("RGB", self.size, data, "raw", "RGB", 0, 1)
        self._resized.clear()
        self.index += 1
        return True

    def get(self, t_media: float, size=None) -> Image.Image:
        if self.proc is None:
            self._spawn(self.start)
        k = int(round((t_media - self._seg_start) / self.speed * self.fps + 1e-6))
        if k < self.index:  # geri sarma: süreci baştan kur
            self._spawn(max(0.0, t_media))
            k = 0
        while self.index < k and not self.eof:
            if not self._read_one():
                if self.loop and self.index >= 0:
                    # Bölüm bitti → aynı başlangıçtan yeniden oku; zaman ekseni devam eder.
                    next_start = self._seg_start + (self.index + 1) / self.fps * self.speed
                    self._spawn(self.start)
                    self._seg_start = next_start
                    k = int(round((t_media - self._seg_start) / self.speed * self.fps + 1e-6))
                    continue
                break
        if self.frame is None:
            self._read_one()
        if size is None or tuple(size) == self.size:
            return self.frame
        im = self._resized.get(tuple(size))
        if im is None:
            im = self.frame.resize(tuple(size), Image.Resampling.BICUBIC, reducing_gap=2.0)
            self._resized[tuple(size)] = im
        return im

    def _err_tail(self) -> str:
        try:
            self.proc.wait(timeout=2)
            self.err.seek(0)
            return self.err.read()[-4000:].decode(errors="replace").strip()
        except Exception:
            return ""

    def close(self):
        if self.proc is not None:
            try:  # ÖNCE süreci öldür: başka iş parçacığı okurken boruyu kapatmak kilitlenir
                self.proc.kill()
                self.proc.wait(timeout=2)
            except Exception:
                pass
            try:
                self.proc.stdout.close()
            except Exception:
                pass
            self.proc = None
        if self.err is not None:
            try:
                self.err.close()
            except Exception:
                pass
            self.err = None


class SolidSource:
    def __init__(self, color, size):
        self.color = color
        self.size = size

    def get(self, t_media, size=None):
        return Image.new("RGB", tuple(size or self.size), self.color)

    def close(self):
        pass
