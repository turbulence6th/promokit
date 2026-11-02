"""HTML ekran: jest sınıflandırması README'deki jest tablosuyla aynı mı + sayfa gerçekten oynuyor mu.

Sözleşme (README → «Jest anlamları»): tek parmak sürükle = kaydırma, uzun bas = sağ tık,
uzun bas+sürükle = sürükleme, iki parmak sürükle = imleç, iki parmak dokun = sağ tık, çimdik = zoom.
Bu testler o tabloyu kilitler; sözleşmeyi bilerek değiştiriyorsanız önce buradaki beklentiyi değiştirin.
Tarayıcı gerektiren testler Chrome/Brave/Chromium yoksa ATLANIR — `run-tests.sh` atlamayı HATA sayar.
"""
import math
import os
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promokit import gestures  # noqa: E402
from promokit.htmlscreen import classify, dp_per_css, TAP_SLOP_DP  # noqa: E402
from promokit.util import PromoError  # noqa: E402

MODEL = gestures.HandModel(fw=0.05, angle=math.radians(-14), aspect=1400 / 920)
CSS = (1400, 920)
EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


def kinds(specs):
    cs, keys, texts = gestures.compile_gestures(specs, MODEL)
    return [g["type"] for g in classify(cs, keys, texts, CSS)["gestures"]]


class TestGestureSemantics(unittest.TestCase):
    def test_one_finger_drag_is_scroll(self):
        self.assertEqual(kinds([{"t": 0, "scroll": [[0.5, 0.8], [0.5, 0.3]]}]), ["scroll"])

    def test_hold_then_move_is_drag(self):
        self.assertEqual(kinds([{"t": 0, "drag": [[0.3, 0.5], [0.6, 0.5]]}]), ["drag"])

    def test_long_press_is_right_click(self):
        self.assertEqual(kinds([{"t": 0, "long_press": [0.4, 0.4]}]), ["secondary"])

    def test_tap(self):
        self.assertEqual(kinds([{"t": 0, "tap": [0.4, 0.4]}]), ["tap"])

    def test_two_finger_tap_is_right_click(self):
        self.assertEqual(kinds([{"t": 0, "two_finger_tap": [0.4, 0.4]}]), ["secondary"])

    def test_two_finger_drag_is_pointer_not_scroll(self):
        self.assertEqual(kinds([{"t": 0, "pointer": [[0.3, 0.5], [0.6, 0.4]]}]), ["pointer"])

    def test_pinch(self):
        self.assertEqual(kinds([{"t": 0, "pinch": [0.5, 0.5], "from": 0.05, "to": 0.2}]), ["pinch"])

    def test_three_finger_swipe(self):
        cs, keys, texts = gestures.compile_gestures([{"t": 0, "swipe3": [[0.5, 0.7], [0.5, 0.3]]}], MODEL)
        g = classify(cs, keys, texts, CSS)["gestures"]
        self.assertEqual([(x["type"], x["fingers"]) for x in g], [("swipe", 3)])

    def test_pen_tap_is_click_pen_stroke_is_ink(self):
        # Kalemle kısa dokunma = tık; kalemle çizgi = mürekkep (sürükleme)
        self.assertEqual(kinds([{"t": 0, "tap": [0.4, 0.4], "tool": "pen"}]), ["tap"])
        self.assertEqual(kinds([{"t": 0, "pen": [[0.2, 0.5], [0.6, 0.4]]}]), ["pen"])

    def test_selector_without_html_screen_is_error(self):
        with self.assertRaises(PromoError):
            gestures.compile_gestures([{"t": 0, "tap": "#x"}], MODEL)


def _browser_available():
    try:
        from promokit.cdp import find_browser
        find_browser()
        return True
    except Exception:
        return False


def compile_on(page, specs, fps=30, latency=0.0, params=None, resolve=True):
    """Senaryodaki gibi derle: seçiciler sayfada çözülür, sonra sınıflandırılır."""
    from promokit.htmlscreen import SelectorResolver, viewport
    css = viewport(page)
    res = SelectorResolver(page, css, (0, 0, 1, 1), fps, latency, params or {}) if resolve else None
    try:
        cs, keys, texts = gestures.compile_gestures(specs, MODEL, res)
    finally:
        if res is not None:
            res.close()
    tl = classify(cs, keys, texts, css, fps=fps, latency=latency)
    tl["params"] = params or {}
    return css, tl, cs


