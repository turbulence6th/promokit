"""Jest sınıflandırması = README'deki jest sözleşmesinin durum makinesi (tarayıcısız, hızlı).

⚠️ Birim sözleşmesi: eşikler **dp**; sayfa CSS px'i dp'ye `htmlscreen.dp_per_css` ile çevrilir
(referans tablet 2800×1840 panel @ density 2.0 = 1400×920 dp, sayfa tabletin TAMAMINI kaplar →
1400 CSS genişliğinde 1 CSS px = 1 dp).
"""
import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promokit import gestures, htmlscreen  # noqa: E402
from promokit.htmlscreen import classify  # noqa: E402
from promokit.touches import Contact  # noqa: E402
from promokit.util import PromoError  # noqa: E402

MODEL = gestures.HandModel(fw=0.05, angle=math.radians(-14), aspect=1400 / 920)
CSS = (1400, 920)


def gest(specs, latency=0.0):
    cs, keys, texts = gestures.compile_gestures(specs, MODEL)
    return classify(cs, keys, texts, CSS, latency=latency)["gestures"]


def kinds(specs):
    return [(g["type"], g["fingers"]) for g in gest(specs)]


def contact(t0, dur, pts, hz=120.0, tool="finger"):
    """Yolu (CSS px) ±0 gürültüyle örnekle: pts = [(t_göreli, x, y), …] doğrusal ara değer."""
    n = max(1, int(dur * hz))
    ts, xs, ys = [], [], []
    for k in range(n + 1):
        t = dur * k / n
        for (ta, xa, ya), (tb, xb, yb) in zip(pts, pts[1:]):
            if ta <= t <= tb:
                u = (t - ta) / (tb - ta) if tb > ta else 0.0
                x, y = xa + (xb - xa) * u, ya + (yb - ya) * u
                break
        else:
            x, y = pts[-1][1], pts[-1][2]
        ts.append(t0 + t)
        xs.append(x / CSS[0])
        ys.append(y / CSS[1])
    c = Contact(ts, xs, ys)
    c.tool = tool
    return c


def jitter(c, amp_px, seed=7):
    rnd = random.Random(seed)
    c.x = [x + rnd.uniform(-amp_px, amp_px) / CSS[0] for x in c.x]
    c.y = [y + rnd.uniform(-amp_px, amp_px) / CSS[1] for y in c.y]
    return c


class TestThresholds(unittest.TestCase):
    """Eşik sabitleri (dp / sn) kilitli: biri değişirse jestlerin ANLAMI değişir — bilerek değiştiriyorsanız
    buradaki değeri de güncelleyin (ve README'deki jest tablosunu)."""

    LOCKED = {
        "TAP_SLOP_DP": 12.0, "GRAB_DRAG_SLOP_DP": 4.0, "LONG_PRESS_S": 0.4, "TWO_TAP_MAX_S": 0.35,
        "DOUBLE_TAP_S": 0.35, "DOUBLE_TAP_SLOP_DP": 30.0, "CURSOR_ARM_DP": 6.0, "CURSOR_SPAN_MAX_DP": 5.0,
        "ZOOM_ARM_DP": 6.0, "KB_SWIPE_DP": 60.0, "DOCK_ENGAGE_DP": 30.0, "ZOOM_GAIN_PER_DP": 0.005,
        "DOCK_DOMINANCE": 1.4, "DOCK_FLING_GAP_S": 0.1, "PEN_SLOP_DP": 8.0,
    }

    def test_thresholds_are_locked(self):
        for name, want in self.LOCKED.items():
            self.assertAlmostEqual(getattr(htmlscreen, name), want, places=6, msg=name)

    def test_dp_mapping(self):
        self.assertEqual(htmlscreen.dp_per_css((1400, 920)), 1.0)
        self.assertAlmostEqual(htmlscreen.dp_per_css((700, 460)), 2.0)


