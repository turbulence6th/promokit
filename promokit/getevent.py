"""`adb shell getevent -lt` çıktısı → Contact listesi + tuş kombinasyonları.

Ham günlük biçimi (capture.py yazar): her satırın başında ana makinenin duvar saati:

    1760870000.123456 [   86412.237560] /dev/input/event4: EV_ABS  ABS_MT_POSITION_X  00000a3c

Senkron: cihazın olay saati (köşeli parantez) ile satırın ana makineye VARIŞ saati
arasındaki fark gecikme kadar dalgalanır ama ASLA gerçek ofsetin altına inmez
(nedensellik) → ofset = min(varış − cihaz). Bu, cihaz saatinin hangi saat olduğundan
(monotonic / realtime) BAĞIMSIZDIR ve getevent'in çıktı tamponlamasından etkilenmez.

Yön: dokunmatik panelin ham eksenleri panelin DOĞAL yönündedir; ekran döndürülmüşse
Android InputReader'ın dönüşümü uygulanır (TouchInputMapper::rotateAndScale):
    0: (u, v)   1/90°: (v, 1−u)   2/180°: (1−u, 1−v)   3/270°: (1−v, u)

Çözücü Android InputReader'ın izinden gider; ilk sürümü kıran gerçek biçimler (2026-10-19):
- `-l` DEĞERİ de etiketler: ABS_MT_TOOL_TYPE → "MT_TOOL_PEN" (getevent.c mt_tool_labels).
  Sayı beklenen yerde metin → kalem/avuç ilk değdiğinde ÇÖKÜYORDU.
- Avuç (MT_TOOL_PALM) temas değildir (Android iptal eder); havadaki kalem de değil: BTN_TOUCH
  kalkık ya da basınç ≤ 0 = hover (MultiTouchInputMapper isHovering).
- SYN_DROPPED: o çerçevenin kalanı yarım → sonraki SYN_REPORT'a dek atılır, açık temaslar
  kapanır, yuvalar "bilinmiyor"a döner ve yalnız TAZE olay gelince sürer. Kaldırma düşmede
  kaybolduysa temas eski yerinde her çerçevede uzuyordu (hayalet daire).
- Çekirdek ABS_MT_SLOT'u yalnız DEĞİŞİNCE yollar → başlangıç yuvası `getevent -lp`'deki güncel
  değer. Yuvasız eski sürücüde (protokol A) kimlik en yakın komşuyla tahmin edilir.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .touches import Contact, KeyPress

LINE = re.compile(r"^\s*(?:(?P<host>\d+\.\d+)\s+)?\[\s*(?P<sec>\d+)\.(?P<usec>\d+)\]\s+"
                  r"(?P<dev>/dev/input/[^:\s]+):?\s+(?P<type>\S+)\s+(?P<code>\S+)\s+(?P<value>\S+)")

# Etiketsiz (-l'siz ya da bilinmeyen) kodlar için asgari tablo
EV_TYPES = {0x00: "EV_SYN", 0x01: "EV_KEY", 0x03: "EV_ABS", 0x04: "EV_MSC"}
ABS_CODES = {0x00: "ABS_X", 0x01: "ABS_Y", 0x18: "ABS_PRESSURE", 0x2f: "ABS_MT_SLOT", 0x30: "ABS_MT_TOUCH_MAJOR",
             0x35: "ABS_MT_POSITION_X", 0x36: "ABS_MT_POSITION_Y", 0x37: "ABS_MT_TOOL_TYPE",
             0x39: "ABS_MT_TRACKING_ID", 0x3a: "ABS_MT_PRESSURE", 0x3b: "ABS_MT_DISTANCE"}
SYN_CODES = {0x00: "SYN_REPORT", 0x02: "SYN_MT_REPORT", 0x03: "SYN_DROPPED"}
KEY_CODES = {0x14a: "BTN_TOUCH", 0x140: "BTN_TOOL_PEN", 0x141: "BTN_TOOL_RUBBER", 0x142: "BTN_TOOL_BRUSH",
             0x143: "BTN_TOOL_PENCIL", 0x144: "BTN_TOOL_AIRBRUSH", 0x145: "BTN_TOOL_FINGER"}

# `-l` ile basılan ABS_MT_TOOL_TYPE değer etiketleri (çekirdek input.h MT_TOOL_*; getevent.c mt_tool_labels)
MT_TOOLS = {"MT_TOOL_FINGER": 0, "MT_TOOL_PEN": 1, "MT_TOOL_PALM": 2, "MT_TOOL_DIAL": 0x0a, "MT_TOOL_MAX": 0x0f}
MT_TOOL_PEN, MT_TOOL_PALM = 1, 2
# Kalem ailesi (Android TouchButtonAccumulator::getToolType → STYLUS/ERASER)
PEN_KEYS = ("BTN_TOOL_PEN", "BTN_TOOL_RUBBER", "BTN_TOOL_BRUSH", "BTN_TOOL_PENCIL", "BTN_TOOL_AIRBRUSH")

MODS = {"KEY_LEFTMETA": "⌘", "KEY_RIGHTMETA": "⌘", "KEY_LEFTALT": "⌥", "KEY_RIGHTALT": "⌥",
        "KEY_LEFTCTRL": "⌃", "KEY_RIGHTCTRL": "⌃", "KEY_LEFTSHIFT": "⇧", "KEY_RIGHTSHIFT": "⇧"}
MOD_ORDER = "⌃⌥⇧⌘"
KEY_LABELS = {"KEY_TAB": "Tab", "KEY_SPACE": "Space", "KEY_ENTER": "⏎", "KEY_BACKSPACE": "⌫", "KEY_ESC": "esc",
              "KEY_UP": "↑", "KEY_DOWN": "↓", "KEY_LEFT": "←", "KEY_RIGHT": "→", "KEY_DELETE": "⌦",
              "KEY_GRAVE": "`", "KEY_MINUS": "-", "KEY_EQUAL": "=", "KEY_COMMA": ",", "KEY_DOT": ".",
              "KEY_SLASH": "/", "KEY_SEMICOLON": ";", "KEY_LEFTBRACE": "[", "KEY_RIGHTBRACE": "]"}


@dataclass
class DevCaps:
    path: str
    name: str = ""
    abs: dict = field(default_factory=dict)    # kod → (min, max)
    keys: set = field(default_factory=set)
    props: set = field(default_factory=set)
    values: dict = field(default_factory=dict)  # kod → `-lp` anındaki güncel değer (ABS_MT_SLOT = son yuva)

    @property
    def is_touchscreen(self) -> bool:
        return "ABS_MT_POSITION_X" in self.abs and ("INPUT_PROP_DIRECT" in self.props
                                                    or "INPUT_PROP_POINTER" not in self.props)

    @property
    def is_touchpad(self) -> bool:
        return "ABS_MT_POSITION_X" in self.abs and "INPUT_PROP_POINTER" in self.props

    @property
    def is_stylus(self) -> bool:
        return "BTN_TOOL_PEN" in self.keys and ("ABS_X" in self.abs or "ABS_MT_POSITION_X" in self.abs)

    @property
    def is_keyboard(self) -> bool:
        return {"KEY_A", "KEY_Z", "KEY_SPACE"} <= self.keys

    def kind(self) -> str:
        if self.is_touchpad:
            return "touchpad"
        if self.is_touchscreen:
            return "touchscreen"
        if self.is_stylus:
            return "stylus"
        if self.is_keyboard:
            return "keyboard"
        return "other"

    def to_json(self):
        return {"path": self.path, "name": self.name, "kind": self.kind(),
                "abs": {k: list(v) for k, v in self.abs.items()}}


def parse_caps(text: str) -> dict[str, DevCaps]:
    """`getevent -lp` çıktısı → cihaz yetenekleri."""
    devs: dict[str, DevCaps] = {}
    cur = None
    section = None
    for raw in text.splitlines():
        line = raw.rstrip()
        m = re.match(r"^add device \d+:\s+(\S+)", line)
        if m:
            cur = DevCaps(m.group(1))
            devs[cur.path] = cur
            section = None
            continue
        if cur is None:
            continue
        s = line.strip()
        m = re.match(r'^name:\s+"(.*)"', s)
        if m:
            cur.name = m.group(1)
            continue
        if s.startswith("events:"):
            section = "events"
            continue
        if s.startswith("input props:"):
            section = "props"
            continue
        if section == "props":
            if s.startswith("INPUT_PROP_"):
                cur.props.add(s.split()[0])
            continue
        m = re.match(r"^(KEY|ABS|SW|LED|REL|MSC|FF)\s*\(\w+\):\s*(.*)$", s)
        if m:
            section = m.group(1)
            s = m.group(2)
        if section == "ABS":
            mm = re.match(r"^(\w+)\s*:\s*value\s+(-?\d+),\s*min\s+(-?\d+),\s*max\s+(-?\d+)", s)
            if mm:
                cur.abs[mm.group(1)] = (int(mm.group(3)), int(mm.group(4)))
                cur.values[mm.group(1)] = int(mm.group(2))
        elif section == "KEY":
            for tok in s.split():
                if tok.startswith(("KEY_", "BTN_")):
                    cur.keys.add(tok)
    return devs


def parse_orientation(dumpsys_input: str, dumpsys_window: str = "") -> int | None:
    """Ekran dönüşü (0..3). Önce dahili ekran viewport'u, sonra eski alanlar."""
    for line in dumpsys_input.splitlines():
        if "Viewport" in line and ("INTERNAL" in line or "type=INTERNAL" in line):
            m = re.search(r"orientation=(?:ROTATION_)?(\d+)", line)
            if m:
                v = int(m.group(1))
                return v // 90 if v >= 90 else v
    m = re.search(r"SurfaceOrientation:\s*(\d+)", dumpsys_input)
    if m:
        return int(m.group(1))
    m = re.search(r"mCurrentRotation=(?:ROTATION_)?(\d+)", dumpsys_window) or \
        re.search(r"mRotation=(\d+)", dumpsys_window)
    if m:
        v = int(m.group(1))
        return v // 90 if v >= 90 else v
    return None


