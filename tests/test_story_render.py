"""Jest derleme, parmak ataması, el izi, senaryo doğrulama, zaman çizelgesi ve uçtan uca kare."""
import glob
import math
import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from promokit import devices, gestures, hand, layout, story  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from promokit.media import cover_crop  # noqa: E402
from promokit.touches import Contact, TouchLog  # noqa: E402
from promokit.util import PromoError  # noqa: E402

MODEL = gestures.HandModel(fw=0.05, angle=math.radians(-14), aspect=1.5)


class TestGestures(unittest.TestCase):
    def test_tap_and_double_tap(self):
        cs, keys, _ = gestures.compile_gestures([{"t": 1.0, "tap": [0.4, 0.5]},
                                              {"t": 2.0, "double_tap": [0.2, 0.2]}], MODEL)
        self.assertEqual(len(cs), 3)
        self.assertAlmostEqual(cs[0].t0, 1.0)
        self.assertEqual(cs[0].pos(1.05), (0.4, 0.5))
        self.assertGreater(cs[2].t0, cs[1].t1)  # ikinci dokunuş birinciden SONRA

    def test_scroll_is_one_finger_like_product(self):
        # tek parmak sürükle = kaydırma (README → «Jest anlamları»)
        cs, *_ = gestures.compile_gestures([{"t": 0.0, "scroll": [[0.5, 0.7], [0.5, 0.3]]}], MODEL)
        self.assertEqual(len(cs), 1)

    def test_drag_holds_before_moving(self):
        cs, *_ = gestures.compile_gestures([{"t": 1.0, "drag": [[0.2, 0.5], [0.6, 0.5]], "dur": 0.5}], MODEL)
        c = cs[0]
        self.assertEqual(c.pos(1.0), c.pos(1.0 + gestures.GRAB_HOLD - 1e-3))   # önce tutulur
        self.assertGreater(c.pos(c.t1)[0], c.pos(1.0)[0] + 0.3)               # sonra taşınır

    def test_scroll_fingers_parallel_and_staggered(self):
        cs, *_ = gestures.compile_gestures([{"t": 0.0, "scroll": [[0.5, 0.7], [0.5, 0.3]], "fingers": 3}], MODEL)
        self.assertEqual(len(cs), 3)
        for c in cs:  # her parmak aynı dikey yolu kat eder
            self.assertAlmostEqual(c.y[0] - c.y[-1], 0.4, places=3)
        self.assertLess(cs[0].t0, cs[2].t0)  # parmaklar aynı anda değil, art arda değer

    def test_pinch_distance_changes(self):
        cs, *_ = gestures.compile_gestures([{"t": 0.0, "pinch": [0.5, 0.5], "from": 0.05, "to": 0.2}], MODEL)
        self.assertEqual(len(cs), 2)
        d = lambda t: math.hypot(cs[0].pos(t)[0] - cs[1].pos(t)[0], (cs[0].pos(t)[1] - cs[1].pos(t)[1]) / 1.5)
        self.assertAlmostEqual(d(0.0), 0.05, places=3)
        self.assertAlmostEqual(d(cs[0].t1), 0.2, places=3)

    def test_pen_and_keys(self):
        cs, keys, _ = gestures.compile_gestures([{"t": 0.0, "pen": [[0.1, 0.1], [0.3, 0.2]]},
                                              {"t": 1.0, "keys": "⌘ C"}], MODEL)
        self.assertEqual(cs[0].tool, "pen")
        self.assertEqual(keys[0].label, "⌘ C")

    def test_pen_tap_tool(self):
        cs, *_ = gestures.compile_gestures([{"t": 0.0, "tap": [0.4, 0.4], "tool": "pen"}], MODEL)
        self.assertEqual(cs[0].tool, "pen")
        with self.assertRaises(PromoError):
            gestures.compile_gestures([{"t": 0.0, "tap": [0.4, 0.4], "tool": "crayon"}], MODEL)
        with self.assertRaises(PromoError):   # kalemle çizgi = 'pen' jesti; kaydırmaya kalem verilmez
            gestures.compile_gestures([{"t": 0.0, "scroll": [[0.5, 0.7], [0.5, 0.3]], "tool": "pen"}], MODEL)

    def test_typo_suggests(self):
        with self.assertRaises(PromoError) as e:
            gestures.compile_gestures([{"t": 0.0, "scrol": [[0, 0], [1, 1]]}], MODEL)
        self.assertIn("scroll", str(e.exception))
        with self.assertRaises(PromoError):
            gestures.compile_gestures([{"tap": [0.1, 0.1]}], MODEL)  # t yok


class TestHand(unittest.TestCase):
    surf = hand.Surface((0, 0, 1500, 1000))

    def _assign(self, contacts):
        g = hand.Group(contacts)
        hand.assign_fingers(g, self.surf, math.radians(-14), 60)
        return [g.names[id(c)] for c in contacts]

    def test_two_adjacent_is_index_middle(self):
        cs, *_ = gestures.compile_gestures([{"t": 0, "pointer": [[0.5, 0.7], [0.5, 0.3]]}], MODEL)
        self.assertEqual(sorted(self._assign(cs)), ["index", "middle"])

    def test_pinch_is_thumb_index(self):
        cs, *_ = gestures.compile_gestures([{"t": 0, "pinch": [0.5, 0.5], "from": 0.05, "to": 0.25}], MODEL)
        self.assertEqual(sorted(self._assign(cs)), ["index", "thumb"])

    def test_track_enter_touch_exit(self):
        c = Contact([1.0, 1.1], [0.5, 0.5], [0.5, 0.5])
        tr = hand.HandTrack([c], self.surf, 30, 120, hand.HandConfig(fw=60))
        self.assertIsNone(tr.at(0))                     # henüz görünmüyor
        p = tr.at(31)                                    # temas anı
        self.assertIsNotNone(p)
        self.assertEqual(p.lift, 0.0)
        self.assertAlmostEqual(p.tips["index"][0], 750, delta=1)
        self.assertIsNone(tr.at(119))                    # çıkmış
        # yaklaşırken havada ve büyük
        q = tr.at(25)
        self.assertGreater(q.lift, 0.0)
        self.assertGreater(q.scale, p.scale)

    def test_pen_pose_tip_on_contact(self):
        c = Contact([1.0, 1.5], [0.4, 0.6], [0.4, 0.5], tool="pen")
        tr = hand.HandTrack([c], self.surf, 30, 90, hand.HandConfig(fw=60))
        p = tr.at(36)
        self.assertEqual(p.pen, 1.0)
        T = self.surf.to_world(*c.pos(36 / 30))
        self.assertAlmostEqual(p.tips["pen"][0], T[0], delta=1)
        self.assertAlmostEqual(p.tips["pen"][1], T[1], delta=1)

    def test_photo_pen_hand_tip_lands_on_contact(self):
        """Kalem eli FOTOĞRAF (photohand.py). Döndürme formülünün işaret hatası kalem ucunu ekranda
        yanlış yere koyar → ucu bağımsız ölç: varlıkta uç pikselini boya, aynı hattan döndür, bul."""
        from promokit import photohand
        im, meta = photohand._load()
        base = im.convert("RGBA")
        tx, ty = meta["tip"]
        marked = base.copy()
        marked.paste((255, 0, 255, 255), (int(tx) - 3, int(ty) - 3, int(tx) + 4, int(ty) + 4))
        saved, cache = photohand._base, dict(photohand._cache)
        try:
            photohand._base = (marked.convert("RGBa"), meta)
            for reach in (-0.5, 0.0, 0.5):
                photohand._cache.clear()
                T = (900.0, 600.0)
                spr, (X, Y), tip = photohand.place(T, 58.0, reach)
                px = spr.load()
                best, at = -1, None
                for y in range(spr.size[1]):
                    for x in range(max(0, int(tip[0]) - 40), min(spr.size[0], int(tip[0]) + 40)):
                        r, g, b, a = px[x, y]
                        v = r + b - 2 * g if a > 200 else -1
                        if v > best:
                            best, at = v, (x, y)
                self.assertLess(math.dist(at, tip), 4.5, f"reach={reach}: boyalı uç {at} ≠ hesaplanan {tip}")
                self.assertAlmostEqual(X + tip[0], T[0], delta=1)
                self.assertAlmostEqual(Y + tip[1], T[1], delta=1)
        finally:
            photohand._base = saved
            photohand._cache.clear()
            photohand._cache.update(cache)

    def test_nail_sits_at_fingertip(self):
        """Tırnak uç falanksın ÖNÜNDE (kullanıcı: "tırnaklar çok ortalı, uca doğru olmalı")."""
        w, r_tip = 1.0, 0.43
        body, _ = hand._nail((0.0, 0.0), (0.0, -1.0), w, r_tip)   # parmak yukarı bakıyor
        along = [-p[1] for p in body]                              # uç yönündeki izdüşüm
        self.assertLess(max(along), r_tip)                         # parmak ucunu taşmaz
        self.assertGreater(max(along), r_tip - 0.12 * w)           # ama uca dayanır
        self.assertGreater((max(along) + min(along)) / 2, -0.05 * w)   # merkezi uç merkezinin önünde


class TestTouchCircles(unittest.TestCase):
    """Varsayılan: el YOK, dokunulan yerde daire (kullanıcı eli iptal etti, 2026-10-19). Daire temasla
    belirir, parmağı izler, kalkınca söner; kalem dokunuşunun dairesi parmağınkinden küçüktür."""

    SCREEN = (30, 60, 90)

    def _timeline(self, gestures: str):
        from promokit.render import Timeline
        d = tempfile.mkdtemp()
        Image.new("RGB", (300, 200), self.SCREEN).save(os.path.join(d, "a.png"))
        p = os.path.join(d, "s.toml")
        with open(p, "w") as f:
            f.write(textwrap.dedent(f"""
                [video]
                size = 540
                fps = 30
                [[scene]]
                duration = 3
                tablet = "a.png"
                gestures = [ {gestures} ]
            """))
        st = story.load(p)
        return st, Timeline(st)

    def _probe(self, tl, t):
        """Kare + dokunuş noktası (ekranın ortası) çevresinde parlak dairenin yarıçapı."""
        im = tl.frame(t)
        rt = tl.rts[0]
        x, y, w, h = rt.devices["tablet"].rect
        cx, cy = int(x + 0.5 * w), int(y + 0.5 * h)
        px = im.load()
        lum = lambda p: 0.3 * p[0] + 0.59 * p[1] + 0.11 * p[2]
        base = lum(self.SCREEN)
        r = 0
        while cx + r + 1 < im.size[0] and lum(px[cx + r + 1, cy]) > base + 25:
            r += 1
        return im, lum(px[cx, cy]) - base, r

    def test_default_is_no_hand(self):
        self.assertEqual(story.VideoCfg().hand, "none")

    def test_circle_follows_touch_and_fades(self):
        st, tl = self._timeline('{ t = 1.0, tap = [0.5, 0.5], hold = 0.5 }')
        try:
            _, before, _ = self._probe(tl, 0.9)
            im, during, r = self._probe(tl, 1.3)
            _, fading, _ = self._probe(tl, 1.62)    # kalkış 1,5 sn; sönme 0,18 sn
            _, after, _ = self._probe(tl, 2.2)
        finally:
            tl.close()
        self.assertLess(before, 5)          # temas yokken daire yok
        self.assertGreater(during, 40)      # temas anında: yarı saydam beyaz daire
        self.assertGreater(r, 6)
        self.assertLess(fading, 0.5 * during)   # kalkınca birden kaybolmaz, söner
        self.assertLess(after, 5)           # kalktıktan sonra söndü
        px = im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata()
        skin = sum(1 for p in px if p[0] > 190 and 130 < p[1] < 190 and 100 < p[2] < 160)
        self.assertEqual(skin, 0)           # el çizilmedi

    def _screen_ok(self, im, pts):
        px = im.load()
        bad = [(x, y, px[x, y]) for x, y in pts if max(abs(a - b) for a, b in zip(px[x, y], self.SCREEN)) > 6]
        return bad

    def test_only_circle_no_trail_no_ring(self):
        """Yalnız daire: kaydırmada iz YOK (iz basamak basamak çiziliyordu — "çentikler"), dokunmada
        yayılan halka YOK; kısa (0,3 sn) basmada uzun bas halkası da yok."""
        import math as m
        st, tl = self._timeline('{ t = 1.0, scroll = [[0.5, 0.78], [0.5, 0.30]], dur = 0.6 }, '
                                '{ t = 2.0, tap = [0.5, 0.5], hold = 0.3 }')
        try:
            t_mid = 1.3
            im = tl.frame(t_mid)
            rt = tl.rts[0]
            x, y, w, h = rt.devices["tablet"].rect
            r0 = rt.fw * 0.62
            c = rt.contacts[0]
            cx, cy = x + c.pos(t_mid)[0] * w, y + c.pos(t_mid)[1] * h
            # parmağın GELDİĞİ yön (aşağı) boyunca dairenin dışı: ekran rengi kalmalı
            behind = [(int(cx), int(cy + d)) for d in range(int(1.0 * r0), int(3.5 * r0))]
            self.assertEqual(self._screen_ok(im, behind), [])
            im2 = tl.frame(2.2)
            cx2, cy2 = x + 0.5 * w, y + 0.5 * h
            ring = [(int(cx2 + rr * m.cos(a)), int(cy2 + rr * m.sin(a)))
                    for rr in (0.95 * r0, 1.15 * r0, 1.4 * r0, 1.8 * r0, 2.3 * r0)
                    for a in [k * m.pi / 12 for k in range(24)]]
            self.assertEqual(self._screen_ok(im2, ring), [])
        finally:
            tl.close()

    def test_pen_circle_is_smaller(self):
        _, tl = self._timeline('{ t = 1.0, tap = [0.5, 0.5], hold = 0.5 }')
        try:
            _, _, r_finger = self._probe(tl, 1.3)
        finally:
            tl.close()
        _, tl = self._timeline('{ t = 1.0, tap = [0.5, 0.5], hold = 0.5, tool = "pen" }')
        try:
            _, _, r_pen = self._probe(tl, 1.3)
        finally:
            tl.close()
        self.assertGreater(r_pen, 1)
        self.assertGreater(r_finger, 2 * r_pen)


