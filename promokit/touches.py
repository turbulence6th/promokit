"""Dokunuş veri modeli: Contact (bir parmağın bas→kaldır izi) + JSON okuma/yazma.

Elle yazılan jestler (gestures.py) ve tabletten KAYDEDİLEN dokunuşlar (getevent.py)
aynı modele derlenir; el animasyonu ve dalga efektleri yalnız bu modeli görür.

Koordinatlar ekran-normalize: (0,0) sol üst, (1,1) sağ alt. Zaman saniye.
"""
from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, field


@dataclass
class Contact:
    t: list[float]
    x: list[float]
    y: list[float]
    p: list[float] = field(default_factory=list)
    tool: str = "finger"   # finger | pen
    on: str = "screen"     # screen | touchpad (klavyeli kılıfın dokunmatik yüzeyi)
    # Tabletin KENDİ arayüzüne (ekran klavyesi) dokunuş: Mac jest makinesine GİRMEZ → başka temasla
    # birleşip çok-parmak jesti sayılmaz (hızlı yazışta üst üste binen tuşlar sağ tık olurdu).
    local: bool = False
    # Tabletin İŞLETİM SİSTEMİ jesti ("back" = kenardan kaydırma, §4.3): uygulamaya dokunuş olarak gelmez →
    # jest makinesine girmez; sayfaya kendi olayı ('back') gider.
    system: str = ""

    def __post_init__(self):
        if not self.p:
            self.p = [1.0] * len(self.t)

    @property
    def t0(self) -> float:
        return self.t[0]

    @property
    def t1(self) -> float:
        return self.t[-1]

    def _interp(self, arr, t):
        if t <= self.t[0]:
            return arr[0]
        if t >= self.t[-1]:
            return arr[-1]
        i = bisect.bisect_right(self.t, t)
        ta, tb = self.t[i - 1], self.t[i]
        k = (t - ta) / (tb - ta) if tb > ta else 0.0
        return arr[i - 1] + (arr[i] - arr[i - 1]) * k

    def pos(self, t: float):
        return (self._interp(self.x, t), self._interp(self.y, t))

    def pressure(self, t: float) -> float:
        return self._interp(self.p, t)

    def active(self, t: float) -> bool:
        return self.t[0] <= t <= self.t[-1]

    def travel(self) -> float:
        d = 0.0
        for i in range(1, len(self.t)):
            d += ((self.x[i] - self.x[i - 1]) ** 2 + (self.y[i] - self.y[i - 1]) ** 2) ** 0.5
        return d

    def shifted(self, dt: float) -> "Contact":
        return Contact([v + dt for v in self.t], list(self.x), list(self.y), list(self.p), self.tool, self.on,
                       self.local)

    def clipped(self, a: float, b: float) -> "Contact | None":
        """[a, b] penceresine kırp (sahne kırpması için). Tamamen dışarıdaysa None."""
        if self.t1 < a or self.t0 > b:
            return None
        idx = [i for i, v in enumerate(self.t) if a <= v <= b]
        if not idx:
            pa = self.pos(max(a, self.t0))
            return Contact([max(a, self.t0)], [pa[0]], [pa[1]], [self.pressure(max(a, self.t0))], self.tool, self.on,
                           self.local)
        return Contact([self.t[i] for i in idx], [self.x[i] for i in idx], [self.y[i] for i in idx],
                       [self.p[i] for i in idx], self.tool, self.on, self.local)

    def to_json(self):
        d = {"tool": self.tool, "on": self.on,
             "samples": [[round(t, 5), round(x, 5), round(y, 5), round(p, 3)]
                         for t, x, y, p in zip(self.t, self.x, self.y, self.p)]}
        if self.local:   # yalnız işaretliyse yazılır → kayıt biçimi (take.json) değişmez
            d["local"] = True
        return d

    @staticmethod
    def from_json(d) -> "Contact":
        s = d["samples"]
        return Contact([r[0] for r in s], [r[1] for r in s], [r[2] for r in s],
                       [r[3] if len(r) > 3 else 1.0 for r in s], d.get("tool", "finger"), d.get("on", "screen"),
                       bool(d.get("local", False)))


@dataclass
class KeyPress:
    t: float
    label: str
    dur: float = 1.1


@dataclass
class TypeText:
    """Klavyeden yazılan metin (HTML ekranlarda odaktaki alana harf harf yazılır)."""
    t: float
    text: str
    rate: float = 14.0   # harf/sn


@dataclass
class TouchLog:
    contacts: list[Contact]
    keys: list[KeyPress] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def save(self, path: str):
        doc = dict(self.meta)
        doc["version"] = 1
        doc["contacts"] = [c.to_json() for c in self.contacts]
        doc["keys"] = [[round(k.t, 4), k.label, round(k.dur, 3)] for k in self.keys]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)

    @staticmethod
    def load(path: str) -> "TouchLog":
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        contacts = [Contact.from_json(c) for c in doc.get("contacts", [])]
        keys = [KeyPress(k[0], k[1], k[2] if len(k) > 2 else 1.1) for k in doc.get("keys", [])]
        meta = {k: v for k, v in doc.items() if k not in ("contacts", "keys")}
        return TouchLog(contacts, keys, meta)

    def window(self, t_in: float, t_out: float, offset: float = 0.0) -> "TouchLog":
        """Klip [t_in, t_out] aralığını sahne zamanına (0'dan) taşı. `offset` senkron düzeltmesi."""
        out = []
        for c in self.contacts:
            c2 = c.shifted(offset).clipped(t_in, t_out)
            if c2 is not None:
                out.append(c2.shifted(-t_in))
        keys = [KeyPress(k.t + offset - t_in, k.label, k.dur) for k in self.keys
                if t_in <= k.t + offset <= t_out]
        return TouchLog(out, keys, dict(self.meta))