def orient(u: float, v: float, rotation: int, flip_x: bool = False, flip_y: bool = False):
    r = rotation % 4
    if r == 1:
        u, v = v, 1 - u
    elif r == 2:
        u, v = 1 - u, 1 - v
    elif r == 3:
        u, v = 1 - v, u
    if flip_x:
        u = 1 - u
    if flip_y:
        v = 1 - v
    return u, v


@dataclass
class Event:
    host: float | None
    dev_t: float
    dev: str
    type: str
    code: str
    value: int | str


def _hexval(s: str):
    if s in ("DOWN", "UP", "REPEAT"):
        return s
    if s in MT_TOOLS:
        return MT_TOOLS[s]
    try:
        v = int(s, 16)
    except ValueError:
        try:
            return int(s)
        except ValueError:
            return s
    if v >= 0x80000000:
        v -= 0x100000000
    return v


def _int(v) -> int | None:
    """Olay değeri → tamsayı; tanınmayan etiket (üretici/gelecek sürüm) → None = olayı atla, ÇÖKME."""
    if isinstance(v, int):
        return v
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_events(lines) -> list[Event]:
    out = []
    for raw in lines:
        m = LINE.match(raw)
        if not m:
            continue
        typ, code = m.group("type"), m.group("code")
        if re.fullmatch(r"[0-9a-fA-F]{4}", typ):
            typ = EV_TYPES.get(int(typ, 16), typ)
        if re.fullmatch(r"[0-9a-fA-F]{4}", code):
            c = int(code, 16)
            code = {"EV_ABS": ABS_CODES, "EV_SYN": SYN_CODES, "EV_KEY": KEY_CODES}.get(typ, {}).get(c, code)
        val = _hexval(m.group("value"))
        if typ == "EV_KEY" and isinstance(val, int):
            val = {0: "UP", 1: "DOWN", 2: "REPEAT"}.get(val, val)
        host = float(m.group("host")) if m.group("host") else None
        out.append(Event(host, int(m.group("sec")) + int(m.group("usec")) / 1e6, m.group("dev"), typ, code, val))
    return out