def compile_ok(test, *a, **k):
    """Olumlu testlerde çözücü hatası çökme (ERROR) değil başarısızlık (FAIL) olarak görünsün."""
    try:
        return compile_on(*a, **k)
    except PromoError as e:
        test.fail(f"seçici çözülemedi: {e}")


def open_page(page, tl, css, size=None):
    from promokit.htmlscreen import HtmlSource
    return HtmlSource(page, css, size or css, tl["fps"], tl)


def stepped(src, t, fps, shots=False):
    """Kare kare (render gibi) t'ye kadar ilerle — kaynağın bulunduğu kareden devam eder. `shots=False`:
    her karede ekran görüntüsü almadan aynı adım dizisi (görüntü durumu değiştirmez: her adım stilini
    kendisi boşaltır) — ~10× hızlı; render yolunun birebiri `shots=True` (dikiş testi)."""
    from promokit.htmlscreen import frame_index
    if shots:
        k = src.k or 0
        while k / fps <= t + 1e-9:
            src.get(k / fps)
            k += 1
        return
    if src.b is None:
        src.get(0.0)
    K = frame_index(t, fps)
    for k in range(src.k + 1, K + 1):
        src.b.eval(f"__promoSeekFrame({k})")
    if K > src.k:
        src.k, src.img = K, None