class TestCable(unittest.TestCase):
    """Kablo: tabletin USB-C yuvası SAĞ kısa kenarda (kullanıcı: "kablo deliği sağ tarafta olur")."""

    def setUp(self):
        from promokit import cable, devices
        self.cable, self.devices = cable, devices
        r = layout.place(1920, 1080, "duo", {"tablet": 1400 / 920, "laptop": 1470 / 956})
        self.L, self.T = r["laptop"], r["tablet"]
        self.an = cable.anchors(self.L, self.T)

    def test_port_on_tablet_right_edge(self):
        tx, ty, tw, th = self.T
        g = self.devices.geometry("tablet", tw, th)
        self.assertAlmostEqual(self.an["port"][0], tx + g.right, delta=0.5)
        self.assertAlmostEqual(self.an["port"][1], ty + th / 2, delta=0.5)
        self.assertGreater(self.an["port"][0], self.an["mac"][0])   # Mac solda, yuva tabletin uzak yanında

    def test_plug_goes_in_and_out(self):
        port, th = self.an["port"], self.an["th"]
        spec = {"t": 0.5, "dur": 0.8}
        F, phi, vis = self.cable.plug_pose(spec, 0.0, port, th)
        self.assertGreater(F[0], port[0])                           # önce dışarıda
        F, phi, vis = self.cable.plug_pose(spec, 1.4, port, th)
        self.assertLess(F[0], port[0])                              # sonra metal uç gövdenin içinde
        self.assertAlmostEqual(phi, 0.0)                            # yuvaya yatay girer
        out = {"t": 0.5, "dur": 0.5, "out": True}
        self.assertEqual(self.cable.plug_pose(out, 0.4, port, th)[2], 1.0)
        self.assertLess(self.cable.plug_pose(out, 0.4, port, th)[0][0], port[0])
        self.assertEqual(self.cable.plug_pose(out, 2.0, port, th)[2], 0.0)   # çıkarıldı → söndü

    def test_cable_hidden_behind_tablet(self):
        """Kablo tabletin ARKASINDAN geçer: kablolu ve kablosuz karede tablet gövdesinin İÇİ
        piksel piksel aynı olmalı (kaynak metnine bakmak yetmez — davranışı ölç)."""
        from promokit.render import Timeline
        d = tempfile.mkdtemp()
        Image.new("RGB", (300, 200), (30, 60, 90)).save(os.path.join(d, "a.png"))
        frames = []
        for cable in ("", "cable = { t = 0.1, dur = 0.3 }"):
            path = os.path.join(d, f"s{len(frames)}.toml")
            with open(path, "w") as f:
                f.write(textwrap.dedent(f"""
                    [video]
                    size = 540
                    fps = 30
                    [[scene]]
                    layout = "duo"
                    duration = 2
                    tablet = "a.png"
                    laptop = "a.png"
                    {cable}
                """))
            st = story.load(path)
            tl = Timeline(st)
            try:
                frames.append((tl.frame(1.5), tl.rts[0].stage_rects(tl.rts[0].camera.at(1.5), 1.5)))
            finally:
                tl.close()
        (a, rects), (b, _) = frames
        tx, ty, tw, th = rects["tablet"]
        from PIL import ImageChops
        inner = (int(tx + 4), int(ty + 4), int(tx + tw - 4), int(ty + th + th * 0.04))  # ekran + alt çerçeve
        self.assertIsNone(ImageChops.difference(a.crop(inner), b.crop(inner)).getbbox())
        # ...ama kablo gerçekten çizildi: tabletin sağında (fiş + sarkan kısım) fark var
        right = (int(tx + tw + th * 0.06), int(ty + th * 0.4), int(min(a.size[0], tx + tw + th * 0.3)), int(ty + th))
        self.assertIsNotNone(ImageChops.difference(a.crop(right), b.crop(right)).getbbox())


class TestStory(unittest.TestCase):
    def _write(self, body: str) -> str:
        d = tempfile.mkdtemp()
        Image.new("RGB", (300, 200), (30, 60, 90)).save(os.path.join(d, "screen.png"))
        p = os.path.join(d, "s.toml")
        with open(p, "w") as f:
            f.write(textwrap.dedent(body))
        return p

    def test_timeline_overlaps(self):
        p = self._write("""
            [video]
            transition_dur = 0.5
            [[scene]]
            type = "title"
            duration = 2
            title = "A"
            [[scene]]
            duration = 3
            tablet = "screen.png"
            [[scene]]
            duration = 2
            tablet = "screen.png"
            transition = "cut"
        """)
        st = story.load(p)
        self.assertEqual([round(s.start, 3) for s in st.scenes], [0.0, 1.5, 4.5])
        self.assertAlmostEqual(st.duration, 6.5)

    def test_unknown_key_suggestion(self):
        p = self._write("""
            [[scene]]
            duration = 2
            tablet = "screen.png"
            captoin = "x"
        """)
        with self.assertRaises(PromoError) as e:
            story.load(p)
        self.assertIn("caption", str(e.exception))

    def test_portrait_override_and_sizes(self):
        p = self._write("""
            [[scene]]
            duration = 2
            layout = "duo"
            tablet = "screen.png"
            laptop = "screen.png"
            portrait = { layout = "tablet" }
        """)
        self.assertEqual(story.load(p).scenes[0].layout, "duo")
        st = story.load(p, aspect="9:16")
        self.assertEqual(st.scenes[0].layout, "tablet")
        self.assertEqual((st.video.width, st.video.height), (1080, 1920))
        self.assertEqual(story.size_for("4:5", 1080), (1080, 1350))

    def test_missing_media(self):
        p = self._write("""
            [[scene]]
            tablet = "yok.png"
        """)
        with self.assertRaises(PromoError):
            story.load(p)


class TestLayoutAndRender(unittest.TestCase):
    def test_cover_crop(self):
        self.assertEqual(cover_crop(200, 100, 100, 100), (0.25, 0.0, 0.5, 1.0))
        self.assertEqual(cover_crop(100, 200, 100, 100), (0.0, 0.25, 1.0, 0.5))
        self.assertEqual(cover_crop(200, 100, 100, 100, "contain"), (0.0, 0.0, 1.0, 1.0))

    def test_duo_layout_fits_and_shares_desk(self):
        for W, H in ((1920, 1080), (1080, 1920), (1080, 1350)):
            r = layout.place(W, H, "duo", {"tablet": 1.5, "laptop": 1.54})
            for x, y, w, h in r.values():
                self.assertGreaterEqual(x, 0)
                self.assertLessEqual(x + w, W)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(y + h, H)

    def test_camera_path(self):
        rects = {"tablet": (100, 100, 600, 400)}
        cp = layout.CameraPath([{"t": 1.0, "zoom": 2.0, "at": [0.5, 0.5], "dur": 1.0}], rects, 1920, 1080, "tablet")
        self.assertAlmostEqual(cp.at(0.5).zoom, 1.0)
        self.assertAlmostEqual(cp.at(2.5).zoom, 2.0)
        c = cp.at(2.5)
        self.assertAlmostEqual(c.apply((400, 300))[0], cp.anchor[0])  # odak sahne merkezine gelir
        self.assertGreater(cp.at(1.5).zoom, 1.0)
        self.assertLess(cp.at(1.5).zoom, 2.0)

    def test_still_and_morph_frames(self):
        from promokit.render import Timeline
        d = tempfile.mkdtemp()
        Image.new("RGB", (300, 200), (30, 60, 90)).save(os.path.join(d, "a.png"))
        Image.new("RGB", (300, 200), (200, 60, 90)).save(os.path.join(d, "b.png"))
        p = os.path.join(d, "s.toml")
        with open(p, "w") as f:
            f.write(textwrap.dedent("""
                [video]
                size = 360
                fps = 30
                hand = "skin"
                [[scene]]
                layout = "duo"
                duration = 2
                caption = "Bir"
                tablet = "a.png"
                laptop = "b.png"
                [[scene]]
                duration = 2
                caption = "İki"
                tablet = "a.png"
                gestures = [ { t = 0.6, tap = [0.5, 0.5] } ]
            """))
        st = story.load(p)
        tl = Timeline(st)
        try:
            for t in (0.2, 1.7, 2.6, 3.9):  # 1.7 = morph geçişinin ortası
                im = tl.frame(t)
                self.assertEqual(im.size, (640, 360))
            # dokunuş anında elin derisi kadrajda olmalı (ten rengi piksel var)
            im = tl.frame(st.scenes[1].start + 0.65)
            px = im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata()
            skin = sum(1 for r, g, b in px if r > 190 and 130 < g < 190 and 100 < b < 160)
            self.assertGreater(skin, 50)
        finally:
            tl.close()


# ── 2026-10-19 render hattı incelemesi: bulgular 1-12 + F1 (rot/fit) + F2 (ses) + F3 (içerik kutusu) ──
SCREEN, LAPTOP_SCREEN = (30, 60, 90), (200, 60, 90)
PAGE = '<!doctype html><meta name="promo-viewport" content="1400x920"><body style="margin:0;background:#fff"></body>'


def _mk(body: str, assets: dict | None = None) -> str:
    """Geçici dizine senaryo + varlıklar (a.png = tablet ekranı, b.png = MacBook ekranı, p.html) yaz."""
    d = tempfile.mkdtemp(prefix="promo-test-")
    imgs = {"a.png": ((300, 200), SCREEN), "b.png": ((300, 195), LAPTOP_SCREEN), **(assets or {})}
    for name, (size, color) in imgs.items():
        Image.new("RGB", size, color).save(os.path.join(d, name))
    with open(os.path.join(d, "p.html"), "w", encoding="utf-8") as f:
        f.write(PAGE)
    p = os.path.join(d, "s.toml")
    with open(p, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(body))
    return p


def _mask(im, color, tol=6):
    """`color`a ±tol yakın pikseller (L, 255)."""
    from PIL import ImageChops
    chans = [c.point(lambda v, c0=c0: 255 if abs(v - c0) <= tol else 0) for c, c0 in zip(im.convert("RGB").split(), color)]
    return ImageChops.multiply(ImageChops.multiply(chans[0], chans[1]), chans[2])