def clock_offset(events: list[Event]) -> float | None:
    """Ana makine duvar saati − cihaz olay saati (en düşük gecikmeli örnekten)."""
    diffs = [e.host - e.dev_t for e in events if e.host is not None]
    return min(diffs) if diffs else None


class _Builder:
    def __init__(self, tool: str):
        self.tool = tool
        self.t, self.x, self.y, self.p = [], [], [], []

    def add(self, t, x, y, p):
        if self.t and t <= self.t[-1]:
            t = self.t[-1] + 1e-4
        self.t.append(t)
        self.x.append(x)
        self.y.append(y)
        self.p.append(p)

    def contact(self) -> Contact | None:
        if not self.t:
            return None
        if len(self.t) == 1:  # tek örnekli temas: kısa bir dokunuş say
            self.add(self.t[0] + 0.06, self.x[0], self.y[0], self.p[0])
        return Contact(self.t, self.x, self.y, self.p, self.tool)


class _Geo:
    """Ham panel koordinatı → ekran-normalize (yön + ters çevirme); ham basınç → 0..1."""

    def __init__(self, rotation: int, flip_x: bool, flip_y: bool):
        self.rotation, self.flip_x, self.flip_y = rotation, flip_x, flip_y

    def pos(self, dev: DevCaps, x, y, mt=True):
        kx, ky = ("ABS_MT_POSITION_X", "ABS_MT_POSITION_Y") if mt else ("ABS_X", "ABS_Y")
        x0, x1 = dev.abs.get(kx, (0, 1))
        y0, y1 = dev.abs.get(ky, (0, 1))
        u = (x - x0) / max(1, x1 - x0)
        v = (y - y0) / max(1, y1 - y0)
        return orient(min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0), self.rotation, self.flip_x, self.flip_y)

    def pressure(self, dev: DevCaps, p, mt=True):
        k = "ABS_MT_PRESSURE" if mt else "ABS_PRESSURE"
        if k not in dev.abs or p is None:
            return 1.0
        lo, hi = dev.abs[k]
        return min(max((p - lo) / max(1, hi - lo), 0.0), 1.0)


