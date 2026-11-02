"""getevent ayrıştırıcı: yetenekler, protokol-B çok noktalı temas, yön, saat ofseti, tuşlar."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promokit import getevent as ge  # noqa: E402

CAPS = """add device 1: /dev/input/event5
  name:     "gpio-keys"
  events:
    KEY (0001): KEY_VOLUMEDOWN        KEY_VOLUMEUP          KEY_POWER
  input props:
    <none>
add device 2: /dev/input/event4
  name:     "hisi_touchscreen"
  events:
    KEY (0001): BTN_TOUCH             BTN_TOOL_FINGER
    ABS (0003): ABS_MT_SLOT           : value 0, min 0, max 9, fuzz 0, flat 0, resolution 0
                ABS_MT_TOUCH_MAJOR    : value 0, min 0, max 255, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_X     : value 0, min 0, max 1000, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_Y     : value 0, min 0, max 2000, fuzz 0, flat 0, resolution 0
                ABS_MT_TRACKING_ID    : value 0, min 0, max 65535, fuzz 0, flat 0, resolution 0
                ABS_MT_PRESSURE       : value 0, min 0, max 255, fuzz 0, flat 0, resolution 0
  input props:
    INPUT_PROP_DIRECT
add device 3: /dev/input/event7
  name:     "Huawei Smart Keyboard"
  events:
    KEY (0001): KEY_ESC               KEY_1                 KEY_A
                KEY_Z                 KEY_SPACE             KEY_TAB
                KEY_LEFTMETA          KEY_LEFTCTRL
  input props:
    <none>
