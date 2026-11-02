"""capture.py / sync.py yaşam döngüsü — CİHAZSIZ: sahte adb aygıtı, sahte sck-record (Python betiği).

Kilitlenen kurallar (2026-10-19 incelemesi; her biri sahada ölçülmüş/kaynaktan çıkarılmış bir arıza):
- ekran seçimi TEK kural: `N` = sıra no, `id:N` = ekran kimliği; verilmezse ANA ekran
- adb çocukları kendi oturumunda + stdin'siz (Ctrl-C ikinci sinyal olup MP4'ü öldürüyordu; adb shell
  Enter tuşunu yutuyordu)
- screenrecord: süre sınırı açık, kendi PID'ine SIGINT, uzak süreç bitmeden çekme yok, cihazdaki
  kopya yalnız ffprobe'dan geçen bir çekimden sonra silinir
- sck-record: stderr boşaltılır, erken ölüm hemen yakalanır, yazıcı hatası başarı sayılmaz,
  getevent her yolda durdurulur, take.json dokunuş çözümünden ÖNCE yazılır
- `touches --auto` tepki gecikmesi çekim kaynağına göre
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promokit import capture as cap  # noqa: E402
from promokit import sync  # noqa: E402
from promokit.touches import Contact, TouchLog  # noqa: E402
from promokit.util import PromoError  # noqa: E402


class _Case(unittest.TestCase):
    def mkdtemp(self):
        d = tempfile.mkdtemp(prefix="promo-test-")
        self.addCleanup(shutil.rmtree, d, True)
        return d


def disp(index, did, name, main=False, vendor=0x0610, model=0xA050, serial=1):
    return {"event": "display", "index": index, "id": did, "name": name, "main": main, "builtin": main,
            "points": [1400, 920], "pixels": [2800, 1840], "hz": 60.0, "vendor": vendor, "model": model,
            "serial": serial}


DISPLAYS = [disp(1, 1, "Yerleşik Retina Ekranı", main=True),
            disp(2, 3, "LG UltraFine", vendor=0x1E6D, model=0x5B71),
            disp(3, 2, "Örnek Harici Monitör", serial=0x1234ABCD)]


class TestResolveDisplay(_Case):
    def pick(self, spec, ds=DISPLAYS, **kw):
        try:
            return cap.resolve_display(spec, ds, **kw)["name"]
        except PromoError as e:  # kural yanlışsa KIRMIZI bir iddia olsun, çökme değil
            self.fail(f"resolve_display({spec!r}) hata verdi: {e}")

    def test_default_is_the_main_display(self):
        self.assertEqual(self.pick(None), "Yerleşik Retina Ekranı")

    def test_default_without_main_flag_is_the_first(self):
        no_main = [dict(d, main=False) for d in DISPLAYS[1:]]
        self.assertEqual(self.pick(None, no_main), "LG UltraFine")

    def test_number_is_index_and_id_needs_prefix(self):
        self.assertEqual(self.pick("2"), "LG UltraFine")  # sıra 2 (id'si 3)
        self.assertEqual(self.pick("id:2"), "Örnek Harici Monitör")  # id 2 (sırası 3)
        self.assertEqual(self.pick("harici"), "Örnek Harici Monitör")

    def test_unknown_display_is_an_error(self):
        for spec in ("9", "id:99", "yok-böyle-ekran"):
            with self.assertRaises(PromoError, msg=spec):
                cap.resolve_display(spec, DISPLAYS)

    def test_bad_id_is_an_error(self):
        with self.assertRaises(PromoError):
            cap.resolve_display("id:abc", DISPLAYS)

    def test_no_displays_is_an_error(self):
        with self.assertRaises(PromoError):
            cap.resolve_display(None, [])

    def test_shot_uses_the_same_rule(self):
        tmp = self.mkdtemp()

        def fake_run(argv, **kw):
            open(argv[-1], "wb").close()
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(cap, "WORK", tmp), mock.patch.object(cap, "mac_displays", return_value=DISPLAYS), \
                mock.patch.object(cap.subprocess, "run", side_effect=fake_run) as run, \
                mock.patch("builtins.print"):
            cap.capture_shot(argparse.Namespace(target="mac", name="x", display="2", serial=None))
        self.assertIn("-D2", run.call_args[0][0])  # `capture mac --display 2` ile AYNI ekran (sıra 2)


# ── sahte sck-record ─────────────────────────────────────────────────────────────────────────────
FAKE_HELPER = r'''
import json, os, sys, time
mode = os.environ.get("FAKE_MODE", "ok")
def emit(**o):
    print(json.dumps(o), flush=True)
if mode == "die":
    sys.stderr.write("Fatal error: SCK yok\n"); sys.stderr.flush(); sys.exit(3)
if mode == "noisy":  # boru (~64 KB) okunmazsa yazım burada KİLİTLENİR
    sys.stderr.write("x" * 300000); sys.stderr.flush()
emit(event="started", id=2, name="T", width=10, height=10, fps=60, codec="hevc", mbps=20.0)
emit(event="first_frame", wall=time.time(), width=10, height=10)
for line in sys.stdin:
    if line.strip() == "q":
        break
st = {"event": "stopped", "frames": 5, "dropped": 0, "duration": 1.0, "status": 2}
if mode == "writer_error":
    st.update(writer_error="Disk dolu", status=3)
emit(**st)
sys.exit(int(os.environ.get("FAKE_RC", "0")))
'''


def write_helper(d):
    p = os.path.join(d, "fake-sck-record")
    with open(p, "w") as f:
        f.write(f"#!{sys.executable}\n" + FAKE_HELPER)
    os.chmod(p, 0o755)
    return p


class TestHelperRun(_Case):
    def setUp(self):
        self.script = write_helper(self.mkdtemp())

    def test_death_before_started_fails_fast_with_stderr(self):
        t0 = time.monotonic()
        with mock.patch.dict(os.environ, {"FAKE_MODE": "die"}), mock.patch.object(cap, "log"):
            run = cap.HelperRun([self.script])
            with self.assertRaises(PromoError) as cm:
                run.wait_started(8)
        self.assertLess(time.monotonic() - t0, 4)  # 20 sn'lik "zaman aşımı"nı beklemedi
        self.assertIn("SCK yok", str(cm.exception))
        self.assertIn("3", str(cm.exception))

    def test_stderr_is_drained(self):
        with mock.patch.dict(os.environ, {"FAKE_MODE": "noisy"}):
            run = cap.HelperRun([self.script])
            try:
                st = run.wait_started(5)
            except PromoError as e:
                run.kill()
                self.fail(f"stderr boşaltılmadı, yardımcı kilitlendi: {e}")
            self.assertEqual(st["event"], "started")
            self.assertEqual(run.stop(10), 0)


class FakeGcap:
    def __init__(self, *a, **kw):
        self.stopped = 0

    def stop(self):
        self.stopped += 1


META = {"rotation": 0, "model": "PCE-W29", "manufacturer": "HUAWEI", "wm_size": "Physical size: 2800x1840",
        "rotation_source": "dumpsys"}


class TestCaptureMac(_Case):
    def setUp(self):
        self.tmp = self.mkdtemp()
        helper = write_helper(self.tmp)
        for p in (mock.patch.object(cap, "WORK", self.tmp), mock.patch.object(cap, "ensure_helper", return_value=helper),
                  mock.patch.object(cap, "mac_displays", return_value=DISPLAYS),
                  mock.patch.object(cap, "_wait_stop", lambda s: None),
                  mock.patch.object(cap, "pick_serial", return_value="S1"),
                  mock.patch.object(cap, "device_meta", return_value=dict(META))):
            p.start()
            self.addCleanup(p.stop)
        self.log = mock.patch.object(cap, "log").start()
        self.addCleanup(mock.patch.stopall)
        self.gcap = FakeGcap()
        mock.patch.object(cap, "GeteventCapture", return_value=self.gcap).start()

    def args(self, **kw):
        a = dict(name="t1", display=None, fps=60, codec="hevc", mbps=None, max_width=None, no_cursor=False,
                 touches=True, serial=None, seconds=None)
        a.update(kw)
        return argparse.Namespace(**a)

    def take(self):
        p = os.path.join(self.tmp, "takes", "t1", "take.json")
        self.assertTrue(os.path.exists(p), "take.json yazılmadı")
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def capture(self, **kw):
        try:
            cap.capture_mac(self.args(**kw))
        except ValueError as e:  # dokunuş çözümü hatası çekimi düşürmemeli
            self.fail(f"çözüm hatası kayda sızdı: {e}")

    def test_getevent_stopped_when_helper_dies(self):
        with mock.patch.dict(os.environ, {"FAKE_MODE": "die"}):
            with self.assertRaises(PromoError):
                cap.capture_mac(self.args())
        self.assertEqual(self.gcap.stopped, 1)

    def test_helper_records_main_display_by_id(self):
        seen = []
        real = cap.HelperRun

        def spy(cmd, *a, **kw):
            seen.append(cmd)
            return real(cmd, *a, **kw)

        with mock.patch.object(cap, "HelperRun", side_effect=spy), \
                mock.patch.object(cap, "_video_ok", return_value=(True, "")), \
                mock.patch.object(cap, "build_touches", return_value=TouchLog([], [], {})):
            cap.capture_mac(self.args())
        cmd = seen[0]
        self.assertEqual(cmd[cmd.index("--display") + 1], "id:1")  # ana ekran

    def test_take_json_written_before_touch_decode(self):
        with mock.patch.object(cap, "_video_ok", return_value=(True, "")), \
                mock.patch.object(cap, "build_touches", side_effect=ValueError("MT_TOOL_PEN")):
            self.capture()  # ayrıştırıcı hatası çekimi YOK ETMEZ
        take = self.take()
        self.assertIn("first_frame_wall", take)  # senkron çapası diskte
        self.assertNotIn("touches", take)
        self.assertEqual(self.gcap.stopped, 1)

    def test_writer_error_is_not_success(self):
        with mock.patch.dict(os.environ, {"FAKE_MODE": "writer_error"}), \
                mock.patch.object(cap, "_video_ok", return_value=(False, "moov atom not found")):
            with self.assertRaises(PromoError) as cm:
                cap.capture_mac(self.args())
        self.assertIn("Disk dolu", str(cm.exception))

    def test_unverifiable_recording_still_writes_take(self):
        # ffprobe çalışmadı (None): iyi bir kayıt "bozuk" sayılıp senkron çapası (take.json) kaybolmamalı
        with mock.patch.object(cap, "_video_ok", return_value=(None, "ffprobe bulunamadı")), \
                mock.patch.object(cap, "build_touches", return_value=TouchLog([], [], {})):
            try:
                cap.capture_mac(self.args())
            except PromoError as e:
                self.fail(f"doğrulanamayan kayıt hata sayıldı: {e}")
        self.assertIn("first_frame_wall", self.take())

    def test_writer_error_with_readable_file_warns(self):
        with mock.patch.dict(os.environ, {"FAKE_MODE": "writer_error"}), \
                mock.patch.object(cap, "_video_ok", return_value=(True, "")), \
                mock.patch.object(cap, "build_touches", return_value=TouchLog([], [], {})):
            cap.capture_mac(self.args())
        self.assertTrue(any("Disk dolu" in str(c) for c in self.log.call_args_list))


# ── sahte adb aygıtı (tablet) ────────────────────────────────────────────────────────────────────
class FakeProc:
    def __init__(self, lines, rc=0, exited=False):
        self.rc = rc
        self._exit = threading.Event()
        if exited:
            self._exit.set()
        self.stdout = self._gen(list(lines))

    def _gen(self, lines):
        for ln in lines:
            yield ln + "\n"
        self._exit.wait(10)

    def poll(self):
        return self.rc if self._exit.is_set() else None

    def wait(self, timeout=None):
        if not self._exit.wait(timeout):
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.rc

    def kill(self):
        self._exit.set()

    def finish(self):
        self._exit.set()


SR_OK = ["CZPID 4242", "Display is 2800x1840 @120.00fps (orientation=ROTATION_0), layerStack=0",
         "Configuring recorder for 2800x1840 video/avc at 40.00Mbps", "Content area is 2800x1840 at offset x=0 y=0"]


class FakeDev:
    serial = "S1"

    def __init__(self, procs, alive=2, pull_ok=True):
        self.calls, self.procs, self.alive, self.pull_ok, self.cur = [], list(procs), alive, pull_ok, None

    def spawn_shell(self, cmd):
        self.calls.append(("spawn", cmd))
        self.cur = self.procs.pop(0)
        return self.cur

    def shell(self, cmd, timeout=30):
        self.calls.append(("shell", cmd))
        if cmd.startswith(("kill -INT", "pkill -INT")):
            self.cur.finish()  # uzak süreç moov'u yazıp çıkar → yerel adb de çıkar
        if "echo alive" in cmd:
            if self.alive:
                self.alive -= 1
                return "alive\n"
            return "gone\n"
        return ""

    def pull(self, remote, local):
        self.calls.append(("pull", remote))
        if not self.pull_ok:
            return False, "adb: error: failed to copy"
        open(local, "wb").close()
        return True, ""


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.005)


class TestScreenRecord(_Case):
    def rec(self, dev, seconds=None, ok=(True, "")):
        return cap.ScreenRecord(dev, "/sdcard/Download/czpromo-x.mp4", 40, None, seconds,
                                video_ok=lambda p: ok, clock=FakeClock())

    def test_command_has_time_limit_and_exec_pid(self):
        r = self.rec(FakeDev([]))
        cmd = r.command(0)
        self.assertTrue(cmd.startswith("echo CZPID $$; exec screenrecord "))
        self.assertIn("--time-limit 0", cmd)
        self.assertEqual(r.wanted_limit(), 0)  # Enter'a kadar → sınırsız (14+)
        self.assertEqual(self.rec(FakeDev([]), seconds=20).wanted_limit(), 25)

    def test_old_android_rejects_unlimited_and_falls_back_to_180(self):
        bad = FakeProc(["CZPID 1", "Time limit 0s outside acceptable range [1,180]"], rc=2, exited=True)
        dev = FakeDev([bad, FakeProc(SR_OK)])
        r = self.rec(dev)
        with mock.patch.object(cap, "log"):
            try:
                lim = r.start(settle=0)
            except PromoError as e:
                self.fail(f"180 sn'ye düşmedi: {e}")
        self.assertEqual(lim, 180)
        self.assertIn("--time-limit 180", dev.calls[-1][1])
        r.stop(timeout=2)

    def test_stop_signals_own_pid_waits_for_exit_then_pull_then_rm(self):
        dev = FakeDev([FakeProc(SR_OK)], alive=2)
        r = self.rec(dev)
        r.start(settle=0)
        wait_for(lambda: r.pid == 4242)
        r.stop(timeout=5)
        r.fetch(os.path.join(self.mkdtemp(), "screen.mp4"))
        seq = [c[1] if c[0] == "shell" else c[0] for c in dev.calls]
        self.assertIn("kill -INT 4242", seq)  # YALNIZ kendi süreci — cihazdaki başka screenrecord değil
        self.assertNotIn("pkill -INT screenrecord", seq)
        kill = seq.index("kill -INT 4242")
        pull = seq.index("pull")
        probes = [i for i, s in enumerate(seq) if "echo alive" in s]
        self.assertTrue(probes and kill < probes[0] and probes[-1] < pull)  # bitişi gördü, SONRA çekti
        self.assertEqual(len(probes), 3)  # alive, alive, gone
        self.assertEqual(seq[-1], "rm -f /sdcard/Download/czpromo-x.mp4")  # en son ve yalnız bir kez
        self.assertEqual(sum(1 for s in seq if str(s).startswith("rm ")), 1)

    def test_unreadable_pull_keeps_device_copy(self):
        dev = FakeDev([FakeProc(SR_OK)], alive=0)
        r = self.rec(dev, ok=(False, "moov atom not found"))
        r.start(settle=0)
        r.stop(timeout=2)
        with self.assertRaises(PromoError) as cm:
            r.fetch(os.path.join(self.mkdtemp(), "screen.mp4"))
        self.assertIn("SİLİNMEDİ", str(cm.exception))
        self.assertFalse(any(str(c[1]).startswith("rm ") for c in dev.calls if c[0] == "shell"))

    def test_unverifiable_pull_keeps_device_copy_but_succeeds(self):
        # ffprobe yok/çalışmadı (None) ≠ bozuk (False): çekim kullanılır, cihazdaki kopya silinmez
        dev = FakeDev([FakeProc(SR_OK)], alive=0)
        r = self.rec(dev, ok=(None, "ffprobe bulunamadı"))
        r.start(settle=0)
        r.stop(timeout=2)
        with mock.patch.object(cap, "log"):
            try:
                r.fetch(os.path.join(self.mkdtemp(), "screen.mp4"))
            except PromoError as e:
                self.fail(f"doğrulanamayan çekim hata sayıldı: {e}")
        self.assertFalse(any(str(c[1]).startswith("rm ") for c in dev.calls if c[0] == "shell"))

    def test_failed_pull_keeps_device_copy(self):
        dev = FakeDev([FakeProc(SR_OK)], alive=0, pull_ok=False)
        r = self.rec(dev)
        r.start(settle=0)
        r.stop(timeout=2)
        with self.assertRaises(PromoError):
            r.fetch(os.path.join(self.mkdtemp(), "screen.mp4"))
        self.assertFalse(any(str(c[1]).startswith("rm ") for c in dev.calls if c[0] == "shell"))


class TestAdbChildren(_Case):
    """Uzun süreli adb çocukları kendi oturumunda (Ctrl-C ulaşmaz) ve stdin'siz (Enter'ı yutmaz)."""

    def test_screenrecord_spawn(self):
        with mock.patch.object(cap.subprocess, "Popen") as P, mock.patch.object(cap, "_adb", return_value="adb"):
            cap.AdbDevice("S1").spawn_shell("echo hi")
        kw = P.call_args.kwargs
        self.assertTrue(kw.get("start_new_session"))
        self.assertIs(kw.get("stdin"), subprocess.DEVNULL)

    def test_getevent_spawn(self):
        with mock.patch.object(cap.subprocess, "Popen") as P, mock.patch.object(cap, "_adb", return_value="adb"):
            P.return_value.stdout = io.BytesIO(b"")
            P.return_value.poll.return_value = 0
            g = cap.GeteventCapture("S1", os.path.join(self.mkdtemp(), "getevent.log"))
            g.stop()
        self.assertTrue(P.call_args.kwargs.get("start_new_session"))
        self.assertIn("-tt", P.call_args.args[0])

    def test_sck_helper_spawn(self):
        with mock.patch.object(cap.subprocess, "Popen") as P:
            P.return_value.stdout = io.StringIO("")
            P.return_value.stderr = io.StringIO("")
            cap.HelperRun(["sck-record"])
        self.assertTrue(P.call_args.kwargs.get("start_new_session"))


class TestCaptureTablet(_Case):
    def setUp(self):
        self.tmp = self.mkdtemp()
        self.gcap = FakeGcap()
        for p in (mock.patch.object(cap, "WORK", self.tmp), mock.patch.object(cap, "_wait_stop", lambda s: None),
                  mock.patch.object(cap, "pick_serial", return_value="S1"),
                  mock.patch.object(cap, "device_meta", return_value=dict(META)),
                  mock.patch.object(cap, "GeteventCapture", return_value=self.gcap),
                  mock.patch.object(cap, "SR_SETTLE", 0), mock.patch.object(cap, "log")):
            p.start()
            self.addCleanup(p.stop)

    def args(self):
        return argparse.Namespace(name="t2", serial=None, mbps=40.0, size=None, touches=True, seconds=None)

    def test_getevent_stopped_when_screenrecord_fails(self):
        bad = FakeProc(["CZPID 9", "ERROR: unable to configure video/avc codec at 2800x1840 (err=-38)"], rc=1,
                       exited=True)
        with mock.patch.object(cap, "AdbDevice", return_value=FakeDev([bad], alive=0)):
            with self.assertRaises(PromoError):
                cap.capture_tablet(self.args())
        self.assertEqual(self.gcap.stopped, 1)

    def test_take_json_written_before_touch_decode(self):
        dev = FakeDev([FakeProc(SR_OK)], alive=0)
        with mock.patch.object(cap, "AdbDevice", return_value=dev), \
                mock.patch.object(cap, "_video_ok", return_value=(True, "")), \
                mock.patch.object(cap, "build_touches", side_effect=ValueError("MT_TOOL_PEN")):
            try:
                cap.capture_tablet(self.args())
            except ValueError as e:
                self.fail(f"çözüm hatası kayda sızdı: {e}")
        p = os.path.join(self.tmp, "takes", "t2", "take.json")
        self.assertTrue(os.path.exists(p), "take.json yazılmadı")
        with open(p, encoding="utf-8") as f:
            take = json.load(f)
        self.assertIn("first_frame_wall", take)
        self.assertEqual(self.gcap.stopped, 1)


class TestTouchesRegenerate(_Case):
    """Kayıt anında çözüm başarısızsa take.json'da `touches` yok ama ham günlük var → yeniden üretilebilmeli."""

    def test_regenerates_from_raw_log_after_capture_time_failure(self):
        d = self.mkdtemp()
        with open(os.path.join(d, "caps.txt"), "w") as f:
            f.write('add device 2: /dev/input/event4\n  name:     "ts"\n  events:\n'
                    "    KEY (0001): BTN_TOUCH\n"
                    "    ABS (0003): ABS_MT_SLOT           : value 0, min 0, max 9, fuzz 0, flat 0, resolution 0\n"
                    "                ABS_MT_POSITION_X     : value 0, min 0, max 1000, fuzz 0, flat 0, resolution 0\n"
                    "                ABS_MT_POSITION_Y     : value 0, min 0, max 1000, fuzz 0, flat 0, resolution 0\n"
                    "                ABS_MT_TRACKING_ID    : value 0, min 0, max 65535, fuzz 0, flat 0, resolution 0\n"
                    "  input props:\n    INPUT_PROP_DIRECT\n")
        ev = [("ABS_MT_TRACKING_ID", "00000001"), ("ABS_MT_POSITION_X", "000001f4"),
              ("ABS_MT_POSITION_Y", "000001f4"), ("SYN_REPORT", "00000000")]
        with open(os.path.join(d, "getevent.log"), "w") as f:
            for i, (code, val) in enumerate(ev + [("ABS_MT_TRACKING_ID", "ffffffff"), ("SYN_REPORT", "00000000")]):
                t = 50.0 + (0.1 if i >= len(ev) else 0.0)
                typ = "EV_SYN" if code.startswith("SYN") else "EV_ABS"
                f.write(f"{1000 + t:.6f} [{int(t):8d}.{round((t % 1) * 1e6):06d}] /dev/input/event4: "
                        f"{typ:<12} {code:<20} {val}\n")
        with open(os.path.join(d, "take.json"), "w", encoding="utf-8") as f:
            json.dump({"screen": "screen.mov", "source": "mac", "first_frame_wall": 1000.0, "rotation": 0}, f)
        a = argparse.Namespace(take=d, plot=None, at=None, auto=False, write=False, offset=None, rotation=0,
                               flip=None, keys=None)
        with mock.patch.object(sync, "log"), mock.patch.object(cap, "log"):
            try:
                sync.run(a)
            except Exception as e:  # noqa: BLE001 — her hata bir İDDİA olsun (mutasyon ölçümü)
                self.fail(f"ham günlükten üretilmedi: {type(e).__name__}: {e}")
        with open(os.path.join(d, "take.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f).get("touches"), "touches.json")
        self.assertEqual(len(TouchLog.load(os.path.join(d, "touches.json")).contacts), 1)


class TestAutoSyncLatency(_Case):
    """Mac çekiminde tepki yalnız giriş yolu (tablet→Mac); tablet çekiminde tam tur görünür."""

    def test_latency_follows_take_source(self):
        self.assertLess(sync.reaction_latency({"source": "mac"}), sync.reaction_latency({"source": "tablet"}))

    def test_offset_subtracts_source_latency(self):
        fps, n = 60.0, 600
        a, m = [0.0] * n, [0.0] * n
        for k in range(200, 230):
            a[k] = 1.0  # parmak 200–229. karelerde hareket ediyor
        for k in range(206, 236):
            m[k] = 5.0  # ekran 6 kare (0,1 sn) sonra
        for src in ("mac", "tablet"):
            lat = sync.reaction_latency({"source": src})
            off, _, _ = sync.offset_from_signals(m, a, fps, latency=lat)
            self.assertAlmostEqual(off, 0.1 - lat, places=3)

    def test_touches_auto_uses_take_source(self):
        d = self.mkdtemp()
        TouchLog([Contact([0.0, 0.5], [0.1, 0.5], [0.1, 0.5])], [], {}).save(os.path.join(d, "touches.json"))
        with open(os.path.join(d, "take.json"), "w", encoding="utf-8") as f:
            json.dump({"screen": "screen.mov", "source": "mac", "touches": "touches.json"}, f)
        a = argparse.Namespace(take=d, plot=None, at=None, auto=True, write=False, offset=None, rotation=None,
                               flip=None, keys=None)
        with mock.patch.object(sync, "estimate_offset", return_value=(0.0, 2.0, 0.5)) as est, \
                mock.patch.object(sync, "log"):
            sync.run(a)
        self.assertAlmostEqual(est.call_args.kwargs.get("latency", sync.SYSTEM_LATENCY),
                               sync.reaction_latency({"source": "mac"}))


if __name__ == "__main__":
    unittest.main()