def _emit(contacts: list, b: _Builder | None, t: float | None = None):
    """Yapıcı → temas. `t`: kaldırma anı (son konumda kapanış örneği)."""
    if b is None:
        return
    if t is not None:
        b.add(t, b.x[-1], b.y[-1], b.p[-1])
    c = b.contact()
    if c:
        contacts.append(c)


_SLOT_AXES = {"ABS_MT_POSITION_X": "x", "ABS_MT_POSITION_Y": "y", "ABS_MT_PRESSURE": "p", "ABS_MT_TOOL_TYPE": "tool"}


class _SlotTracker:
    """Çok-noktalı protokol B: yuva (slot) durumu, çerçeve (SYN_REPORT) başına bir örnek."""

    def __init__(self, dev: DevCaps, geo: _Geo):
        self.dev, self.geo = dev, geo
        self.contacts: list[Contact] = []
        self.slots: dict[int, dict] = {}
        self.btn: dict[str, bool] = {}
        self.drop = False
        # Çekirdek ABS_MT_SLOT'u yalnız DEĞİŞİNCE yollar: kaydın öncesindeki son yuvaya düşen ilk parmak
        # için olay GELMEZ → başlangıç `getevent -lp`deki güncel değer (0 varsaymak iki parmakta ilk
        # parmağı yanlış yuvaya yazıp izini kesiyordu)
        self.slot = dev.values.get("ABS_MT_SLOT", 0)
        self.has_tool_type = "ABS_MT_TOOL_TYPE" in dev.abs
        self.has_pressure = "ABS_MT_PRESSURE" in dev.abs
        # BTN_TOUCH kapısı yalnız havada (hover) bildirebilen cihazda (kalem ya da mesafe ekseni): kayıt
        # başında tuş durumu bilinmez → kalkık varsayılır; salt-parmak panelde bu varsayım, kayıt
        # başlarken basılı duran parmağın yanındaki İKİNCİ parmağı yutardı
        self.hover_capable = "BTN_TOUCH" in dev.keys and (any(k in dev.keys for k in PEN_KEYS)
                                                          or "ABS_MT_DISTANCE" in dev.abs)
        self.pen_only = "BTN_TOOL_PEN" in dev.keys and "BTN_TOOL_FINGER" not in dev.keys

    def _slot(self) -> dict:
        # active: True canlı · False kalkık · None bilinmiyor (kayıt öncesinden ya da SYN_DROPPED sonrası)
        return self.slots.setdefault(self.slot, {"active": None, "x": None, "y": None, "p": None, "tool": 0,
                                                 "b": None, "end": False, "seen": False})

    def feed(self, e: Event, t: float):
        if e.type == "EV_SYN":
            if e.code == "SYN_DROPPED":
                self.drop = True
                self.btn.pop("BTN_TOUCH", None)  # kalkış düşmüş olabilir → bilinmiyor (= kalkık)
            elif e.code == "SYN_REPORT":
                self._resync() if self.drop else self._frame(t)
            return
        if e.type == "EV_KEY":
            if e.value in ("DOWN", "UP"):  # tuş durumu MUTLAK değer → yarım çerçevede de doğru
                self.btn[str(e.code)] = e.value == "DOWN"
            return
        if e.type != "EV_ABS":
            return
        v = _int(e.value)
        if v is None:  # tanınmayan değer etiketi: olayı atla, ÇÖKME
            return
        if e.code == "ABS_MT_SLOT":
            self.slot = v  # yarım çerçevede de: sonraki olayların yuvası budur
            return
        if self.drop:
            return
        sl = self._slot()
        if e.code == "ABS_MT_TRACKING_ID":
            if v < 0:
                sl["end"] = True
            else:  # aynı yuvaya yeni kimlik: bitirilmemiş temas kapanır
                _emit(self.contacts, sl["b"])
                sl.update(active=True, b=None, end=False)
        elif str(e.code).startswith("ABS_MT_"):
            key = _SLOT_AXES.get(e.code)
            if key:
                sl[key] = v
            sl["seen"] = True  # çekirdek eksen olayını yalnız CANLI yuvaya yollar

    def _hovering(self, sl: dict) -> bool:
        """Android MultiTouchInputMapper isHovering: BTN_TOUCH kalkık ya da basınç ≤ 0."""
        if self.hover_capable and not self.btn.get("BTN_TOUCH", False):
            return True
        return self.has_pressure and sl["p"] is not None and sl["p"] <= 0

    def _tool(self, sl: dict) -> str:
        if self.has_tool_type:  # yuva aracını kendisi söylüyor
            return "pen" if sl["tool"] == MT_TOOL_PEN else "finger"
        # Söylemiyorsa cihazın BTN_TOOL_* durumu (Android TouchButtonAccumulator::getToolType)
        return "pen" if self.pen_only or any(self.btn.get(k) for k in PEN_KEYS) else "finger"

    def _frame(self, t: float):
        for sl in self.slots.values():
            seen, sl["seen"] = sl["seen"], False
            if sl["end"]:
                _emit(self.contacts, sl["b"], t)
                sl.update(active=False, b=None, end=False)
                continue
            if sl["active"] is None and seen:
                sl["active"] = True  # durumu bilinmeyen yuvaya taze olay geldi → canlı
            if not sl["active"] or sl["x"] is None or sl["y"] is None:
                continue
            if self.has_tool_type and sl["tool"] == MT_TOOL_PALM:
                sl["b"] = None  # avuç: Android işaretçiyi İPTAL eder — temas sayılmaz
                continue
            if self._hovering(sl):
                _emit(self.contacts, sl["b"], t)  # değme bitti (kalem havaya kalktı)
                sl["b"] = None
                continue
            if sl["b"] is None:
                sl["b"] = _Builder(self._tool(sl))
            u, v = self.geo.pos(self.dev, sl["x"], sl["y"])
            sl["b"].add(t, u, v, self.geo.pressure(self.dev, sl["p"]))

    def _resync(self):
        """SYN_DROPPED'dan sonraki ilk SYN_REPORT: açık temaslar SON bilinen örnekte kapanır; yuva durumu
        ve konumu bilinmiyor — yalnız taze X ve Y gelen yuva yeniden temas olur (bayat konum çizilmez)."""
        for sl in self.slots.values():
            _emit(self.contacts, sl["b"])
            sl.update(active=None, x=None, y=None, p=None, b=None, end=False, seen=False)
        self.drop = False

    def finish(self):
        for sl in self.slots.values():
            _emit(self.contacts, sl["b"])
            sl["b"] = None