"""

DEV = "/dev/input/event4"
KB = "/dev/input/event7"


def L(host, dev_t, dev, typ, code, val):
    sec = int(dev_t)
    usec = int(round((dev_t - sec) * 1e6))
    return f"{host:.6f} [{sec:8d}.{usec:06d}] {dev}: {typ:<12} {code:<20} {val}"


def two_finger_log(base_host=1000.0, base_dev=50.0, latency=0.004):
    """İki parmak: 0. yuva (100,200)→(100,400), 1. yuva 20 ms sonra (300,200)→(300,400)."""
    lines = ["add device 2: /dev/input/event4", '  name:     "hisi_touchscreen"']
    t = base_dev
    def emit(dt, typ, code, val, extra_lat=0.0):
        lines.append(L(base_host + (t + dt - base_dev) + latency + extra_lat, t + dt, DEV, typ, code, val))
    emit(0.0, "EV_ABS", "ABS_MT_SLOT", "00000000")
    emit(0.0, "EV_ABS", "ABS_MT_TRACKING_ID", "00000011")
    emit(0.0, "EV_ABS", "ABS_MT_POSITION_X", f"{100:08x}")
    emit(0.0, "EV_ABS", "ABS_MT_POSITION_Y", f"{200:08x}")
    emit(0.0, "EV_KEY", "BTN_TOUCH", "DOWN")
    emit(0.0, "EV_SYN", "SYN_REPORT", "00000000", extra_lat=0.05)  # tamponlanmış geç varış
    emit(0.02, "EV_ABS", "ABS_MT_SLOT", "00000001")
    emit(0.02, "EV_ABS", "ABS_MT_TRACKING_ID", "00000012")
    emit(0.02, "EV_ABS", "ABS_MT_POSITION_X", f"{300:08x}")
    emit(0.02, "EV_ABS", "ABS_MT_POSITION_Y", f"{200:08x}")
    emit(0.02, "EV_SYN", "SYN_REPORT", "00000000")
    for k in range(1, 6):
        dt = 0.02 + k * 0.03
        emit(dt, "EV_ABS", "ABS_MT_SLOT", "00000000")
        emit(dt, "EV_ABS", "ABS_MT_POSITION_Y", f"{200 + k * 40:08x}")
        emit(dt, "EV_ABS", "ABS_MT_SLOT", "00000001")
        emit(dt, "EV_ABS", "ABS_MT_POSITION_Y", f"{200 + k * 40:08x}")
        emit(dt, "EV_SYN", "SYN_REPORT", "00000000")
    end = 0.02 + 6 * 0.03
    emit(end, "EV_ABS", "ABS_MT_SLOT", "00000000")
    emit(end, "EV_ABS", "ABS_MT_TRACKING_ID", "ffffffff")
    emit(end, "EV_ABS", "ABS_MT_SLOT", "00000001")
    emit(end, "EV_ABS", "ABS_MT_TRACKING_ID", "ffffffff")
    emit(end, "EV_KEY", "BTN_TOUCH", "UP")
    emit(end, "EV_SYN", "SYN_REPORT", "00000000")
    return lines


class TestCaps(unittest.TestCase):
    def test_kinds_and_ranges(self):
        caps = ge.parse_caps(CAPS)
        self.assertEqual(caps[DEV].kind(), "touchscreen")
        self.assertEqual(caps[DEV].abs["ABS_MT_POSITION_X"], (0, 1000))
        self.assertEqual(caps[DEV].abs["ABS_MT_POSITION_Y"], (0, 2000))
        self.assertEqual(caps[KB].kind(), "keyboard")
        self.assertEqual(caps["/dev/input/event5"].kind(), "other")


class TestOrientation(unittest.TestCase):
    def test_android_inputreader_rotations(self):
        # TouchInputMapper::rotateAndScale ile birebir
        self.assertEqual(ge.orient(0.1, 0.2, 0), (0.1, 0.2))
        self.assertEqual(ge.orient(0.1, 0.2, 1), (0.2, 0.9))
        u, v = ge.orient(0.1, 0.2, 2)
        self.assertAlmostEqual(u, 0.9)
        self.assertAlmostEqual(v, 0.8)
        u, v = ge.orient(0.1, 0.2, 3)
        self.assertAlmostEqual(u, 0.8)
        self.assertAlmostEqual(v, 0.1)

    def test_parse_orientation_variants(self):
        self.assertEqual(ge.parse_orientation(
            "  Viewport INTERNAL: displayId=0, uniqueId=local:1, port=0, orientation=1, logicalFrame=[0, 0, 2800, 1840]"), 1)
        self.assertEqual(ge.parse_orientation(
            "Viewport INTERNAL: displayId=0, orientation=ROTATION_270, logicalFrame=[0, 0, 1, 1]"), 3)
        self.assertEqual(ge.parse_orientation("    SurfaceOrientation: 2"), 2)
        self.assertEqual(ge.parse_orientation("", "mCurrentRotation=ROTATION_90"), 1)
        self.assertIsNone(ge.parse_orientation("nothing here"))


class TestDecode(unittest.TestCase):
    def setUp(self):
        self.caps = ge.parse_caps(CAPS)
        self.events = ge.parse_events(two_finger_log())

    def test_clock_offset_uses_minimum_latency(self):
        off = ge.clock_offset(self.events)
        # gerçek ofset 950 sn + 4 ms gecikme; 50 ms geç varan satır minimumu bozmamalı
        self.assertAlmostEqual(off, 950.004, places=5)

    def test_two_contacts_normalized(self):
        cs, keys = ge.decode(self.events, self.caps, rotation=0, t_zero=0.0, offset=0.0)
        self.assertEqual(len(cs), 2)
        a, b = cs
        self.assertAlmostEqual(a.x[0], 0.1)
        self.assertAlmostEqual(a.y[0], 0.1)
        self.assertAlmostEqual(b.x[0], 0.3)
        self.assertAlmostEqual(a.y[-1], 0.2)  # 200+5*40=400 → 0.2
        self.assertAlmostEqual(b.t0 - a.t0, 0.02, places=4)
        self.assertGreater(a.t1, a.t0)
        self.assertEqual(keys, [])

    def test_time_zero_shifts_into_video_time(self):
        off = ge.clock_offset(self.events)
        cs, _ = ge.decode(self.events, self.caps, 0, t_zero=1000.0, offset=off)
        self.assertAlmostEqual(cs[0].t0, 0.004, places=4)

    def test_rotation_applied(self):
        cs, _ = ge.decode(self.events, self.caps, rotation=1)
        a = cs[0]
        self.assertAlmostEqual(a.x[0], 0.1)          # u'=v
        self.assertAlmostEqual(a.y[0], 0.9)          # v'=1-u

    def test_keyboard_combo(self):
        lines = [L(5.0, 1.0, KB, "EV_KEY", "KEY_LEFTMETA", "DOWN"),
                 L(5.1, 1.1, KB, "EV_KEY", "KEY_TAB", "DOWN"),
                 L(5.2, 1.2, KB, "EV_KEY", "KEY_TAB", "UP"),
                 L(5.3, 1.3, KB, "EV_KEY", "KEY_LEFTMETA", "UP"),
                 L(5.4, 1.4, KB, "EV_KEY", "KEY_A", "DOWN")]
        _, keys = ge.decode(ge.parse_events(lines), self.caps)
        self.assertEqual([k.label for k in keys], ["⌘ Tab"])  # düz 'A' kombinasyon değil
        _, keys = ge.decode(ge.parse_events(lines), self.caps, key_mode="all")
        self.assertEqual([k.label for k in keys], ["⌘ Tab", "A"])

    def test_unlabeled_hex_codes(self):
        # -l'siz çıktı: tür/kod sayısal
        lines = ["[   10.000000] /dev/input/event4: 0003 0039 00000005",
                 "[   10.000000] /dev/input/event4: 0003 0035 000001f4",
                 "[   10.000000] /dev/input/event4: 0003 0036 000003e8",
                 "[   10.000000] /dev/input/event4: 0000 0000 00000000",
                 "[   10.050000] /dev/input/event4: 0003 0039 ffffffff",
                 "[   10.050000] /dev/input/event4: 0000 0000 00000000"]
        cs, _ = ge.decode(ge.parse_events(lines), self.caps)
        self.assertEqual(len(cs), 1)
        self.assertAlmostEqual(cs[0].x[0], 0.5)
        self.assertAlmostEqual(cs[0].y[0], 0.5)


# ── 2026-10-19 incelemesi: gerçek getevent -lt çıktısının çözücüyü kıran biçimleri ─────────────────
def _caps(name, keys, abs_lines, props="INPUT_PROP_DIRECT", path="/dev/input/event4"):
    rows = "\n".join(f"                {a}" for a in abs_lines[1:])
    return (f'add device 2: {path}\n  name:     "{name}"\n  events:\n    KEY (0001): {keys}\n'
            f"    ABS (0003): {abs_lines[0]}\n{rows}\n  input props:\n    {props}\n")


def _ax(name, lo, hi, value=0):
    return f"{name:<22}: value {value}, min {lo}, max {hi}, fuzz 0, flat 0, resolution 0"


class _Log:
    """Gerçek `getevent -lt` biçiminde (değer etiketleri dahil) olay satırı üretici."""

    def __init__(self, dev=DEV):
        self.dev, self.lines = dev, []

    def __call__(self, t, typ, code, val=0):
        if isinstance(val, int):
            val = f"{val & 0xffffffff:08x}"
        self.lines.append(L(1000.0 + t + 0.004, t, self.dev, typ, code, val))

    def syn(self, t):
        self(t, "EV_SYN", "SYN_REPORT")

    def touch(self, t, slot, tid, x=None, y=None, **extra):
        if slot is not None:
            self(t, "EV_ABS", "ABS_MT_SLOT", slot)
        if tid is not None:
            self(t, "EV_ABS", "ABS_MT_TRACKING_ID", tid)
        for code, v in extra.items():
            self(t, "EV_ABS", code, v)
        if x is not None:
            self(t, "EV_ABS", "ABS_MT_POSITION_X", x)
        if y is not None:
            self(t, "EV_ABS", "ABS_MT_POSITION_Y", y)


def decode_or_fail(tc, lines, caps, **kw):
    """Çözücü istisnası testte ÇÖKME değil başarısız İDDİA olsun (mutasyon ölçümü iddia görmeli)."""
    try:
        return ge.decode(ge.parse_events(lines), caps, **kw)
    except Exception as e:  # noqa: BLE001
        tc.fail(f"çözücü çöktü: {type(e).__name__}: {e}")


COMBO = _caps("touch+pen", "BTN_TOUCH             BTN_TOOL_FINGER       BTN_TOOL_PEN",
              [_ax("ABS_MT_SLOT", 0, 9), _ax("ABS_MT_POSITION_X", 0, 1000), _ax("ABS_MT_POSITION_Y", 0, 1000),
               _ax("ABS_MT_TOOL_TYPE", 0, 15), _ax("ABS_MT_TRACKING_ID", 0, 65535)])


class TestToolTypeLabels(unittest.TestCase):
    """`getevent -l` ABS_MT_TOOL_TYPE DEĞERİNİ de etiketler (getevent.c print_event → mt_tool_labels):
    ilk sürüm `int("MT_TOOL_PEN")`da çöküyor ve çekimin take.json'ı hiç yazılmıyordu."""

    def setUp(self):
        self.caps = ge.parse_caps(COMBO)

    def test_labeled_pen_then_finger_in_same_slot(self):
        g = _Log()
        g.touch(1.0, 0, 1, 200, 200, ABS_MT_TOOL_TYPE="MT_TOOL_PEN")
        g(1.0, "EV_KEY", "BTN_TOOL_PEN", "DOWN")
        g(1.0, "EV_KEY", "BTN_TOUCH", "DOWN")
        g.syn(1.0)
        g.touch(1.1, None, None, 300)
        g.syn(1.1)
        g.touch(1.2, None, -1)
        g(1.2, "EV_KEY", "BTN_TOOL_PEN", "UP")
        g(1.2, "EV_KEY", "BTN_TOUCH", "UP")
        g.syn(1.2)
        # parmak AYNI yuvada: araç değiştiği için çekirdek MT_TOOL_FINGER'ı da (etiketli) yollar
        g.touch(2.0, None, 2, 500, ABS_MT_TOOL_TYPE="MT_TOOL_FINGER")
        g(2.0, "EV_KEY", "BTN_TOOL_FINGER", "DOWN")
        g(2.0, "EV_KEY", "BTN_TOUCH", "DOWN")
        g.syn(2.0)
        g.touch(2.1, None, -1)
        g(2.1, "EV_KEY", "BTN_TOUCH", "UP")
        g.syn(2.1)
        cs, _ = decode_or_fail(self, g.lines, self.caps)
        self.assertEqual([c.tool for c in cs], ["pen", "finger"])
        self.assertAlmostEqual(cs[0].x[-1], 0.3)
        self.assertAlmostEqual(cs[1].y[0], 0.2)  # Y değişmedi → çekirdek yollamadı, yuvada kaldı

    def test_palm_is_not_a_touch(self):
        g = _Log()
        g.touch(1.0, 0, 5, 100, 900, ABS_MT_TOOL_TYPE="MT_TOOL_PALM")  # çizerken dayanan el
        g.touch(1.0, 1, 6, 500, 500)
        g(1.0, "EV_KEY", "BTN_TOUCH", "DOWN")
        g.syn(1.0)
        for k in range(1, 5):
            g.touch(1.0 + k * 0.05, 0, None, 100 + k * 5)
            g.touch(1.0 + k * 0.05, 1, None, 500 + k * 20)
            g.syn(1.0 + k * 0.05)
        g.touch(1.3, 0, -1)
        g.touch(1.3, 1, -1)
        g(1.3, "EV_KEY", "BTN_TOUCH", "UP")
        g.syn(1.3)
        cs, _ = decode_or_fail(self, g.lines, self.caps)
        self.assertEqual(len(cs), 1)
        self.assertAlmostEqual(cs[0].x[0], 0.5)

    def test_unknown_value_label_is_skipped_not_fatal(self):
        g = _Log()
        g.touch(1.0, 0, 1, 400, 400, ABS_MT_TOOL_TYPE="MT_TOOL_KNUCKLE")  # üretici/gelecek etiketi
        g(1.0, "EV_KEY", "BTN_TOUCH", "DOWN")
        g.syn(1.0)
        g.touch(1.1, None, -1)
        g(1.1, "EV_KEY", "BTN_TOUCH", "UP")
        g.syn(1.1)
        cs, _ = decode_or_fail(self, g.lines, self.caps)
        self.assertEqual([c.tool for c in cs], ["finger"])


