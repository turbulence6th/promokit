"""Altyazıyı görüntüye yakma — `promo.py burn VIDEO` (promokit/burn.py).

Kullanıcı (2026-10-22): Instagram Reels altyazı dosyası kabul etmiyor; videolar İngilizce seslendirilir, Türkçe
izleyici için çeviri görüntüye basılır. Bu makinedeki ffmpeg'de drawtext/libass YOK → her satır PIL ile şeffaf
PNG'ye çizilip ffmpeg `overlay` ile KENDİ zaman aralığında bindirilir. Kilitlenenler: SRT okuma · 9:16'da altyazı
platformun alt şeridinin ÜSTÜNDE (Reels'in yazı/düğme alanı altyazıyı örtmesin) · zaman pencereleri · gerçek
videoda altyazı yalnız kendi aralığında görünüyor ve ses akışı korunuyor.
"""
import os
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from promokit import burn  # noqa: E402
from promokit.layout import safe_bottom  # noqa: E402
from promokit.text import Fonts  # noqa: E402
from promokit.util import PromoError, ffmpeg_bin, ffprobe_bin  # noqa: E402

SRT = """1
00:00:00,100 --> 00:00:02,220
Mac'iniz tableti bir ekran olarak görür…

2
00:00:02,600 --> 00:00:04,460
…yüksek dinamik aralık ve yüksek
yenileme hızıyla.
"""


class ParseSrt(unittest.TestCase):
    def test_cues_with_times_and_joined_lines(self):
        cues = burn.parse_srt(SRT)
        self.assertEqual(len(cues), 2)
        self.assertAlmostEqual(cues[0].start, 0.1)
        self.assertAlmostEqual(cues[0].end, 2.22)
        self.assertEqual(cues[0].text, "Mac'iniz tableti bir ekran olarak görür…")
        # SRT'nin kendi satır kırılımı birleştirilir — yakarken çerçeve genişliğine göre yeniden kırılır
        self.assertEqual(cues[1].text, "…yüksek dinamik aralık ve yüksek yenileme hızıyla.")

    def test_crlf_and_trailing_blank_lines(self):
        cues = burn.parse_srt(SRT.replace("\n", "\r\n") + "\r\n\r\n")
        self.assertEqual([round(c.start, 2) for c in cues], [0.1, 2.6])

    def test_empty_or_broken_srt_is_an_error(self):
        with self.assertRaises(PromoError):
            burn.parse_srt("")
        with self.assertRaises(PromoError):
            burn.parse_srt("1\nbozuk zaman\nmetin\n")


class Placement(unittest.TestCase):
    def test_tall_frame_stays_above_platform_strip(self):
        W, H = 1080, 1920
        img = burn.cue_image(Fonts(), "…yüksek dinamik aralık ve yüksek yenileme hızıyla.", W, H)
        x, y = burn.placement(W, H, img.width, img.height)
        self.assertLessEqual(y + img.height, safe_bottom(W, H))       # Reels'in alt şeridi örtmesin
        self.assertGreater(y + img.height, H * 0.7)                   # ama aşağıda: içerik ortada
        self.assertAlmostEqual(x + img.width / 2, W / 2, delta=1)     # yatayda ortalı
        self.assertLessEqual(img.width, W * 0.9)                      # uzun satır KIRILIR, kadrajdan taşmaz

    def test_wide_frame_near_the_bottom(self):
        W, H = 1920, 1080
        img = burn.cue_image(Fonts(), "Kısa satır.", W, H)
        x, y = burn.placement(W, H, img.width, img.height)
        self.assertLessEqual(y + img.height, H)
        self.assertGreater(y + img.height, H * 0.85)

    def test_pill_is_dark_under_light_text(self):
        img = burn.cue_image(Fonts(), "Kısa satır.", 1080, 1920).convert("RGBA")
        corner = img.getpixel((img.width // 2, 3))        # kutunun üst kenarı: koyu, yarı saydam
        self.assertLess(sum(corner[:3]) / 3, 80)
        self.assertGreater(corner[3], 120)


class FilterGraph(unittest.TestCase):
    def test_each_cue_only_inside_its_window(self):
        cues = burn.parse_srt(SRT)
        g = burn.filter_graph([(c, 10, 20) for c in cues])
        self.assertIn("enable='between(t,0.100,2.220)'", g)
        self.assertIn("enable='between(t,2.600,4.460)'", g)
        self.assertIn("[0:v][1:v]overlay=x=10:y=20", g)
        self.assertIn("[v1][2:v]overlay=", g)
        self.assertTrue(g.endswith("[v2]"))


def _probe_streams(path):
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "stream=codec_type",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True).stdout
    return sorted(out.split())


def _frame(path, t, out_png):
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-ss", f"{t}", "-i", path, "-frames:v", "1", out_png], check=True)
    return Image.open(out_png).convert("RGB")


class BurnEndToEnd(unittest.TestCase):
    """Beyaz 9:16 video + ses; altyazı 0,5–1,0 sn. Altyazı bölgesi o aralıkta KOYU, sonra yine BEYAZ."""

    def test_subtitle_visible_only_in_its_window_and_audio_kept(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "v.mp4")
            subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=white:s=360x640:d=2:r=30",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", src], check=True)
            srt = os.path.join(d, "v.tr.srt")
            with open(srt, "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,500 --> 00:00:01,000\nAltyazı burada\n")
            out = burn.burn(src, srt, os.path.join(d, "v.tr-sub.mp4"))
            self.assertEqual(_probe_streams(out), ["audio", "video"])
            img = burn.cue_image(Fonts(), "Altyazı burada", 360, 640)
            x, y = burn.placement(360, 640, img.width, img.height)
            probe = (x + 4, y + img.height // 2)                       # kutunun sol iç kenarı (yazının dışı)
            inside = _frame(out, 0.75, os.path.join(d, "a.png")).getpixel(probe)
            after = _frame(out, 1.5, os.path.join(d, "b.png")).getpixel(probe)
            self.assertLess(sum(inside) / 3, 110, f"altyazı aralığında kutu yok: {inside}")
            self.assertGreater(sum(after) / 3, 235, f"aralık bitince kutu kalmış: {after}")

    def test_default_paths_follow_the_video_name(self):
        self.assertEqual(burn.default_srt("/x/kisa-ekran.mp4", "tr"), "/x/kisa-ekran.tr.srt")
        self.assertEqual(burn.default_out("/x/kisa-ekran.mp4", "tr"), "/x/kisa-ekran.tr-sub.mp4")


if __name__ == "__main__":
    unittest.main()