@unittest.skipUnless(_browser_available(), "Chrome/Brave yok")
class TestRuntimeInBrowser(unittest.TestCase):
    """Gerçek başsız tarayıcı: tek parmak kaydırma içeriği kaydırıyor mu, seçici doğru yeri buluyor mu."""

    def test_scroll_then_selector(self):
        d = tempfile.mkdtemp()
        page = os.path.join(d, "p.html")
        with open(page, "w") as f:
            f.write(textwrap.dedent("""<!doctype html><meta name="promo-viewport" content="400x300">
              <style>body{margin:0} .s{height:300px} .b{height:90px;border-bottom:1px solid #ccc}</style>
              <div class="s" data-scroll><div>""" + "".join(f'<div class="b" id="b{i}">{i}</div>' for i in range(20)) +
                                    "</div></div>"))
        specs = [{"t": 0.2, "scroll": [[0.5, 0.8], [0.5, 0.2]], "dur": 0.5, "ease": "linear"}, {"t": 2.0, "tap": "#b4"}]
        css, tl, cs = compile_ok(self, page, specs)
        # Parmak 0,6 × 300 = 180 px; ürün ilk 12 dp'yi kaydırmaz (TAP_SLOP) → momentumsuz taban
        slop = TAP_SLOP_DP / dp_per_css(css)
        base = 180 - slop
        src = open_page(page, tl, css)
        try:
            stepped(src, 0.45, 30)        # sürükleme ortası: içerik = parmak yolu − eşik (ilk yol Mac'e gitmez)
            mid = src.b.eval("promo.state.scroll.values().next().value.y")
            t = src.k / 30                # sayfa kare ızgarasında: 0,45 → 13/30 sn
            self.assertAlmostEqual(mid, 180 * (t - 0.2) / 0.5 - slop, delta=0.5)
            stepped(src, 1.5, 30)
            y = src.b.eval("promo.state.scroll.values().next().value.y")
            # seçici, kaymış sayfada #b4'ün görünür kutusunu bulmalı (dokunuştan bir kare önce)
            stepped(src, 1.95, 30)
            box = src.b.eval("(() => { const r = document.getElementById('b4').getBoundingClientRect();"
                             " return [r.top, r.bottom]; })()")
        finally:
            src.close()
        self.assertGreater(y, base + 8)   # bırakış hızıyla SÜZÜLME var (momentum öldürülürse y = taban)
        self.assertLess(y, base + 160)
        tap = cs[-1]
        self.assertGreaterEqual(tap.y[0] * 300, max(0, box[0]) - 1)
        self.assertLessEqual(tap.y[0] * 300, min(300, box[1]) + 1)

    def test_tablet_back_gesture_reaches_page_and_draws_os_arrow(self):
        """Tabletin geri jesti sayfaya kalkışta 'back' olarak gider (üründe Mac'e sayfa geri); jest sürerken
        kenarda OS'un ok balonu görünür, sonra söner. Kaydırma DEĞİL: kaydırılabilir içerik kımıldamaz."""
        d = tempfile.mkdtemp()
        page = os.path.join(d, "p.html")
        with open(page, "w") as f:
            f.write(textwrap.dedent("""<!doctype html><meta name="promo-viewport" content="700x460">
              <style>body{margin:0} .s{height:460px}</style>
              <div class="s" data-scroll><div style="height:2000px"></div></div>
              <script>promo.on('back', e => { document.body.dataset.back = e.side + '@' + e.t.toFixed(2); });</script>"""))
        css, tl, _ = compile_ok(self, page, [{"t": 0.5, "back": [0.0, 0.5], "dur": 0.3}], fps=30)
        probe = ("(() => { const b = document.querySelector('.promo-back'); const s = promo.state.scroll.values()"
                 ".next().value; return [document.body.dataset.back || null, b ? +b.style.opacity : null,"
                 " b ? b.getBoundingClientRect().left : null, s.y]; })()")
        src = open_page(page, tl, css)
        try:
            stepped(src, 0.7, 30)
            during = src.b.eval(probe)
            stepped(src, 1.4, 30)
            after = src.b.eval(probe)
        finally:
            src.close()
        self.assertIsNone(during[0])                     # olay KALKIŞTA, jest sürerken değil
        self.assertGreater(during[1], 0.5)               # ok balonu görünüyor
        self.assertEqual(during[2], 0)                   # sol kenara yapışık
        self.assertEqual(after[0], "left@0.80")
        self.assertEqual(after[1], 0)                    # söndü
        self.assertEqual(after[3], 0)                    # sayfa KAYMADI

    def test_rotated_container_scroll_zoom_drag_follow_finger(self):
        """Dikey sayfa: 920×1400 kap `translate(1400px,0) rotate(90deg)` ile görünüm alanını kaplar. Görüntüde
        +x sürükleme, kabın YEREL −y'sidir → içerik parmağı izlemeli (yerel eksende kaymalı)."""
        d = tempfile.mkdtemp()
        page = os.path.join(d, "rot.html")
        blocks = "".join(f'<div style="height:200px;border-bottom:1px solid #ccc">{i}</div>' for i in range(12))
        with open(page, "w") as f:
            f.write(textwrap.dedent(f"""<!doctype html><meta name="promo-viewport" content="1400x920">
              <style>body{{margin:0}} #rot{{position:absolute;left:0;top:0;width:920px;height:1400px;
                transform-origin:0 0;transform:translate(1400px,0) rotate(90deg)}}
                #sc{{position:absolute;left:0;top:0;width:920px;height:700px}}
                #z{{position:absolute;left:40px;top:760px;width:400px;height:600px;background:#eef}}
                #dr{{position:absolute;left:520px;top:1000px;width:120px;height:120px;background:#0a0}}</style>
              <div id="rot"><div id="sc" data-scroll><div>{blocks}</div></div>
                <div id="z" data-zoom="1,3"></div><div id="dr" data-drag></div></div>"""))
        P = lambda x, y: [x / 1400, y / 920]   # görüntü px → ekran-normalize
        specs = [{"t": 0.2, "scroll": [P(900, 400), P(1200, 400)], "dur": 0.5, "ease": "linear"},
                 {"t": 1.6, "pinch": P(190, 140), "from": 0.03, "to": 0.06, "dur": 0.4},
                 {"t": 2.4, "drag": [P(340, 580), P(440, 580)], "dur": 0.5}]
        css, tl, _ = compile_on(page, specs, resolve=False)
        src = open_page(page, tl, css, (700, 460))
        try:
            stepped(src, 1.2, 30)
            s = src.b.eval("(() => { const s = promo.state.scroll.values().next().value; return [s.x, s.y]; })()")
            stepped(src, 2.2, 30)
            z = src.b.eval("(() => { const z = [...promo.state.zoom.values()][0]; return [z.ox, z.oy]; })()")
            stepped(src, 3.6, 30)
            dr = src.b.eval("(() => { const d = [...promo.state.drag.values()][0]; return [d.x, d.y]; })()")
        finally:
            src.close()
        self.assertAlmostEqual(s[0], 0, delta=1e-6)
        self.assertGreater(s[1], 250)                     # +x görüntü → yerel −y: içerik yukarı (yerel) kaydı
        self.assertAlmostEqual(z[0], 25, delta=1.5)       # çimdik noktası yerel kutuda (100, 450) / (400, 600)
        self.assertAlmostEqual(z[1], 75, delta=1.5)
        self.assertAlmostEqual(dr[0], 0, delta=1.5)       # +100 görüntü x → yerel (0, −100)
        self.assertAlmostEqual(dr[1], -100, delta=3)

    def test_example_screen_reacts_to_gestures(self):
        """examples/ekran.html: kaydırma listeyi kaydırır, `tap` seçiciyle notu seçer, `type` alana yazar,
        `tap` düğmeyi açar. Seçiciler sayfadan çözülür (SelectorResolver) — örnek sayfa sözleşmeyi gösteriyor."""
        page = os.path.join(EXAMPLES, "ekran.html")
        specs = [{"t": 0.2, "scroll": [[0.17, 0.8], [0.17, 0.4]], "dur": 0.6},
                 {"t": 1.5, "tap": "#n6"}, {"t": 2.2, "tap": "#like"},
                 {"t": 2.8, "tap": "#field"}, {"t": 3.2, "type": "Merhaba", "rate": 10}]
        css, tl, _ = compile_ok(self, page, specs)
        src = open_page(page, tl, css)
        probe = ("(() => [promo.state.scroll.values().next().value.y,"
                 " document.getElementById('n6').classList.contains('selected'),"
                 " document.getElementById('like').classList.contains('on'),"
                 " document.querySelector('[data-type-target]').textContent])()")
        try:
            stepped(src, 1.2, 30)
            before = src.b.eval(probe)
            stepped(src, 4.5, 30)
            after = src.b.eval(probe)
        finally:
            src.close()
        self.assertGreater(before[0], 50)            # liste kaydı
        self.assertFalse(before[1])                  # henüz dokunulmadı
        self.assertTrue(after[1])                    # #n6 seçildi
        self.assertTrue(after[2])                    # #like açıldı
        self.assertEqual(after[3], "Merhaba")        # yazı alana yazıldı

    def test_example_screen_loads_without_file_access_flag(self):
        """Sayfalar `file://` ile ve `--allow-file-access-from-files` OLMADAN açılır (araç tarayıcıyı bu bayraksız koşar)."""
        from promokit import cdp
        self.assertNotIn("--allow-file-access-from-files", cdp.BROWSER_FLAGS)
        b = cdp.Browser(1400, 920, 0.5)
        try:
            b.open("file://" + os.path.join(EXAMPLES, "ekran.html"))
            n = b.eval("document.querySelectorAll('.note').length")
        finally:
            b.close()
        self.assertEqual(n, 14)