class TestSynDropped(unittest.TestCase):
    """Okuyucu geride kalınca çekirdek tamponu boşaltıp SYN_DROPPED koyar; arada kaldırma kaybolursa
    ilk sürüm o parmağı sonraki HER çerçevede eski yerinde sürdürüyordu (5,6 sn'lik hayalet daire)."""

    def test_lost_lift_leaves_no_phantom_and_live_finger_resumes(self):
        caps = ge.parse_caps(CAPS)
        g = _Log()
        g.touch(1.0, 0, 1, 100, 200)
        g.touch(1.0, 1, 2, 800, 1600)
        g(1.0, "EV_KEY", "BTN_TOUCH", "DOWN")
        g.syn(1.0)
        # iki kaldırma + 0. yuvaya yeni parmak düşmede kayboldu; yarım çerçeve, sonra tek parmak kaydırma
        g(1.5, "EV_SYN", "SYN_DROPPED")
        g.touch(1.5, 0, 3, 300)
        g.syn(1.5)
        for k in range(1, 50):
            g.touch(1.5 + k * 0.1, None, None, 300 + k % 3, 200 + k * 20)
            g.syn(1.5 + k * 0.1)
        g.touch(6.6, None, -1)
        g.syn(6.6)
        cs, _ = decode_or_fail(self, g.lines, caps)
        ghost = [c for c in cs if any(abs(x - 0.8) < 1e-6 for x in c.x)]
        self.assertTrue(ghost)
        self.assertLessEqual(max(c.t1 for c in ghost), 1.5)  # düşmede kapandı, 6,6 sn'ye SÜRMEDİ
        live = [c for c in cs if c.t1 > 2.0]
        self.assertEqual(len(live), 1)  # yalnız gerçekten hareket eden parmak sürdü
        self.assertAlmostEqual(live[0].x[-1], 0.3, places=2)  # bayat x (0.1) DEĞİL — taze değer bekledi
        self.assertGreater(live[0].y[-1], live[0].y[0])