def _blue(im, box=None):
    """Vurgu mavisi (Wi-Fi / ses dalgası / takıldı halkası) piksel sayısı."""
    im = im.convert("RGB") if box is None else im.convert("RGB").crop(tuple(int(round(v)) for v in box))
    r, g, b = im.split()
    hit = [1 for rr, bb in zip(r.tobytes(), b.tobytes()) if bb > 150 and bb - rr > 70]
    return len(hit)


def _lum(im, box):
    """Kutudaki ortalama parlaklık."""
    c = im.convert("L").crop(tuple(int(round(v)) for v in box))
    h = c.histogram()
    return sum(i * n for i, n in enumerate(h)) / max(1, sum(h))


def _first_line_px(im) -> int:
    """Metin görüntüsünün İLK satırının mürekkep yüksekliği (px) — satır sayısından bağımsız punto ölçüsü."""
    a = im.getchannel("A")
    rows = [a.crop((0, y, a.size[0], y + 1)).getbbox() is not None for y in range(a.size[1])]
    y0 = rows.index(True)
    return (rows.index(False, y0) if False in rows[y0:] else len(rows)) - y0


def _frames(path, times, aspect=None):
    from promokit.render import Timeline
    st = story.load(path, aspect=aspect)
    tl = Timeline(st)
    try:
        return st, tl, [tl.frame(t) for t in times]
    finally:
        tl.close()


class TestValidation(unittest.TestCase):
    """Senaryo hataları YÜKLEMEDE PromoError (render ortasında ham traceback değil — bulgu 8)."""

    BASE = 'duration = 2\ntablet = "a.png"'
    DUO = 'duration = 2\nlayout = "duo"\ntablet = "a.png"\nlaptop = "b.png"'
    CASES = [  # (ad, [video] ekleri, sahne gövdesi)
        ("kablo anahtarı", "", DUO + '\ncable = { t = 0.5, durr = 1 }'),
        ("kablo değeri", "", DUO + '\ncable = { t = "erken" }'),
        ("kablo out", "", DUO + '\ncable = { t = 0.5, out = "evet" }'),
        ("wifi anahtarı", "", DUO + '\nwifi = { t = 1, len = 2 }'),
        ("wifi süresi", "", DUO + '\nwifi = { t = 1, dur = -1 }'),
        ("ses anahtarı", "", BASE + '\nsound = { t = 1, d = 2 }'),
        ("ses değeri", "", BASE + '\nsound = { t = "x" }'),
        ("anim anahtarı", "", BASE + '\nanim = { tablet = [ { t = 0, z = 1 } ] }'),
        ("anim cihazı", "", BASE + '\nanim = { phone = [ { t = 0, x = 1 } ] }'),
        ("anim yumuşatma", "", BASE + '\nanim = { tablet = [ { t = 0, x = 0 }, { t = 1, x = 1, ease = "inoutt" } ] }'),
        ("anim değeri", "", BASE + '\nanim = { tablet = [ { t = 0, o = "yarım" } ] }'),
        ("anim s", "", BASE + '\nanim = { tablet = [ { t = 0, s = "fill" } ] }'),
        ("anim rot", "", BASE + '\nanim = { tablet = [ { t = 0, rot = "sağa" } ] }'),
        ("anim aralığı", "", BASE + '\nanim = { tablet = [ { t = 0, o = 50 } ] }'),
        ("anim biçimi", "", BASE + '\nanim = { tablet = { t = 0, x = 1 } }'),
        ("kamera anahtarı", "", BASE + '\ncamera = [ { t = 1, zom = 1.2 } ]'),
        ("kamera yumuşatma", "", BASE + '\ncamera = [ { t = 1, zoom = 1.2, ease = "inoutt" } ]'),
        ("kamera zoom", "", BASE + '\ncamera = [ { t = 1, zoom = 0 } ]'),
        ("kamera at", "", BASE + '\ncamera = [ { t = 1, zoom = 1.2, at = [0.5] } ]'),
        ("kamera on", "", BASE + '\ncamera = [ { t = 1, zoom = 1.2, at = [0.5, 0.5], on = "phone" } ]'),
        ("başlık anahtarı", "", BASE + '\ncaptions = [ { t = 0, txt = "a" } ]'),
        ("başlık değeri", "", BASE + '\ncaptions = [ { t = "baş", text = "a" } ]'),
        ("yerleşim", "", BASE + '\nlayout = "trio"'),
        ("geçiş", "", BASE + '\ntransition = "wipe"'),
        ("html sayfasız", "", 'type = "html"\nduration = 2'),
        ("html sayfa yok", "", 'type = "html"\nduration = 2\npage = "yok.html"'),
        ("süre", "", 'duration = 0\ntablet = "a.png"'),
        ("keys_at", "", BASE + '\nkeys_at = [0.5]'),
        ("still değeri", "", BASE + '\nstill = "son"'),
        ("still eksi", "", BASE + '\nstill = -0.5'),
        ("still sahneden uzun", "", BASE + '\nstill = 2.5'),
        ("medya in/out", "", 'duration = 2\ntablet = { src = "a.png", in = 2.0, out = 1.0 }'),
        ("tema", 'theme = "drak"', BASE),
        ("kasa rengi", 'laptop_finish = "gold"', BASE),
        ("tablet rengi", 'tablet_color = "silver"', BASE),
        ("codec", 'codec = "h265"', BASE),
        ("fps", 'fps = 0', BASE),
        ("boyut", 'size = 0', BASE),
        ("geçiş (video)", 'transition = "wipe"', BASE),
        ("font", 'font = "/yok/font.ttf"', BASE),
    ]

    def test_base_story_is_valid(self):
        for body in (self.BASE, self.DUO):
            story.load(_mk("[video]\n[[scene]]\n" + body + "\n"))

    def test_bad_values_raise_at_load(self):
        for name, video, scene in self.CASES:
            with self.subTest(name):
                p = _mk("[video]\n" + video + "\n[[scene]]\n" + scene + "\n")
                with self.assertRaises(PromoError):
                    story.load(p)

    def test_transition_clamped_by_previous_scene(self):
        p = _mk('[video]\ntransition_dur = 2.0\n[[scene]]\nduration = 1.0\ntablet = "a.png"\n'
                '[[scene]]\nduration = 6.0\ntablet = "a.png"\n')
        st = story.load(p)
        self.assertAlmostEqual(st.overlap(st.scenes[1]), 0.5)   # önceki sahne sürenin yarısı
        self.assertAlmostEqual(st.scenes[1].start, 0.5)

    def test_size_for_even_and_ratio(self):
        for asp, (a, b) in story.ASPECTS.items():
            for short in (540, 720, 1080, 1440, 2160):
                with self.subTest(aspect=asp, short=short):
                    w, h = story.size_for(asp, short)
                    self.assertEqual((w % 2, h % 2), (0, 0))
                    self.assertLessEqual(abs(min(w, h) - short), 1)
                    self.assertAlmostEqual(w / h, a / b, delta=0.005)
        self.assertEqual(story.size_for("4:3", 1080), (1440, 1080))
        p = _mk('[video]\nsize = "4k"\n[[scene]]\nduration = 1\ntablet = "a.png"\n')
        v = story.load(p).video
        self.assertEqual((v.width, v.height), (3840, 2160))
        v = story.load(p, aspect="4:5", short=540).video   # --preview --aspect 4:5 (675 tek → 674)
        self.assertEqual((v.width, v.height), (540, 674))

    def test_duo_shares_desk_line(self):
        from promokit.devices import geometry
        for W, H in ((1920, 1080), (1080, 1080)):
            r = layout.place(W, H, "duo", {"tablet": 1400 / 920, "laptop": 1470 / 956})
            lx, ly, lw, lh = r["laptop"]
            tx, ty, tw, th = r["tablet"]
            self.assertAlmostEqual(ly + geometry("laptop", lw, lh).bottom, ty + geometry("tablet", tw, th).bottom,
                                   delta=0.01)


class TestMorphContinuity(unittest.TestCase):
    """Geçiş, sahnelerin cihazlarını NORMAL karelerle AYNI yoldan çizer: anim durumu, Wi-Fi ve ekran
    durumu geçişin ilk/son karesinde zıplamaz (bulgu 1)."""

    def test_no_pop_at_morph_start(self):
        p = _mk("""
            [video]
            size = 540
            fps = 30
            transition_dur = 0.5
            [[scene]]
            layout = "duo"
            duration = 3
            tablet = "a.png"
            laptop = "b.png"
            wifi = { t = 0.2, dur = 10 }
            anim = { tablet = [ { t = 0.5, x = 0.0, s = 1.0 }, { t = 1.5, x = 0.3, y = -0.1, s = 0.85 } ] }
            [[scene]]
            duration = 3
            tablet = "a.png"
        """)
        st = story.load(p)
        n = round(st.scenes[1].start * 30)
        _, _, (before, at) = _frames(p, [(n - 1) / 30, n / 30])
        b0, b1 = _mask(before, SCREEN).getbbox(), _mask(at, SCREEN).getbbox()
        for a, b in zip(b0, b1):
            self.assertLess(abs(a - b), 4, f"tablet tek karede zıpladı: {b0} → {b1}")
        self.assertGreater(_blue(at), 0.5 * _blue(before))   # Wi-Fi simgesi birden kaybolmaz
        self.assertGreater(_blue(before), 30)

    def test_no_pop_when_zoomed_scene_morphs(self):
        """Bant kuralı geçişte de: yakınlaşıp aşağı kaydırılmış sahneden geçişin ilk karesi aynı yerden
        başlar (geçiş kaydırmasız kamerayla çizseydi tablet tek karede yukarı sıçrardı)."""
        p = _mk("""
            [video]
            size = 540
            fps = 30
            transition_dur = 0.5
            [[scene]]
            duration = 3
            tablet = "a.png"
            caption = "Başlık"
            camera = [ { t = 0.2, zoom = 1.5, at = [0.5, 0.5], dur = 0.6 } ]
            [[scene]]
            duration = 3
            tablet = "a.png"
            caption = "Başlık"
        """)
        st = story.load(p)
        n = round(st.scenes[1].start * 30)
        _, _, (before, at) = _frames(p, [(n - 1) / 30, n / 30])
        b0, b1 = _mask(before, SCREEN).getbbox(), _mask(at, SCREEN).getbbox()
        for a, b in zip(b0, b1):
            self.assertLess(abs(a - b), 4, f"tablet geçiş başında zıpladı: {b0} → {b1}")

    def test_morph_reaches_incoming_screen_state(self):
        """Gelen sahnenin tableti EKRANI KAPALI başlar: geçişin son karesi de karanlık olmalı."""
        p = _mk("""
            [video]
            size = 540
            fps = 30
            transition_dur = 0.5
            [[scene]]
            duration = 2
            tablet = "a.png"
            [[scene]]
            duration = 2
            tablet = "a.png"
            anim = { tablet = [ { t = 0.0, on = 0.0 }, { t = 1.5, on = 0.0 } ] }
        """)
        st = story.load(p)
        end = round((st.scenes[0].start + st.scenes[0].duration) * 30)
        _, tl, (last_morph, first_b) = _frames(p, [(end - 1) / 30, end / 30])
        mid = (st.video.width / 2 - 4, st.video.height / 2 - 4, st.video.width / 2 + 4, st.video.height / 2 + 4)
        self.assertLess(abs(_lum(last_morph, mid) - _lum(first_b, mid)), 8)

    def test_touch_circle_follows_animated_tablet(self):
        p = _mk("""
            [video]
            size = 540
            fps = 30
            [[scene]]
            duration = 2
            tablet = "a.png"
            anim = { tablet = [ { t = 0, x = 0.4, s = 0.6 } ] }
            gestures = [ { t = 0.5, tap = [0.5, 0.5], hold = 0.5 } ]
        """)
        _, _, (im,) = _frames(p, [0.7])
        sx0, sy0, sx1, sy1 = _mask(im, SCREEN).getbbox()
        dot = im.convert("L").crop((sx0, sy0, sx1, sy1)).point(lambda v: 255 if v > 110 else 0).getbbox()
        self.assertIsNotNone(dot, "dokunuş dairesi tabletin üstünde değil")
        dcx, dcy = sx0 + (dot[0] + dot[2]) / 2, sy0 + (dot[1] + dot[3]) / 2
        self.assertLess(abs(dcx - (sx0 + sx1) / 2), 4)
        self.assertLess(abs(dcy - (sy0 + sy1) / 2), 4)


