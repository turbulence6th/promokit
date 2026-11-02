// promo.js — HTML sayfalarının çalışma zamanı (tanıtım videosu aracı). Sayfaya ARAÇ enjekte eder; siz eklemezsiniz.
//
// Sayfa gerçek zamanda ÇALIŞMAZ: Python her kare için `__promoSeekFrame(k)` çağırır, ardından ekran görüntüsü
// alır. Durum SABİT bir kare ızgarasında ilerler — adım k = ((k−1)/fps, k/fps]. Bir kareye doğrudan atlamak da
// kare kare gelmek de AYNI adım dizisini koşar → paralel render parçalarının sınırında dikiş yok
// (kilit: tests/test_html.py `test_fresh_seek_equals_stepping`). Her adımda sırasıyla:
//   1. kaydırma zaman çizelgesi uygulanır (senaryodaki `scroll = [...]`; sayfa GERÇEKTEN kaydırılır)
//   2. 'frame' olayı (sayfa koreografisi: `promo.on('frame', ({t, k}) => …)`)
//   3. yeni CSS animasyon/geçişleri O ADIMIN zamanına sabitlenir ve hepsi o zamana sarılır
//
// Sayfa sözleşmesi:
//   • CSS animasyonları ve geçişleri serbestçe kullanılır — araç onları kareye sabitler (Web Animations API).
//   • Kod gerektiren hareket için `promo.on('frame', ({t}) => …)`; t = sahne içi sayfa zamanı (sn).
//   • `promo.params` = senaryodaki `params` (+ `lang`) — sayfa betikleri çalışırken HAZIR; `promo.W/H` = görünüm
//     alanı (CSS px); `promo.scrollY` = o karedeki kaydırma konumu.
//   • ⛔ Sayfada requestAnimationFrame / setTimeout / setInterval / Date.now ile ZAMAN tutmayın: her şey
//     `promo.t`'den. (Gerçek zaman bu çekimde işlemez; kare ızgarası ilerler.)
//   • CSS `scroll-behavior: smooth` araç tarafından kapatılır (kaydırma zaten yumuşatılıyor).
//   • `IntersectionObserver` ve `scroll` olayları kare ızgarasına bağlıdır ("görününce belir" efektleri her renderda aynı
//     karede tetiklenir). `scroll` dinleyicileri idempotent olmalı.
(() => {
  if (window.promo) return;
  const boot = window.__promoBoot || {};   // araç, sayfa betiklerinden ÖNCE verir → `promo.params` yüklenirken okunabilir
  const promo = window.promo = { t: 0, fps: boot.fps || 60, W: window.innerWidth, H: window.innerHeight, scrollY: 0,
                                 params: boot.params || {}, _h: {}, state: {} };
  promo.on = (type, fn) => { (promo._h[type] = promo._h[type] || []).push(fn); return promo; };
  const emit = (type, ev) => { for (const f of promo._h[type] || []) f(ev); };

  // Yumuşatma eğrileri — Python `util.ease` ile AYNI adlar/formüller
  const clamp01 = (x) => Math.max(0, Math.min(1, x));
  const EASE = {
    linear: (t) => t,
    in: (t) => t * t * t,
    out: (t) => 1 - Math.pow(1 - t, 3),
    inout: (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
    sine: (t) => -(Math.cos(Math.PI * t) - 1) / 2,
    outback: (t) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); },
    quart: (t) => 1 - Math.pow(1 - t, 4),
  };
  promo.ease = (t, kind) => (EASE[kind || 'inout'] || EASE.inout)(clamp01(t));

  let S = null;
  const born = new WeakMap();

  // Bu adımda doğan animasyon/geçişler O ADIMIN zamanına sabitlenir; hepsi t'ye sarılır (adım başına).
  // Kaydırma-zaman-çizelgeli CSS animasyonları (`animation-timeline: scroll()/view()`) belge zamanında DEĞİL →
  // dokunulmaz: gerçek kaydırmayla zaten doğru ilerler.
  function scrub(t) {
    for (const a of document.getAnimations()) {
      if (a.timeline && a.timeline !== document.timeline) continue;
      if (!born.has(a)) born.set(a, t);
      a.pause();
      a.currentTime = Math.max(0, (t - born.get(a)) * 1000);
    }
  }

  // ── kaydırma ──
  const docEl = () => document.scrollingElement || document.documentElement;
  const maxY = () => (S.scrollEl ? S.scrollEl.scrollHeight - S.scrollEl.clientHeight : docEl().scrollHeight - window.innerHeight);
  function setY(y) {
    if (S.scrollEl) S.scrollEl.scrollTop = y; else window.scrollTo(0, y);
    return S.scrollEl ? S.scrollEl.scrollTop : window.scrollY;
  }
  function topOf(el) {   // öğenin kaydırma içeriğindeki mutlak üst konumu
    const r = el.getBoundingClientRect();
    return S.scrollEl ? r.top - S.scrollEl.getBoundingClientRect().top + S.scrollEl.scrollTop : r.top + window.scrollY;
  }
  function targetOf(m) {   // hareketin varış konumu (px), kaydırılabilir aralığa sıkıştırılmış
    let y = m.y;
    if (m.to != null) {
      let el = null;
      try { el = document.querySelector(m.to); } catch (e) { throw new Error('scroll to: geçersiz seçici ' + m.to + ' — ' + e); }
      if (!el) throw new Error('scroll to: seçici sayfada yok: ' + m.to);
      y = topOf(el) + (m.offset || 0);
    }
    return Math.max(0, Math.min(maxY(), y));
  }
  // Hareketler SIRALI ve birbirini beklemez: her biri `t`de, o anki konumdan başlayıp `dur` sn'de hedefe varır.
  // Konum zamanın SAF fonksiyonu → doğrudan seek ile kare-kare aynı sonucu verir.
  function scrollAt(t) {
    let cur = 0;
    for (const m of S.moves) {
      if (t < m.t) return cur;
      const target = targetOf(m);
      const k = m.dur > 0 ? (t - m.t) / m.dur : 1;
      if (k < 1) return cur + (target - cur) * promo.ease(k, m.ease);
      cur = target;
    }
    return cur;
  }

  // ── IntersectionObserver (kare ızgarasına bağlı) ──
  // Gerçek IntersectionObserver geri çağrıları render fırsatında, GERÇEK zamanda koşar: doğrudan bir kareye atlayan
  // (paralel render parçası) ile kare kare gelen oynatma "görününce belir" efektini FARKLI karelerde tetiklerdi.
  // Bu eşzamanlı sürüm her adımın içinde, kaydırmadan hemen sonra koşar → atlayarak gelmek de adım adım gelmek de
  // aynı geri çağrı dizisini üretir. Kök = görünüm alanı ya da `root` öğesi; `rootMargin` (px/%) ve `threshold` desteklenir.
  const observers = new Set();
  const marginPx = (s, W, H) => {
    const p = String(s || '0px').trim().split(/\s+/).map((v) => v.endsWith('%') ? [parseFloat(v), true] : [parseFloat(v) || 0, false]);
    const [t, r, b, l] = [p[0], p[1] || p[0], p[2] || p[0], p[3] || p[1] || p[0]];
    return [t[1] ? H * t[0] / 100 : t[0], r[1] ? W * r[0] / 100 : r[0], b[1] ? H * b[0] / 100 : b[0], l[1] ? W * l[0] / 100 : l[0]];
  };
  class PromoIntersectionObserver {
    constructor(cb, opts) {
      opts = opts || {};
      this._cb = cb; this._targets = new Map();
      this.root = opts.root || null; this.rootMargin = opts.rootMargin || '0px';
      const th = opts.threshold;
      this.thresholds = (Array.isArray(th) ? th.slice() : [th == null ? 0 : th]).sort((a, b) => a - b);
      observers.add(this);
    }
    observe(el) { if (!this._targets.has(el)) this._targets.set(el, null); }
    unobserve(el) { this._targets.delete(el); }
    disconnect() { this._targets.clear(); }
    takeRecords() { return []; }
    _check(time) {
      const W = window.innerWidth, H = window.innerHeight, out = [];
      const base = this.root ? this.root.getBoundingClientRect() : { left: 0, top: 0, right: W, bottom: H };
      const [mt, mr, mb, ml] = marginPx(this.rootMargin, base.right - base.left, base.bottom - base.top);
      const rb = { left: base.left - ml, top: base.top - mt, right: base.right + mr, bottom: base.bottom + mb };
      for (const [el, last] of this._targets) {
        const r = el.getBoundingClientRect();
        const has = el.isConnected && el.getClientRects().length > 0;
        const iw = Math.min(r.right, rb.right) - Math.max(r.left, rb.left);
        const ih = Math.min(r.bottom, rb.bottom) - Math.max(r.top, rb.top);
        const area = r.width * r.height;
        const hit = has && iw >= 0 && ih >= 0;
        const ratio = !hit ? 0 : area > 0 ? (iw * ih) / area : 1;
        const isInt = hit && (area === 0 || (iw > 0 && ih > 0));
        let idx = 0;
        if (isInt) { idx = this.thresholds.findIndex((x) => x > ratio); if (idx < 0) idx = this.thresholds.length; }
        if (last && last.isInt === isInt && last.idx === idx) continue;
        this._targets.set(el, { isInt, idx });
        const ir = hit ? { left: Math.max(r.left, rb.left), top: Math.max(r.top, rb.top), width: Math.max(0, iw), height: Math.max(0, ih) }
                       : { left: 0, top: 0, width: 0, height: 0 };
        const rect = (o) => ({ ...o, right: o.left + o.width, bottom: o.top + o.height, x: o.left, y: o.top });
        out.push({ target: el, time, isIntersecting: isInt, intersectionRatio: ratio,
                   boundingClientRect: r, intersectionRect: rect(ir),
                   rootBounds: this.root ? base : { left: 0, top: 0, right: W, bottom: H, width: W, height: H, x: 0, y: 0 } });
      }
      if (out.length) this._cb(out, this);
    }
  }
  window.IntersectionObserver = PromoIntersectionObserver;
  const checkObservers = (t) => { for (const o of Array.from(observers)) o._check(t * 1000); };

  function advance(k) {
    const t = k / promo.fps;
    promo.t = t;
    if (S.moves.length) {
      const before = promo.scrollY;
      promo.scrollY = setY(scrollAt(t));
      // Tarayıcı `scroll` olayını render fırsatında (gerçek zamanda) atar; adım içinde de eşzamanlı bir tane atıyoruz →
      // atlayarak gelen oynatma da dinleyiciyi aynı karede koşturur. Dinleyiciler idempotent olmalı (olağan durum).
      if (promo.scrollY !== before) (S.scrollEl || document).dispatchEvent(new Event('scroll', { bubbles: !S.scrollEl }));
    }
    checkObservers(t);
    emit('frame', { t, k });
    scrub(t);
    S.k = k;
  }

  const BASE_CSS = 'html,body,*{scroll-behavior:auto !important}';

  window.__promoInit = (data) => {
    const st = document.createElement('style');
    st.textContent = BASE_CSS;
    document.head.prepend(st);
    promo.fps = data.fps || promo.fps;
    promo.params = data.params || promo.params;
    promo.W = window.innerWidth;
    promo.H = window.innerHeight;
    const sc = data.scroll || {};
    let scrollEl = null;
    if (sc.target) {
      try { scrollEl = document.querySelector(sc.target); } catch (e) { throw new Error('scroller: geçersiz seçici ' + sc.target + ' — ' + e); }
      if (!scrollEl) throw new Error('scroller: seçici sayfada yok: ' + sc.target);
    }
    S = { k: 0, moves: (sc.moves || []).slice().sort((a, b) => a.t - b.t), scrollEl };
    promo.state = S;
    for (const m of S.moves) targetOf(m);   // seçici hataları ilk karede, render'ın ortasında değil
    promo.t = 0;
    if (S.moves.length) promo.scrollY = setY(scrollAt(0));
    checkObservers(0);
    emit('init', { data });
    emit('frame', { t: 0, k: 0 });
    scrub(0);
    return true;
  };

  window.__promoSeekFrame = (k) => {
    if (!S) throw new Error('__promoInit çağrılmadı');
    k = Math.max(0, Math.floor(k));
    while (S.k < k) advance(S.k + 1);
    return S.k;
  };
  window.__promoSeek = (t) => window.__promoSeekFrame(Math.floor(t * promo.fps + 1e-3));
})();
