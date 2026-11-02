#!/usr/bin/env bash
# promokit test koşucusu.
#
#   bash run-tests.sh
#
# ⛔ NEDEN çıplak `python3 -m unittest` DEĞİL: HTML ekran testleri başsız bir Chromium tarayıcısı
# (Chrome/Brave/Chromium) ister; bulunamazsa testler ATLANIR ve unittest yine "OK (skipped=N)" deyip
# exit 0 verir → tarayıcısız bir makinede (CI dahil) sayfa oynatıcı kilitleri hiç koşmadan "yeşil" görünür.
# Bu betik atlamayı HATA sayar.
#
# macOS notu: Chromium her açılışta /var/folders/…/X/<kimlik>.code_sign_clone/ altına ~1.400 dosyalık klon
# açar ve yalnız düzenli kapanışta siler; geride kalanları macOS bir sonraki AÇILIŞTA boşaltır ve bitene
# kadar BEKLER (orijinal projede binlerce klon birikince Mac saatlerce açılmadı). Araç tarayıcıyı bu klonu
# açmayacak şekilde başlatır (promokit/cdp.py) ve bu betik koşu sonunda geride klon kalmadığını denetler.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
X_DIR="$(dirname "$(getconf DARWIN_USER_TEMP_DIR 2>/dev/null || echo /yok/T/)")/X"
clones() { find "$X_DIR" -mindepth 2 -maxdepth 2 -path '*.code_sign_clone/*' 2>/dev/null | LC_ALL=C sort || true; }
clones_before=$(clones)
clone_gate() {
  local rc=$? leaked
  leaked=$(LC_ALL=C comm -13 <(printf '%s\n' "$clones_before") <(clones) | sed '/^$/d')
  if [ -n "$leaked" ]; then
    echo "✗ başsız tarayıcı $(printf '%s\n' "$leaked" | wc -l | tr -d ' ') code-sign klonu bıraktı:" >&2
    printf '%s\n' "$leaked" | sed 's/^/  /' >&2
    [ "$rc" -ne 0 ] || rc=4
  fi
  exit "$rc"
}
trap clone_gate EXIT
out=$(python3 -B -m unittest discover -s tests 2>&1) && rc=0 || rc=$?
printf '%s\n' "$out" | tail -n 25
if [ "$rc" -ne 0 ]; then
  echo "✗ promokit testleri KIRMIZI" >&2
  exit "$rc"
fi
if printf '%s\n' "$out" | grep -q "skipped="; then
  echo "✗ testler ATLANDI — tarayıcı (Chrome/Brave/Chromium) bulunamadı mı? PROMO_BROWSER=<yol> verin." >&2
  exit 3
fi