class TestRotate(unittest.TestCase):
    """F1: `rot` (derece, + = saat yönü) + `s = "fit"` — cihaz merkezinde döner, içerik kutusuna sığar."""

    def _story(self, anim, extra="", layout_="tablet"):
        lap = 'laptop = "b.png"\n' if layout_ == "duo" else ""
        return _mk(f'[video]\nsize = 540\nfps = 30\n[[scene]]\nduration = 3\nlayout = "{layout_}"\n'
                   f'tablet = "a.png"\n{lap}anim = {{ tablet = [ {anim} ] }}\n{extra}\n')

    def test_rotate_to_portrait_fits_content_box(self):
        for aspect in ("16:9", "9:16"):
            with self.subTest(aspect):
                p = self._story('{ t = 0, rot = 0 }, { t = 1, rot = -90, s = "fit" }')
                st, tl, (im,) = _frames(p, [2.0], aspect)
                x0, y0, x1, y1 = _mask(im, SCREEN).getbbox()
                w, h = x1 - x0, y1 - y0
                self.assertGreater(h, 1.3 * w)                        # dikey duruyor
                bz = devices.TABLET["bezel"] * min(w, h)            # tablet çerçevesi
                bx0, by0, bx1, by1 = tl.box
                fill = max((w + 2 * bz) / (bx1 - bx0), (h + 2 * bz) / (by1 - by0))
                self.assertAlmostEqual(fill, 1.0, delta=0.03)          # kutuya SIĞAR (taşmaz, küçülmez)
                self.assertLess(abs((x0 + x1) / 2 - (bx0 + bx1) / 2), 3)   # ortalı
                self.assertLess(abs((y0 + y1) / 2 - (by0 + by1) / 2), 3)

    def test_fit_rotation_stays_in_box_midway(self):
        """45°'de çapraz sınır kutusu en büyük: dönüş boyunca da cihaz içerik kutusunda kalır (başlık
        bandına / kadraj dışına taşmaz)."""
        from promokit.render import Timeline
        for aspect in ("16:9", "9:16"):
            with self.subTest(aspect):
                st = story.load(self._story('{ t = 0, rot = 0, s = 1 }, { t = 1, rot = -90, s = "fit", ease = "linear" }'),
                                aspect=aspect)
                tl = Timeline(st)
                try:
                    rt = tl._rt(st.scenes[0])
                    for t in (0.25, 0.5, 0.75):
                        x0, y0, x1, y1 = rt.poses(rt.camera_at(t), t)["tablet"].body_bbox()
                        bx0, by0, bx1, by1 = tl.box
                        self.assertGreaterEqual(x0, bx0 - 1)
                        self.assertGreaterEqual(y0, by0 - 1)
                        self.assertLessEqual(x1, bx1 + 1)
                        self.assertLessEqual(y1, by1 + 1)
                finally:
                    tl.close()

    def test_touch_maps_through_rotation(self):
        # rot = -90 (saat yönünün tersi): ekranın SAĞ yanındaki dokunuş ÜST yarıya düşer
        p = self._story('{ t = 0, rot = -90 }', 'gestures = [ { t = 0.5, tap = [0.8, 0.5], hold = 0.5 } ]')
        _, _, (im,) = _frames(p, [0.7])
        sx0, sy0, sx1, sy1 = _mask(im, SCREEN).getbbox()
        dot = im.convert("L").crop((sx0, sy0, sx1, sy1)).point(lambda v: 255 if v > 110 else 0).getbbox()
        self.assertIsNotNone(dot)
        dcx, dcy = sx0 + (dot[0] + dot[2]) / 2, sy0 + (dot[1] + dot[3]) / 2
        self.assertLess(abs(dcx - (sx0 + sx1) / 2), 4)
        self.assertLess(dcy, sy0 + 0.3 * (sy1 - sy0))

    def test_rotated_shadow_falls_down(self):
        p = self._story('{ t = 0, rot = -90 }')
        _, _, (im,) = _frames(p, [1.0])
        x0, y0, x1, y1 = _mask(im, SCREEN).getbbox()
        bz = devices.TABLET["bezel"] * min(x1 - x0, y1 - y0) + 2
        s = 0.06 * (y1 - y0)
        dark = lambda box: 255 - _lum(im, box)
        below = dark((x0, y1 + bz, x1, y1 + bz + s))
        above = dark((x0, y0 - bz - s, x1, y0 - bz))
        left = dark((x0 - bz - s, y0, x0 - bz, y1))
        right = dark((x1 + bz, y0, x1 + bz + s, y1))
        self.assertGreater(below, above + 2)          # gölge AŞAĞI düşer
        self.assertLess(abs(left - right), 2)         # yana kaymaz

    def test_rotation_keys_validated(self):
        p = self._story('{ t = 0, rot = -90, s = "fit", x = 0.1 }')
        story.load(p)   # geçerli


class TestSound(unittest.TestCase):
    """F2: `sound = { t, dur }` — tabletin kısa kenarlarının dışında ses dalgaları (cihazla döner)."""

    def _bands(self, im, vertical=False):
        x0, y0, x1, y1 = _mask(im, SCREEN).getbbox()
        w, h = x1 - x0, y1 - y0
        m = 0.06 * min(w, h)
        if not vertical:
            a = (x0 - m - 0.45 * h, y0 + 0.2 * h, x0 - m, y1 - 0.2 * h)
            b = (x1 + m, y0 + 0.2 * h, x1 + m + 0.45 * h, y1 - 0.2 * h)
        else:
            a = (x0 + 0.2 * w, y0 - m - 0.45 * w, x1 - 0.2 * w, y0 - m)
            b = (x0 + 0.2 * w, y1 + m, x1 - 0.2 * w, y1 + m + 0.45 * w)
        return a, b, (x0, y0, x1, y1)

    def test_waves_outside_short_sides(self):
        for aspect, lay_ in (("16:9", "tablet"), ("9:16", "duo"), ("16:9", "duo")):
            with self.subTest(aspect=aspect, layout=lay_):
                lap = 'laptop = "b.png"\n' if lay_ == "duo" else ""
                p = _mk(f'[video]\nsize = 540\nfps = 30\n[[scene]]\nduration = 3\nlayout = "{lay_}"\n'
                        f'tablet = "a.png"\n{lap}sound = {{ t = 0.3, dur = 5 }}\n')
                _, _, (pre, on) = _frames(p, [0.1, 1.2], aspect)
                a, b, scr = self._bands(on)
                self.assertGreater(_blue(on, a), 15)
                self.assertGreater(_blue(on, b), 15)
                self.assertEqual(_blue(pre, a) + _blue(pre, b), 0)     # başlamadan yok
                self.assertEqual(_blue(on, scr), 0)                     # ekranın üstüne çizilmez

    def test_waves_fit_in_portrait_frame(self):
        """Dikey kadrajda tek tablet kutuyu enine doldurur: ses sahnesinde kısa kenarlara pay ayrılır,
        dalgalar kadraj kenarında kırpılmaz."""
        p = _mk('[video]\nsize = 540\nfps = 30\n[[scene]]\nduration = 3\ntablet = "a.png"\nsound = { t = 0.3, dur = 5 }\n')
        _, _, (im,) = _frames(p, [1.2], "9:16")
        W = im.size[0]
        self.assertGreater(_blue(im), 60)
        self.assertEqual(_blue(im, (0, 0, 3, im.size[1])) + _blue(im, (W - 3, 0, W, im.size[1])), 0)

    def test_waves_rotate_with_device(self):
        p = _mk('[video]\nsize = 540\nfps = 30\n[[scene]]\nduration = 3\ntablet = "a.png"\n'
                'anim = { tablet = [ { t = 0, rot = 90, s = "fit" } ] }\nsound = { t = 0.3, dur = 5 }\n')
        _, _, (im,) = _frames(p, [1.2])
        a, b, _ = self._bands(im, vertical=True)
        self.assertGreater(_blue(im, a), 15)
        self.assertGreater(_blue(im, b), 15)


class TestPortraitGeometry(unittest.TestCase):
    """Bulgu 3: Wi-Fi ve kablo dikey (üst üste) yerleşimde de doğru yerde."""

    def _duo(self, extra, aspect, t):
        p = _mk(f'[video]\nsize = 540\nfps = 30\n[[scene]]\nduration = 3\nlayout = "duo"\n'
                f'tablet = "a.png"\nlaptop = "b.png"\n{extra}\n')
        return _frames(p, [t], aspect)

    def test_wifi_between_devices(self):
        for aspect in ("9:16", "4:5", "16:9"):
            with self.subTest(aspect):
                st, _, (im,) = self._duo('wifi = { t = 0.1, dur = 5 }', aspect, 1.5)
                L = _mask(im, LAPTOP_SCREEN).getbbox()
                T = _mask(im, SCREEN).getbbox()
                blue = _mask(im, (79, 124, 255), tol=70).getbbox()
                self.assertIsNotNone(blue)
                if aspect == "16:9":
                    self.assertGreater(blue[0], L[2])
                    self.assertLess(blue[2], T[0])
                else:
                    self.assertGreater(blue[1], L[3])
                    self.assertLess(blue[3], T[1])

    def test_cable_stays_in_frame_when_stacked(self):
        """Üst üste yerleşimde kablo Mac'in sağından iner: kadrajın sağ kenarına DEĞMEZ (pay ayrılır),
        ama gerçekten çizilir (tabletin sağında koyu kablo/fiş)."""
        for aspect in ("9:16", "4:5"):
            with self.subTest(aspect):
                _, _, (im,) = self._duo('cable = { t = 0.1, dur = 0.3 }', aspect, 1.5)
                W, H = im.size
                g = im.convert("L")
                dark_cols = [x for x in range(W) if min(g.crop((x, 0, x + 1, H)).tobytes()) < 90]
                T = _mask(im, SCREEN).getbbox()
                self.assertGreater(max(dark_cols), T[2] + 0.06 * (T[3] - T[1]))   # tabletin sağında kablo var
                self.assertLess(max(dark_cols), W - 0.03 * W)                        # kenara değmez
                # …ve iki cihazın ARASINDAN (tabletin önünden çaprazlama) geçmez: yandan iner
                L = _mask(im, LAPTOP_SCREEN).getbbox()
                gap = (T[0], int(L[3] + 0.1 * (L[2] - L[0])), T[2], int(T[1] - 0.08 * (T[3] - T[1])))
                self.assertGreater(gap[3] - gap[1], 2)
                self.assertGreater(min(g.crop(gap).tobytes()), 90)

    def test_stacked_cable_route_never_goes_behind_tablet(self):
        """Üst üste yerleşimde kablonun ekseni tabletin SAĞINDA kalır (yan yana rotası gibi tabletin
        arkasına dalıp masaya inmez — dikeyde masa yok, kablo çapraz görünüyordu)."""
        from promokit import cable
        from promokit.render import Timeline
        p = _mk('[video]\nsize = 540\nfps = 30\n[[scene]]\nduration = 3\nlayout = "duo"\n'
                'tablet = "a.png"\nlaptop = "b.png"\ncable = { t = 0.1, dur = 0.3 }\n')
        for aspect in ("9:16", "4:5"):
            with self.subTest(aspect):
                st = story.load(p, aspect=aspect)
                tl = Timeline(st)
                try:
                    rt = tl._rt(st.scenes[0])
                    poses = rt.poses(rt.camera.at(1.5), 1.5)
                finally:
                    tl.close()
                an = cable.anchors(poses["laptop"], poses["tablet"], (st.video.width, st.video.height))
                self.assertTrue(an["stacked"])
                for t in (0.0, 0.3, 1.5):
                    F, phi, _ = cable.plug_pose(st.scenes[0].cable, t, an["port"], an["th"], an["rot"])
                    pts = cable.cable_path(an, F, phi)
                    right = poses["tablet"].body_bbox()[2]
                    self.assertGreaterEqual(min(x for x, _ in pts), right - 1)