class TestOneFinger(unittest.TestCase):
    def test_tap_long_press_drag_scroll(self):
        self.assertEqual(kinds([{"t": 0, "tap": [0.4, 0.4]}]), [("tap", 1)])
        g = gest([{"t": 1.0, "long_press": [0.4, 0.4]}])[0]
        self.assertEqual((g["type"], g.get("via")), ("secondary", "longpress"))
        self.assertAlmostEqual(g["ta"], 1.0 + htmlscreen.LONG_PRESS_S, places=6)   # halka yeşile döner
        g = gest([{"t": 1.0, "drag": [[0.3, 0.5], [0.6, 0.5]]}])[0]
        self.assertEqual(g["type"], "drag")
        self.assertGreater(g["tb"], g["ta"])             # sol tuş GRAB'dan sonra, 4 dp aşılınca iner
        g = gest([{"t": 1.0, "scroll": [[0.5, 0.8], [0.5, 0.3]]}])[0]
        self.assertEqual(g["type"], "scroll")
        self.assertGreater(g["ts"], g["t0"])             # içerik 12 dp eşiği aşılınca kayar

    def test_drag_hold_must_reach_long_press(self):
        with self.assertRaises(PromoError):
            gestures.compile_gestures([{"t": 0, "drag": [[0.3, 0.5], [0.6, 0.5]], "hold": 0.3}], MODEL)
        gestures.compile_gestures([{"t": 0, "drag": [[0.3, 0.5], [0.6, 0.5]], "hold": 0.4}], MODEL)

    def test_slop_is_displacement_not_path_length(self):
        # Kaydedilmiş dokunuş titrer: yol uzunluğu birikir ama parmak YERİNDEN oynamadı (#8)
        lp = jitter(contact(1.0, 0.7, [(0, 500, 400), (0.7, 500, 400)]), 1.0)
        g = classify([lp], [], [], CSS)["gestures"][0]
        self.assertEqual(g["type"], "secondary")
        tap = jitter(contact(3.0, 0.12, [(0, 500, 400), (0.12, 500, 400)]), 2.0)
        self.assertEqual(classify([tap], [], [], CSS)["gestures"][0]["type"], "tap")

    def test_grab_then_small_move_is_drag(self):
        # Ürün: GRAB sonrası 4 dp (GRAB_DRAG_SLOP) → seçim/sürükleme; 12 dp'ye kadar beklemez
        c = contact(1.0, 0.9, [(0, 500, 400), (0.5, 500, 400), (0.9, 507, 400)])
        self.assertEqual(classify([c], [], [], CSS)["gestures"][0]["type"], "drag")

    def test_double_tap_flag_and_position_lock(self):
        gs = gest([{"t": 1.0, "double_tap": [0.4, 0.4]}])
        self.assertEqual([g["type"] for g in gs], ["tap", "tap"])
        self.assertTrue(gs[1].get("double"))
        a = contact(1.0, 0.08, [(0, 500, 400), (0.08, 500, 400)])
        b = contact(1.2, 0.08, [(0, 515, 410), (0.08, 515, 410)])     # 30 dp içinde, biraz kaymış
        g2 = classify([a, b], [], [], CSS)["gestures"][1]
        self.assertTrue(g2.get("double"))
        self.assertAlmostEqual(g2["path"][0][1], 500, delta=0.01)    # çift-tık konum kilidi: ilk dokunuşun yeri

    def test_back_to_back_taps_stay_taps(self):
        # Biri bittiği AN öteki başlayan iki dokunuş eşzamanlı DEĞİL (eskiden 2 parmak sağ tık oluyordu, #4)
        self.assertEqual(kinds([{"t": 1.0, "tap": [0.3, 0.3]}, {"t": 1.09, "tap": [0.6, 0.6]}]),
                         [("tap", 1), ("tap", 1)])

    def test_fast_keytaps_are_independent_keyboard_taps(self):
        # Ekran klavyesi tabletin KENDİ görünümü: üst üste binen tuş dokunuşları Mac jesti değildir (#4)
        cs, keys, texts = gestures.compile_gestures([{"t": 1.0, "keytaps": "termos", "rate": 14}], MODEL,
                                                    resolver=lambda *a, **k: (0.3, 0.8, 0.02, 0.05))
        gs = classify(cs, keys, texts, CSS)["gestures"]
        self.assertEqual([(g["type"], g.get("local")) for g in gs], [("tap", True)] * 6)


