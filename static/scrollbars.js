// Overlay scroll bars for mouse devices. The native bars are hidden (see style.css); a slim bar fades in while something scrolls or when the pointer
// comes near the edge of a scrollable area, and fades out again after a moment. One vertical and one horizontal bar are shared by all scroll areas
// (only one is in use at a time). Touch devices and the app keep the system's own overlay bars: nothing happens there.
(function () {
  'use strict';
  if (!window.matchMedia || !matchMedia('(hover: hover) and (pointer: fine)').matches) return;

  const HIDE_AFTER = 1100;   // ms without activity until the bars fade out
  const EDGE = 16;           // px from the edge of a scroll area at which the pointer brings the bar back
  const MIN_THUMB = 36;      // px, so the thumb stays easy to grab in a very long list
  const INSET = 3;           // px between the bar and the edge of the scroll area
  const SKIP = '.pager, .f-pills, #tabs, .chips';  // controls that scroll sideways: no bar wanted
  const QUIET = '.f-lyr';    // scrolls by itself (lyrics follow the song): shows its bar only when the pointer asks for it

  const make = (axis) => {
    const track = document.createElement('div');
    track.className = 'osb osb-' + axis;
    track.setAttribute('aria-hidden', 'true');
    const thumb = document.createElement('div');
    thumb.className = 'osb-thumb';
    track.appendChild(thumb);
    document.body.appendChild(track);
    return { track, thumb, axis };
  };
  const bars = { y: make('y'), x: make('x') };
  let current = null;   // the scroll area the bars belong to
  let timer = 0;
  let drag = null;
  let overBar = false;

  const root = () => document.scrollingElement || document.documentElement;
  const isRoot = (el) => el === root() || el === document.documentElement || el === document.body;

  function metrics(el) {
    if (isRoot(el)) {
      const r = root();
      return { sh: r.scrollHeight, ch: window.innerHeight, sw: r.scrollWidth, cw: window.innerWidth, st: r.scrollTop, sl: r.scrollLeft };
    }
    return { sh: el.scrollHeight, ch: el.clientHeight, sw: el.scrollWidth, cw: el.clientWidth, st: el.scrollTop, sl: el.scrollLeft };
  }

  // The part of the scroll area that is really visible (inside the window and inside every clipping ancestor).
  function visibleRect(el) {
    let r = isRoot(el) ? { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight } : el.getBoundingClientRect();
    r = { left: Math.max(r.left, 0), top: Math.max(r.top, 0), right: Math.min(r.right, window.innerWidth), bottom: Math.min(r.bottom, window.innerHeight) };
    for (let p = isRoot(el) ? null : el.parentElement; p && p !== document.body && p !== document.documentElement; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (/(auto|scroll|hidden|clip)/.test(cs.overflowX + cs.overflowY)) {
        const b = p.getBoundingClientRect();
        r = { left: Math.max(r.left, b.left), top: Math.max(r.top, b.top), right: Math.min(r.right, b.right), bottom: Math.min(r.bottom, b.bottom) };
      }
    }
    return r;
  }

  const scrollableY = (el) => { const m = metrics(el); return m.sh - m.ch > 1 && (isRoot(el) || /(auto|scroll)/.test(getComputedStyle(el).overflowY)); };
  const scrollableX = (el) => { const m = metrics(el); return m.sw - m.cw > 1 && (isRoot(el) || /(auto|scroll)/.test(getComputedStyle(el).overflowX)); };

  function layout() {
    if (!current || !current.isConnected) { hideNow(); return; }
    const m = metrics(current), v = visibleRect(current);
    const raw = isRoot(current) ? v : current.getBoundingClientRect();
    // A bar sits on the right / bottom edge of its area: if that edge is cut off (scrolled out of a parent), there is no place for the bar.
    const vy = scrollableY(current) && raw.right <= v.right + 1, vx = scrollableX(current) && raw.bottom <= v.bottom + 1;
    const w = v.right - v.left, h = v.bottom - v.top;
    if (w < 40 || h < 40) { hideNow(); return; }
    const place = (bar, on, track, size, total, pos) => {
      bar.track.classList.toggle('on', on);
      if (!on) return;
      const thumb = Math.max(MIN_THUMB, Math.round(track * size / total));
      const offset = total - size > 0 ? Math.round((track - thumb) * pos / (total - size)) : 0;
      bar.thumb.style[bar.axis === 'y' ? 'height' : 'width'] = thumb + 'px';
      bar.thumb.style.transform = bar.axis === 'y' ? `translateY(${offset}px)` : `translateX(${offset}px)`;
      bar.metrics = { track, thumb, range: total - size };
    };
    const trackY = h - INSET * 2 - (vx ? 10 : 0), trackX = w - INSET * 2 - (vy ? 10 : 0);
    Object.assign(bars.y.track.style, { left: (v.right - INSET - 10) + 'px', top: (v.top + INSET) + 'px', height: Math.max(trackY, 0) + 'px' });
    Object.assign(bars.x.track.style, { left: (v.left + INSET) + 'px', top: (v.bottom - INSET - 10) + 'px', width: Math.max(trackX, 0) + 'px' });
    place(bars.y, vy, trackY, m.ch, m.sh, m.st);
    place(bars.x, vx, trackX, m.cw, m.sw, m.sl);
    if (!vy && !vx) hideNow();
  }

  function hideNow() {
    clearTimeout(timer);
    bars.y.track.classList.remove('on');
    bars.x.track.classList.remove('on');
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(() => { if (drag || overBar) schedule(); else hideNow(); }, HIDE_AFTER);
  }

  function show(el) {
    current = el;
    layout();
    schedule();
  }

  // ---- what brings the bars up ----

  document.addEventListener('scroll', (e) => {
    if (drag) return;
    const el = e.target === document ? root() : e.target;
    if (!(el instanceof Element) && el !== root()) return;
    if (el !== root() && (el.matches(SKIP) || el.matches(QUIET))) return;
    show(el);
  }, true);

  // The pointer near the edge of a scroll area (vertical: right edge, horizontal: bottom edge) asks for its bar.
  let moveBusy = false;
  document.addEventListener('pointermove', (e) => {
    if (moveBusy || drag || e.pointerType === 'touch') return;
    moveBusy = true;
    requestAnimationFrame(() => {
      moveBusy = false;
      for (let el = e.target instanceof Element ? e.target : null; el; el = el.parentElement) {
        if (el.matches && el.matches(SKIP)) continue;
        const y = el !== document.documentElement && el !== document.body ? scrollableY(el) : false;
        const x = el !== document.documentElement && el !== document.body ? scrollableX(el) : false;
        if (!y && !x) continue;
        const v = visibleRect(el);
        if ((y && e.clientX >= v.right - EDGE && e.clientX <= v.right && e.clientY >= v.top && e.clientY <= v.bottom)
            || (x && e.clientY >= v.bottom - EDGE && e.clientY <= v.bottom && e.clientX >= v.left && e.clientX <= v.right)) {
          show(el);
        }
        return;  // the innermost scroll area under the pointer decides
      }
    });
  }, { passive: true, capture: true });

  window.addEventListener('resize', () => { if (current && (bars.y.track.classList.contains('on') || bars.x.track.classList.contains('on'))) layout(); });

  // ---- dragging the thumb ----

  for (const bar of [bars.y, bars.x]) {
    bar.track.addEventListener('pointerenter', () => { overBar = true; clearTimeout(timer); });
    bar.track.addEventListener('pointerleave', () => { overBar = false; if (!drag) schedule(); });
    bar.thumb.addEventListener('pointerdown', (e) => {
      if (!current || !bar.metrics || e.button !== 0) return;
      e.preventDefault();
      const m = metrics(current);
      drag = { bar, el: current, start: bar.axis === 'y' ? e.clientY : e.clientX, from: bar.axis === 'y' ? m.st : m.sl,
               ratio: bar.metrics.range / Math.max(1, bar.metrics.track - bar.metrics.thumb), smooth: current.style.scrollBehavior };
      if (!isRoot(current)) current.style.scrollBehavior = 'auto';  // a smooth-scrolling area would lag behind the thumb
      bar.track.classList.add('drag');
      bar.thumb.setPointerCapture(e.pointerId);
    });
    bar.thumb.addEventListener('pointermove', (e) => {
      if (!drag || drag.bar !== bar) return;
      const delta = ((bar.axis === 'y' ? e.clientY : e.clientX) - drag.start) * drag.ratio;
      const target = isRoot(drag.el) ? window : drag.el;
      if (bar.axis === 'y') target.scrollTo ? target.scrollTo({ top: drag.from + delta, behavior: 'instant' }) : (target.scrollTop = drag.from + delta);
      else target.scrollTo ? target.scrollTo({ left: drag.from + delta, behavior: 'instant' }) : (target.scrollLeft = drag.from + delta);
      layout();
    });
    const end = (e) => {
      if (!drag || drag.bar !== bar) return;
      if (!isRoot(drag.el)) drag.el.style.scrollBehavior = drag.smooth;
      bar.track.classList.remove('drag');
      try { bar.thumb.releasePointerCapture(e.pointerId); } catch (_) { /* already released */ }
      drag = null;
      schedule();
    };
    bar.thumb.addEventListener('pointerup', end);
    bar.thumb.addEventListener('pointercancel', end);
  }
})();
