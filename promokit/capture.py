"""Kayıt: Mac ekranı (ScreenCaptureKit yardımcısı), tablet ekranı (adb screenrecord),
ekran görüntüleri ve tabletteki GERÇEK dokunuşlar (adb getevent).

Bir "çekim" (take) bir dizindir:
    work/takes/<ad>/
        take.json      — meta: ekran dosyası, dokunuş dosyası, senkron ofseti, yön
        screen.mov|mp4 — kayıt
        touches.json   — ekran-normalize dokunuşlar (video zamanında)
        getevent.log   — ham olay günlüğü (yeniden üretim için: `touches <çekim> --rotation …`)
        caps.txt, dumpsys-input.txt — cihaz yetenekleri / ekran yönü

Senaryoda: `take = "../work/takes/<ad>"` → tablet ekranına kayıt, el o dokunuşları oynatır.

Süreç kuralları (2026-10-19 incelemesi; ihlalleri cihazsız testlerde kilitli — tests/test_capture.py):
- Uzun süreli çocuklar (adb, sck-record) KENDİ oturumunda başlar: terminalin Ctrl-C'si onlara
  ULAŞMAZ, durdurmayı yalnız bu modül yapar. Yoksa Ctrl-C yerel `adb shell screenrecord`u
  öldürüyor, adbd uzak sürece SIGHUP yolluyor (shell_service.cpp) ve bizim ardından gelen
  SIGINT'imiz screenrecord'un İKİNCİ sinyali oluyordu — ilk sinyalde varsayılan işleyiciye
  döndüğü için MP4 moov yazılmadan ölüyordu.
- `adb shell` stdin'i uzak komuta aktarır (`-n` yoksa): terminali miras alırsa "durdurmak için
  Enter" tuşunu YUTAR → stdin'siz.
- Cihazdaki kayıt yalnız ffprobe'dan geçen bir yerel kopyadan SONRA silinir.
- take.json dokunuş çözümünden ÖNCE yazılır: ilk-kare saati (senkron çapası) yalnız bellekteydi,
  ayrıştırıcı hatası çekimi geri dönülmez biçimde kaybettiriyordu.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque

from . import getevent as ge
from .touches import TouchLog
from .util import PromoError, adb_bin, ffprobe_bin, log

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(TOOL_DIR, "work")
HELPER_SRC = os.path.join(TOOL_DIR, "sck_record.swift")
HELPER_BIN = os.path.join(TOOL_DIR, ".build", "sck-record")
PROMO = os.path.join(TOOL_DIR, "promo.py")

# screenrecord süre sınırı: Android ≤ 13 yalnız 1..180 sn kabul eder (0 reddedilir); 14+ 0 = sınırsız
SR_MAX_OLD = 180
SR_SETTLE = 1.0  # başlatma sonrası "hemen öldü mü" bekleyişi (sn)


# ── yardımcılar ───────────────────────────────────────────────────────────────
def ensure_helper() -> str:
    if sys.platform != "darwin":
        raise PromoError("Mac ekran kaydı yalnız macOS'ta (ScreenCaptureKit)")
    if os.path.exists(HELPER_BIN) and os.path.getmtime(HELPER_BIN) >= os.path.getmtime(HELPER_SRC):
        return HELPER_BIN
    os.makedirs(os.path.dirname(HELPER_BIN), exist_ok=True)
    log("… ekran kaydedici derleniyor (yalnız ilk seferde / kaynak değişince)")
    r = subprocess.run(["swiftc", "-O", "-parse-as-library", "-swift-version", "5", "-o", HELPER_BIN, HELPER_SRC],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise PromoError(f"sck-record derlenemedi (Xcode CLT kurulu mu?):\n{r.stderr[-2000:]}")
    return HELPER_BIN


def mac_displays() -> list[dict]:
    r = subprocess.run([ensure_helper(), "--list"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    out = []
    for ln in r.stdout.splitlines():
        if ln.startswith("{"):
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
    return out


def resolve_display(spec: str | None, displays: list[dict]) -> dict:
    """`--display` → ekran. TEK kural (capture mac + capture shot; sck_record.swift pickDisplay aynı):
    `N` = sıra no (capture list), `id:N` = CGDirectDisplayID, diğer = ad parçası. Sayı hem sıra hem
    kimlik olarak DENENMEZ — ikisi çakışınca iki komut farklı ekran seçiyordu. Verilmezse ANA ekran."""
    if not displays:
        raise PromoError("aktif ekran yok")
    if spec:
        s = spec.strip()
        if s.lower().startswith("id:"):
            try:
                n = int(s[3:])
            except ValueError:
                raise PromoError(f"ekran kimliği sayı olmalı: {spec}") from None
            hit = [x for x in displays if x["id"] == n]
        elif s.isdigit():
            hit = [x for x in displays if x["index"] == int(s)]
        else:
            hit = [x for x in displays if s.lower() in x["name"].lower()]
        if not hit:
            raise PromoError(f"ekran bulunamadı: {spec}  (capture list; kimlik için id:<n>)")
        return hit[0]
    return next((x for x in displays if x["main"]), displays[0])


def _adb() -> str:
    a = adb_bin()
    if not a:
        raise PromoError("adb bulunamadı (ADB ortam değişkeniyle tam yolu verin)")
    return a


def adb(serial: str | None, *args, timeout: float = 30, check: bool = False) -> str:
    cmd = [_adb()] + (["-s", serial] if serial else []) + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise PromoError(f"adb {timeout:g} sn'de yanıt vermedi ({' '.join(args)})") from None
    if check and r.returncode != 0:
        raise PromoError(f"adb hata ({' '.join(args)}): {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def adb_devices() -> list[tuple[str, str]]:
    out = adb(None, "devices", "-l")
    devs = []
    for ln in out.splitlines()[1:]:
        if "\tdevice" in ln or " device " in ln:
            serial = ln.split()[0]
            m = re.search(r"model:(\S+)", ln)
            devs.append((serial, m.group(1) if m else "?"))
    return devs


def pick_serial(serial: str | None) -> str:
    devs = adb_devices()
    if serial:
        if not any(s == serial for s, _ in devs):
            raise PromoError(f"adb cihazı yok: {serial}  (bağlı: {', '.join(s for s, _ in devs) or '—'})")
        return serial
    if not devs:
        raise PromoError("bağlı adb cihazı yok — tableti USB ya da kablosuz adb ile bağlayın")
    if len(devs) > 1:
        # USB + kablosuz aynı cihaz ikilenebiliyor (hafıza: adb_reverse_multi_device) → açıkça seçtir
        raise PromoError("birden çok adb cihazı var, --serial ile seçin:\n  " +
                         "\n  ".join(f"{s}  ({m})" for s, m in devs))
    return devs[0][0]


def new_take_dir(name: str | None) -> tuple[str, str]:
    name = name or time.strftime("take-%Y%m%d-%H%M%S")
    if not re.fullmatch(r"[\w.\-]+", name):
        raise PromoError("çekim adı yalnız harf/rakam/._- içerebilir")
    d = os.path.join(WORK, "takes", name)
    if os.path.exists(os.path.join(d, "take.json")):
        raise PromoError(f"çekim zaten var: {d} (başka ad verin ya da dizini silin)")
    os.makedirs(d, exist_ok=True)
    return name, d


def device_meta(serial: str, d: str) -> dict:
    caps_txt = adb(serial, "shell", "getevent", "-lp", timeout=20)
    with open(os.path.join(d, "caps.txt"), "w") as f:
        f.write(caps_txt)
    di = adb(serial, "shell", "dumpsys", "input", timeout=20)
    with open(os.path.join(d, "dumpsys-input.txt"), "w") as f:
        f.write(di)
    dw = adb(serial, "shell", "dumpsys", "window", "displays", timeout=20)
    rot = ge.parse_orientation(di, dw)
    caps = ge.parse_caps(caps_txt)
    size = adb(serial, "shell", "wm", "size").strip()
    meta = {
        "serial": serial,
        "model": adb(serial, "shell", "getprop", "ro.product.model").strip(),
        "manufacturer": adb(serial, "shell", "getprop", "ro.product.manufacturer").strip(),
        "wm_size": size,
        "rotation": rot if rot is not None else 0,
        "rotation_source": "dumpsys" if rot is not None else "varsayılan",
        "input_devices": [c.to_json() for c in caps.values() if c.kind() != "other"],
    }
    return meta


def _video_ok(path: str) -> tuple[bool | None, str]:
    """ffprobe dosyayı açabiliyor mu + video akışı ve süresi var mı (moov'suz MP4 burada düşer).
    True sağlam · False bozuk · None DOĞRULANAMADI (ffprobe yok/çalışmadı) — None'ı "bozuk" saymak
    iyi bir kaydı atıp senkron çapasını (take.json) kaybettirirdi."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False, "dosya yok ya da boş"
    try:
        r = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=codec_name:format=duration", "-of", "json", path],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired, PromoError) as e:
        return None, f"ffprobe çalışmadı: {e}"
    if r.returncode != 0:
        return False, (r.stderr or "").strip()[-300:] or f"ffprobe çıkış kodu {r.returncode}"
    try:
        info = json.loads(r.stdout or "{}")
        dur = float((info.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        return False, "süre okunamadı"
    if not info.get("streams"):
        return False, "video akışı yok"
    if dur <= 0:
        return False, "süre 0"
    return True, ""


class GeteventCapture:
    """`adb shell -tt getevent -lt` akışını ana-makine varış saatiyle günlüğe yazar.

    `-tt` (uzakta sözde-terminal) TAMPON için DEĞİL: getevent'in stdout'u zaten tamponsuz
    (`setbuf(stdout, NULL)`, toolbox/getevent.c). Gerekçe TEMİZLİK: yerel adb ölünce PTY kapanır
    ve çekirdek uzak oturumun TAMAMINA SIGHUP yollar; PTY'siz adbd yalnız `sh -c`nin PID'ine yollar
    (shell_service.cpp) ve kabuk çatallamışsa getevent cihazda yetim kalır. PTY'nin tek yan etkisi
    satır sonu ("\\n" → "\\r\\n"; pompada kırpılır)."""

    def __init__(self, serial: str, path: str):
        self.path = path
        self.lines = 0
        self._lock = threading.Lock()
        self.f = open(path, "w", encoding="utf-8")
        # Kendi oturumunda (terminalin Ctrl-C'si ulaşmaz) · stdin boru: hiç yazılmaz, kapanmaz
        self.proc = subprocess.Popen([_adb(), "-s", serial, "shell", "-tt", "getevent", "-lt"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     bufsize=0, start_new_session=True)
        self.th = threading.Thread(target=self._pump, daemon=True)
        self.th.start()

    def _pump(self):
        buf = b""
        while True:
            chunk = self.proc.stdout.read(4096) if self.proc.stdout else b""
            if not chunk:
                break
            now = time.time()
            buf += chunk
            *lines, buf = buf.split(b"\n")
            with self._lock:
                if self.f.closed:
                    return
                for ln in lines:
                    s = ln.decode(errors="replace").rstrip("\r")
                    if s:
                        self.f.write(f"{now:.6f} {s}\n")
                        self.lines += 1

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
        self.th.join(timeout=3)
        with self._lock:
            if not self.f.closed:
                self.f.close()


class HelperRun:
    """sck-record süreci: JSON olayları (stdout), stderr kuyruğu, erken ölüm, durdurma."""

    def __init__(self, cmd: list[str]):
        self.events: dict = {}
        self.err: deque = deque(maxlen=40)
        self._got = threading.Event()
        # Kendi oturumunda: kurulum anındaki Ctrl-C yardımcıyı .mov'u sonlandırmadan öldürmesin
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, start_new_session=True)
        self._threads = [threading.Thread(target=self._pump_out, daemon=True)]
        self._threads.append(threading.Thread(target=self._pump_err, daemon=True))  # okunmazsa boru dolar
        for th in self._threads:
            th.start()

    def _pump_out(self):
        for ln in self.proc.stdout:
            try:
                ev = json.loads(ln)
            except ValueError:
                continue
            name = ev.get("event")
            self.events[name] = ev
            if name == "error":
                log(f"✗ sck-record: {ev.get('message')} ({ev.get('domain', '')} {ev.get('code', '')})")
            if name in ("started", "error"):
                self._got.set()
        self._got.set()  # stdout kapandı = süreç bitti → bekleyen HEMEN uyansın

    def _pump_err(self):
        for ln in self.proc.stderr:
            self.err.append(ln.rstrip())

    def stderr_tail(self, n: int = 8) -> str:
        return "\n".join(list(self.err)[-n:])

    def wait_started(self, timeout: float = 20.0) -> dict:
        self._got.wait(timeout)
        if "started" in self.events:
            return self.events["started"]
        self.kill()
        err = self.events.get("error", {})
        rc = self.proc.poll()
        why = err.get("message") or (f"süreç {rc} koduyla çıktı" if rc is not None else f"{timeout:g} sn zaman aşımı")
        hint = ""
        if err.get("code") in (-3801, -3802) or "TCC" in str(err) or "declined" in str(err.get("message", "")):
            hint = ("\n  → Sistem Ayarları › Gizlilik ve Güvenlik › Ekran ve Sistem Sesi Kaydı: bu komutu "
                    "çalıştıran uygulamayı (Terminal/iTerm/IDE) açın, sonra uygulamayı yeniden başlatın.")
        tail = self.stderr_tail()
        raise PromoError(f"Mac kaydı başlamadı: {why}" + (f" (çıkış kodu {rc})" if rc is not None else "") + hint +
                         (f"\n  sck-record stderr:\n{tail}" if tail else ""))

    def stop(self, timeout: float = 30.0) -> int:
        """'q' → yardımcı yakalamayı durdurup .mov'u sonlandırır; çıkış kodu."""
        try:
            self.proc.stdin.write("q\n")
            self.proc.stdin.flush()
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            rc = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill()
            raise PromoError(f"sck-record {timeout:g} sn'de kapanmadı — kayıt sonlanmamış olabilir") from None
        for th in self._threads:
            th.join(timeout=3)
        return rc

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass


class AdbDevice:
    """Tek cihaz. Uzun süreli adb çocukları kendi oturumunda ve stdin'siz başlar (modül başı)."""

    def __init__(self, serial: str):
        self.serial = serial

    def shell(self, cmd: str, timeout: float = 30) -> str:
        return adb(self.serial, "shell", cmd, timeout=timeout)

    def spawn_shell(self, cmd: str) -> subprocess.Popen:
        return subprocess.Popen([_adb(), "-s", self.serial, "shell", cmd], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                start_new_session=True)

    def pull(self, remote: str, local: str) -> tuple[bool, str]:
        try:
            r = subprocess.run([_adb(), "-s", self.serial, "pull", remote, local], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=900)
        except subprocess.TimeoutExpired:
            return False, "adb pull zaman aşımı"
        return r.returncode == 0 and os.path.exists(local), (r.stderr or r.stdout or "").strip()


class ScreenRecord:
    """Tablette `screenrecord`: süre sınırı açık başlar, YALNIZ kendi PID'ine SIGINT ile durur, uzak
    süreç gerçekten bitince çekilir ve ffprobe ile denetlenir; cihazdaki dosya yalnız sağlam bir
    yerel kopyadan SONRA silinir."""

    def __init__(self, dev, remote: str, mbps: float, size: str | None = None, seconds: float | None = None,
                 video_ok=None, clock=time):
        self.dev, self.remote, self.mbps, self.size, self.seconds = dev, remote, mbps, size, seconds
        self.video_ok = video_ok or _video_ok
        self.clock = clock
        self.proc = None
        self.th: threading.Thread | None = None
        self.pid: int | None = None
        self.lines: list[tuple[float, str]] = []
        self.limit: int | None = None
        self.t_launch = 0.0
        self.stopping = False
        self.remote_gone = False

    def wanted_limit(self) -> int:
        if self.seconds:
            return int(math.ceil(self.seconds)) + 5  # kendi durdurmamız önce gelsin
        return 0  # Enter'a kadar → sınırsız (Android 14+); eski sürümde 180'e düşülür

    def command(self, limit: int) -> str:
        args = ["screenrecord", "--verbose", "--bit-rate", str(int(self.mbps * 1e6)), "--time-limit", str(limit)]
        if self.size:
            args += ["--size", self.size]
        args.append(self.remote)
        # `exec` PID'i korur: ilk satır uzak PID → durdurma ve bitiş beklemesi YALNIZ bu sürece bakar
        return "echo CZPID $$; exec " + " ".join(args)

    def _pump(self):
        for ln in self.proc.stdout:
            s = ln.rstrip()
            self.lines.append((self.clock.time(), s))
            m = re.match(r"CZPID (\d+)", s)
            if m and self.pid is None:
                self.pid = int(m.group(1))
        if not self.stopping and self.limit is not None:
            log("⚠️ screenrecord kendiliğinden durdu (süre sınırı?) — video burada bitiyor")

    def start(self, settle: float | None = None) -> int:
        settle = SR_SETTLE if settle is None else settle
        want = self.wanted_limit()
        tries = [want] + ([SR_MAX_OLD] if want == 0 or want > SR_MAX_OLD else [])
        for limit in tries:
            self.lines, self.pid = [], None
            self.t_launch = self.clock.time()
            self.proc = self.dev.spawn_shell(self.command(limit))
            self.th = threading.Thread(target=self._pump, daemon=True)
            self.th.start()
            self.clock.sleep(settle)
            if self.proc.poll() is None:
                self.limit = limit
                return limit
            self.th.join(timeout=3)
            text = "\n".join(s for _, s in self.lines)
            if limit != tries[-1] and re.search(r"[Tt]ime limit", text):
                log(f"⚠️ bu cihazın screenrecord'u en fazla {SR_MAX_OLD} sn kaydeder — kayıt orada kendiliğinden biter")
                continue
            raise PromoError("screenrecord başlamadı:\n" + text)
        raise PromoError("screenrecord başlamadı")

    def stop(self, timeout: float = 20.0):
        """Uzak screenrecord'a SIGINT (yalnız kendi PID'i) → moov yazılıp süreç BİTENE dek bekle."""
        if self.proc is None:
            return
        self.stopping = True
        if self.proc.poll() is None:
            try:
                self.dev.shell(f"kill -INT {self.pid}" if self.pid else "pkill -INT screenrecord", timeout=15)
            except PromoError as e:
                log(f"⚠️ screenrecord durdurulamadı: {e}")
        try:
            self.proc.wait(timeout=timeout)  # yerel adb, uzak komut bitince çıkar
        except subprocess.TimeoutExpired:
            pass
        self.remote_gone = self._wait_remote_exit(timeout)
        if self.th is not None:
            self.th.join(timeout=3)
        if self.proc.poll() is None:  # bağlantı askıda: yerel istemciyi bırak (uzak dosya korunur)
            self.proc.kill()

    def _wait_remote_exit(self, timeout: float) -> bool:
        """Uzak süreç gerçekten bitti mi — yerel adb'nin çıkması yetmez (bağlantı kopmuş olabilir)."""
        if self.pid:
            probe = f'[ "$(cat /proc/{self.pid}/comm 2>/dev/null)" = screenrecord ] && echo alive || echo gone'
        else:
            probe = "pidof screenrecord >/dev/null && echo alive || echo gone"
        deadline = self.clock.time() + timeout
        while True:
            try:
                out = self.dev.shell(probe, timeout=10)
            except PromoError:
                out = ""
            if "gone" in out:
                return True
            if self.clock.time() >= deadline:
                return False
            self.clock.sleep(0.25)

    def fetch(self, local: str):
        """Çek + doğrula; cihazdaki kopya YALNIZ sağlam yerel kopyadan sonra silinir."""
        if not self.remote_gone:
            log("⚠️ screenrecord'un bittiği doğrulanamadı — dosya yine de çekilip denetlenecek")
        keep = f"cihazdaki kopya SİLİNMEDİ: adb -s {self.dev.serial} pull {self.remote}"
        ok, msg = self.dev.pull(self.remote, local)
        if not ok:
            raise PromoError(f"kayıt çekilemedi ({msg or 'adb pull başarısız'}) — {keep}")
        good, why = self.video_ok(local)
        if good is False:
            raise PromoError(f"çekilen kayıt okunamıyor ({why}) — {keep}")
        if good is None:  # doğrulanamadı: yerel kopya kullanılır, cihazdaki YEDEK olarak kalır
            log(f"⚠️ kayıt doğrulanamadı ({why}) — {keep}")
            return
        self.dev.shell(f"rm -f {self.remote}")


def build_touches(d: str, meta: dict, t_zero: float, rotation: int | None = None, flip: str = "",
                  key_mode: str = "combos") -> TouchLog:
    """Ham günlükten touches.json üret (video zamanına göre)."""
    with open(os.path.join(d, "caps.txt")) as f:
        caps = ge.parse_caps(f.read())
    with open(os.path.join(d, "getevent.log"), encoding="utf-8") as f:
        events = ge.parse_events(f)
    off = ge.clock_offset(events)
    if off is None:
        off = 0.0
    rot = meta.get("rotation", 0) if rotation is None else rotation
    notes: list[str] = []
    contacts, keys = ge.decode(events, caps, rot, t_zero, off, "x" in flip, "y" in flip,
                               key_mode=key_mode, notes=notes)
    for n in notes:
        log(f"⚠️ {n}")
    tl = TouchLog(contacts, keys if key_mode != "none" else [],
                  {"rotation": rot, "flip": flip, "clock_offset": off, "t_zero": t_zero,
                   "source": "getevent"})
    tl.save(os.path.join(d, "touches.json"))
    return tl


def _write_take(d: str, take: dict):
    with open(os.path.join(d, "take.json"), "w", encoding="utf-8") as f:
        json.dump(take, f, ensure_ascii=False, indent=1)


def _build_touches_safe(d: str, take: dict, meta: dict, t_zero: float, sync: str) -> TouchLog | None:
    """Dokunuşları üret ve take.json'a bağla; ayrıştırıcı hatası çekimi YOK ETMEZ (take.json zaten yazıldı,
    ham günlük duruyor → `touches <çekim> --rotation N` ile yeniden üretilebilir)."""
    try:
        tl = build_touches(d, meta, t_zero)
    except Exception as e:  # noqa: BLE001 — bilinçli emniyet ağı: kayıt diskte, yalnız türetim başarısız
        log(f"⚠️ dokunuşlar üretilemedi ({type(e).__name__}: {e}) — ham günlük duruyor:\n"
            f"   python3 {PROMO} touches {d} --rotation {take.get('rotation', 0)}")
        return None
    take["touches"] = "touches.json"
    take["sync"] = sync
    _write_take(d, take)
    return tl


def _wait_stop(seconds: float | None):
    if seconds:
        log(f"● kayıt sürüyor — {seconds:g} sn sonra duracak (erken durdurmak için Ctrl-C)")
        try:
            time.sleep(seconds)
        except KeyboardInterrupt:
            pass
        return
    log("● kayıt sürüyor — durdurmak için Enter'a basın")
    try:
        input()
    except (KeyboardInterrupt, EOFError):
        pass


def _summary(d: str, name: str, tl: TouchLog | None):
    if tl is not None:
        taps = sum(1 for c in tl.contacts if c.t1 - c.t0 < 0.25)
        log(f"  dokunuş: {len(tl.contacts)} temas ({taps} kısa dokunuş) · {len(tl.keys)} tuş kombinasyonu")
    log(f"✓ çekim hazır: {d}")
    log(f"  senaryoda:  take = \"{d}\"   (senaryo dosyasına göre göreli yol da olur)")
    if tl is not None:
        log(f"  kontrol:    python3 {PROMO} touches {d} --plot")


# ── komutlar ─────────────────────────────────────────────────────────────────
def capture_mac(a):
    name, d = new_take_dir(a.name)
    helper = ensure_helper()
    disp = resolve_display(a.display, mac_displays())
    out = os.path.join(d, "screen.mov")
    cmd = [helper, "--display", f"id:{disp['id']}", "--out", out, "--fps", str(a.fps), "--codec", a.codec]
    if a.mbps:
        cmd += ["--mbps", str(a.mbps)]
    if a.no_cursor:
        cmd += ["--no-cursor"]
    if a.max_width:
        cmd += ["--max-width", str(a.max_width)]
    gcap = meta = run = None
    try:
        if a.touches:
            try:
                serial = pick_serial(a.serial)
            except PromoError as e:
                if a.serial or "birden çok" in str(e):
                    raise
                log(f"⚠️ dokunuşlar kaydedilmeyecek: {e}")
                serial = None
            if serial:
                meta = device_meta(serial, d)
                gcap = GeteventCapture(serial, os.path.join(d, "getevent.log"))
        run = HelperRun(cmd)
        st = run.wait_started(20)
        log(f"  ekran: {st['name']} {st['width']}×{st['height']} @ {st['fps']} fps · {st['codec']} {st['mbps']:.0f} Mb/sn"
            + (f" · dokunuşlar: {meta['model']} (yön {meta['rotation']})" if meta else ""))
        _wait_stop(a.seconds)
        rc = run.stop()
    finally:
        if run is not None and run.proc.poll() is None:  # istisna: yarım kalan yardımcıyı bırakma
            run.kill()
        if gcap:  # her yolda: yetim `adb getevent` kalmasın
            gcap.stop()
    stopped = run.events.get("stopped") or {}
    ff = run.events.get("first_frame")
    tail = run.stderr_tail()
    if not ff:
        raise PromoError("hiç kare gelmedi (ekran boş/uyuyor olabilir)" + (f"\n  sck-record stderr:\n{tail}" if tail else ""))
    problems = []
    if rc != 0:
        problems.append(f"çıkış kodu {rc}")
    if not stopped:
        problems.append("'stopped' olayı gelmedi")
    if stopped.get("writer_error"):
        problems.append(f"yazıcı hatası: {stopped['writer_error']}")
    if stopped.get("status") not in (None, 2):  # AVAssetWriter.Status.completed = 2
        problems.append(f"yazıcı durumu {stopped.get('status')}")
    ok, why = _video_ok(out)
    if problems or ok is not True:
        detail = " · ".join(problems + ([] if ok else [f"ffprobe: {why}"])) + (f"\n  sck-record stderr:\n{tail}" if tail else "")
        if ok is False:
            raise PromoError(f"Mac kaydı bozuk: {detail}")
        log(f"⚠️ kayıt {'doğrulanamadı' if ok is None else 'okunuyor ama sorun bildirildi'} ({detail}) — "
            "izleyip kontrol edin")
    log(f"  {stopped.get('frames', '?')} kare, {stopped.get('duration', 0):.1f} sn, atlanan {stopped.get('dropped', 0)}")
    take = {"screen": "screen.mov", "source": "mac", "display": st, "first_frame_wall": ff["wall"],
            "touch_offset": 0.0, "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    if gcap:
        take["device"] = meta
        take["rotation"] = meta["rotation"]
    _write_take(d, take)  # dokunuş çözümünden ÖNCE: ilk-kare saati (senkron çapası) diske
    tl = None
    if gcap:
        tl = _build_touches_safe(d, take, meta, ff["wall"], "kare-saat (SCK ilk kare ↔ getevent min-gecikme)")
    _summary(d, name, tl)


def capture_tablet(a):
    name, d = new_take_dir(a.name)
    serial = pick_serial(a.serial)
    meta = device_meta(serial, d)
    rec = ScreenRecord(AdbDevice(serial), f"/sdcard/Download/czpromo-{name}.mp4", a.mbps, a.size, a.seconds)
    gcap = None
    try:
        if a.touches:
            gcap = GeteventCapture(serial, os.path.join(d, "getevent.log"))
        limit = rec.start()
        log(f"  tablet: {meta['manufacturer']} {meta['model']} · {meta['wm_size']} · yön {meta['rotation']} · "
            f"süre sınırı {'yok' if limit == 0 else f'{limit} sn'}")
        _wait_stop(a.seconds)
    finally:
        try:
            rec.stop()
        finally:
            if gcap:  # her yolda: yetim `adb getevent` kalmasın
                gcap.stop()
    local = os.path.join(d, "screen.mp4")
    rec.fetch(local)
    lines = rec.lines
    # İlk kare zamanı: screenrecord'un son kurulum satırı ("Content area…") ≈ kodlayıcı başlangıcı
    setup = [t for t, s in lines if re.search(r"Content area|Configuring recorder", s)]
    t_first = (setup[-1] + 0.03) if setup else (rec.t_launch + 0.6)
    for _, s in lines:
        m = re.search(r"orientation=ROTATION_(\d+)", s)
        if m and meta.get("rotation_source") != "dumpsys":
            meta["rotation"] = int(m.group(1)) // 90
    done = next((s for _, s in lines if "recorded" in s), "")
    if done:
        log(f"  {done.strip()}")
    take = {"screen": "screen.mp4", "source": "tablet", "device": meta, "rotation": meta["rotation"],
            "first_frame_wall": t_first, "first_frame_estimated": True, "touch_offset": 0.0,
            "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    _write_take(d, take)  # dokunuş çözümünden ÖNCE (bkz. capture_mac)
    tl = None
    if gcap:
        tl = _build_touches_safe(d, take, meta, t_first,
                                 "TAHMİNİ (screenrecord kurulum satırı) — `touches <çekim> --auto` ile iyileştirin")
    _summary(d, name, tl)
    if gcap:
        log("  ⚠️ tablet kaydında senkron tahmini; doğrulamak için: touches <çekim> --auto")


def capture_shot(a):
    d = os.path.join(WORK, "shots")
    os.makedirs(d, exist_ok=True)
    name = a.name or time.strftime(f"{a.target}-%Y%m%d-%H%M%S")
    out = os.path.join(d, name + ".png")
    if a.target == "mac":
        x = resolve_display(a.display, mac_displays())  # `capture mac` ile AYNI kural
        r = subprocess.run(["screencapture", "-x", f"-D{x['index']}", out], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        if r.returncode != 0 or not os.path.exists(out):
            raise PromoError(f"screencapture başarısız: {r.stderr.strip()} (Ekran Kaydı izni?)")
    else:
        serial = pick_serial(a.serial)
        try:
            with open(out, "wb") as f:
                r = subprocess.run([_adb(), "-s", serial, "exec-out", "screencap", "-p"], stdout=f,
                                   stdin=subprocess.DEVNULL, timeout=60)
            failed = r.returncode != 0
        except subprocess.TimeoutExpired:
            failed = True
        if failed:
            if os.path.exists(out):
                os.remove(out)  # yarım PNG kalmasın
            raise PromoError("tablet ekran görüntüsü alınamadı")
    print(out)


def capture_list(a):
    print("Mac ekranları (capture mac --display <sıra|id:N|ad>):")
    try:
        for x in mac_displays():
            tag = " · ana" if x["main"] else ""
            print(f"  {x['index']}) {x['name']}  {x['pixels'][0]}×{x['pixels'][1]} @ {x['hz']:g} Hz{tag}  (id:{x['id']})")
    except PromoError as e:
        print(f"  — {e}")
    print("adb cihazları (capture … --serial <seri>):")
    try:
        devs = adb_devices()
        for s, m in devs:
            print(f"  {s}  ({m})")
        if not devs:
            print("  — yok")
    except PromoError as e:
        print(f"  — {e}")


def add_parser(sub):
    p = sub.add_parser("capture", help="Mac/tablet ekranı + dokunuş kaydı")
    cs = p.add_subparsers(dest="what", required=True)
    cs.add_parser("list", help="ekranları ve adb cihazlarını listele")
    m = cs.add_parser("mac", help="Mac ekranını kaydet (ScreenCaptureKit)")
    m.add_argument("name", nargs="?", help="çekim adı (work/takes/<ad>)")
    m.add_argument("--display", help="sıra no (capture list), id:<ekran kimliği> ya da ad parçası "
                                     "(vars.: ana ekran)")
    m.add_argument("--fps", type=int, default=60)
    m.add_argument("--codec", choices=["hevc", "h264", "prores"], default="hevc")
    m.add_argument("--mbps", type=float, help="bit hızı (vars.: çözünürlükten)")
    m.add_argument("--max-width", type=int, help="genişliği sınırla (ör. 3840)")
    m.add_argument("--no-cursor", action="store_true", help="Mac imlecini kayda katma")
    m.add_argument("--no-touches", dest="touches", action="store_false", help="tablet dokunuşlarını kaydetme")
    m.add_argument("--serial", help="adb seri no (birden çok cihaz varsa)")
    m.add_argument("--seconds", type=float, help="süre (vars.: Enter'a kadar)")
    t = cs.add_parser("tablet", help="tablet ekranını kaydet (adb screenrecord)")
    t.add_argument("name", nargs="?")
    t.add_argument("--serial")
    t.add_argument("--mbps", type=float, default=40.0)
    t.add_argument("--size", help="WxH (vars.: ekran boyutu)")
    t.add_argument("--no-touches", dest="touches", action="store_false")
    t.add_argument("--seconds", type=float, help="süre (vars.: Enter'a kadar; Android ≤ 13 en fazla 180 sn)")
    s = cs.add_parser("shot", help="ekran görüntüsü")
    s.add_argument("target", choices=["mac", "tablet"])
    s.add_argument("name", nargs="?")
    s.add_argument("--display", help="sıra no, id:<ekran kimliği> ya da ad parçası (vars.: ana ekran)")
    s.add_argument("--serial")


def run(a):
    {"list": capture_list, "mac": capture_mac, "tablet": capture_tablet, "shot": capture_shot}[a.what](a)