class _ProtoATracker:
    """Çok-noktalı protokol A (yuvasız eski sürücü): çerçevede her temas SYN_MT_REPORT ile ayrılır,
    kaldırma = temasın çerçevede OLMAMASI. Kimlik: ABS_MT_TRACKING_ID varsa o, yoksa önceki çerçevedeki
    en yakın temas (Android assignPointerIds'in kaba hâli)."""

    MATCH = 0.15  # çerçeveler arası en büyük sıçrama (ekran birimi); ötesi yeni temas sayılır
    _AXES = {"ABS_MT_POSITION_X": "x", "ABS_MT_POSITION_Y": "y", "ABS_MT_PRESSURE": "p",
             "ABS_MT_TRACKING_ID": "id", "ABS_MT_TOOL_TYPE": "tool"}

    def __init__(self, dev: DevCaps, geo: _Geo):
        self.dev, self.geo = dev, geo
        self.contacts: list[Contact] = []
        self.cur: dict = {}
        self.groups: list[dict] = []
        self.live: list[tuple] = []  # (kimlik, _Builder, (u, v))
        self.drop = False
        self.serial = 0
        self.pen_only = "BTN_TOOL_PEN" in dev.keys and "BTN_TOOL_FINGER" not in dev.keys

    def _end_group(self):
        if not self.drop and self.cur.get("x") is not None and self.cur.get("y") is not None:
            self.groups.append(self.cur)
        self.cur = {}

    def feed(self, e: Event, t: float):
        if e.type == "EV_SYN":
            if e.code == "SYN_DROPPED":
                self.drop = True
            elif e.code == "SYN_MT_REPORT":
                self._end_group()
            elif e.code == "SYN_REPORT":
                self._end_group()  # son temasın SYN_MT_REPORT'unu unutan sürücü
                if self.drop:  # yarım çerçeve: açıkları kapat, sıfırdan başla
                    for _, b, _ in self.live:
                        _emit(self.contacts, b)
                    self.live, self.drop = [], False
                else:
                    self._frame(t)
                self.groups = []
            return
        if self.drop or e.type != "EV_ABS":
            return
        v = _int(e.value)
        key = self._AXES.get(e.code)
        if key and v is not None:
            self.cur[key] = v

    def _frame(self, t: float):
        pts = []
        for g in self.groups:
            if g.get("tool") == MT_TOOL_PALM:
                continue
            if "ABS_MT_PRESSURE" in self.dev.abs and g.get("p") is not None and g["p"] <= 0:
                continue  # havada
            pts.append((g, self.geo.pos(self.dev, g["x"], g["y"])))
        pairs = []
        for i, (key, _, (lu, lv)) in enumerate(self.live):
            for j, (g, (u, v)) in enumerate(pts):
                if g.get("id") is not None:
                    if key == ("id", g["id"]):
                        pairs.append((0.0, i, j))
                elif key[0] == "nn" and math.hypot(u - lu, v - lv) <= self.MATCH:
                    pairs.append((math.hypot(u - lu, v - lv), i, j))
        pairs.sort()
        used_i, used_j, live = set(), set(), []
        for _, i, j in pairs:
            if i in used_i or j in used_j:
                continue
            used_i.add(i)
            used_j.add(j)
            key, b, _ = self.live[i]
            g, (u, v) = pts[j]
            b.add(t, u, v, self.geo.pressure(self.dev, g.get("p")))
            live.append((key, b, (u, v)))
        for i, (_, b, _) in enumerate(self.live):
            if i not in used_i:
                _emit(self.contacts, b, t)  # bu çerçevede yok → kalktı
        for j, (g, (u, v)) in enumerate(pts):
            if j in used_j:
                continue
            b = _Builder("pen" if g.get("tool") == MT_TOOL_PEN or self.pen_only else "finger")
            b.add(t, u, v, self.geo.pressure(self.dev, g.get("p")))
            if g.get("id") is not None:
                key = ("id", g["id"])
            else:
                key, self.serial = ("nn", self.serial), self.serial + 1
            live.append((key, b, (u, v)))
        self.live = live

    def finish(self):
        for _, b, _ in self.live:
            _emit(self.contacts, b)
        self.live = []