class TestCaptionLayout(unittest.TestCase):
    """Bulgu 4: başlık bandı gerçek (sarılmış) başlıkla + caption_size ile ayrılır; yerleşim aynı kutuyu kullanır."""

    LONG = 'caption = "Kabloyu takın. Mac\'inizin ikinci ekranı."\nsubcaption = "Geliştirici modu yok · ADB yok"'

    def _rt(self, extra, aspect, video=""):
        from promokit.render import Timeline
        p = _mk(f'[video]\nsize = 540\nfps = 30\n{video}\n[[scene]]\nduration = 3\nlayout = "duo"\n'
                f'tablet = "a.png"\nlaptop = "b.png"\n{extra}\n')
        st = story.load(p, aspect=aspect)
        return st, Timeline(st)

    def test_wrapped_caption_reserves_band(self):
        from promokit.devices import geometry
        for aspect, video in (("4:5", ""), ("1:1", "caption_size = 1.6"), ("9:16", "")):
            with self.subTest(aspect=aspect, video=video):
                st, tl = self._rt(self.LONG, aspect, video)
                try:
                    tl.frame(1.0)
                    rt = tl.rts[0]
                    cap = rt.captions[0][2]
                    bottom = layout.caption_top(st.video.width, st.video.height) + cap.extent()
                    self.assertGreaterEqual(tl.box[1], bottom)
                    for kind, (x, y, w, h) in rt.rects.items():
                        self.assertGreaterEqual(y + geometry(kind, w, h).top, bottom)   # cihaz bandın altında
                finally:
                    tl.close()

    ZOOM_STORY = """[video]
size = 540
fps = 30
[[scene]]
duration = 3
tablet = "a.png"
caption = "Kabloyu takın. Mac'inizin ikinci ekranı."
subcaption = "Geliştirici modu yok · ADB yok"
{camera}
"""

    def test_zoomed_device_stays_out_of_caption_band(self):
        """Kullanıcı (2026-10-20): yakınlaşan ekran başlığın ALTINA girmesin. Odak cihazın altındayken
        büyük yakınlaşma gövdeyi bandın içine iterdi → kamera içeriği aşağı kaydırır."""
        from promokit.render import Timeline
        p = _mk(self.ZOOM_STORY.format(camera='camera = [ { t = 0.2, zoom = 2.2, at = [0.5, 0.95], dur = 0.5 } ]'))
        for aspect in ("16:9", "9:16"):
            st = story.load(p, aspect=aspect)
            tl = Timeline(st)
            try:
                rt = tl._rt(st.scenes[0])
                for t in (0.0, 0.45, 1.0, 2.5):
                    for k, dp in rt.poses(rt.camera_at(t), t).items():
                        self.assertGreaterEqual(dp.visible_bbox()[1], tl.box[1] - 0.5, f"{aspect} t={t} {k}")
            finally:
                tl.close()

    def test_zoom_clear_of_band_is_not_shifted(self):
        """Bandla çakışmayan yakınlaşma (odak cihazın üstünde → gövde AŞAĞI büyür) olduğu gibi kalır:
        kaydırma yalnız gerektiğinde. (Kutuyu dolduran tablette ortadan 1,2× bile üst çerçeveyi yazının
        altına sokar — orada kayma DOĞRU davranış.)"""
        from promokit.render import Timeline
        p = _mk(self.ZOOM_STORY.format(camera='camera = [ { t = 0.2, zoom = 1.2, at = [0.5, 0.1], dur = 0.5 } ]'))
        st = story.load(p, aspect="16:9")
        tl = Timeline(st)
        try:
            rt = tl._rt(st.scenes[0])
            cam = rt.camera_at(1.0)
            self.assertAlmostEqual(cam.zoom, 1.2, places=6)
            self.assertEqual(cam.anchor, rt.camera.anchor)
        finally:
            tl.close()

    def test_closed_lid_does_not_push_camera(self):
        """Kapalı MacBook'un görünmeyen kapağı kamerayı itmez (görünen yükseklik ∝ ease(lid)); kontrol:
        kapak AÇIKKEN aynı yakınlaşma bandı gerçekten zorluyor (test boşa geçmesin)."""
        from promokit.render import Timeline
        body = """[video]
size = 540
fps = 30
[[scene]]
layout = "duo"
duration = 3
laptop = "b.png"
tablet = "a.png"
caption = "Kapağı kapatın, tablet ana ekran olsun."
{anim}
camera = [ {{ t = 1.2, zoom = 1.3, at = [0.5, 0.5], on = "tablet", dur = 0.6 }} ]
"""
        shifted = {}
        for name, anim in (("kapalı", 'anim = { laptop = [ { t = 0.2, lid = 1.0 }, { t = 0.9, lid = 0.0 } ] }'),
                           ("açık", "")):
            st = story.load(_mk(body.format(anim=anim)), aspect="16:9")
            tl = Timeline(st)
            try:
                rt = tl._rt(st.scenes[0])
                shifted[name] = rt.camera_at(2.5).anchor[1] - rt.camera.anchor[1]
            finally:
                tl.close()
        self.assertGreater(shifted["açık"], 1.0)
        self.assertAlmostEqual(shifted["kapalı"], 0.0, places=6)

    def test_no_caption_box_when_zoomed(self):
        """Yakınlaşmada başlığın arkasına kutu (panel) ÇİZİLMEZ: bant, yakınlaşmasız karedekiyle birebir.
        (Bir tur yakınlaşınca beyaz bir kutu belirip kayboluyordu — "bug gibi", kullanıcı 2026-10-20.)"""
        from PIL import ImageChops
        flat = _mk(self.ZOOM_STORY.format(camera=""))
        # odak tabletin alt yarısında → kural olmasa gövde bandın içine kadar büyür (kuralı ZORLAYAN durum)
        zoom = _mk(self.ZOOM_STORY.format(camera='camera = [ { t = 0.2, zoom = 1.5, at = [0.5, 0.6], dur = 0.4 } ]'))
        for aspect in ("16:9", "9:16"):
            _, tl, (a,) = _frames(flat, [1.5], aspect=aspect)
            _, _, (b,) = _frames(zoom, [1.5], aspect=aspect)
            # YAZININ kapladığı alan (bandın, içerik kutusuna kadarki kenar payı hariç kısmı). Kutu yazının
            # arkasını boyardı (≈200+ seviye), gövde koyu çerçeveyle ≥100; kalan tek fark en üste yaslanan
            # cihazın yumuşak gölgesinin ucu (≈10 seviye, gözle seçilmez).
            u = min(a.size)
            text_bottom = tl.box[1] - u * (0.05 if a.size[1] > a.size[0] else 0.035)
            region = (0, 0, a.size[0], int(text_bottom))
            lo, hi = ImageChops.difference(a.crop(region), b.crop(region)).convert("L").getextrema()
            self.assertLessEqual(hi, 24, aspect)

    def test_caption_drawn_in_band_for_device_html_card(self):
        from PIL import ImageChops
        cases = [
            ("cihaz", 'duration = 2\ntablet = "a.png"', 'caption = "Başlık burada"'),
            ("html", 'type = "html"\nduration = 2\npage = "p.html"', 'caption = "Başlık burada"'),
        ]
        for name, body, cap in cases:
            with self.subTest(name):
                ims = []
                for extra in ("", cap):
                    p = _mk(f'[video]\nsize = 540\nfps = 30\n[[scene]]\n{body}\n{extra}\n')
                    st, tl, (im,) = _frames(p, [1.5])
                    ims.append(im)
                band = (0, 0, st.video.width, int(tl.box[1]))
                rest = (0, int(tl.box[1]), st.video.width, st.video.height)
                self.assertIsNotNone(ImageChops.difference(ims[0].crop(band), ims[1].crop(band)).getbbox())
                self.assertIsNone(ImageChops.difference(ims[0].crop(rest), ims[1].crop(rest)).getbbox())
        # kart (outro): başlık ve rozet GERÇEKTEN çizilir
        variants = {"boş": "", "başlık": 'title = "Tabletiniz zaten hazır."',
                    "rozet": 'title = "Tabletiniz zaten hazır."\nbadge = "Apple Silicon Mac"'}
        ims = {}
        for k, extra in variants.items():
            p = _mk(f'[video]\nsize = 540\nfps = 30\n[[scene]]\ntype = "outro"\nduration = 3\n{extra}\n')
            _, _, (ims[k],) = _frames(p, [2.5])
        self.assertIsNotNone(ImageChops.difference(ims["boş"], ims["başlık"]).getbbox())
        d = ImageChops.difference(ims["başlık"], ims["rozet"]).getbbox()
        self.assertIsNotNone(d)
        self.assertGreater(_blue(ims["rozet"], d), 10)            # rozet vurgu renginde metin

    def test_caption_continues_after_timed_captions(self):
        """Bulgu 10: önceki sahnede (bitmiş) zamanlı başlık olsa da AYNI başlık geçişte sönüp yeniden girmez."""
        p = _mk("""
            [video]
            size = 360
            fps = 30
            [[scene]]
            duration = 3
            tablet = "a.png"
            caption = "Aynı başlık"
            captions = [ { t = 0.2, d = 0.8, text = "Geçici" } ]
            [[scene]]
            duration = 3
            tablet = "a.png"
            caption = "Aynı başlık"
        """)
        st = story.load(p)
        n = round(st.scenes[1].start * 30)
        _, tl, frames = _frames(p, [(n - 1) / 30, n / 30, (n + 3) / 30])
        top = (0, 0, st.video.width, int(tl.box[1]))
        counts = [sum(1 for v in f.convert("L").crop(top).tobytes() if v < 100) for f in frames]
        self.assertGreater(counts[0], 100)
        for c in counts[1:]:
            self.assertGreater(c, 0.8 * counts[0])


class TestFullStagePage(unittest.TestCase):
    """2a + F3: tam ekran HTML sahnesi yerleşimi KOMŞU sahnelerin gerçek medya oranlarından alır;
    `promo.params.content` = cihaz yerleşiminin içerik kutusu (sayfa CSS px)."""

    def _page_params(self, after):
        from promokit.render import Timeline
        p = _mk(f'[video]\nsize = 540\nfps = 30\n[[scene]]\ntype = "html"\nduration = 2\npage = "p.html"\n'
                f'[[scene]]\nduration = 2\n{after}\n', {"sq.png": ((400, 400), SCREEN)})
        st = story.load(p)
        tl = Timeline(st)
        rt = tl._rt(st.scenes[0])
        try:
            return st, tl, rt.page.timeline["params"]
        finally:
            tl.close()

    def test_layouts_use_adjacent_media_aspect(self):
        _, _, params = self._page_params('tablet = "sq.png"')
        x, y, w, h = params["layouts"]["tablet"]["tablet"]
        self.assertAlmostEqual(w / h, 1.0, delta=0.01)
        _, _, params = self._page_params('tablet = "#203040"')      # medya yok → sabit 1400/920
        x, y, w, h = params["layouts"]["tablet"]["tablet"]
        self.assertAlmostEqual(w / h, 1400 / 920, delta=0.01)

    def test_content_box_param(self):
        st, tl, params = self._page_params('tablet = "a.png"')
        k = min(st.video.width, st.video.height) / 1080.0
        bx0, by0, bx1, by1 = tl.box
        for got, want in zip(params["content"], (bx0 / k, by0 / k, (bx1 - bx0) / k, (by1 - by0) / k)):
            self.assertAlmostEqual(got, want, places=3)


