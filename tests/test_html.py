"""HTML sayfa çalışma zamanı: kare saati, zaman çizelgeli kaydırma, kare ızgarasına bağlı IntersectionObserver,
atlayarak gelmek = adım adım gelmek (paralel render parçalarının sınırında dikiş yok).

Tarayıcı gerektiren testler Chrome/Brave/Chromium yoksa ATLANIR — `run-tests.sh` atlamayı HATA sayar.
"""
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

from promokit import story  # noqa: E402
from promokit.util import PromoError  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


def _browser_available():
    try:
        from promokit.cdp import find_browser
        find_browser()
        return True
    except Exception:
        return False


def write_page(body: str, name="p.html", vp="400x300", extra_files=None) -> str:
    d = tempfile.mkdtemp()
    for fn, text in (extra_files or {}).items():
        with open(os.path.join(d, fn), "w") as f:
            f.write(text)
    page = os.path.join(d, name)
    with open(page, "w") as f:
        f.write(f'<!doctype html><meta name="promo-viewport" content="{vp}">' + textwrap.dedent(body))
    return page


def open_page(page, scroll=None, fps=30, params=None, size=None):
    """Sayfayı senaryodaki gibi aç (`scroll` = {"target", "moves"} ya da None). `size=None` → CSS boyutu (tam ölçek)."""
    from promokit.htmlscreen import HtmlSource, viewport
    css = viewport(page)
    tl = {"fps": fps, "params": params or {}, "scroll": scroll or {"target": None, "moves": []}}
    return HtmlSource(page, css, size or css, fps, tl)


def seek(src, t):
    """Kareye atla (render parçası gibi) — ekran görüntüsü alınır."""
    return src.get(t)


def step_to(src, t, fps):
    """Kare kare (render gibi) t'ye kadar — her karede ekran görüntüsü; kaynağın bulunduğu kareden devam eder."""
    from promokit.htmlscreen import frame_index
    k = src.k or 0
    last = frame_index(t, fps)
    img = None
    while k <= last:
        img = src.get(k / fps)
        k += 1
    return img


def js(src, expr):
    return src.b.eval(expr)


class TestScrollSchema(unittest.TestCase):
    """`scroll = [...]` yüklemede doğrulanır ve normalize edilir (tarayıcı gerekmez)."""

    def _load(self, scene_body: str):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "a.html"), "w") as f:
            f.write("<!doctype html><body>x")
        p = os.path.join(d, "s.toml")
        with open(p, "w") as f:
            f.write("[video]\n[[scene]]\n" + scene_body + "\n")
        return story.load(p)

    def test_moves_are_sorted_and_normalized(self):
        st = self._load('duration = 5\ntablet = { src = "a.html", scroller = "#feed", scroll = ['
                        '{ t = 3, to = "#b", offset = -40 }, { t = 0.5, y = 200, dur = 1.5, ease = "out" } ] }')
        ref = st.scenes[0].tablet
        self.assertEqual(ref.scroller, "#feed")
        self.assertEqual([m["t"] for m in ref.scroll], [0.5, 3.0])
        self.assertEqual(ref.scroll[0], {"t": 0.5, "dur": 1.5, "ease": "out", "y": 200.0})
        self.assertEqual(ref.scroll[1], {"t": 3.0, "dur": 1.0, "ease": "inout", "to": "#b", "offset": -40.0})

    def test_html_scene_takes_scroll_at_scene_level(self):
        st = self._load('type = "html"\npage = "a.html"\nduration = 4\nscroll = [ { t = 1, y = 900, dur = 2 } ]')
        self.assertEqual(st.scenes[0].scroll[0]["y"], 900.0)

    def test_overlapping_moves_are_rejected(self):
        with self.assertRaises(PromoError) as cm:
            self._load('duration = 4\ntablet = { src = "a.html", scroll = [ { t = 0, y = 5, dur = 2 }, { t = 1, y = 9 } ] }')
        self.assertIn("çakışıyor", str(cm.exception))