PEN_MT = _caps("mt-pen", "BTN_TOUCH             BTN_TOOL_PEN          BTN_STYLUS",
               [_ax("ABS_MT_SLOT", 0, 0), _ax("ABS_MT_POSITION_X", 0, 1000), _ax("ABS_MT_POSITION_Y", 0, 1000),
                _ax("ABS_MT_TRACKING_ID", 0, 65535), _ax("ABS_MT_PRESSURE", 0, 4096), _ax("ABS_MT_DISTANCE", 0, 255)])


class TestHover(unittest.TestCase):
    """Android MultiTouchInputMapper: BTN_TOUCH kalkık ya da basınç ≤ 0 → havada (dokunma DEĞİL)."""

    def test_pen_hover_is_not_a_stroke(self):
        g = _Log()
        g.touch(1.0, None, 7, 100, 100, ABS_MT_DISTANCE=40)
        g(1.0, "EV_KEY", "BTN_TOOL_PEN", "DOWN")
        g.syn(1.0)
        for k in range(1, 21):  # 2 sn ekranın üstünde havada gezinme
            g.touch(1.0 + k * 0.1, None, None, 100 + k * 40)
            g.syn(1.0 + k * 0.1)
        g.touch(3.5, None, None, 500, ABS_MT_PRESSURE=2000, ABS_MT_DISTANCE=0)
        g(3.5, "EV_KEY", "BTN_TOUCH", "DOWN")
        g.syn(3.5)
        g.touch(3.7, None, None, 600, ABS_MT_PRESSURE=2100)
        g.syn(3.7)
        g.touch(4.0, None, None, None, ABS_MT_PRESSURE=0, ABS_MT_DISTANCE=30)
        g(4.0, "EV_KEY", "BTN_TOUCH", "UP")
        g.syn(4.0)
        g.touch(4.5, None, -1)
        g(4.5, "EV_KEY", "BTN_TOOL_PEN", "UP")
        g.syn(4.5)
        cs, _ = decode_or_fail(self, g.lines, ge.parse_caps(PEN_MT))
        self.assertEqual(len(cs), 1)
        self.assertEqual(cs[0].tool, "pen")
        self.assertAlmostEqual(cs[0].t0, 3.5, places=3)
        self.assertAlmostEqual(cs[0].t1, 4.0, places=3)

    def test_zero_pressure_is_hover_on_finger_panel(self):
        caps = ge.parse_caps(CAPS)  # salt parmak; ABS_MT_PRESSURE var
        g = _Log()
        g.touch(1.0, 0, 9, 500, 1000, ABS_MT_PRESSURE=0)
        g.syn(1.0)
        g.touch(1.1, None, None, None, None, ABS_MT_PRESSURE=40)
        g.syn(1.1)
        g.touch(1.2, None, -1)
        g.syn(1.2)
        cs, _ = decode_or_fail(self, g.lines, caps)
        self.assertEqual(len(cs), 1)
        self.assertAlmostEqual(cs[0].t0, 1.1, places=3)

    def test_pen_on_combined_panel_without_tool_type_axis(self):
        caps = ge.parse_caps(_caps("touch+pen-notool", "BTN_TOUCH             BTN_TOOL_FINGER       BTN_TOOL_PEN",
                                   [_ax("ABS_MT_SLOT", 0, 9), _ax("ABS_MT_POSITION_X", 0, 1000),
                                    _ax("ABS_MT_POSITION_Y", 0, 1000), _ax("ABS_MT_TRACKING_ID", 0, 65535)]))
        g = _Log()
        g(1.0, "EV_KEY", "BTN_TOOL_PEN", "DOWN")
        g(1.0, "EV_KEY", "BTN_TOUCH", "DOWN")
        g.touch(1.0, 0, 1, 100, 100)
        g.syn(1.0)
        g.touch(1.1, None, -1)
        g(1.1, "EV_KEY", "BTN_TOOL_PEN", "UP")
        g(1.1, "EV_KEY", "BTN_TOUCH", "UP")
        g.syn(1.1)
        g(2.0, "EV_KEY", "BTN_TOOL_FINGER", "DOWN")
        g(2.0, "EV_KEY", "BTN_TOUCH", "DOWN")
        g.touch(2.0, None, 2, 700)
        g.syn(2.0)
        g.touch(2.1, None, -1)
        g(2.1, "EV_KEY", "BTN_TOUCH", "UP")
        g.syn(2.1)
        cs, _ = decode_or_fail(self, g.lines, caps)
        self.assertEqual([c.tool for c in cs], ["pen", "finger"])  # Android: BTN_TOOL_* araç türü