class TestMultiFinger(unittest.TestCase):
    def test_two_finger_tap_is_delayed_right_click(self):
        g = gest([{"t": 1.0, "two_finger_tap": [0.4, 0.4]}])[0]
        self.assertEqual((g["type"], g["fingers"]), ("secondary", 2))
        self.assertAlmostEqual(g["tf"], g["t1"] + htmlscreen.DOUBLE_TAP_S, places=6)

    def test_touch_inside_window_cancels_two_finger_right_click(self):
        gs = gest([{"t": 1.0, "two_finger_tap": [0.4, 0.4]}, {"t": 1.25, "tap": [0.7, 0.7]}])
        self.assertEqual([(g["type"], g["fingers"]) for g in gs], [("tap", 1)])

    def test_two_finger_double_tap_is_smart_zoom(self):
        # Ürün (TouchscreenGestures.onUp): ikinci 2-parmak tap'ın KALKIŞI ilkinin kalkışından ≤ çift-dokunma
        # penceresi sonra → SMARTZOOM, ikinci kalkışta; ilkinin gecikmeli sağ tıkı yeni inişle iptal olur
        gs = gest([{"t": 1.0, "two_finger_tap": [0.4, 0.4]}, {"t": 1.2, "two_finger_tap": [0.4, 0.4]}])
        self.assertEqual([(g["type"], g["fingers"]) for g in gs], [("smartzoom", 2)])
        self.assertAlmostEqual(gs[0]["tf"], gs[0]["t1"], places=6)
        # İkinci tap pencerede İNDİ (ilkini iptal etti) ama geç KALKTI → kendisi gecikmeli sağ tık
        gs = gest([{"t": 1.0, "two_finger_tap": [0.4, 0.4]}, {"t": 1.3, "two_finger_tap": [0.4, 0.4], "dur": 0.2}])
        self.assertEqual([g["type"] for g in gs], ["secondary"])
        # Smart zoom sayacı sıfırlar (`lastTwoTapMs = 0`): üçüncü tap yeni bir çiftin İLKİDİR
        gs = gest([{"t": 1.0, "two_finger_tap": [0.4, 0.4]}, {"t": 1.2, "two_finger_tap": [0.4, 0.4]},
                   {"t": 1.4, "two_finger_tap": [0.4, 0.4]}])
        self.assertEqual([g["type"] for g in gs], ["smartzoom", "secondary"])

    def test_pinch_and_pointer_arm_like_product(self):
        self.assertEqual(kinds([{"t": 0, "pinch": [0.5, 0.5], "from": 0.05, "to": 0.2}]), [("pinch", 2)])
        # 6 dp yarıçap değişimi yeter (eski 30 px eşiği küçük çimdiği imleç sanıyordu)
        g = gest([{"t": 0, "pinch": [0.5, 0.5], "from": 0.05, "to": 0.065}])[0]
        self.assertEqual(g["type"], "pinch")
        self.assertGreater(g["ta"], g["t0"])
        self.assertEqual(kinds([{"t": 0, "pointer": [[0.3, 0.5], [0.6, 0.4]]}]), [("pointer", 2)])

    def test_touching_contacts_are_not_concurrent(self):
        # Bir parmak basılı; öteki parmak art arda iki kez dokunuyor (biri kalkarken öteki iniyor) → en çok
        # İKİ eşzamanlı temas. Eskiden bitiş/başlangıç eşitliği 3 sayılıp Mission Control jesti oluyordu.
        hold = contact(1.0, 1.0, [(0, 300, 400), (1.0, 300, 400)])
        a = contact(1.2, 0.2, [(0, 600, 400), (0.2, 600, 400)])
        b = contact(1.4, 0.2, [(0, 640, 420), (0.2, 640, 420)])
        gs = classify([hold, a, b], [], [], CSS)["gestures"]
        self.assertEqual([g["fingers"] for g in gs], [2])

    def test_three_finger_flick_back_cancels(self):
        # Ürün (DockSwipeStream.lift): kalkış hızı birikmiş yolun TERSİNEyse İPTAL — Dock geri yaslanır
        g = gest([{"t": 0, "swipe3": [[0.5, 0.72], [0.5, 0.40], [0.5, 0.55]], "ease": "linear", "smooth": False}])[0]
        self.assertEqual((g["axis"], g["cancel"]), ("v", True))
        g = gest([{"t": 0, "swipe3": [[0.5, 0.72], [0.5, 0.32]], "ease": "linear"}])[0]
        self.assertFalse(g["cancel"])

    def test_three_finger_axis_lock(self):
        g = gest([{"t": 0, "swipe3": [[0.5, 0.72], [0.5, 0.32]]}])[0]
        self.assertEqual((g["type"], g["fingers"], g["axis"]), ("swipe", 3, "v"))
        g = gest([{"t": 0, "swipe3": [[0.7, 0.5], [0.3, 0.5]]}])[0]
        self.assertEqual(g["axis"], "h")
        g = gest([{"t": 0, "swipe3": [[0.5, 0.5], [0.5, 0.52]]}])[0]   # 30 dp altı → Dock hiç görmez
        self.assertIsNone(g["axis"])

    def test_four_finger_keyboard_needs_threshold(self):
        g = gest([{"t": 0, "swipe4": [[0.4, 0.8], [0.4, 0.45]]}])[0]
        self.assertEqual(g["kb"]["show"], True)
        g = gest([{"t": 0, "swipe4": [[0.4, 0.45], [0.4, 0.8]]}])[0]
        self.assertEqual(g["kb"]["show"], False)
        g = gest([{"t": 0, "swipe4": [[0.4, 0.8], [0.4, 0.75]]}])[0]  # 46 dp < 60 dp
        self.assertIsNone(g["kb"])