class TestHtmlPageTime(unittest.TestCase):
    """2b: sayfa `in + t·hız` anında oynatılır → jestler ve seçici çözümü de AYNI sayfa zamanında."""

    def _rt(self, tablet, gestures, latency=0.0):
        from promokit.render import Timeline
        p = _mk(f'[video]\nsize = 360\nfps = 30\nlatency = {latency}\n[[scene]]\nduration = 4\n'
                f'tablet = {tablet}\ngestures = [ {gestures} ]\n')
        st = story.load(p)
        tl = Timeline(st)
        return tl, tl._rt(st.scenes[0])

    def test_latency_applied(self):
        tl, rt = self._rt('"p.html"', '{ t = 2.0, tap = [0.5, 0.5] }', latency=0.05)
        try:
            self.assertAlmostEqual(rt.contacts[0].t0, 2.0)
            self.assertAlmostEqual(rt.devices["tablet"].timeline["gestures"][0]["t0"], 2.05, places=6)
        finally:
            tl.close()

    def test_page_gestures_follow_in_and_speed(self):
        tl, rt = self._rt('{ src = "p.html", in = 1.0, speed = 2.0 }', '{ t = 2.0, tap = [0.5, 0.5] }', latency=0.05)
        try:
            self.assertAlmostEqual(rt.contacts[0].t0, 2.0)                        # daire: sahne zamanı
            g = rt.devices["tablet"].timeline["gestures"][0]
            self.assertAlmostEqual(g["t0"], 1.0 + (2.0 + 0.05) * 2.0, places=6)    # sayfa: in + (t+gecikme)·hız
        finally:
            tl.close()

    def test_selector_resolved_at_page_time(self):
        import promokit.htmlscreen as hs
        calls = []

        class Fake:
            def __init__(self, *a, **k):
                pass

            def __call__(self, sel, t, contacts, keys, texts, **kw):
                calls.append((sel, t, [c.t0 for c in contacts]))
                return (0.4, 0.4, 0.2, 0.2)

            def close(self):
                pass
        saved = hs.SelectorResolver
        hs.SelectorResolver = Fake
        try:
            tl, rt = self._rt('{ src = "p.html", in = 1.0 }', '{ t = 0.5, tap = [0.2, 0.2] }, { t = 2.0, tap = "#x" }')
            tl.close()
        finally:
            hs.SelectorResolver = saved
        sel, t, prior = calls[0]
        self.assertEqual(sel, "#x")
        self.assertAlmostEqual(t, 3.0)              # sahne 2,0 s → sayfa 1,0 + 2,0
        self.assertAlmostEqual(prior[0], 1.5)       # önceki dokunuş da sayfa zamanında
        self.assertAlmostEqual(rt.contacts[1].t0, 2.0)


class TestLaptopLid(unittest.TestCase):
    """Kapak kapanışı, TAM KARŞIDAN. Bulgu 2: AÇIK kapağın gölgesi havada asılı kalmaz. Kullanıcı (2026-10-20):
    "macbook kapağı kapatıncaki görüntü garip duruyor" → kapak k³ ile eziliyor (ilk yarıda çöküş) ve kapalıyken
    tabandan DAR ayrı bir şerit kalıyordu; yükseltilmiş bakış denendi ve geri alındı ("tam karşıdan bakıyoruz,
    ince çizgi kalacak kapanınca")."""

    DARK = 'background = "#0b0f16"\n'     # gövde ölçümleri: gümüş kapak açık zeminde eşiğin altında, gölge karışır

    def _frame(self, kfs, t, layout="laptop", aspect=None, bg=""):
        p = _mk(f'[video]\nsize = 540\nfps = 30\n{bg}[[scene]]\nduration = 3\nlayout = "{layout}"\nlaptop = "b.png"\n'
                + ('tablet = "a.png"\n' if layout == "duo" else "") + f'anim = {{ laptop = [ {kfs} ] }}\n')
        _, tl, (im,) = _frames(p, [t], aspect=aspect)
        return im

    def _body(self, im, empty):
        """Gövdenin (gölge HARİÇ: belirgin fark) sınır kutusu."""
        from PIL import ImageChops
        d = ImageChops.difference(im.convert("RGB"), empty.convert("RGB")).convert("L")
        return d.point(lambda v: 255 if v > 40 else 0).getbbox()

    def test_no_ghost_shadow(self):
        from PIL import ImageChops
        # kapalı kapağın üstünde (açık kapağın yeri) gölge yok: sahne orada boş zeminle aynı
        empty = self._frame('{ t = 0, o = 0.0 }', 0.8)
        closed = self._frame('{ t = 0, lid = 0.0 }', 0.8)
        open_ = self._frame('{ t = 0, lid = 1.0 }', 0.8)
        L = _mask(open_, LAPTOP_SCREEN).getbbox()
        above = (L[0], L[1], L[2], int(L[1] + 0.5 * (L[3] - L[1])))
        d2 = ImageChops.difference(closed.crop(above), empty.crop(above)).convert("L")
        self.assertLessEqual(max(d2.getextrema()), 3)
        # kapanırken de: açık kapağın üst çeyreği boş zemin (gölge ezilen kapağı izler, açık kapağınki kalmaz);
        # görünen kapak oradan gölge yarıçapının çok altında olmalı (yoksa test boşa geçer)
        half = self._frame('{ t = 0, lid = 1.0 }, { t = 1, lid = 0.0, ease = "linear" }', 0.6)
        band = (L[0], L[1], L[2], int(L[1] + 0.25 * (L[3] - L[1])))
        self.assertGreater(self._body(half, empty)[1], band[3] + 40)
        d3 = ImageChops.difference(half.crop(band), empty.crop(band)).convert("L")
        self.assertLessEqual(max(d3.getextrema()), 3)

    def test_closed_laptop_is_a_thin_front_edge(self):
        """Kapalıyken tam karşıdan yalnız ince bir çizgi (kapağın ön kenarı + taban; klavye/sırt YOK) ve kapağın
        kenarı TABAN KADAR geniş — eskiden tabandan dar, ayrı bir şerit gibi duruyordu."""
        from PIL import ImageChops
        empty = self._frame('{ t = 0, o = 0.0 }', 0.8, bg=self.DARK)
        closed = self._frame('{ t = 0, lid = 0.0 }', 0.8, bg=self.DARK)
        x0, y0, x1, y1 = self._body(closed, empty)
        self.assertLess((y1 - y0) / (x1 - x0), 0.09, (x0, y0, x1, y1))
        d = ImageChops.difference(closed.convert("RGB"), empty.convert("RGB")).convert("L")
        top = d.crop((0, y0, d.size[0], y0 + 2)).point(lambda v: 255 if v > 40 else 0).getbbox()
        self.assertGreater((top[2] - top[0]) / (x1 - x0), 0.95, (top, (x0, x1)))

    def test_closed_lid_has_no_gap(self):
        """Kapalı kapak tabana OTURUR: aralarında koyu menteşe bandı ya da uçlarda zemin görünmez (kullanıcı,
        2026-10-20: "kapağı kapatınca arada hafif boşluk kalmış"). Gövdenin yüksekliği boyunca dikey taramada
        her piksel gümüş (koyu zeminde boşluk/menteşe koyu görünür); uçlar ve ortadaki başparmak oyuğu dahil."""
        from PIL import ImageChops
        empty = self._frame('{ t = 0, o = 0.0 }', 0.8, bg=self.DARK)
        closed = self._frame('{ t = 0, lid = 0.0 }', 0.8, bg=self.DARK).convert("L")
        x0, y0, x1, y1 = self._body(closed.convert("RGB"), empty)
        for f in (0.04, 0.10, 0.30, 0.70, 0.90, 0.96):
            x = int(x0 + f * (x1 - x0))
            col = [closed.getpixel((x, y)) for y in range(y0 + 1, y1 - 1)]
            self.assertGreater(min(col), 110, f"x={f:.2f}: {col}")
        # EN UÇLAR: kapak kenarıyla tabanın birleştiği satırlarda gövde kenardan kenara (alt köşesi yuvarlak
        # kenar uçlarda küçük çentik bırakıyordu — sütun taraması o kadar uca bakmıyordu, mutant hayatta kaldı)
        body = ImageChops.difference(closed.convert("RGB"), empty.convert("RGB")).convert("L").point(lambda v: 255 if v > 40 else 0)
        h = y1 - y0
        for y in range(int(y0 + 0.25 * h), int(y1 - 0.35 * h)):
            row = body.crop((x0, y, x1, y + 1)).getbbox()
            self.assertIsNotNone(row, y)
            self.assertLessEqual(row[0], 1, f"y={y}: {row}")
            self.assertGreaterEqual(row[2], x1 - x0 - 1, f"y={y}: {row}")

    def test_lid_rotates_not_squashes(self):
        """Kapak menteşe etrafında DÖNER: yarı yolda (φ = 45°) görünen kapak ≈ sin 45° = %71 (eski k³
        ezilmesinde %12'ydi — ilk yarıda çöküp sonra sürünüyordu)."""
        from promokit.render import Timeline
        from promokit.devices import LAPTOP
        empty = self._frame('{ t = 0, o = 0.0 }', 0.8, bg=self.DARK)
        p = _mk('[video]\nsize = 540\nfps = 30\n' + self.DARK + '[[scene]]\nduration = 3\nlayout = "laptop"\nlaptop = "b.png"\n'
                'anim = { laptop = [ { t = 0, lid = 1.0 }, { t = 1, lid = 0.0, ease = "linear" } ] }\n')
        st = story.load(p)
        tl = Timeline(st)
        try:
            rt = tl._rt(st.scenes[0])
            pose = rt.poses(rt.camera_at(0.5), 0.5)["laptop"]
            x, y, w, h = pose.rect
            hinge = y + h + w * LAPTOP["chin"]
            full = hinge - (y - w * LAPTOP["top"])
            drawn = self._body(tl.frame(0.5), empty)[1]
        finally:
            tl.close()
        self.assertAlmostEqual((hinge - drawn) / full, 0.72, delta=0.06)

    def test_front_edge_stays_on_the_desk(self):
        """Ön kenar (gövdenin altı) açık ↔ kapalı AYNI yerde: kapalı gövde aşağı uzayıp alttaki tablete
        binmez, masadan kalkmaz. Kapanışın ilk anı açık mockup'la birebir (sıçrama yok)."""
        from PIL import ImageChops
        empty = self._frame('{ t = 0, o = 0.0 }', 0.8, bg=self.DARK)
        op = self._body(self._frame('{ t = 0, lid = 1.0 }', 0.8, bg=self.DARK), empty)
        cl = self._body(self._frame('{ t = 0, lid = 0.0 }', 0.8, bg=self.DARK), empty)
        self.assertAlmostEqual(cl[3], op[3], delta=2)
        start = self._frame('{ t = 0, lid = 1.0 }, { t = 1, lid = 0.0, ease = "linear" }', 0.003)
        d = ImageChops.difference(start, self._frame('{ t = 0, lid = 1.0 }', 0.8)).convert("L")
        # yalnız yuvarlak köşelerde yeniden örnekleme farkı (~60 px); eski modelde kapak ilk karede ~3 px
        # çöküyordu → tüm kenar boyunca binlerce piksel
        self.assertLess(sum(d.histogram()[25:]), 150)

    def test_visible_box_matches_the_drawing(self):
        """Başlık bandı kuralı `visible_bbox`a bakar → çizimle AYNI formül olmalı (kapanışın her anında
        görünen üst kenar ±3 px)."""
        from promokit.render import Timeline
        empty = self._frame('{ t = 0, o = 0.0 }', 0.8, bg=self.DARK)
        p = _mk('[video]\nsize = 540\nfps = 30\n' + self.DARK + '[[scene]]\nduration = 3\nlayout = "laptop"\nlaptop = "b.png"\n'
                'anim = { laptop = [ { t = 0, lid = 1.0 }, { t = 1, lid = 0.0, ease = "linear" } ] }\n')
        st = story.load(p)
        tl = Timeline(st)
        try:
            rt = tl._rt(st.scenes[0])
            for t in (0.2, 0.5, 0.75, 0.9, 1.2):
                im = tl.frame(t)
                pose = rt.poses(rt.camera_at(t), t)["laptop"]
                drawn = self._body(im, empty)[1]
                self.assertAlmostEqual(pose.visible_bbox()[1], drawn, delta=3, msg=f"t={t}")
        finally:
            tl.close()


