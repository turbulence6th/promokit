"""Başsız tarayıcı macOS'ta iz bırakmasın — Chromium'un code-sign klonu (2026-10-23 açılış kilidi).

Chromium (Brave/Chrome) macOS'ta her açılışta uygulama paketinin ~1.433 dosyalık klonunu
`/var/folders/…/X/<paket kimliği>.code_sign_clone/` altına açar ve yalnız DÜZENLİ kapanışta siler.
`cdp.py` tarayıcıyı SIGTERM'le kapatıyordu → her açılış bir klon bıraktı; test + mutasyon koşuları
~49,5k klon (~71M dosya) biriktirdi ve macOS açılışta bu klasörü boşaltırken saatlerce bekledi
(FileVault şifresinden sonra masaüstü gelmedi).

İki bağımsız koruma — tek bir mutant (ya da tek bir kazara silme) sızıntıya dönmesin:
  1. `--disable-features=…,MacAppCodeSignClone` → klon hiç açılmaz.
  2. Gözcü tarayıcıyı önce SIGINT'le kapatır → düzenli kapanış, klon silinir
     (ölçüldü 2026-10-24: SIGTERM'de klon kalıyor, SIGINT'te silinmiş).
Her test korumalardan birini TEK BAŞINA sınar ve kendi açtığı klonu siler (kapı ısırdığında,
ör. mutasyon koşusunda, iz kalmasın). Süit düzeyindeki kapı: scripts/test-promo.sh.
"""
import os
import plistlib
import shutil
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from promokit import cdp  # noqa: E402
from promokit.util import PromoError  # noqa: E402

FEATURE = "MacAppCodeSignClone"
APPEAR_S = 2.0    # ölçüldü: klon başlatmadan +0,24 sn'de, Browser() dönmeden ÖNCE beliriyor
CLEANUP_S = 10.0  # düzenli kapanışta klonu ayrı bir temizlik süreci siler — çıkıştan biraz sonra


def _clone_root():
    """<DARWIN_USER_TEMP_DIR'in üstü>/X/<paket kimliği>.code_sign_clone — tarayıcı .app değilse None."""
    try:
        exe = cdp.find_browser()
    except PromoError:
        return None
    if ".app/" not in exe:
        return None
    with open(os.path.join(exe.split(".app/")[0] + ".app", "Contents", "Info.plist"), "rb") as f:
        bundle_id = plistlib.load(f)["CFBundleIdentifier"]
    tmp = subprocess.run(["getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True, text=True).stdout.strip()
    return os.path.join(os.path.dirname(tmp.rstrip("/")), "X", bundle_id + ".code_sign_clone")


def _ls(root):
    return set(os.listdir(root)) if os.path.isdir(root) else set()


def _new_clones(root, before, timeout):
    """`timeout` içinde beliren yeni klonlar (ilk görüldüğü an döner)."""
    end = time.time() + timeout
    while True:
        new = _ls(root) - before
        if new or time.time() >= end:
            return new
        time.sleep(0.05)


def _remaining(root, names, timeout):
    """`names`ten `timeout` sonunda hâlâ duranlar (hepsi silinince erken döner)."""
    end = time.time() + timeout
    while True:
        left = names & _ls(root)
        if not left or time.time() >= end:
            return left
        time.sleep(0.1)


def _without(flags, feature):
    """`--disable-features` listesinden `feature`ı çıkar — koruma 2'yi bayraksız sınamak için."""
    out = []
    for f in flags:
        if f.startswith("--disable-features="):
            f = "--disable-features=" + ",".join(x for x in f.split("=", 1)[1].split(",") if x != feature)
        out.append(f)
    return out


@unittest.skipUnless(sys.platform == "darwin", "code-sign klonu yalnız macOS'ta")
class TestNoCodeSignClone(unittest.TestCase):
    def setUp(self):
        self.root = _clone_root()
        if self.root is None:
            self.skipTest("Chrome/Brave (.app) yok")
        self.before = _ls(self.root)

    def tearDown(self):
        for name in _ls(self.root) - self.before:
            if name.startswith("code_sign_clone."):
                shutil.rmtree(os.path.join(self.root, name), ignore_errors=True)

    def test_open_browser_makes_no_clone(self):
        """Koruma 1 (bayrak): açık tarayıcı klon AÇMAZ — kapanış biçiminden bağımsız."""
        b = cdp.Browser(400, 300, 0.5)
        try:
            new = _new_clones(self.root, self.before, APPEAR_S)
        finally:
            b.close()
        self.assertEqual(new, set(), f"başsız tarayıcı code-sign klonu açtı — BROWSER_FLAGS'ta "
                                     f"--disable-features=…,{FEATURE} var mı? (TEK bayrak: ikincisi ilkini ezer)")

    def test_close_removes_clone_without_flag(self):
        """Koruma 2 (kapanış): bayraksız açılan tarayıcının klonu close() ile silinir."""
        saved = cdp.BROWSER_FLAGS
        cdp.BROWSER_FLAGS = _without(saved, FEATURE)
        try:
            b = cdp.Browser(400, 300, 0.5)
        finally:
            cdp.BROWSER_FLAGS = saved
        try:
            made = _new_clones(self.root, self.before, APPEAR_S)
        finally:
            b.close()
        self.assertTrue(made, "uyaran çalışmadı: bayraksız tarayıcı klon açmadı — bu test kapanışı ölçemiyor")
        self.assertEqual(_remaining(self.root, made, CLEANUP_S), set(),
                         "close() klonu bıraktı — gözcü tarayıcıyı önce SIGINT'le kapatmalı (SIGTERM'de klon kalır)")


if __name__ == "__main__":
    unittest.main()