class TestSlotSeed(unittest.TestCase):
    """Çekirdek ABS_MT_SLOT'u yalnız DEĞİŞİNCE yollar: kayıt başlarken son yuva 1 ise oraya düşen ilk
    parmak için olay gelmez. 0 varsaymak iki parmakta ilk parmağın izini 60 ms'lik dokunuşa indiriyordu."""

    def test_first_contact_lands_on_kernel_current_slot(self):
        caps = ge.parse_caps(CAPS.replace("ABS_MT_SLOT           : value 0", "ABS_MT_SLOT           : value 1"))
        self.assertEqual(caps[DEV].values["ABS_MT_SLOT"], 1)
        g = _Log()
        g.touch(3.0, None, 10, 100, 100)  # A: 1. yuva — ABS_MT_SLOT olayı YOK
        g.syn(3.0)
        g.touch(3.1, 0, 11, 600, 600)  # B: 0. yuva
        g.syn(3.1)
        for k in range(1, 11):
            g.touch(3.1 + k * 0.1, 1, None, None, 100 + k * 50)  # A sürükleniyor
            g.syn(3.1 + k * 0.1)
        g.touch(4.3, 1, -1)
        g.touch(4.3, 0, -1)
        g.syn(4.3)
        cs, _ = decode_or_fail(self, g.lines, caps)
        self.assertEqual(len(cs), 2)
        a = next(c for c in cs if abs(c.x[0] - 0.1) < 1e-6)
        self.assertAlmostEqual(a.y[-1], 0.3)  # 600/2000: sürüklemenin tamamı
        self.assertGreater(a.t1, 4.2)


