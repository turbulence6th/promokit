"""Dizüstü modelleri — `[video] laptop_model`: 14" MacBook Pro (varsayılan) · genel Windows dizüstü.

Sitenin Windows sayfaları için (2026-10-24) aracın Windows karşılığı: aynı sahne, Windows dizüstüyle. Kilitler:
1. Seçenek verilmemesi = MacBook'un TIPKISI — mevcut her senaryo aynı çıktıyı üretir (geometri, yerleşim, sprite).
2. Model senaryo düzeyinde YÜKLEMEDE doğrulanır: yazım hatası öneriyle, kaplama MODELE göre (Windows'ta "spaceblack"
   yok, MacBook'ta "graphite" yok), varsayılan kaplama modelin.
3. Windows gövdesi kendi geometrisiyle yerleşir ve çizilir: çentik YOK, panel düz köşeli, 16:10 ekran, 14".
4. Kablo fişi MODELİN gövdesine takılır (alt gövdenin sağ yüzü) — sabit bir nokta değil.
Tarayıcı gerekmez (düz renk medya).
"""
import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promokit import devices, layout, story  # noqa: E402
from promokit.util import PromoError  # noqa: E402

WIN = {"model": "windows"}


def _load(video: str, scene: str = 'layout = "duo"\nduration = 2\nlaptop = "#88aaff"\ntablet = "#ffaa88"\n') -> story.Story:
    d = tempfile.mkdtemp(prefix="promo-laptop-")
    p = os.path.join(d, "s.toml")
    with open(p, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent("[video]\n" + video + "\n[[scene]]\n" + scene))
    return story.load(p)


def _frame(st: story.Story, t: float):
    from promokit.render import Timeline
    tl = Timeline(st)
    try:
        return tl.frame(t)
    finally:
        tl.close()


class Defaults(unittest.TestCase):
    def test_default_is_macbook(self):
        v = _load("").video
        self.assertEqual((v.laptop_model, v.laptop_finish), ("macbook", "silver"))

    def test_no_option_equals_macbook(self):
        """MacBook yolu seçeneksiz çağrılarla BİREBİR: geometri, yerleşim, sprite (mevcut çıktılar değişmez)."""
        mac = {"model": "macbook"}
        self.assertEqual(devices.geometry("laptop", 700, 455), devices.geometry("laptop", 700, 455, mac))
        self.assertEqual(devices.lid_marks(700), devices.lid_marks(700, mac))
        for W, H in ((1920, 1080), (1080, 1920)):
            self.assertEqual(layout.place(W, H, "duo", {}), layout.place(W, H, "duo", {}, opts={"laptop": mac}))
        a = devices.chrome("laptop", 420, 273, {"finish": "silver"})
        b = devices.chrome("laptop", 420, 273, {"finish": "silver", "model": "macbook"})
        self.assertEqual(a.body.tobytes(), b.body.tobytes())
        self.assertIs(a.spec, devices.LAPTOP)


class Validation(unittest.TestCase):
    def test_windows_loads_with_its_own_default_finish(self):
        v = _load('laptop_model = "windows"').video
        self.assertEqual((v.laptop_model, v.laptop_finish), ("windows", "graphite"))
        self.assertEqual(_load('laptop_model = "windows"\nlaptop_finish = "silver"').video.laptop_finish, "silver")

    def test_bad_values_raise_at_load(self):
        for video, hint in (('laptop_model = "windwos"', "windows"),                          # yazım hatası → öneri
                            ('laptop_model = "windows"\nlaptop_finish = "spaceblack"', "graphite"),
                            ('laptop_finish = "graphite"', "spaceblack")):                   # MacBook kaplaması değil
            with self.subTest(video), self.assertRaises(PromoError) as cm:
                _load(video)
            self.assertIn(hint, str(cm.exception))


class WindowsBody(unittest.TestCase):
    def test_no_notch_square_panel(self):
        w = devices.chrome("laptop", 420, 263, WIN)
        m = devices.chrome("laptop", 420, 273, {})
        self.assertIsNone(w.notch)
        self.assertIsNotNone(m.notch)
        self.assertIs(w.spec, devices.WIN_LAPTOP)                  # kapak kapanması bu oranlarla çizer
        self.assertEqual(w.mask.getpixel((0, 0)), 255)              # Windows paneli düz köşeli
        self.assertEqual(m.mask.getpixel((0, 0)), 0)                # MacBook'un ekran köşesi yuvarlak
        # sprite gövdenin geometrisiyle aynı boyda (yerleşim/başlık bandı aynı kutuyu kullanır)
        g = devices.geometry("laptop", 420, 263, WIN)
        self.assertEqual(w.body.size, (round(g.width), round(g.height)))

    def test_layout_uses_the_models_screen(self):
        """Medyasız Windows dizüstü 16:10 ve 14" — tabletle oranı fiziksel köşegenlerden."""
        for model, diag, aspect in (({}, 14.2, 1470 / 956), (WIN, 14.0, 1440 / 900)):
            r = layout.place(1920, 1080, "duo", {}, opts={"laptop": model})
            (_, _, lw, lh), (_, _, tw, th) = r["laptop"], r["tablet"]
            self.assertAlmostEqual(lw / lh, aspect, places=6)
            self.assertAlmostEqual((lw ** 2 + lh ** 2) ** .5 / (tw ** 2 + th ** 2) ** .5, diag / 11.5, places=6)

    def test_lid_starts_closing_without_a_jump(self):
        """Kapak kapanması (`render._closing_laptop`) MODELİN oranlarıyla çizer: kapanışın ilk karesi açık gövdeyle
        birebir (MacBook'taki `test_front_edge_stays_on_the_desk` kuralı). MacBook oranlarıyla çizilseydi Windows
        kapağı ilk karede daralıp zıplardı (kapak payı 0,05 ↔ 0,06 ekran genişliği)."""
        from PIL import ImageChops
        video = 'size = 540\nfps = 30\nlaptop_model = "windows"\nlaptop_finish = "silver"\nbackground = "#0b0f16"'
        scene = 'layout = "laptop"\nduration = 3\nlaptop = "#88aaff"\nanim = {{ laptop = [ {} ] }}\n'
        still = _frame(_load(video, scene.format('{ t = 0, lid = 1.0 }')), 0.8)
        start = _frame(_load(video, scene.format('{ t = 0, lid = 1.0 }, { t = 1, lid = 0.0, ease = "linear" }')), 0.003)
        d = ImageChops.difference(start.convert("RGB"), still.convert("RGB")).convert("L")
        self.assertLess(sum(d.histogram()[25:]), 150)


if __name__ == "__main__":
    unittest.main()