class TestEncodeAndMedia(unittest.TestCase):
    """Bulgu 5-7 + 9: müzik videoyu kesmez · ffmpeg stderr'i okuyucuyu kilitlemez · `out` oynatmayı
    sınırlar · ProRes .mov ister (ffmpeg girdileri küçük, render DEĞİL)."""

    @staticmethod
    def _ff(*args):
        import subprocess
        from promokit.util import ffmpeg_bin
        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", *args], check=True)

    def test_short_music_keeps_video_length(self):
        import json
        import subprocess
        from promokit.render import _finalize, encoder_args
        from promokit.util import ffmpeg_bin, ffprobe_bin
        d = tempfile.mkdtemp(prefix="promo-test-")
        parts = []
        for k in range(2):
            out = os.path.join(d, f"part{k}.mp4")
            subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x36:r=30:d=0.5",
                            "-pix_fmt", "rgb24", "-f", "rawvideo", os.path.join(d, "raw")], check=True)
            subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "64x36",
                            "-r", "30", "-i", os.path.join(d, "raw")] + encoder_args("h264", 30, True) + [out],
                           check=True)
            parts.append(out)
        music = os.path.join(d, "m.m4a")
        self._ff("-f", "lavfi", "-i", "sine=f=440:d=0.3", music)
        v = story.VideoCfg()
        v.fps, v.music = 30.0, music
        final = os.path.join(d, "out.mp4")
        _finalize(parts, final, v, None, 1.0, 0.0)
        r = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v", "-count_frames",
                            "-show_entries", "stream=nb_read_frames", "-of", "json", final], capture_output=True, text=True)
        self.assertEqual(int(json.loads(r.stdout)["streams"][0]["nb_read_frames"]), 30)

    def test_reader_survives_noisy_stderr(self):
        import threading
        import types
        from promokit.media import VideoSource
        d = tempfile.mkdtemp(prefix="promo-test-")
        fake = os.path.join(d, "ffmpeg")
        with open(fake, "w") as f:
            f.write(f"#!{sys.executable}\nimport sys\nsys.stderr.write('x' * 400000)\nsys.stderr.flush()\n"
                    "sys.stdout.buffer.write(bytes(16 * 8 * 3) * 40)\n")
        os.chmod(fake, 0o755)
        saved = os.environ.get("FFMPEG")
        os.environ["FFMPEG"] = fake
        probe = types.SimpleNamespace(path=fake, matrix=None, height=100)
        vs = VideoSource(probe, (0.0, 0.0, 1.0, 1.0), (16, 8), 30.0, 0.0)
        got = []
        th = threading.Thread(target=lambda: got.extend(vs.get(k / 30).size for k in range(30)), daemon=True)
        try:
            th.start()
            th.join(10)
            self.assertFalse(th.is_alive(), "okuyucu ffmpeg stderr'i yüzünden kilitlendi")
            self.assertEqual(len(got), 30)
        finally:
            if vs.proc is not None:   # kilitliyse önce süreci öldür (okuyan iş parçacığı EOF alsın)
                vs.proc.kill()
            th.join(5)
            vs.close()
            if saved is None:
                os.environ.pop("FFMPEG", None)
            else:
                os.environ["FFMPEG"] = saved

    def test_clip_out_bounds_playback(self):
        from promokit.render import Timeline
        d = tempfile.mkdtemp(prefix="promo-test-")
        self._ff("-f", "lavfi", "-i", "color=c=black:s=64x36:d=6:r=30,format=rgb24,geq=r='min(255,T*40)':g=128:b=128",
                 "-pix_fmt", "yuv420p", "-c:v", "libx264", os.path.join(d, "v.mp4"))
        for loop in ("false", "true"):
            p = os.path.join(d, f"s{loop}.toml")
            with open(p, "w") as f:
                f.write(f'[video]\nsize = 360\nfps = 30\n[[scene]]\nduration = 4\n'
                        f'tablet = {{ src = "v.mp4", in = 1.0, out = 2.0, loop = {loop} }}\n')
            st = story.load(p)
            tl = Timeline(st)
            try:
                rt = tl._rt(st.scenes[0])
                media_t = [rt.content("tablet", t, (32, 18)).getpixel((16, 9))[0] / 40 for t in (0.5, 1.5, 2.5, 3.5)]
            finally:
                tl.close()
            with self.subTest(loop=loop):
                self.assertAlmostEqual(media_t[0], 1.5, delta=0.2)
                if loop == "false":       # `out` sonrası son karede durur
                    for m in media_t[1:]:
                        self.assertLess(m, 2.15)
                else:                     # [in, out] içinde döner
                    self.assertAlmostEqual(media_t[1], 1.5, delta=0.2)
                    self.assertAlmostEqual(media_t[2], 1.5, delta=0.2)

    def test_prores_needs_mov_and_story_codec_sets_extension(self):
        from promokit import render
        p = _mk('[video]\ncodec = "prores"\nsize = 360\n[[scene]]\nduration = 1\ntablet = "a.png"\n')
        called = []
        saved = render._render_range
        render._render_range = lambda *a, **k: called.append(1)
        try:
            with self.assertRaises(PromoError):
                render.render(p, os.path.join(tempfile.mkdtemp(), "x.mp4"), {})
        finally:
            render._render_range = saved
        self.assertEqual(called, [])          # hata kare kodlanmadan ÖNCE (tüm iş boşa gitmesin)
        out = render.resolve_output(p, None, None, None, False, tempfile.mkdtemp())
        self.assertTrue(out.endswith(".mov"))
        p2 = _mk('[video]\nout = "film/son.mp4"\n[[scene]]\nduration = 1\ntablet = "a.png"\n')
        out = render.resolve_output(p2, None, None, None, False, tempfile.mkdtemp())
        self.assertEqual(out, os.path.join(os.path.dirname(p2), "film", "son.mp4"))


class TestDeterminismAndText(unittest.TestCase):
    """Bulgu 11-12: dokunuş dairesi önbelleği sıra-bağımsız · harf aralıklı başlık ortalı ve kırpılmaz."""

    def test_dot_sprite_independent_of_cache_order(self):
        from promokit import overlays as ov
        ov._DOTS.clear()
        ov._dot(10.26, 1.0)
        a = ov._dot(10.74, 1.0).copy()
        ov._DOTS.clear()
        b = ov._dot(10.74, 1.0).copy()
        ov._DOTS.clear()
        self.assertEqual(a.size, b.size)
        self.assertEqual(a.tobytes(), b.tobytes())

    def test_tracked_text_centered_and_unclipped(self):
        from promokit.text import Fonts, render_text
        f = Fonts()
        for txt in ("AVAWAYATAVATTA Yo Te", "Tabletiniz zaten hazır.", "Kabloyu takın. Mac'inizin ikinci ekranı."):
            with self.subTest(txt):
                im = render_text(f, txt, 56, 700, "#000000", tracking=-0.02)
                x0, _, x1, _ = im.getchannel("A").getbbox()
                left, right = x0, im.size[0] - x1
                self.assertGreater(right, 3)                      # sağdan kırpılmaz
                self.assertLess(abs(left - right), 6)             # ortalı

    def test_wrap_breaks_at_phrase_separator_first(self):
        """" · " birbirinden bağımsız iki ifadeyi ayırır: satır SIĞMAZSA önce oradan kırılır (nokta düşer).
        Kelime-kelime kırma "… Tablet app: Google Play" / "/ AppGallery" bırakıyordu (9:16 kapanış kartı)."""
        from promokit.text import Fonts, text_width, wrap
        f = Fonts()
        a, b = "Mac app: example.com/mac", "Tablet app: Google Play / AppGallery"
        w = max(text_width(f, a, 40, 400), text_width(f, b, 40, 400)) + 4    # her ifade sığar, ikisi birden değil
        self.assertEqual(wrap(f, f"{a} · {b}", 40, 400, w), [a, b])
        self.assertEqual(wrap(f, "Mac · Android", 40, 400, 2000), ["Mac · Android"])   # sığıyorsa dokunulmaz

    def test_wrap_never_starts_line_with_separator(self):
        from promokit.text import Fonts, text_width, wrap
        f = Fonts()
        txt = "Tablet app: Google Play / AppGallery — Android tablet · HarmonyOS"
        for w in range(int(text_width(f, "AppGallery —", 40, 400)) + 2, int(text_width(f, txt, 40, 400)), 23):
            for ln in wrap(f, txt, 40, 400, w):
                self.assertFalse(ln.split(" ")[0] in ("/", "·", "—", "–", "|"), f"{w}: {ln!r}")


class TestStoryLanguage(unittest.TestCase):
    def test_video_lang_reaches_every_html_page(self):
        """`[video] lang` her HTML ekranın (cihaz + tam ekran) `params.lang`ine iner; sahne ezebilir. Seçici
        ölçümü de AYNI parametrelerle yapılır → İngilizce yerleşimde dokunuş doğru yere düşer."""
        d = tempfile.mkdtemp(prefix="promo-lang-")
        with open(os.path.join(d, "p.html"), "w", encoding="utf-8") as f:
            f.write('<!doctype html><meta name="promo-viewport" content="400x300"><body></body>')
        Image.new("RGB", (300, 200), (40, 90, 160)).save(os.path.join(d, "a.png"))
        p = os.path.join(d, "s.toml")
        with open(p, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent("""
                [video]
                lang = "en"
                [[scene]]
                duration = 1
                tablet = "p.html"
                [[scene]]
                duration = 1
                tablet = { src = "p.html", params = { lang = "tr" } }
                [[scene]]
                type = "html"
                page = "p.html"
                duration = 1
                [[scene]]
                duration = 1
                tablet = "a.png"
            """))
        st = story.load(p)
        self.assertEqual(st.video.lang, "en")
        self.assertEqual(st.scenes[0].tablet.params.get("lang"), "en")
        self.assertEqual(st.scenes[1].tablet.params.get("lang"), "tr")          # sahne ezer
        self.assertEqual(st.scenes[2].params.get("lang"), "en")
        self.assertNotIn("lang", st.scenes[3].tablet.params)                    # görüntüye parametre gitmez
        with open(p, "w", encoding="utf-8") as f:
            f.write('[video]\nlang = "xx"\n[[scene]]\nduration = 1\ntablet = "a.png"\n')
        with self.assertRaises(PromoError):
            story.load(p)

    def test_voice_speaks_video_language(self):
        """`[voice] lang` yazılmazsa videonun dili: İngilizce senaryoda unutulan satır İngilizce metni Türkçe
        modelle + Türkçe okunuşla ("Mac" → "Mek") okutuyordu. Açıkça yazılan ezer."""
        st = story.load(_mk('[video]\nlang = "en"\n[[scene]]\nduration = 1\ntablet = "a.png"\n'))
        self.assertEqual(st.voice.lang, "en")
        st = story.load(_mk('[video]\nlang = "en"\n[voice]\nlang = "tr"\n[[scene]]\nduration = 1\ntablet = "a.png"\n'))
        self.assertEqual(st.voice.lang, "tr")
        self.assertEqual(story.load(_mk('[[scene]]\nduration = 1\ntablet = "a.png"\n')).voice.lang, "tr")

    def test_output_in_language_folder(self):
        """a/en/demo.toml ile a/demo.toml aynı adı taşır → İngilizce çıktı `out/en/`e
        (biri ötekini EZMESİN); Türkçe (vars.) yerinde kalır."""
        from promokit import render
        d = tempfile.mkdtemp()
        en = render.resolve_output(_mk('[video]\nlang = "en"\n[[scene]]\nduration = 1\ntablet = "a.png"\n'),
                                   None, None, None, False, d)
        tr = render.resolve_output(_mk('[[scene]]\nduration = 1\ntablet = "a.png"\n'), None, None, None, False, d)
        self.assertEqual(os.path.dirname(en), os.path.join(d, "en"))
        self.assertEqual(os.path.dirname(tr), d)


class TestTabletBezel(unittest.TestCase):
    """Kullanıcı (2026-10-20): "tabletin çerçevesi de çok kalın onu da ince yapalım" — ekranın kısa kenarının
    %5,2'si (~8,5 mm) → ince (~%3,4 ≈ 5,5 mm; güncel tabletler 5-6 mm). Çizilen çerçeve ÖLÇÜLÜR."""

    def test_rendered_bezel_is_thin(self):
        p = _mk('[video]\nsize = 1080\nfps = 30\nbackground = "#f4f6fb"\n[[scene]]\nduration = 1\nlayout = "tablet"\ntablet = "a.png"\n')
        _, tl, (im,) = _frames(p, [0.5])
        sx0, sy0, sx1, sy1 = _mask(im, SCREEN).getbbox()
        # ekranın solundan dışarı: çerçevenin koyu pikselleri bitene kadar
        y = (sy0 + sy1) // 2
        px = im.convert("L").load()
        x = sx0 - 1
        while x > 0 and px[x, y] < 120:
            x -= 1
        bezel = (sx0 - 1 - x) / min(sx1 - sx0, sy1 - sy0)
        self.assertLess(bezel, 0.040)
        self.assertGreater(bezel, 0.025)