PROTO_A = _caps("ts-protoA", "BTN_TOUCH",
                [_ax("ABS_MT_POSITION_X", 0, 1000), _ax("ABS_MT_POSITION_Y", 0, 1000)])


class TestProtocolA(unittest.TestCase):
    """Yuvasız (protokol A) sürücü: temaslar SYN_MT_REPORT ile ayrılır, kaldırma = çerçevede olmamak."""

    def test_drag_and_two_fingers(self):
        g = _Log()
        for k in range(10):
            t = 2.0 + k * 0.02
            g.touch(t, None, None, 200 + k * 20, 500)
            g(t, "EV_SYN", "SYN_MT_REPORT")
            if k >= 5:  # ikinci parmak
                g.touch(t, None, None, 800, 300 - k * 10)
                g(t, "EV_SYN", "SYN_MT_REPORT")
            g.syn(t)
        g(2.3, "EV_SYN", "SYN_MT_REPORT")
        g.syn(2.3)
        notes = []
        cs, _ = decode_or_fail(self, g.lines, ge.parse_caps(PROTO_A), notes=notes)
        self.assertEqual(len(cs), 2)
        drag = next(c for c in cs if abs(c.x[0] - 0.2) < 1e-6)
        self.assertAlmostEqual(drag.x[-1], 0.38)
        self.assertAlmostEqual(drag.t1, 2.3, places=3)
        second = next(c for c in cs if c is not drag)
        self.assertAlmostEqual(second.t0, 2.1, places=3)
        self.assertLess(second.y[-1], second.y[0])
        self.assertTrue(any("protokol A" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