@unittest.skipUnless(_browser_available(), "Chrome/Brave yok")
class TestBrowserProcess(unittest.TestCase):
    """Başsız tarayıcının yaşam döngüsü: ebeveyn ölünce tarayıcı da gitmeli."""

    @unittest.skipIf(os.name == "nt", "SIGKILL/pkill POSIX'e özgü (Windows kutusunda atlanır)")
    def test_browser_dies_with_hard_killed_parent(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        child = subprocess.Popen(
            [sys.executable, "-B", "-c",
             "import sys, time; sys.path.insert(0, sys.argv[1]); from promokit.cdp import Browser; "
             "b = Browser(400, 300, 0.5); print(b.tmp, b.port, flush=True); time.sleep(60)", root],
            stdout=subprocess.PIPE, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        try:
            tmp, port = child.stdout.readline().split()
            os.kill(child.pid, signal.SIGKILL)
            child.wait(5)
            deadline = time.time() + 15
            alive = True
            while time.time() < deadline:
                try:
                    socket.create_connection(("127.0.0.1", int(port)), timeout=0.5).close()
                except OSError:
                    alive = False
                    break
                time.sleep(0.2)
            self.assertFalse(alive, "ebeveyn öldü ama tarayıcı DevTools portu hâlâ açık")
            while time.time() < deadline and os.path.exists(tmp):
                time.sleep(0.2)
            self.assertFalse(os.path.exists(tmp), "geçici profil silinmedi")
        finally:
            if child.poll() is None:
                child.kill()
            if "tmp" in locals():   # kapı ısırdıysa (ör. mutasyon) sahipsiz tarayıcı test makinesinde kalmasın
                subprocess.run(["pkill", "-f", tmp], capture_output=True)
                import shutil
                shutil.rmtree(tmp, ignore_errors=True)