class TestPlatformSafeArea(unittest.TestCase):
    """Dikey kısa video (9:16 — Reels/TikTok/Shorts): platform arayüzü üstte ~%10-13 (durum çubuğu, sekmeler,
    ikonlar) ve altta ~%15 (açıklama, kullanıcı adı, ses) videonun ÜSTÜNE biner. Kullanıcı (2026-10-20):
    "dikey videolarda yazı çok üstte … üstte şerit oluyor yazıyı blokluyor". Yatay/kare/4:5 değişmez.
    Kullanıcı (2026-10-23): YouTube webde Shorts altyazısını ÜSTTE gösteriyor ve iki satırlık başlığın 1.
    satırına (%13,5-17) iniyordu → başlık %20'den başlar: arada ~%3,6 (altyazı üç satıra bölünse de yeter)."""

    def test_layout_keeps_text_and_devices_inside(self):
        W, H = 1080, 1920
        self.assertGreaterEqual(layout.caption_top(W, H), 0.20 * H)
        box = layout.content_box(W, H)
        self.assertLessEqual(box[3], 0.85 * H)
        for name in ("tablet", "laptop", "duo"):
            for k, r in layout.place(W, H, name, {}, box).items():
                self.assertLessEqual(r[1] + r[3], 0.85 * H, f"{name}/{k}")
        for w, h in ((1920, 1080), (1080, 1350), (1080, 1080)):     # 16:9 · 4:5 · 1:1 — eski yerinde
            self.assertAlmostEqual(layout.caption_top(w, h), min(w, h) * (0.075 if h > w else 0.048), msg=f"{w}x{h}")
            self.assertEqual(layout.safe_bottom(w, h), h)

    def test_caption_readable_on_phone(self):
        """9:16 telefonda tam ekran izlenir: alt satır 16:9'daki oranla ~20 px kalıyor, okunmuyordu (inceleme,
        2026-10-20) → dikeyde başlık ve özellikle alt satır büyür; ayrılan bant da büyür (cihazlar binmez)."""
        from promokit.overlays import Caption
        from promokit.text import Fonts
        f = Fonts()
        tall, wide = Caption(f, 1080, 1920, "Plug in the cable.", "No developer mode"), Caption(f, 1920, 1080, "Plug in the cable.", "No developer mode")
        self.assertGreaterEqual(tall.sub.size[1] / wide.sub.size[1], 1.4)
        self.assertGreaterEqual(tall.title.size[1] / wide.title.size[1], 1.1)
        self.assertGreater(Caption.band(1080, 1920), Caption.band(1920, 1080))
        self.assertGreaterEqual(Caption.band(1080, 1920) + 1, tall.extent())     # ayrılan bant büyüyen yazıyı karşılar

    def test_outro_card_readable_on_phone(self):
        """Kapanış kartı da başlıkla aynı kurala uyar: 9:16'da adres satırı 16:9 oranıyla ~32 px kalıyordu
        (incelemenin 2. turu, 2026-10-20) → dikeyde büyür, yine de genişliğe ve platform şeritlerinin
        arasına sığar."""
        from promokit.render import Timeline
        body = ('[[scene]]\ntype = "outro"\nduration = 3\ntitle = "Free during the open beta."\n'
                'subtitle = "Mac app: example.com/mac · Tablet app: Google Play / AppGallery"\n'
                'badge = "Apple Silicon Mac · Android tablet"\n')
        cards = {}
        for aspect in ("9:16", "16:9"):
            st = story.load(_mk(f'[video]\naspect = "{aspect}"\nsize = 1080\nfps = 30\n{body}'))
            tl = Timeline(st)
            try:
                items, top = tl._rt(st.scenes[0]).card
            finally:
                tl.close()
            cards[aspect] = ({k: im for k, im, _ in items}, top, st.video.width, st.video.height)
        (tall, top, W, H), (wide, _, _, _) = cards["9:16"], cards["16:9"]
        # satır sayısı dikeyde artar (kırılır) → görüntü boyu değil İLK satırın mürekkep yüksekliği kıyaslanır
        self.assertGreaterEqual(_first_line_px(tall["title"]) / _first_line_px(wide["title"]), 1.1)
        self.assertGreaterEqual(_first_line_px(tall["subtitle"]) / _first_line_px(wide["subtitle"]), 1.4)
        for k, im in tall.items():
            self.assertLessEqual(im.size[0], W * 0.92, k)                                   # genişliğe sığar
        bottom = top + sum(im.size[1] for im in tall.values()) + 0.1 * H                    # boşluklar dahil üst sınır
        self.assertGreaterEqual(top, layout.caption_top(W, H))
        self.assertLessEqual(bottom, layout.safe_bottom(W, H))

    def test_caption_drawn_below_platform_bar(self):
        p = _mk('[video]\naspect = "9:16"\nsize = 540\nfps = 30\n[[scene]]\nduration = 2\ntablet = "a.png"\n'
                'caption = "Kapağı kapatın."\nsubcaption = "Kapak kapalıyken de yayın sürer"\n')
        _, tl, (im,) = _frames(p, [1.0])
        W, H = im.size
        top = im.convert("L").crop((0, 0, W, int(0.20 * H)))
        self.assertGreater(min(top.getdata()), 150)                     # üst şeritte koyu (yazı) piksel yok
        band = im.convert("L").crop((0, int(0.20 * H), W, int(0.37 * H)))
        self.assertLess(min(band.getdata()), 80)                        # başlık hemen altında (test boşa geçmesin)



class TestStills(unittest.TestCase):
    """Sahne kareleri KODDAN: sahnenin `still` anı = o sahnenin görseli; `promo.py stills` her sahneden bir
    dosya. PNG alfasız 24-bit; JPG `max_kb` tavanına inene dek kalite düşürür."""

    TWO = """
        [video]
        size = 360
        fps = 30
        transition = "cut"
        [[scene]]
        duration = 2
        tablet = "a.png"
        caption = "Kabloyu takın"
        still = 1.0
        [[scene]]
        duration = 2
        tablet = "a.png"
        caption = "Çizim: kalem ve basınç"
        still = 1.5
        [[scene]]
        duration = 1
        tablet = "a.png"
        caption = "Görsel olmayan sahne"
    """

    def _stills(self, body, **kw):
        from promokit.render import stills
        out = tempfile.mkdtemp(prefix="promo-stills-")
        return stills(_mk(body), out, {}, **kw), out

    def test_one_image_per_scene_with_still(self):
        paths, _ = self._stills(self.TWO)
        self.assertEqual([os.path.basename(p) for p in paths],
                         ["01-kabloyu-takin.png", "02-cizim-kalem-ve-basinc.png"])   # still'siz sahne atlanır
        ims = [Image.open(p) for p in paths]
        for im in ims:
            self.assertEqual(im.mode, "RGB")                    # 24-bit PNG, alfa kanalı YOK
            self.assertEqual(im.size, (640, 360))
        from PIL import ImageChops
        self.assertIsNotNone(ImageChops.difference(ims[0], ims[1]).getbbox())   # her sahnenin kendi başlığı

    def test_still_is_the_scene_moment(self):
        """Görsel = sahnenin `still` anındaki kare (zaman çizelgesindeki sahne başı + still)."""
        from promokit.render import still
        paths, out = self._stills(self.TWO)
        ref = still(_mk(self.TWO), 2.0 + 1.5, os.path.join(out, "ref.png"), {})
        self.assertIsNone(ImageChops_diff(Image.open(paths[1]), Image.open(ref)))

    def test_jpg_respects_size_cap(self):
        free, _ = self._stills(self.TWO, fmt="jpg")
        cap_kb = min(os.path.getsize(p) for p in free) * 0.7 / 1024     # en yüksek kalitede sığmayan tavan
        paths, _ = self._stills(self.TWO, fmt="jpg", max_kb=cap_kb)
        for p in paths:
            self.assertTrue(p.endswith(".jpg"))
            self.assertLessEqual(os.path.getsize(p), cap_kb * 1024)
            self.assertEqual(Image.open(p).format, "JPEG")

    def test_story_without_stills_is_error(self):
        with self.assertRaises(PromoError):
            self._stills('[video]\nsize = 360\n[[scene]]\nduration = 2\ntablet = "a.png"\n')









    def test_still_inside_transition_is_error(self):
        """Geçiş anındaki kare iki sahnenin KARIŞIMI — görsel olamaz."""
        body = ('[video]\nsize = 360\ntransition = "fade"\ntransition_dur = 0.6\n'
                '[[scene]]\nduration = 2\ntablet = "a.png"\nstill = 1.0\n'
                '[[scene]]\nduration = 2\ntablet = "b.png"\nstill = 0.1\n')
        with self.assertRaises(PromoError):
            self._stills(body)








    def test_balanced_wrap_keeps_line_count_and_avoids_orphans(self):
        """Kanca satırları DENGELİ: açgözlü kırılımla AYNI satır sayısı, ama en geniş satırı en dar olan bölünüş —
        "mikrofon yok / mu?" yerine "mikrofon / yok mu?". Sığan metne ve elle kırılmış metne dokunmaz."""
        from promokit.text import Fonts, balanced_wrap, text_width, wrap
        f = Fonts(None)
        w = text_width(f, "mikrofon yok", 100, 700) + 2
        self.assertEqual(wrap(f, "mikrofon yok mu?", 100, 700, w), ["mikrofon yok", "mu?"])
        self.assertEqual(balanced_wrap(f, "mikrofon yok mu?", 100, 700, w), "mikrofon\nyok mu?")
        self.assertEqual(balanced_wrap(f, "Kısa", 100, 700, w), "Kısa")
        self.assertEqual(balanced_wrap(f, "elle\nkırık satır", 100, 700, 50), "elle\nkırık satır")

def ImageChops_diff(a, b):
    from PIL import ImageChops
    return ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox()


class TestShippedExamples(unittest.TestCase):
    """examples/ altındaki senaryoların hepsi, iki en-boyda: hiçbir cihaz hiçbir anda başlık bandına girmez
    (kamera + anim x/y/s/rot dahil) ve kamera odağı içerik kutusunda kalır (kaydırma odağı dışarı
    itmesin). Kendi senaryolarınızı da examples/ altına koyup bu kapıdan geçirebilirsiniz."""

    def test_devices_never_enter_caption_band(self):
        from promokit.render import Timeline
        stories_dir = os.path.join(ROOT, "examples")
        names = sorted(os.path.relpath(f, stories_dir) for f in glob.glob(os.path.join(stories_dir, "**", "*.toml"),
                                                                        recursive=True))
        self.assertGreater(len(names), 0)
        for name in names:
            for aspect in ("16:9", "9:16"):
                st = story.load(os.path.join(stories_dir, name), aspect=aspect)
                for sc in st.scenes:
                    sc.gestures = []          # duruş jestlerden bağımsız; seçici çözümü (tarayıcı) gerekmesin
                tl = Timeline(st)
                try:
                    for sc in st.scenes:
                        if sc.type != "device":
                            continue
                        rt = tl._rt(sc)
                        n = max(1, int(sc.duration / 0.2))
                        for i in range(n + 1):
                            t = sc.duration * i / n
                            cam = rt.camera_at(t)
                            where = f"{name} {aspect} sahne #{sc.index + 1} t={t:.2f}"
                            self.assertLessEqual(cam.anchor[1], tl.box[3] + 0.5, where + " (odak kutu dışında)")
                            for k, dp in rt.poses(cam, t).items():
                                self.assertGreaterEqual(dp.visible_bbox()[1], tl.box[1] - 0.5, f"{where} {k}")
                finally:
                    tl.close()


if __name__ == "__main__":
    unittest.main()