class TestTabletBack(unittest.TestCase):
    """Tabletin KENDİ geri jesti (docs/input/touchscreen-input.md §4.3): kenar kaydırma OS'un jestidir, uygulamaya
    dokunuş olarak gelmez; ürün geri OLAYINI yakalayıp Mac'e sayfa geri gönderir. Kaydırma DEĞİLDİR."""

    def test_edge_swipe_is_back_not_scroll(self):
        g = gest([{"t": 1.0, "back": [0.0, 0.5]}])
        self.assertEqual([(x["type"], x["side"]) for x in g], [("back", "left")])
        self.assertTrue(g[0]["local"])
        p = g[0]["path"]
        self.assertAlmostEqual(p[0][1], 0.0, delta=1.0)        # sol kenardan (CSS px)
        self.assertGreater(p[-1][1], p[0][1] + 60)             # içeri doğru
        g = gest([{"t": 1.0, "back": [1.0, 0.4]}])[0]
        self.assertEqual(g["side"], "right")
        self.assertLess(g["path"][-1][1], g["path"][0][1] - 60)

    def test_back_must_start_at_an_edge(self):
        # ortadan tek parmak sürükleme üründe KAYDIRMADIR (iddia mesaja bakar: bilinmeyen-tür hatası da geçirirdi)
        with self.assertRaisesRegex(PromoError, "kenardan"):
            gest([{"t": 1.0, "back": [0.4, 0.5]}])


class TestPen(unittest.TestCase):
    def test_still_pen_is_click_even_when_long(self):
        self.assertEqual(kinds([{"t": 0, "tap": [0.4, 0.4], "tool": "pen"}]), [("tap", 1)])
        g = gest([{"t": 0, "long_press": [0.4, 0.4], "tool": "pen"}])[0]
        self.assertEqual((g["type"], g.get("tool")), ("tap", "pen"))
        self.assertEqual(kinds([{"t": 0, "pen": [[0.2, 0.5], [0.6, 0.4]]}]), [("pen", 1)])

    def test_pressure_profile_reaches_each_key(self):
        """`pressure = [a, b, c, …]` (3+ anahtar): vuruş boyunca eşit aralıklı anahtarlar → fırça kalemi gibi
        ince-kalın-ince dalga. İki değerli eski biçim (uçlar ince, orta kalın) değişmez. İnceleme (2026-10-20):
        `[0.05, 1.0]` √sin ile hemen tavana çıkıyordu → çizgi neredeyse eşit kalın, basınç videoda görünmüyordu."""
        def contact(pr):
            cs, _, _ = gestures.compile_gestures([{"t": 0, "pen": [[0.2, 0.5], [0.6, 0.4]], "dur": 1.0,
                                                   "pressure": pr}], MODEL)
            return cs[0]
        c = contact([0.1, 1.0, 0.3, 1.0, 0.1])
        for t, want in ((0.0, 0.1), (0.25, 1.0), (0.5, 0.3), (0.75, 1.0), (1.0, 0.1)):
            self.assertAlmostEqual(c.pressure(t), want, delta=0.02, msg=t)
        self.assertLess(c.pressure(0.375), 0.9)                       # anahtarlar arası yumuşak geçiş, düzlük yok
        self.assertGreater(c.pressure(0.375), 0.4)
        c = contact([0.05, 1.0])                                      # eski biçim
        self.assertAlmostEqual(c.pressure(0.5), 1.0, delta=0.01)
        self.assertAlmostEqual(c.pressure(0.0), 0.05, delta=0.01)


class TestLatency(unittest.TestCase):
    def test_latency_shifts_every_time_field(self):
        a = gest([{"t": 1.0, "two_finger_tap": [0.4, 0.4]}, {"t": 3.0, "drag": [[0.3, 0.5], [0.6, 0.5]]},
                  {"t": 5.0, "swipe4": [[0.4, 0.8], [0.4, 0.45]]}])
        b = gest([{"t": 1.0, "two_finger_tap": [0.4, 0.4]}, {"t": 3.0, "drag": [[0.3, 0.5], [0.6, 0.5]]},
                  {"t": 5.0, "swipe4": [[0.4, 0.8], [0.4, 0.45]]}], latency=0.05)
        for ga, gb in zip(a, b):
            for k in ("t0", "t1", "tf", "ta", "tb", "ts"):
                if k in ga:
                    self.assertAlmostEqual(gb[k] - ga[k], 0.05, places=9, msg=k)
            if ga.get("kb"):
                self.assertAlmostEqual(gb["kb"]["t"] - ga["kb"]["t"], 0.05, places=9)


if __name__ == "__main__":
    unittest.main()