@unittest.skipUnless(_browser_available(), "Chrome/Brave yok")
class TestRuntimeInBrowser(unittest.TestCase):
    """Gerçek başsız tarayıcı."""

    def test_css_animation_is_pinned_to_the_frame_clock(self):
        page = write_page("""
            <style>body{margin:0} #a{height:20px;width:0;background:#000;animation:w 1s linear forwards}
            @keyframes w{to{width:100px}}</style><div id="a"></div>""")
        src = open_page(page)
        try:
            for k, want in ((0, 0), (15, 50), (30, 100)):
                seek(src, k / 30)
                self.assertAlmostEqual(js(src, "document.getElementById('a').getBoundingClientRect().width"), want, delta=0.01)
        finally:
            src.close()

    def test_frame_event_gets_scene_time_and_params(self):
        page = write_page("""
            <body><script>
              const lang = promo.params.lang;       // params are ready WHILE the page script runs
              promo.on('frame', ({t, k}) => { document.body.dataset.t = t.toFixed(4); document.body.dataset.k = k;
                                              document.body.dataset.lang = lang; });
            </script>""")
        src = open_page(page, params={"lang": "en"})
        try:
            seek(src, 0.5)
            self.assertEqual(js(src, "[document.body.dataset.t, document.body.dataset.k, document.body.dataset.lang]"),
                             ["0.5000", "15", "en"])
        finally:
            src.close()

    def test_document_scroll_follows_the_timeline(self):
        blocks = "".join(f'<div class="b" id="b{i}">{i}</div>' for i in range(30))
        page = write_page("<style>body{margin:0}.b{height:100px}</style>" + blocks)
        moves = [{"t": 0.2, "y": 300, "dur": 0.5, "ease": "linear"},
                 {"t": 1.0, "to": "#b12", "offset": -20, "dur": 0.5, "ease": "linear"}]
        src = open_page(page, {"target": None, "moves": moves})
        try:
            seek(src, 0.1)
            self.assertEqual(js(src, "scrollY"), 0)                       # henüz başlamadı
            seek(src, 0.45)                                               # kare 13 → t = 13/30
            self.assertAlmostEqual(js(src, "scrollY"), 300 * (13 / 30 - 0.2) / 0.5, delta=0.6)
            seek(src, 0.9)
            self.assertAlmostEqual(js(src, "scrollY"), 300, delta=0.6)    # bekler
            seek(src, 2.0)
            self.assertAlmostEqual(js(src, "scrollY"), 1200 - 20, delta=0.6)   # #b12'nin üstü − 20
            self.assertEqual(js(src, "promo.scrollY"), js(src, "scrollY"))
        finally:
            src.close()

    def test_target_is_clamped_to_the_scrollable_range(self):
        page = write_page("<style>body{margin:0}</style><div style='height:700px'></div>")
        src = open_page(page, {"target": None, "moves": [{"t": 0, "y": 99999, "dur": 0.5, "ease": "linear"}]})
        try:
            seek(src, 0.25)
            mid = js(src, "scrollY")
            seek(src, 1.0)
            end = js(src, "scrollY")
        finally:
            src.close()
        self.assertEqual(end, 400)                       # 700 − 300 (görünüm alanı)
        self.assertAlmostEqual(mid, 400 * (7 / 30 / 0.5), delta=1.0)   # süre, SIKIŞTIRILMIŞ hedefe göre: ölü bekleme yok

    def test_scroller_element(self):
        page = write_page("""
            <style>body{margin:0} #feed{height:300px;overflow:auto}</style>
            <div id="feed"><div style="height:1500px"></div></div>""")
        src = open_page(page, {"target": "#feed", "moves": [{"t": 0, "y": 500, "dur": 1, "ease": "linear"}]})
        try:
            seek(src, 1.0)
            self.assertEqual(js(src, "document.getElementById('feed').scrollTop"), 500)
            self.assertEqual(js(src, "scrollY"), 0)       # sayfanın kendisi kaymadı
        finally:
            src.close()

    def test_smooth_scroll_css_is_overridden(self):
        page = write_page("<style>html{scroll-behavior:smooth} body{margin:0}</style><div style='height:2000px'></div>")
        src = open_page(page, {"target": None, "moves": [{"t": 0, "y": 1000, "dur": 0.0}]})
        try:
            seek(src, 0.1)
            self.assertEqual(js(src, "scrollY"), 1000)    # CSS smooth olsaydı henüz vardığı yerde değil, yolda olurdu
        finally:
            src.close()

    def test_scroll_event_fires_synchronously_with_the_step(self):
        page = write_page("""
            <style>body{margin:0}</style><div style="height:2000px"></div>
            <script>window.addEventListener('scroll', () => { document.title = String(scrollY); });</script>""")
        src = open_page(page, {"target": None, "moves": [{"t": 0, "y": 600, "dur": 1, "ease": "linear"}]})
        try:
            seek(src, 1.0)
            self.assertEqual(js(src, "document.title"), "600")
        finally:
            src.close()

    def test_unknown_scroll_selector_fails_before_rendering(self):
        page = write_page("<div>hi</div>")
        src = open_page(page, {"target": None, "moves": [{"t": 0, "to": "#yok", "dur": 1}]})
        try:
            with self.assertRaises(PromoError) as cm:
                seek(src, 0.0)
            self.assertIn("seçici sayfada yok", str(cm.exception))
        finally:
            src.close()

    def test_intersection_observer_runs_on_the_frame_grid(self):
        """Sayfanın KENDİ IntersectionObserver'ı (görününce belir): aynı kareye atlayarak da kare kare de gelinse sonuç aynı."""
        blocks = "".join(f'<div class="r" id="r{i}" style="height:150px">{i}</div>' for i in range(12))
        page = write_page("""
            <style>body{margin:0} .r{opacity:0} .r.in{opacity:1}</style>""" + blocks + """
            <script>window.log = [];
              const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add('in');
                window.log.push([e.target.id, Math.round(promo.t * 30)]); io.unobserve(e.target); } }), { threshold: 0.5 });
              document.querySelectorAll('.r').forEach(el => io.observe(el));</script>""")
        sc = {"target": None, "moves": [{"t": 0.0, "y": 1500, "dur": 1.0, "ease": "linear"}]}
        stepped = open_page(page, sc)
        try:
            for k in range(0, 41):
                stepped.get(k / 30)
            seq = js(stepped, "window.log")
        finally:
            stepped.close()
        jumped = open_page(page, sc)
        try:
            jumped.get(40 / 30)
            fresh = js(jumped, "window.log")
        finally:
            jumped.close()
        self.assertEqual(fresh, seq)
        ids = [i for i, _ in seq]
        self.assertEqual(ids, [f"r{i}" for i in range(12)])           # hepsi, sırayla, bir kez
        self.assertEqual([k for _, k in seq][:3], sorted(k for _, k in seq)[:3])   # kaydırmayla birlikte artan kare

    def test_fresh_seek_equals_stepping(self):
        """Paralel render parçaları sayfaya DOĞRUDAN bir kareye atlayarak girer; sonuç, kare kare gelenle bayt-birebir
        aynı olmalı (tam ölçekte; kesirli ölçekte kompozitörün CSS `transform` animasyonu rasterı bir-iki kenar pikselinde
        oynayabilir — README «Sınırlar»). Örnek site: sticky menü + CSS giriş animasyonu + görününce-belir + kaydırma."""
        from PIL import ImageChops
        page = os.path.join(EXAMPLES, "site", "index.html")
        sc = {"target": None, "moves": [{"t": 0.5, "to": "#features", "offset": -64, "dur": 1.0, "ease": "inout"},
                                        {"t": 2.0, "to": "#pricing", "offset": -64, "dur": 1.0, "ease": "inout"}]}
        ks = (20, 45, 52, 60, 66, 75, 90)
        seq = {}
        s = open_page(page, sc)
        try:
            for k in range(0, 91):
                im = s.get(k / 30)
                if k in ks:
                    seq[k] = im.copy()
        finally:
            s.close()
        for k in ks:
            f = open_page(page, sc)
            try:
                im = f.get(k / 30)
            finally:
                f.close()
            self.assertIsNone(ImageChops.difference(im, seq[k]).getbbox(), f"kare {k}: atlayarak ≠ adım adım")

    def test_example_site_reveals_and_opens_faq_on_cue(self):
        page = os.path.join(EXAMPLES, "site", "index.html")
        sc = {"target": None, "moves": [{"t": 0.0, "to": "#pricing", "offset": -64, "dur": 1.0, "ease": "linear"}]}
        src = open_page(page, sc)
        try:
            seek(src, 0.2)
            early = js(src, "document.querySelectorAll('.plan.in').length")
            seek(src, 2.0)
            late = js(src, "document.querySelectorAll('.plan.in').length")
            q2_before = js(src, "document.getElementById('q2').classList.contains('open')")
            seek(src, 9.6)
            q2_after = js(src, "document.getElementById('q2').classList.contains('open')")
        finally:
            src.close()
        self.assertEqual(early, 0)                       # fiyat kartları görünür değil → belirmedi
        self.assertEqual(late, 2)                        # kaydırınca sitenin KENDİ IntersectionObserver'ı çalıştı
        self.assertFalse(q2_before)
        self.assertTrue(q2_after)                        # `promo.on('frame')` koreografisi

    def test_es_module_and_fetch_do_not_work_over_file(self):
        """README'deki kısıtın kilidi: sayfalar `file://` ile açılır → ES module ve fetch ÇALIŞMAZ (Chromium kuralı).
        Bu test yeşil kalırken bir gün çalışmaya başlarsa README'deki uyarı bayatlamıştır."""
        page = write_page("""<body><script type="module" src="m.js"></script>
            <script>fetch('d.json').then(r=>r.json()).then(()=>document.body.dataset.fetch='ok')
            .catch(()=>document.body.dataset.fetch='err')</script></body>""",
                          extra_files={"m.js": "document.body.dataset.mod='ok';", "d.json": '{"a":1}'})
        from promokit import cdp
        b = cdp.Browser(400, 300, 0.5)
        try:
            b.open("file://" + page)
            time.sleep(0.8)
            got = b.eval("[document.body.dataset.mod || null, document.body.dataset.fetch || null]")
        finally:
            b.close()
        self.assertIsNone(got[0])
        self.assertNotEqual(got[1], "ok")

    def test_pages_load_without_file_access_flag(self):
        """Sayfalar `file://` ile ve `--allow-file-access-from-files` OLMADAN açılır (araç tarayıcıyı bu bayraksız koşar)."""
        from promokit import cdp
        self.assertNotIn("--allow-file-access-from-files", cdp.BROWSER_FLAGS)
        b = cdp.Browser(1470, 956, 0.5)
        try:
            b.open("file://" + os.path.join(EXAMPLES, "site", "index.html"))
            n = b.eval("[document.querySelectorAll('.card').length, getComputedStyle(document.querySelector('.hero h1')).fontSize]")
        finally:
            b.close()
        self.assertEqual(n[0], 3)                        # site.css + site.js ayrı dosyalardan yüklendi


