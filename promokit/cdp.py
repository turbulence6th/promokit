"""Başsız Chromium (Brave/Chrome) sürücüsü — Chrome DevTools Protocol, yalnız stdlib.

HTML ekran sayfalarını kare kare çekmek için: sayfaya kare verilir (`__promoSeekFrame(k)`),
ardından `Page.captureScreenshot`. Gerçek zamanlı kayıt DEĞİL — her kare deterministik.

WebSocket istemcisi burada (RFC 6455'in CDP'ye yeten alt kümesi): istemci çerçeveleri
maskeli, sunucu çerçeveleri maskesiz; büyük yanıtlar (ekran görüntüsü) 64-bit uzunlukla gelir.

Süreç hijyeni: tarayıcı bir GÖZCÜ sürecin çocuğu olarak başlar. Gözcü bizim stdin borumuzu okur;
boru kapanınca — `close()` ya da bu Python süreci SIGKILL ile ölünce (çekirdek boruyu kapatır) —
tarayıcıyı sonlandırır ve geçici profili siler. Arkada sahipsiz başsız tarayıcı (açık, kimliksiz bir
DevTools portuyla) kalmaz.
"""
from __future__ import annotations

import base64
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from PIL import Image

from .util import PromoError

_MAC_APPS = ["Google Chrome.app/Contents/MacOS/Google Chrome", "Brave Browser.app/Contents/MacOS/Brave Browser",
             "Chromium.app/Contents/MacOS/Chromium", "Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
BROWSERS = (["/Applications/" + a for a in _MAC_APPS] + ["~/Applications/" + a for a in _MAC_APPS] + [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe",
    "/usr/bin/chromium", "/usr/bin/google-chrome",
])

# ⛔ `--allow-file-access-from-files` YOK: sayfalar yan dosyalarını (CSS/JS/SVG) <script>/<link>/<img> ile
# yüklüyor ve bunlar file:// sayfasında bayraksız çalışır (ölçüldü: examples/site/ yükleniyor). Bayrak yalnız fetch/XHR ile HER yerel dosyayı okumayı açıyordu — ağa açık başsız tarayıcıda
# gereksiz bir sızdırma yüzeyi (tests/test_html.py `test_pages_load_without_file_access_flag`).
# ⛔ `MacAppCodeSignClone` KAPALI (2026-10-23 açılış kilidi): Chromium macOS'ta her açılışta uygulamanın ~1.433
# dosyalık klonunu /var/folders/…/X/ altına açar ve yalnız düzenli kapanışta siler; kalanları macOS açılışta
# boşaltır ve bitene kadar BEKLER — test + mutasyon koşularının bıraktığı ~49,5k klon (~71M dosya) Mac'i saatlerce
# açtırmadı. TEK `--disable-features` bayrağı: ikincisi ilkini EZER (ölçüldü: klon yine açıldı).
# Kapı: tests/test_browser_clone.py.
BROWSER_FLAGS = ["--headless=new", "--remote-debugging-port=0", "--no-first-run", "--no-default-browser-check",
                 "--disable-extensions", "--disable-sync", "--disable-background-networking",
                 "--disable-component-update", "--hide-scrollbars", "--mute-audio", "--force-color-profile=srgb",
                 "--disable-features=Translate,MediaRouter,MacAppCodeSignClone"]

# Gözcü: tarayıcının EBEVEYNİ. stdin EOF (ebeveyn kapattı ya da öldü) → tarayıcıyı sonlandır + profili sil.
# Tarayıcı kendiliğinden ölürse gözcü de çıkar → ebeveyn `poll()` ile görür.
# Sonlandırma SIGINT → SIGTERM → SIGKILL: Chromium macOS'ta SIGTERM'de düzenli kapanmıyor ve code-sign klonunu
# bırakıyor, SIGINT'te siliyor (ölçüldü 2026-10-24) — bayrağın yanında İKİNCİ koruma. Windows'ta SIGINT
# gönderilemez (ValueError) → doğrudan SIGTERM. Aşama başına 3 sn: toplam 9 sn < close()'un 10 sn beklemesi
# (aşılırsa ebeveyn gözcüyü öldürür ve tarayıcı sahipsiz kalır).
_WATCHDOG = r"""
import os, shutil, signal, subprocess, sys, threading
prof, args = sys.argv[1], sys.argv[2:]
p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
closing = threading.Event()
def watch():
    p.wait()
    if not closing.is_set():
        os._exit(3)
threading.Thread(target=watch, daemon=True).start()
try:
    while sys.stdin.buffer.read(65536):
        pass
except BaseException:
    pass
closing.set()
for stop in (lambda: p.send_signal(signal.SIGINT), p.terminate, p.kill):
    try:
        stop()
        p.wait(3)
        break
    except BaseException:
        pass
shutil.rmtree(prof, ignore_errors=True)
"""


def find_browser() -> str:
    env = os.environ.get("PROMO_BROWSER")
    if env:
        if os.path.exists(env):
            return env
        raise PromoError(f"PROMO_BROWSER yolu yok: {env}")
    for p in BROWSERS:
        p = os.path.expanduser(p)
        if os.path.exists(p):
            return p
    for n in ("chromium", "google-chrome", "chrome", "brave"):
        p = shutil.which(n)
        if p:
            return p
    raise PromoError("HTML ekranlar için Chrome/Brave/Chromium gerekli (PROMO_BROWSER ile yol verilebilir)")


class WebSocket:
    def __init__(self, url: str, timeout: float = 60.0):
        u = urlparse(url)
        self.timeout = timeout
        try:
            self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        except OSError as e:
            raise PromoError(f"tarayıcıya bağlanılamadı ({url}): {e}")
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self._send(req.encode())
        self.buf = bytearray()
        while b"\r\n\r\n" not in self.buf:
            chunk = self._recv(4096)
            if not chunk:
                raise PromoError("WebSocket el sıkışması kapandı")
            self.buf += chunk
        head, _, rest = bytes(self.buf).partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise PromoError(f"WebSocket yükseltmesi reddedildi: {head[:120]!r}")
        self.buf = bytearray(rest)

    # Soket hataları/zaman aşımı ham `TimeoutError`/`OSError` olarak sızmasın: çağıranlar yalnız PromoError tanır
    def _recv(self, n: int) -> bytes:
        try:
            return self.sock.recv(n)
        except TimeoutError:
            raise PromoError(f"tarayıcı {self.timeout:g} sn içinde yanıt vermedi")
        except OSError as e:
            raise PromoError(f"tarayıcı bağlantısı koptu: {e}")

    def _send(self, data: bytes):
        try:
            self.sock.sendall(data)
        except TimeoutError:
            raise PromoError(f"tarayıcıya {self.timeout:g} sn içinde yazılamadı")
        except OSError as e:
            raise PromoError(f"tarayıcı bağlantısı koptu: {e}")

    def _exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self._recv(max(65536, n - len(self.buf)))
            if not chunk:
                raise PromoError("WebSocket bağlantısı koptu")
            self.buf += chunk
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def _frame(self, op: int, data: bytes):
        n = len(data)
        head = bytearray([0x80 | op])
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += n.to_bytes(2, "big")
        else:
            head.append(0x80 | 127)
            head += n.to_bytes(8, "big")
        mask = os.urandom(4)
        if n:
            m = (mask * (n // 4 + 1))[:n]
            data = (int.from_bytes(data, "big") ^ int.from_bytes(m, "big")).to_bytes(n, "big")
        self._send(bytes(head) + mask + data)

    def send(self, text: str):
        self._frame(0x1, text.encode())

    def recv(self) -> str:
        parts = []
        while True:
            b0, b1 = self._exact(2)
            op, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = int.from_bytes(self._exact(2), "big")
            elif n == 127:
                n = int.from_bytes(self._exact(8), "big")
            mask = self._exact(4) if b1 & 0x80 else None
            payload = self._exact(n)
            if mask:
                payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
            if op == 0x9:  # ping
                self._frame(0xA, payload)
                continue
            if op == 0x8:
                raise PromoError("tarayıcı bağlantıyı kapattı")
            if op in (0x1, 0x2, 0x0):
                parts.append(payload)
                if b0 & 0x80:
                    return b"".join(parts).decode("utf-8", errors="replace")

    def close(self):
        try:
            self._frame(0x8, b"")
        except Exception:
            pass
        self.sock.close()


class Browser:
    """Tek sekmeli başsız tarayıcı. `width×height` CSS piksel, `scale` = cihaz piksel oranı."""

    def __init__(self, width: int, height: int, scale: float = 1.0):
        self.exe = find_browser()
        self.tmp = tempfile.mkdtemp(prefix="promo-cdp-")
        self.proc = None
        self.ws = None
        self.port = None
        args = [self.exe, *BROWSER_FLAGS, f"--user-data-dir={self.tmp}", f"--window-size={width},{height}",
                "about:blank"]
        try:  # başlatma SONRASI her hata: süreç + profil temizlenir (yoksa sahipsiz tarayıcı kalıyordu)
            self.proc = subprocess.Popen([sys.executable, "-c", _WATCHDOG, self.tmp, *args], stdin=subprocess.PIPE,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            port_file = os.path.join(self.tmp, "DevToolsActivePort")
            t0 = time.time()
            while not os.path.exists(port_file) or os.path.getsize(port_file) == 0:
                if self.proc.poll() is not None:
                    raise PromoError(f"tarayıcı başlamadı: {self.exe}")
                if time.time() - t0 > 20:
                    raise PromoError("tarayıcı 20 sn içinde DevTools portu açmadı")
                time.sleep(0.05)
            with open(port_file) as f:
                self.port = int(f.readline().strip())
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    info = json.load(r)
            except (urllib.error.URLError, OSError, ValueError) as e:
                raise PromoError(f"tarayıcı sekmesi açılamadı: {e}")
            self.ws = WebSocket(info["webSocketDebuggerUrl"])
            self._id = 0
            self.events: list[dict] = []
            self.width, self.height, self.scale = width, height, scale
            self.call("Page.enable")
            self.call("Runtime.enable")
            self.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
                      deviceScaleFactor=scale, mobile=False)
        except BaseException:
            self.close()
            raise

    def call(self, method: str, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise PromoError(f"CDP {method}: {msg['error'].get('message')}")
                return msg.get("result", {})
            if "method" in msg and len(self.events) < 200:
                self.events.append(msg)

    def eval(self, expr: str, await_promise: bool = False):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=await_promise, returnByValue=True)
        if "exceptionDetails" in r:
            ex = r["exceptionDetails"]
            desc = (ex.get("exception") or {}).get("description") or ex.get("text")
            raise PromoError(f"sayfa betiği hata verdi: {desc}")
        return (r.get("result") or {}).get("value")

    def add_init_script(self, source: str):
        self.call("Page.addScriptToEvaluateOnNewDocument", source=source)

    def open(self, url: str, timeout: float = 20.0):
        self.call("Page.navigate", url=url)
        t0 = time.time()
        while True:
            try:
                if self.eval("document.readyState") == "complete":
                    break
            except PromoError:
                pass
            if time.time() - t0 > timeout:
                raise PromoError(f"sayfa yüklenmedi: {url}")
            time.sleep(0.03)
        self.eval("document.fonts ? document.fonts.ready.then(() => true) : true", await_promise=True)

    def screenshot(self, quality: int = 92) -> Image.Image:
        r = self.call("Page.captureScreenshot", format="jpeg", quality=quality, fromSurface=True,
                      captureBeyondViewport=False, optimizeForSpeed=True)
        im = Image.open(io.BytesIO(base64.b64decode(r["data"])))
        im.load()
        return im.convert("RGB")

    def close(self):
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None
        if self.proc is not None:
            try:
                self.proc.stdin.close()      # gözcü: tarayıcıyı sonlandır + profili sil
            except Exception:
                pass
            try:
                self.proc.wait(timeout=10)
            except Exception:
                try:
                    self.proc.kill()
                    self.proc.wait(timeout=5)
                except Exception:
                    pass
            self.proc = None
        shutil.rmtree(self.tmp, ignore_errors=True)