class _SingleTracker:
    """Tek noktalı (kalem paneli): ABS_X/ABS_Y + BTN_TOUCH (+ ABS_PRESSURE)."""

    def __init__(self, dev: DevCaps, geo: _Geo):
        self.dev, self.geo = dev, geo
        self.contacts: list[Contact] = []
        self.x = self.y = self.p = None
        self.touch = False
        self.b: _Builder | None = None
        self.drop = False
        self.has_pressure = "ABS_PRESSURE" in dev.abs

    def feed(self, e: Event, t: float):
        if e.type == "EV_SYN":
            if e.code == "SYN_DROPPED":
                self.drop = True
                self.touch = False  # kalkış düşmüş olabilir → yeni BTN_TOUCH DOWN gelene dek kalkık
            elif e.code == "SYN_REPORT":
                if self.drop:
                    _emit(self.contacts, self.b)
                    self.b, self.drop = None, False
                else:
                    self._frame(t)
            return
        if e.type == "EV_KEY":
            if e.code == "BTN_TOUCH" and e.value in ("DOWN", "UP"):
                self.touch = e.value == "DOWN"
            return
        if self.drop or e.type != "EV_ABS":
            return
        v = _int(e.value)
        if v is None:
            return
        if e.code == "ABS_X":
            self.x = v
        elif e.code == "ABS_Y":
            self.y = v
        elif e.code == "ABS_PRESSURE":
            self.p = v

    def _frame(self, t: float):
        # Android SingleTouchInputMapper: BTN_TOUCH kalkık ya da basınç ≤ 0 = havada
        touching = self.touch and not (self.has_pressure and self.p is not None and self.p <= 0)
        if touching and self.x is not None and self.y is not None:
            if self.b is None:
                self.b = _Builder("pen")
            u, v = self.geo.pos(self.dev, self.x, self.y, mt=False)
            self.b.add(t, u, v, self.geo.pressure(self.dev, self.p, mt=False))
        elif not touching and self.b is not None:
            _emit(self.contacts, self.b, t)
            self.b = None

    def finish(self):
        _emit(self.contacts, self.b)
        self.b = None