@unittest.skipUnless(_browser_available(), "Chrome/Brave yok")
class TestHtmlScenes(unittest.TestCase):
    """Tam ekran `type = "html"` sahnesi ve cihaz ekranındaki HTML, senaryodan uçtan uca."""

    def test_full_frame_scene_gets_params_and_scroll(self):
        from promokit.render import Timeline
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "a.html"), "w") as f:
            f.write("<!doctype html><style>body{margin:0}.t{height:3000px;background:linear-gradient(#fff,#08f)}"
                    "h1{position:fixed;top:0;margin:0}</style><h1 id='h'></h1><div class='t'></div>"
                    "<script>document.getElementById('h').textContent = promo.params.word;</script>")
        p = os.path.join(d, "s.toml")
        with open(p, "w") as f:
            f.write('[video]\nsize = 360\nfps = 30\n[[scene]]\ntype = "html"\npage = "a.html"\nduration = 2\n'
                    'params = { word = "Hello" }\nscroll = [ { t = 0.2, y = 400, dur = 1 } ]\n')
        st = story.load(p)
        tl = Timeline(st)
        try:
            top, bottom = tl.frame(0.0), tl.frame(1.6)
            rt = tl._rt(st.scenes[0])
            word = rt.page.b.eval("document.getElementById('h').textContent")
            scrolled = rt.page.b.eval("scrollY")
        finally:
            tl.close()
        self.assertEqual(word, "Hello")
        self.assertGreater(scrolled, 300)
        self.assertNotEqual(top.getpixel((10, top.size[1] - 5)), bottom.getpixel((10, bottom.size[1] - 5)))

    def test_device_screen_scrolls_inside_a_laptop_frame(self):
        from promokit.render import Timeline
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "a.html"), "w") as f:
            f.write('<!doctype html><meta name="promo-viewport" content="1470x956"><style>body{margin:0}'
                    '.t{height:3000px;background:linear-gradient(#fff,#d00)}</style><div class="t"></div>')
        p = os.path.join(d, "s.toml")
        with open(p, "w") as f:
            f.write('[video]\nsize = 360\nfps = 30\n[[scene]]\nlayout = "laptop"\nduration = 2\n'
                    'laptop = { src = "a.html", scroll = [ { t = 0, y = 2000, dur = 1.5 } ] }\n')
        st = story.load(p)
        tl = Timeline(st)
        try:
            a, b = tl.frame(0.05), tl.frame(1.8)
            scrolled = tl._rt(st.scenes[0]).devices["laptop"].source.b.eval("scrollY")
        finally:
            tl.close()
        self.assertGreater(scrolled, 1900)
        self.assertNotEqual(a.tobytes(), b.tobytes())


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


if __name__ == "__main__":
    unittest.main()