def _tracker(dev: DevCaps, geo: _Geo, notes: list | None):
    if "ABS_MT_POSITION_X" not in dev.abs:
        return _SingleTracker(dev, geo)
    if "ABS_MT_SLOT" in dev.abs:
        return _SlotTracker(dev, geo)
    if notes is not None:
        notes.append(f"{dev.path} ({dev.name}): çok-noktalı protokol A (yuvasız) — parmak kimlikleri en yakın "
                     "komşuyla TAHMİN edildi; çok parmaklı jestleri --plot ile kontrol edin")
    return _ProtoATracker(dev, geo)


def decode(events: list[Event], caps: dict[str, DevCaps], rotation: int = 0, t_zero: float = 0.0,
           offset: float = 0.0, flip_x: bool = False, flip_y: bool = False, key_mode: str = "combos",
           notes: list | None = None):
    """Olaylar → (dokunuşlar, tuşlar). Zaman: ana makine saatinde `t_zero`'a göre saniye.

    `offset`: cihaz saatini ana makine saatine çeviren fark (clock_offset).
    `notes`: verilirse kullanıcıya gösterilecek uyarılar eklenir (ör. protokol A tahmini)."""
    keys: list[KeyPress] = []
    mods: set = set()
    geo = _Geo(rotation, flip_x, flip_y)
    trackers: dict = {}

    for e in events:
        dev = caps.get(e.dev)
        if dev is None:
            continue
        t = e.dev_t + offset - t_zero
        kind = dev.kind()
        if kind == "keyboard" or (e.type == "EV_KEY" and str(e.code).startswith("KEY_") and kind == "other"):
            if e.type != "EV_KEY":
                continue
            name = str(e.code)
            if name in MODS:
                if e.value == "DOWN":
                    mods.add(MODS[name])
                elif e.value == "UP":
                    mods.discard(MODS[name])
                continue
            if e.value != "DOWN":
                continue
            label = KEY_LABELS.get(name) or (name[4:] if name.startswith("KEY_") else name)
            if len(label) == 1:
                label = label.upper()
            held = [m for m in MOD_ORDER if m in mods]
            if key_mode == "all" or (key_mode == "combos" and set(held) - {"⇧"}):
                keys.append(KeyPress(t, " ".join(held + [label])))
            continue
        if kind not in ("touchscreen", "stylus"):
            continue
        tr = trackers.get(e.dev)
        if tr is None:
            tr = trackers[e.dev] = _tracker(dev, geo, notes)
        tr.feed(e, t)
    contacts: list[Contact] = []
    for tr in trackers.values():
        tr.finish()  # açık kalan temaslar (kayıt temas sürerken durdu)
        contacts += tr.contacts
    contacts.sort(key=lambda c: c.t0)
    return contacts, keys
