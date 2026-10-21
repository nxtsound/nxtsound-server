'use strict';

// ?v=<hash of the frontend> of this script: the files it loads itself (languages) get the same, so they can be kept for offline use
const ASSET_V = (() => { try { return new URL(document.currentScript.src).search; } catch (_) { return ''; } })();

const $ = (s, el = document) => el.querySelector(s);
// Block zoom (iOS partly ignores user-scalable)
['gesturestart', 'gesturechange', 'gestureend'].forEach((n) => document.addEventListener(n, (e) => e.preventDefault()));
// Short haptic feedback: Android via vibrate, iOS (17.4+) via a switch
let _hapt;
function haptic() {
  try {
    if (navigator.vibrate) { navigator.vibrate(12); return; }
    if (!_hapt) {
      _hapt = document.createElement('label');
      _hapt.className = 'hapt';
      _hapt.innerHTML = '<input type="checkbox" switch tabindex="-1" aria-hidden="true">';
      document.body.appendChild(_hapt);
    }
    _hapt.click();
  } catch (e) { /* no haptic device */ }
}
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
// ---------- Translations ----------
// Source strings in the code are English; /i18n/<lang>.json maps them to other languages (the same catalog the server uses).

const I18N = { lang: 'en', catalog: {} };
function tr(msg, vars) {
  let s = I18N.catalog[msg] || msg;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  return s;
}
const trn = (n, one, many, values = {}) => tr(n === 1 ? one : many, { n, ...values });  // singular/plural (more values can be passed for the text)
const locale = () => (I18N.lang === 'de' ? 'de-DE' : 'en-US');
function detectLang(pref) {  // pref: 'en' | 'de' | '' (automatic: browser language)
  if (pref === 'en' || pref === 'de') return pref;
  return String(navigator.language || 'en').toLowerCase().startsWith('de') ? 'de' : 'en';
}
// Elements with data-i18n (text), data-i18n-ph (placeholder), data-i18n-aria (aria-label), data-i18n-title (title) hold English source text.
function applyI18n(root = document) {
  root.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = tr(el.dataset.i18n); });
  root.querySelectorAll('[data-i18n-ph]').forEach((el) => { el.placeholder = tr(el.dataset.i18nPh); });
  root.querySelectorAll('[data-i18n-aria]').forEach((el) => { el.setAttribute('aria-label', tr(el.dataset.i18nAria)); });
  root.querySelectorAll('[data-i18n-title]').forEach((el) => { el.title = tr(el.dataset.i18nTitle); });
}
async function setLanguage(pref) {
  const lang = detectLang(pref);
  I18N.lang = lang;
  I18N.catalog = {};
  if (lang !== 'en') {
    try { I18N.catalog = await (await fetch(`/i18n/${lang}.json${ASSET_V}`)).json(); } catch (_) { /* English stays */ }
  }
  document.documentElement.lang = lang;
  document.cookie = `nx_lang=${lang}; path=/; max-age=31536000; samesite=lax`;  // the server translates its messages accordingly
  try { localStorage.setItem('app_lang', pref || ''); } catch (_) { /* no storage */ }
  applyI18n();
}

// Two audio elements: DJ mode crossfades onto the second one. `audio` always points at the active one and
// behaves like a single element (events of the inactive one are ignored).
const elA = $('#audio');
const elB = document.createElement('audio');
elB.preload = 'auto';
elB.setAttribute('playsinline', '');
elB.hidden = true;
document.body.appendChild(elB);
let curEl = elA;
const audio = new Proxy(elA, {
  get(_, k) {
    if (k === 'addEventListener') return (type, fn, opts) => [elA, elB].forEach((el) => el.addEventListener(type, (e) => { if (el === curEl) fn.call(el, e); }, opts));
    const v = curEl[k];
    return typeof v === 'function' ? v.bind(curEl) : v;
  },
  set(_, k, v) {
    if (k === 'volume' || k === 'muted') { elA[k] = v; elB[k] = v; } else curEl[k] = v;
    return true;
  },
});

// Project name from the server (configured centrally) so it is never hardcoded in the frontend
fetch('/api/server-info').then((r) => r.json()).then((i) => {
  if (!i.name) return;
  document.title = i.name;
  document.querySelectorAll('[data-appname]').forEach((el) => { el.textContent = i.name; });
}).catch(() => { /* default name stays */ });

// ---------- Icons (Material-Pfade) ----------

const PATHS = {
  search: 'M15.5 14h-.79l-.28-.27A6.47 6.47 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z',
  back: 'M15.41 7.41 14 6l-6 6 6 6 1.41-1.41L10.83 12z',
  up: 'M7.41 15.41 12 10.83l4.59 4.58L18 14l-6-6-6 6z',
  lyrics: 'M4 6h16v2H4zm0 5h16v2H4zm0 5h10v2H4z',
  disc: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 14a4 4 0 1 1 0-8 4 4 0 0 1 0 8zm0-5.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z',
  down: 'M16.59 8.59 12 13.17 7.41 8.59 6 10l6 6 6-6z',
  right: 'M10 6 8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z',
  history: 'M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z',
  fill: 'M5 15h2V8.41L18.59 20 20 18.59 8.41 7H15V5H5v10z',
  play: 'M8 5v14l11-7z',
  pause: 'M6 19h4V5H6v14zm8-14v14h4V5h-4z',
  next: 'M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z',
  prev: 'M6 6h2v12H6zm3.5 6 8.5 6V6z',
  shuffle: 'M10.59 9.17 5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z',
  repeat: 'M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z',
  repeat1: 'M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4zm-4-2V9h-1l-2 1v1h1.5v4H13z',
  like: 'M9 21h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2zM9 9l4.34-4.34L12 10h9v2l-3 7H9V9zM1 9h4v12H1V9z',
  liked: 'M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z',
  more: 'M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z',
  close: 'M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z',
  plus: 'M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z',
  drag: 'M20 9H4v2h16V9zM4 15h16v-2H4v2z',
  vol: 'M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z',
  mute: 'M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51A8.8 8.8 0 0 0 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3 3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06a8.99 8.99 0 0 0 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4 9.91 6.09 12 8.18V4z',
  home: 'M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z',
  people: 'M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5s-3 1.34-3 3 1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z',
  gear: 'M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.49.49 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.48.48 0 0 0-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z',
  bell: 'M12 22c1.1 0 2-.9 2-2h-4c0 1.1.89 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z',
  chev: 'M16.59 8.59 12 13.17 7.41 8.59 6 10l6 6 6-6z',
  power: 'M13 3h-2v10h2V3zm4.83 2.17-1.42 1.42C17.99 7.86 19 9.81 19 12c0 3.87-3.13 7-7 7s-7-3.13-7-7c0-2.19 1.01-4.14 2.58-5.42L6.17 5.17C4.23 6.82 3 9.26 3 12c0 4.97 4.03 9 9 9s9-4.03 9-9c0-2.74-1.23-5.18-3.17-6.83z',
  refresh: 'M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 12 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z',
  download: 'M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z',
  upload: 'M9 16h6v-6h4l-7-7-7 7h4v6zm-4 2h14v2H5v-2z',
  trash: 'M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z',
  check: 'M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4L9 16.2z',
  friends: 'M15 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm-9-2V7H4v3H1v2h3v3h2v-3h3v-2H6zm9 4c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z',
  lists: 'M3 13h2v-2H3v2zm0 4h2v-2H3v2zm0-8h2V7H3v2zm4 4h14v-2H7v2zm0 4h14v-2H7v2zM7 7v2h14V7H7z',
};
const SPIN = '<svg class="ic spinner" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-dasharray="38 100"/></svg>';
const IC = Object.fromEntries(Object.entries(PATHS).map(([k, d]) => [k, `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>`]));
document.querySelectorAll('[data-ic]').forEach((el) => { el.innerHTML = IC[el.dataset.ic] || ''; });

// ---------- State ----------

const S = {
  user: null,
  csrf: '',       // CSRF token of the session (header X-CSRF-Token on state-changing calls)
  ver: null,
  queue: [],
  idx: -1,
  lists: {},      // lists of the page currently shown, by key
  modes: {},      // 'radio' (tap starts radio) | 'pick' (search hit artist/album) | 'list' (tap plays the list)
  nav: 0,         // counter against stale answers when changing pages
  routes: 0,
  reported: false,
  extendP: null,
  radioToken: 0,
  plId: null,
  sheetActions: [],
  picker: null,
  likes: new Set(),
  repeat: 'off',  // off | all | one
  shuffle: false,
  errStreak: 0,
  loading: false,
  loadTimer: 0,
  resumeFor: '',
  lastPosSave: 0,
  wantPlay: false,
  hist: { queries: [], picks: [] },
  sugg: [],
  suggFor: '',   // the text S.sugg was fetched for
  suggSeq: 0,
  searchQ: '',
  scrub: false,
};

class ApiError extends Error {
  constructor(msg, status) { super(msg); this.status = status; }
}

// ---------- API ----------

async function api(path, opts = {}, timeoutMs = 25000) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  const init = { credentials: 'same-origin', method: opts.method || 'GET', signal: ctl.signal };
  if (opts.json !== undefined) {
    init.method = opts.method || 'POST';
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(opts.json);
  }
  if (init.method !== 'GET') init.headers = { ...init.headers, 'X-CSRF-Token': S.csrf };
  try {
    const r = await fetch('/api' + path, init);
    if (r.status === 401 && !opts.noRedirect) { showLogin(); throw new ApiError(tr('Not logged in'), 401); }
    if (!r.ok) {
      let msg = r.statusText || tr('Error');
      try {
        const j = await r.json();
        msg = Array.isArray(j.detail) ? (j.detail[0] && j.detail[0].msg) || msg : j.detail || msg;
      } catch (_) { /* not JSON */ }
      throw new ApiError(String(msg), r.status);
    }
    const data = await r.json();
    if (data && typeof data.csrf === 'string') S.csrf = data.csrf;
    return data;
  } catch (e) {
    if (e.name === 'AbortError') throw new ApiError(tr('Timed out, please try again'), 0);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

let toastTimer;
function toast(msg, ms = 2200, isErr = false) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.toggle('err', isErr);
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), ms);
}

const pick = (t) => ({
  id: t.id, title: t.title || '', artist: t.artist || '', artist_id: t.artist_id || '',
  album: t.album || '', album_id: t.album_id || '', duration: t.duration || '', thumb: t.thumb || '',
});

function go(hash) {
  closeFull();
  if (location.hash === hash) route();
  else location.hash = hash;
}

// Images: shimmer placeholder until they have loaded
document.addEventListener('load', (e) => { if (e.target.tagName === 'IMG') e.target.classList.add('ld'); }, true);
document.addEventListener('error', (e) => { if (e.target.tagName === 'IMG') e.target.classList.add('ld'); }, true);

const skeleton = () => '<div class="skel">' + '<div class="sk sk-h"></div><div class="sk-row">' + '<i class="sk"></i>'.repeat(4) + '</div>'.repeat(1) + '<div class="sk sk-h"></div><div class="sk-row">' + '<i class="sk"></i>'.repeat(4) + '</div></div>';

// ---------- Building blocks ----------

const cover = (url, cls = '') => (url
  ? `<img class="${cls}" loading="lazy" referrerpolicy="no-referrer" src="${esc(url)}" alt="">`
  : `<div class="ph ${cls}"></div>`);

const currentTrack = () => S.queue[S.idx] || null;
const currentId = () => (currentTrack() ? currentTrack().id : null);

const EQ = '<span class="eq" aria-hidden="true"><i></i><i></i><i></i></span>';

function trackRow(t, key, i) {
  const now = t.id === currentId() ? ' now' : '';
  // In lists the artist is not a link (too easy to tap it by accident). Still reachable via menu, player and album page.
  const artist = esc(t.artist);
  const dur = t.duration ? ` · ${esc(t.duration)}` : '';
  const drag = key === 'queue' ? `<span class="ico drag" data-drag aria-label="${esc(tr('Move'))}">${IC.drag}</span>` : '';
  return `<div class="track${now}" data-l="${key}" data-i="${i}">${cover(t.thumb)}<div class="meta"><div class="t">${EQ}${esc(t.title)}</div><div class="a">${artist}${dur}${srcTag(t)}</div></div>${drag}<button class="ico" data-menu="${i}" data-l="${key}" aria-label="${esc(tr('Menu'))}">${IC.more}</button></div>`;
}

function trackCard(t, key, i) {
  return `<div class="card" data-l="${key}" data-i="${i}">${cover(t.thumb)}<div class="t">${esc(t.title)}</div><div class="a">${esc(t.artist)}</div></div>`;
}

function artistCard(a) {
  return `<div class="card round" data-nav="#/artist/${esc(a.id)}">${cover(a.thumb, 'round')}<div class="t">${esc(a.name)}</div></div>`;
}

function albumCard(a) {
  const sub = [a.kind, a.year].filter(Boolean).join(' · ');
  return `<div class="card" data-nav="#/album/${esc(a.id)}">${cover(a.thumb)}<div class="t">${esc(a.title)}</div><div class="a">${esc(sub)}</div></div>`;
}

const loading = (msg = tr('Loading …')) => `<div class="empty">${esc(msg)}</div>`;
const errorBox = (msg, act = 'reload') => `<div class="empty">${esc(msg)}<br><br><button class="chip" data-act="${act}">${esc(tr('Try again'))}</button></div>`;

function setList(key, tracks, mode = 'list') { S.lists[key] = tracks; S.modes[key] = mode; }
function resetLists() { S.lists = {}; S.modes = {}; }

function rows(key) {
  const list = S.lists[key] || [];
  return list.length ? list.map((t, i) => trackRow(t, key, i)).join('') : `<div class="empty">${esc(tr('Nothing here.'))}</div>`;
}

const shuffled = (arr) => {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
  return a;
};

// ---------- Bottom sheet ----------

function openSheet(html) { $('#sheet .panel').innerHTML = html; $('#sheet').hidden = false; }
function closeSheet() {
  const onClose = S.sheetClose;
  S.sheetClose = null;
  $('#sheet').hidden = true;
  $('#sheet .panel').innerHTML = '';
  if (onClose) onClose();
}

// In-page dialogs instead of confirm()/alert()/prompt(): some web views (e.g. the iOS app) swallow the native ones silently,
// so actions guarded by confirm() would just do nothing. Resolves true/false (confirm), the entered text or null (prompt).
function uiDialog({ message, value = null, cancel = true, danger = false }) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (done) return; done = true; S.sheetClose = null; closeSheet(); resolve(v); };
    const text = esc(message).replace(/\n/g, '<br>');
    openSheet(`<div class="dlg-msg">${text}</div>
      ${value !== null ? `<form class="sh-new" id="dlgForm"><input name="v" value="${esc(value)}" maxlength="80" autocomplete="off"></form>` : ''}
      <div class="actions"><button class="btn${danger ? ' danger' : ''}" id="dlgOk" type="button">${esc(tr('OK'))}</button>${cancel ? `<button class="btn sec" id="dlgNo" type="button">${esc(tr('Cancel'))}</button>` : ''}</div>`);
    const val = () => (value !== null ? $('#dlgForm').elements.v.value : true);
    S.sheetClose = () => finish(value !== null ? null : false);  // closed by tapping outside or Escape = cancel
    $('#dlgOk').onclick = () => finish(val());
    if ($('#dlgNo')) $('#dlgNo').onclick = () => finish(value !== null ? null : false);
    if (value !== null) {
      $('#dlgForm').onsubmit = (e) => { e.preventDefault(); finish(val()); };
      $('#dlgForm').elements.v.focus();
    } else $('#dlgOk').focus();
  });
}
const uiConfirm = (message, danger = false) => uiDialog({ message, danger });
const uiPrompt = (message, value = '') => uiDialog({ message, value });
const uiAlert = (message) => uiDialog({ message, cancel: false });

function sheetMenu(title, items) {
  S.sheetActions = items.map((i) => i.fn);
  openSheet(`<div class="sh-title">${esc(title)}</div>` + items.map((it, i) =>
    `<button class="sh-item${it.danger ? ' danger' : ''}" data-sa="${i}">${esc(it.label)}</button>`).join(''));
}

$('#sheet .backdrop').addEventListener('click', closeSheet);
$('#sheet .panel').addEventListener('click', (e) => {
  const sa = e.target.closest('[data-sa]');
  if (sa) { const fn = S.sheetActions[+sa.dataset.sa]; closeSheet(); if (fn) fn(); return; }
  if (e.target.closest('[data-close]')) { closeSheet(); return; }
  const cp = e.target.closest('[data-copy]');
  if (cp) { copyText(cp.dataset.copy).then((ok) => toast(ok ? tr('Copied') : tr('Please copy manually'), 1800, !ok)); return; }
  const ud = e.target.closest('[data-undis]');
  if (ud) {
    api(`/dislikes/${ud.dataset.undis}/${encodeURIComponent(ud.dataset.id)}`, { method: 'DELETE' }).then(showDislikes).catch((er) => toast(er.message));
    return;
  }
  const pl = e.target.closest('[data-pl]');
  if (pl && S.picker) { const p = S.picker.lists[+pl.dataset.pl]; const tracks = S.picker.tracks; closeSheet(); addToPlaylist(p, tracks); }
});
$('#sheet .panel').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target.closest('form[data-newpl]');
  if (!form) return;
  const name = form.elements.name.value.trim();
  if (!name) return;
  const tracks = S.picker ? S.picker.tracks : [];
  closeSheet();
  try {
    await api('/playlists', { json: { name, tracks } });
    toast(tr('Playlist “{name}” created', { name }));
    if (location.hash.startsWith('#/playlists')) route();
  } catch (err) { toast(err.message); }
});

async function openPlaylistPicker(tracks) {
  openSheet(`<div class="sh-title">${esc(tr('Add to playlist'))}</div><div class="empty">${esc(tr('Loading …'))}</div>`);
  try {
    const { playlists } = await api('/playlists');
    S.picker = { lists: playlists, tracks: tracks.map(pick) };
    openSheet(`<div class="sh-title">${esc(tr('Add to playlist'))}</div>
      <form class="sh-new" data-newpl><input name="name" placeholder="${esc(tr('New playlist'))}" maxlength="80" autocomplete="off"><button class="btn" type="submit">${esc(tr('Create'))}</button></form>
      ${playlists.map((p, i) => `<button class="sh-item" data-pl="${i}"><span>${esc(p.name)}</span><span class="dim">${p.count}</span></button>`).join('')}`);
  } catch (e) { closeSheet(); toast(e.message); }
}

async function addToPlaylist(pl, tracks) {
  try {
    const r = await api(`/playlists/${pl.id}/tracks`, { json: { tracks } });
    toast(r.added ? tr('{n} added to “{name}”', { n: r.added, name: pl.name }) : tr('Already in the playlist'));
  } catch (e) { toast(e.message); }
}

function trackMenu(t, key, i) {
  const liked = S.likes.has(t.id);
  const items = [
    { label: tr('Start radio'), fn: () => startRadio(t) },
    { label: tr('Play next'), fn: () => playNext(t) },
    { label: tr('Add to queue'), fn: () => enqueue(t) },
    { label: tr('Add to playlist …'), fn: () => openPlaylistPicker([t]) },
    { label: liked ? tr('Remove like') : tr('Like'), fn: () => toggleLike(t) },
  ];
  if (t.artist_id) items.push({ label: tr('Go to artist'), fn: () => go('#/artist/' + t.artist_id + '?name=' + encodeURIComponent((t.artist || '').split(', ')[0])) });
  if (t.album_id) items.push({ label: tr('Go to album'), fn: () => go('#/album/' + t.album_id) });
  if (key === 'pl' && t.entry_id) items.push({ label: tr('Remove from playlist'), danger: true, fn: () => removeFromPlaylist(t) });
  if (key === 'queue') {
    if (i !== S.idx) items.splice(1, 2, { label: tr('Play next'), fn: () => moveNextInQueue(i) }, { label: tr('Move to end of queue'), fn: () => moveInQueue(i, S.queue.length - 1) });
    else items.splice(1, 2);
    items.push({ label: tr('Remove from queue'), danger: true, fn: () => removeFromQueue(i) });
  }
  items.push({ label: tr('Stop recommending …'), danger: true, fn: () => dislikeMenu(t) });
  sheetMenu(t.title, items);
}

// ---------- Stop recommending ----------

function dislikeMenu(t) {
  const items = [{ label: tr('Stop recommending this track'), fn: () => addDislike('track', t.id, t.title) }];
  if (t.artist_id) items.push({ label: tr('Stop recommending {artist}', { artist: t.artist }), fn: () => addDislike('artist', t.artist_id, t.artist) });
  sheetMenu(tr('Stop recommending'), items);
}

async function addDislike(kind, id, title) {
  try {
    await api('/dislikes', { json: { kind, id, title } });
    const bad = (x) => (kind === 'track' ? x.id === id : x.artist_id === id);
    S.radioToken++;
    S.queue = S.queue.filter((x, i) => i <= S.idx || !bad(x));  // remove what is coming up; played/current stays
    refreshQueueIfOpen();
    toast(tr('“{title}” will no longer be recommended', { title }));
  } catch (e) { toast(e.message); }
}

async function showDislikes() {
  openSheet(`<div class="sh-title">${esc(tr('No longer recommended'))}</div><div class="empty">${esc(tr('Loading …'))}</div>`);
  try {
    const { items } = await api('/dislikes');
    openSheet(`<div class="sh-title">${esc(tr('No longer recommended'))}</div>` + (items.length
      ? items.map((d) => `<div class="sh-item"><span>${esc(d.title || d.id)}<span class="dim"> · ${esc(d.kind === 'artist' ? tr('Artist') : tr('Track'))}</span></span><button class="chip" data-undis="${d.kind}" data-id="${esc(d.id)}">${esc(tr('Allow again'))}</button></div>`).join('')
      : `<div class="empty">${esc(tr('Nothing hidden yet.'))}</div>`));
  } catch (e) { closeSheet(); toast(e.message); }
}

// ---------- Actions (data-act) ----------

const actions = {
  reload: () => route(),
  playAll: (n) => { const l = S.lists[n.dataset.key] || []; if (l.length) playList(l, 0); },
  shuffle: (n) => { const l = S.lists[n.dataset.key] || []; if (l.length) playList(shuffled(l), 0); },
  addAll: (n) => { const l = S.lists[n.dataset.key] || []; if (l.length) openPlaylistPicker(l); },
  artistRadio: (n) => { const l = S.lists[n.dataset.key] || []; if (l.length) startRadio(l[0]); else toast(tr('No songs for a radio')); },
  toggleDesc: (n) => n.classList.toggle('clamp'),
  kind: (n) => go('#/search?q=' + encodeURIComponent(S.searchQ) + '&kind=' + n.dataset.kind),
  newPlaylist: () => { S.picker = { lists: [], tracks: [] }; openSheet(`<div class="sh-title">${esc(tr('New playlist'))}</div><form class="sh-new" data-newpl><input name="name" placeholder="${esc(tr('Name'))}" maxlength="80" autocomplete="off"><button class="btn" type="submit">${esc(tr('Create'))}</button></form>`); },
  renamePlaylist: async (n) => {
    const name = ((await uiPrompt(tr('New name'), n.dataset.name)) || '').trim();
    if (!name) return;
    try { await api(`/playlists/${S.plId}`, { method: 'PATCH', json: { name } }); route(); } catch (e) { toast(e.message); }
  },
  deletePlaylist: async () => {
    if (!(await uiConfirm(tr('Really delete this playlist?')))) return;
    try { await api(`/playlists/${S.plId}`, { method: 'DELETE' }); go('#/playlists'); } catch (e) { toast(e.message); }
  },
};

function onListClick(e) {
  let n;
  if (e.target.closest('[data-drag]')) return;
  if ((n = e.target.closest('[data-menu]'))) {
    e.stopPropagation();
    const i = +n.dataset.menu;
    const t = (S.lists[n.dataset.l] || [])[i];
    if (t) trackMenu(t, n.dataset.l, i);
    return;
  }
  if ((n = e.target.closest('[data-artist]'))) { go('#/artist/' + n.dataset.artist); return; }
  if ((n = e.target.closest('[data-nav]'))) { go(n.dataset.nav); return; }
  if ((n = e.target.closest('[data-act]'))) { const fn = actions[n.dataset.act]; if (fn) fn(n); return; }
  if ((n = e.target.closest('[data-l]'))) playFromList(n.dataset.l, +n.dataset.i);
}
$('#view').addEventListener('click', onListClick);
$('#qList').addEventListener('click', onListClick);

// ---------- Pages ----------

function setTab(name) {
  document.querySelectorAll('#tabs a, #side a').forEach((a) => a.classList.toggle('on', a.dataset.tab === name));
}

function qTile(t, key, i) {
  return `<div class="qtile" data-l="${key}" data-i="${i}">${cover(t.thumb)}<div class="meta"><div class="t">${esc(t.title)}</div><div class="a">${esc(t.artist)}${srcTag(t)}</div></div></div>`;
}

async function versionLine() {
  if (!S.ver) {
    try { S.ver = await api('/version'); } catch (_) { return ''; }
  }
  return `<div class="ver">v${esc(S.ver.version)} (${esc(S.ver.commit)})</div>`;
}

// ---------- Community ----------

async function loadCommunity() {
  try { S.community = await api('/community', { noRedirect: true }); } catch (_) { S.community = { enabled: false }; }
  const on = !!(S.community && S.community.enabled);
  document.querySelectorAll('#sideCommunity, #tabCommunity').forEach((el) => { el.hidden = !on; });
}

async function viewCommunity(my) {
  setTab('community');
  const view = $('#view');
  view.innerHTML = loading();
  const st = await api('/community');
  if (my !== S.nav) return;
  if (!st.enabled) { view.innerHTML = `<div class="empty">${esc(tr('The community feature is turned off.'))}</div>`; return; }
  const { people } = await api('/community/people');
  if (my !== S.nav) return;
  const rows = people.slice().sort((x, y) => Number(y.following) - Number(x.following)).map((p) => `<div class="prow">
    <div class="avatar">${esc((p.name[0] || '?').toUpperCase())}</div>
    <div class="pmeta"><div class="t">${esc(p.name)}</div><div class="a">${esc([p.follows_me ? tr('follows you') : '', p.shares ? '' : tr('does not share what they listen to')].filter(Boolean).join(' · '))}</div></div>
    <button class="pbtn${p.following ? '' : ' ok'}" data-act="follow" data-id="${p.id}" data-on="${p.following ? '' : '1'}">${esc(p.following ? tr('Following') : tr('Follow'))}</button></div>`).join('');
  view.innerHTML = `<div class="acct"><div class="acct-head"><div><h1>${esc(tr('Community'))}</h1><div class="sub" style="margin:0">${esc(trn(st.followers, '{n} person follows you', '{n} people follow you'))}</div></div></div>
    <section class="acard"><h3>${esc(tr('What you share'))}</h3>
    <label class="chk"><input type="checkbox" id="shareBox"${st.share ? ' checked' : ''}> ${esc(tr('Share what I listen to with the people who follow me'))}</label>
    <p class="dim small" style="margin:8px 0 0">${esc(tr('Off by default. Your followers then see the songs you played in the last 7 days, nothing else. You can switch it off at any time; then they see nothing again.'))}</p></section>
    <section class="acard"><h3>${esc(tr('People'))}</h3>${rows || `<div class="sub" style="margin:0">${esc(tr('There is nobody else on this server yet.'))}</div>`}</section></div>`;
  $('#shareBox').onchange = async (e) => {
    try { await api('/community/share', { method: 'PUT', json: { share: e.target.checked } }); toast(tr('Saved')); } catch (er) { toast(er.message, 3000, true); e.target.checked = !e.target.checked; }
  };
}

async function renderHome(my, refresh = false) {
  setTab('home');
  const view = $('#view');
  if (!refresh) view.innerHTML = skeleton();
  const d = await api('/home' + (refresh ? '?refresh=1' : ''), {}, 60000);
  if (my !== S.nav) return;
  if (d.empty) {
    view.innerHTML = '<div class="empty"><h1>' + esc(tr('Hello {name}', { name: S.user.name })) + '</h1>' + esc(tr('Search for a song, listen to it and tap it: then the radio starts.')) + '<br>' + esc(tr('Once you have listened to something, this home page builds itself around your taste.')) + '</div>' + await versionLine();
    return;
  }
  resetLists();
  let html = '';
  let friendsHtml = '';
  if (d.friends && d.friends.length) {
    setList('fr', d.friends.map((f) => f.track));
    friendsHtml = `<section class="sect sect-flat"><div class="phead"><h2>${esc(tr('What your friends listen to'))}</h2></div><div class="hscroll">${d.friends.map((f, i) => `<div class="card" data-l="fr" data-i="${i}">${cover(f.track.thumb)}<div class="t">${esc(f.track.title)}</div><div class="a">${f.now ? '<span class="dot on"></span>' : ''}${esc(f.user.name)} · ${esc(f.now ? tr('listening now') : ago(f.played_at))}</div></div>`).join('')}</div></section>`;
  } else if (d.community && d.community.following === 0) {
    friendsHtml = `<section class="sect sect-flat"><div class="phead"><h2>${esc(tr('Community'))}</h2><div class="sub">${esc(tr('Follow friends to see what they listen to.'))}</div></div><div class="actions" style="margin:4px 8px 0"><button class="btn sec" data-nav="#/community">${esc(tr('Find people'))}</button></div></section>`;
  }
  d.shelves.forEach((sh, si) => {
    const key = 's' + si;
    setList(key, sh.tracks);
    const head = `<div class="phead"><h2>${esc(sh.title)}</h2>${sh.subtitle ? `<div class="sub">${esc(sh.subtitle)}</div>` : ''}</div>`;
    if (sh.kind === 'recent') {
      const items = sh.tracks.slice(0, 18);
      const pages = [];
      for (let p = 0; p < items.length; p += 6) pages.push(items.slice(p, p + 6).map((t, j) => qTile(t, key, p + j)).join(''));
      const dots = pages.length > 1 ? `<div class="dots">${pages.map((_, p) => `<i${p ? '' : ' class="on"'}></i>`).join('')}</div>` : '';
      html += `<section class="sect sect-flat">${head}<div class="pager">${pages.map((p) => `<div class="ppage">${p}</div>`).join('')}</div>${dots}</section>`;
    } else {
      const cls = sh.kind === 'mix' ? ' sect-hero' : ' sect-flat';
      html += `<section class="sect${cls}">${head}<div class="hscroll">${sh.tracks.map((t, i) => trackCard(t, key, i)).join('')}</div></section>`;
    }
    if (si === 0) html += friendsHtml;
  });
  if (d.artists.length) html += `<section class="sect sect-flat"><div class="phead"><h2>${esc(tr('Your artists'))}</h2></div><div class="hscroll">${d.artists.map(artistCard).join('')}</div></section>`;
  view.innerHTML = html + await versionLine();
  if (my !== S.nav) return;
  view.querySelectorAll('.pager').forEach((pg) => {
    const dots = pg.parentNode.querySelectorAll('.dots i');
    pg.addEventListener('scroll', () => {
      const i = Math.round(pg.scrollLeft / pg.clientWidth);
      dots.forEach((d, n) => d.classList.toggle('on', n === i));
    }, { passive: true });
  });
}

// Pull down to reload (all pages)
function initPull() {
  const view = $('#view');
  const ind = $('#ptr');
  let y0 = null, x0 = 0, pull = 0, busy = false;
  const TH = 70;
  const onHome = () => (location.hash || '#/home').startsWith('#/home');
  const canPull = () => $('#sheet').hidden && !$('#full').classList.contains('open') && $('#tour').hidden;
  const set = (px, anim) => {
    ind.style.transition = anim ? 'transform .25s ease, opacity .25s' : 'none';
    view.style.transition = anim ? 'transform .25s ease' : 'none';
    view.style.transform = px ? `translateY(${px}px)` : '';
    ind.style.transform = `translate(-50%, ${px - 44}px) rotate(${px * 4}deg)`;
    ind.style.opacity = Math.min(1, px / TH);
  };
  view.addEventListener('touchstart', (e) => {
    y0 = (!busy && canPull() && view.scrollTop <= 0 && e.touches.length === 1) ? e.touches[0].clientY : null;
    x0 = e.touches[0].clientX;
    pull = 0;
  }, { passive: true });
  view.addEventListener('touchmove', (e) => {
    if (y0 === null) return;
    const dy = e.touches[0].clientY - y0;
    if (!pull && Math.abs(e.touches[0].clientX - x0) > Math.abs(dy)) { y0 = null; return; }
    if (dy <= 0 || view.scrollTop > 0) { if (pull) set(0, true); pull = 0; return; }
    pull = Math.min(dy * 0.5, 110);
    if (e.cancelable) e.preventDefault();
    set(pull, false);
  }, { passive: false });
  const end = async () => {
    if (y0 === null) return;
    const fire = pull >= TH;
    y0 = null;
    if (!fire) { set(0, true); pull = 0; return; }
    busy = true;
    ind.classList.add('spin');
    set(TH * 0.8, true);
    try {
      if (onHome()) await renderHome(S.nav, true);
      else { route(); await new Promise((r) => setTimeout(r, 700)); }
    } catch (e) { toast(tr('Reload failed')); }
    ind.classList.remove('spin');
    set(0, true);
    busy = false;
    pull = 0;
  };
  view.addEventListener('touchend', end);
  view.addEventListener('touchcancel', end);
}

async function viewArtist(my, id, name = '') {
  setTab('');
  const view = $('#view');
  view.innerHTML = loading();
  const d = await api('/artist/' + encodeURIComponent(id) + (name ? '?name=' + encodeURIComponent(name) : ''), {}, 40000);
  if (my !== S.nav) return;
  resetLists();
  setList('songs', d.songs);
  let html = `<div class="hero">${cover(d.thumb, 'round')}<h1>${esc(d.name)}</h1>${[d.subscribers, srcName({ id })].filter(Boolean).length ? `<div class="sub">${[d.subscribers, srcName({ id })].filter(Boolean).map(esc).join(' · ')}</div>` : ''}</div>
    <div class="actions"><button class="btn" data-act="playAll" data-key="songs">▶ ${esc(tr('Play'))}</button><button class="btn sec" data-act="shuffle" data-key="songs">${esc(tr('Shuffle'))}</button><button class="btn sec" data-act="artistRadio" data-key="songs">${esc(tr('Radio'))}</button></div>`;
  if (d.description) html += `<p class="desc clamp" data-act="toggleDesc">${esc(d.description)}</p>`;
  if (d.songs.length) {
    html += `<h2>${esc(tr('Popular songs'))}</h2><div id="songs">${rows('songs')}</div>`;
  }
  if (d.albums.length) html += `<h2>${esc(tr('Albums'))}</h2><div class="hscroll">${d.albums.map(albumCard).join('')}</div>`;
  if (d.singles.length) html += `<h2>${esc(tr('Singles & EPs'))}</h2><div class="hscroll">${d.singles.map(albumCard).join('')}</div>`;
  view.innerHTML = html;
}

async function viewAlbum(my, id) {
  setTab('');
  const view = $('#view');
  view.innerHTML = loading();
  const d = await api('/album/' + encodeURIComponent(id), {}, 40000);
  if (my !== S.nav) return;
  resetLists();
  setList('album', d.tracks);
  const artist = d.artist_id ? `<span class="lnk" data-artist="${esc(d.artist_id)}">${esc(d.artist)}</span>` : esc(d.artist);
  const meta = [d.kind, d.year, d.duration, srcName({ id })].filter(Boolean).map(esc).join(' · ');
  view.innerHTML = `<div class="hero">${cover(d.thumb)}<h1>${esc(d.title)}</h1><div class="sub">${artist}${meta ? ' · ' + meta : ''}</div></div>
    <div class="actions"><button class="btn" data-act="playAll" data-key="album">▶ ${esc(tr('Play'))}</button><button class="btn sec" data-act="shuffle" data-key="album">${esc(tr('Shuffle'))}</button><button class="btn sec" data-act="addAll" data-key="album">+ ${esc(tr('Playlist'))}</button></div>
    <div>${rows('album')}</div>`;
}

async function viewPlaylists(my) {
  setTab('playlists');
  const view = $('#view');
  view.innerHTML = loading();
  const [{ playlists }, likes, auto] = await Promise.all([api('/playlists'), api('/likes'), api('/auto').catch(() => ({ lists: [] }))]);
  if (my !== S.nav) return;
  S.likes = new Set(likes.tracks.map((t) => t.id));
  resetLists();
  const likeRow = `<div class="track" data-nav="#/likes"><div class="ph likeph">${IC.liked}</div><div class="meta"><div class="t">${esc(tr('Liked songs'))}</div><div class="a">${esc(trn(likes.tracks.length, '{n} track', '{n} tracks'))}</div></div></div>`;
  const autoRows = auto.lists.length
    ? `<h2>${esc(tr('For you'))}</h2>` + auto.lists.map((p) => `<div class="track" data-nav="#/auto/${encodeURIComponent(p.id)}">${cover(p.thumb)}<div class="meta"><div class="t">${esc(p.name)}</div><div class="a">${esc(p.subtitle)}${p.count ? ' · ' + esc(trn(p.count, '{n} track', '{n} tracks')) : ''}</div></div></div>`).join('') + `<h2>${esc(tr('Your playlists'))}</h2>`
    : '';
  view.innerHTML = `<div class="actions"><button class="btn" data-act="newPlaylist">+ ${esc(tr('New playlist'))}</button></div>` + likeRow + autoRows +
    (playlists.length
      ? playlists.map((p) => `<div class="track" data-nav="#/playlist/${p.id}">${cover(p.thumb)}<div class="meta"><div class="t">${esc(p.name)}</div><div class="a">${esc(trn(p.count, '{n} track', '{n} tracks'))}</div></div></div>`).join('')
      : `<div class="empty">${esc(tr('No playlists of your own yet. Use the menu (⋮) next to a song to create one.'))}</div>`);
}

async function viewPlaylist(my, id) {
  setTab('playlists');
  const view = $('#view');
  view.innerHTML = loading();
  const p = await api('/playlists/' + encodeURIComponent(id));
  if (my !== S.nav) return;
  resetLists();
  S.plId = p.id;
  setList('pl', p.tracks);
  view.innerHTML = `<div class="hero"><h1>${esc(p.name)}</h1><div class="sub">${esc(trn(p.tracks.length, '{n} track', '{n} tracks'))}</div></div>
    <div class="actions"><button class="btn" data-act="playAll" data-key="pl">▶ ${esc(tr('Play'))}</button><button class="btn sec" data-act="shuffle" data-key="pl">${esc(tr('Shuffle'))}</button>
    <button class="btn sec" data-act="renamePlaylist" data-name="${esc(p.name)}">${esc(tr('Rename'))}</button><button class="btn sec" data-act="deletePlaylist">${esc(tr('Delete'))}</button></div>
    <div>${p.tracks.length ? rows('pl') : `<div class="empty">${esc(tr('Empty. Add songs via the menu (⋮).'))}</div>`}</div>`;
}

async function viewAuto(my, id) {
  setTab('playlists');
  const view = $('#view');
  view.innerHTML = loading();
  const p = await api('/auto/' + encodeURIComponent(id));
  if (my !== S.nav) return;
  resetLists();
  setList('auto', p.tracks);
  view.innerHTML = `<div class="hero"><h1>${esc(p.name)}</h1><div class="sub">${esc(p.subtitle)} · ${esc(trn(p.tracks.length, '{n} track', '{n} tracks'))}</div></div>
    <div class="actions"><button class="btn" data-act="playAll" data-key="auto">▶ ${esc(tr('Play'))}</button><button class="btn sec" data-act="shuffle" data-key="auto">${esc(tr('Shuffle'))}</button><button class="btn sec" data-act="addAll" data-key="auto">+ ${esc(tr('Save as playlist'))}</button></div>
    <div>${p.tracks.length ? rows('auto') : `<div class="empty">${esc(tr('Too few tracks for this list yet.'))}</div>`}</div>`;
}

async function viewLikes(my) {
  setTab('likes');
  const view = $('#view');
  view.innerHTML = loading();
  const { tracks } = await api('/likes');
  if (my !== S.nav) return;
  S.likes = new Set(tracks.map((t) => t.id));
  resetLists();
  setList('likes', tracks);
  view.innerHTML = `<div class="hero"><div class="ph likeph" style="display:flex;align-items:center;justify-content:center">${IC.liked}</div><h1>${esc(tr('Liked songs'))}</h1><div class="sub">${esc(trn(tracks.length, '{n} track', '{n} tracks'))}</div></div>
    <div class="actions"><button class="btn" data-act="playAll" data-key="likes">▶ ${esc(tr('Play'))}</button><button class="btn sec" data-act="shuffle" data-key="likes">${esc(tr('Shuffle'))}</button></div>
    <div>${tracks.length ? rows('likes') : `<div class="empty">${esc(tr('Nothing liked yet. Tap “Like” in the player.'))}</div>`}</div>`;
}

// ---------- Account and user management ----------

const fmtDec = (v) => Number(v).toLocaleString(locale(), { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const fmtBytes = (b) => (b >= 1024 ** 3 ? fmtDec(b / 1024 ** 3) + ' GB' : Math.round(b / 1024 ** 2) + ' MB');

function ago(ts) {
  if (!ts) return tr('never');
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return tr('just now');
  if (s < 3600) return tr('{n} min ago', { n: Math.round(s / 60) });
  if (s < 86400) return tr('{n} h ago', { n: Math.round(s / 3600) });
  const d = Math.floor(s / 86400);
  if (d < 2) return tr('yesterday');
  if (d < 7) return tr('{n} days ago', { n: d });
  return new Date(ts * 1000).toLocaleDateString(locale());
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (_) { /* e.g. http without HTTPS */ }
  try {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch (_) { return false; }
}

// Live name check: on every keystroke, the answer appears small in red/green under the field
const nameField = (value = '', placeholder = tr('Name'), mode = 'self') =>
  `<div class="nfield"><input name="name" type="text" maxlength="32" value="${esc(value)}" placeholder="${esc(placeholder)}" autocomplete="off" autocapitalize="words" spellcheck="false" data-namecheck="${mode}" required><div class="ncheck" aria-live="polite"></div></div>`;

const nc = { seq: 0, ctl: null };

async function runNameCheck(inp) {
  const box = inp.closest('.nfield').querySelector('.ncheck');
  const btn = inp.form.querySelector('[data-needs-name]');
  const setOk = (ok) => { if (btn) btn.disabled = !ok; };
  if (nc.ctl) nc.ctl.abort();
  const ctl = (nc.ctl = new AbortController());
  const seq = ++nc.seq;
  if (!inp.value.trim()) { box.innerHTML = ''; setOk(false); return; }
  try {
    const r = await fetch('/api/name-check?name=' + encodeURIComponent(inp.value) + (inp.dataset.namecheck === 'new' ? '&new=1' : ''), { credentials: 'same-origin', signal: ctl.signal });
    if (r.status === 401) { showLogin(); return; }
    const j = await r.json();
    if (seq !== nc.seq) return;
    if (j.state === 'free') box.innerHTML = `<span class="ok">✓ ${esc(tr('Name is available'))}</span>`;
    else if (j.state === 'taken') {
      box.innerHTML = `<span class="bad">✗ ${esc(j.message)}</span>` + (j.suggestions && j.suggestions.length
        ? `<div class="sugg"><span class="dim">${esc(tr('How about'))}</span> ${j.suggestions.map((s) => `<button type="button" class="chip" data-act="pickName" data-name="${esc(s)}">${esc(s)}</button>`).join('')}</div>` : '');
    } else if (j.state === 'invalid') box.innerHTML = `<span class="bad">✗ ${esc(j.message)}</span>`;
    else if (j.state === 'same') box.innerHTML = `<span class="dim">${esc(j.message)}</span>`;
    else box.innerHTML = '';
    setOk(j.state === 'free');
  } catch (e) { if (e.name !== 'AbortError') box.innerHTML = `<span class="bad">${esc(tr('Check failed'))}</span>`; }
}

$('#view').addEventListener('input', (e) => { if (e.target.matches('[data-namecheck]')) runNameCheck(e.target); });

function userBadges(u, self) {
  return (u.is_admin ? `<span class="badge">${esc(tr('Admin'))}</span>` : '') + (self ? `<span class="badge">${esc(tr('You'))}</span>` : '')
    + (!u.has_password ? `<span class="badge warn">${esc(tr('no password'))}</span>` : u.must_change_password ? `<span class="badge warn">${esc(tr('password reset'))}</span>` : '');
}

function userRow(u, meId) {
  const self = u.id === meId;
  const online = u.last_active && Date.now() / 1000 - u.last_active < 300;
  const exact = u.last_active ? esc(new Date(u.last_active * 1000).toLocaleString(locale())) : '';
  return `<tr data-uid="${u.id}">
    <td data-l="${esc(tr('Name'))}"><div class="uname">${esc(u.name)}</div><div class="ubadges">${userBadges(u, self)}</div></td>
    <td data-l="${esc(tr('Last active'))}"><span class="dot${online ? ' on' : ''}"></span><span title="${exact}">${esc(ago(u.last_active))}</span></td>
    <td class="uact" data-l="">${self ? '' : `<button class="chip" data-act="uReset" data-uid="${u.id}" data-name="${esc(u.name)}">${esc(tr('Reset password'))}</button><button class="chip danger" data-act="uDel" data-uid="${u.id}" data-name="${esc(u.name)}">${esc(tr('Delete'))}</button>`}</td></tr>`;
}

async function viewAccount(my) {
  setTab('');
  const view = $('#view');
  view.innerHTML = loading();
  const [{ user }, recap] = await Promise.all([api('/me'), api('/me/recap?period=month&tz=' + -new Date().getTimezoneOffset()).catch(() => null)]);
  if (my !== S.nav) return;
  S.user = { ...S.user, ...user };
  const mine = recap && recap.plays ? `<div class="stats mini">${stat(fmtHours(recap.seconds), tr('Listened'))}${stat(fmtInt(recap.plays), tr('Plays'))}${stat(recap.top_artists[0] ? recap.top_artists[0].name : '–', tr('Top artist'))}</div>` : `<div class="sub" style="margin:0 0 8px">${esc(tr('Play some music and your numbers show up here.'))}</div>`;
  view.innerHTML = `<div class="acct">
    <div class="acct-head"><div class="avatar big">${esc((user.name[0] || '?').toUpperCase())}</div><div><h1>${esc(user.name)}</h1><div class="sub">${esc(user.is_admin ? tr('Admin') : tr('User'))}</div></div></div>
    ${user.must_change_password ? `<div class="banner">${esc(tr('Your password was reset by the admin. Please choose your own now.'))}</div>` : ''}
    <section class="acard"><h3>${esc(tr('Your last 30 days'))}</h3>${mine}
    <div class="actions" style="margin:0"><button class="btn sec" data-nav="#/account/recap">${esc(tr('Recap'))}</button></div></section>
    <section class="acard"><h3>${esc(tr('Username'))}</h3>
    <form id="nameForm" autocomplete="off">${nameField(user.name)}
    <button class="btn" type="submit" data-needs-name disabled>${esc(tr('Change name'))}</button></form></section>
    <section class="acard"><h3>${esc(tr('Language'))}</h3>
    <select id="langSel" class="langsel"><option value=""${user.lang ? '' : ' selected'}>${esc(tr('Automatic (browser)'))}</option><option value="en"${user.lang === 'en' ? ' selected' : ''}>English</option><option value="de"${user.lang === 'de' ? ' selected' : ''}>Deutsch</option></select></section>
    <section class="acard"><h3>${esc(tr('Change password'))}</h3>
    <form id="pwForm" autocomplete="on"><input type="text" name="username" value="${esc(user.name)}" autocomplete="username" hidden>
    <input name="old" type="password" placeholder="${esc(tr('Old password'))}" autocomplete="current-password" required>
    <input name="new" type="password" placeholder="${esc(tr('New password (at least 8 characters)'))}" autocomplete="new-password" minlength="8" required>
    <button class="btn" type="submit">${esc(tr('Change'))}</button><div class="err" id="pwErr"></div></form></section>
    ${user.is_admin ? `<section class="acard"><h3>${esc(tr('Administration'))}</h3><div class="actions" style="margin:0"><button class="btn sec" data-nav="#/admin">${esc(tr('Admin area'))}</button></div></section>` : ''}
    <section class="acard"><h3>${esc(tr('Help'))}</h3><div class="actions" style="margin:0"><button class="btn sec" data-act="tour">${esc(tr('View introduction'))}</button><button class="btn sec" data-act="logout">${esc(tr('Log out'))}</button></div></section></div>`;
}

const fmtHours = (s) => {
  if (s < 60) return tr('{n} sec', { n: Math.round(s) });
  if (s < 3600) return tr('{n} min', { n: Math.round(s / 60) });
  return tr('{n} h', { n: (s / 3600).toLocaleString(locale(), { minimumFractionDigits: s < 36000 ? 1 : 0, maximumFractionDigits: s < 36000 ? 1 : 0 }) });
};
const fmtInt = (n) => Number(n).toLocaleString(locale());
const stat = (value, label) => `<div class="stat"><b>${esc(value)}</b><span>${esc(label)}</span></div>`;

// ---------- Recap: the user's own listening in numbers ----------

async function viewRecap(my, params) {
  setTab('');
  const view = $('#view');
  const periods = [['week', tr('7 days')], ['month', tr('30 days')], ['year', tr('Year')], ['all', tr('All time')]];
  const period = periods.some(([k]) => k === params.get('p')) ? params.get('p') : 'month';
  view.innerHTML = loading();
  const d = await api('/me/recap?period=' + period + '&tz=' + -new Date().getTimezoneOffset());
  if (my !== S.nav) return;
  const head = `<div class="acct wide recap"><button class="back" data-nav="#/account">‹ ${esc(tr('Account'))}</button><h1>${esc(tr('Your recap'))}</h1>
    <div class="chips" style="margin:8px 0 14px">${periods.map(([k, l]) => `<button class="chip${k === period ? ' on' : ''}" data-nav="#/account/recap?p=${k}">${esc(l)}</button>`).join('')}</div>`;
  if (!d.plays) { view.innerHTML = head + `<div class="empty">${esc(tr('Nothing to recap yet. Play some music.'))}</div></div>`; return; }
  const fmt = new Intl.DateTimeFormat(locale(), { weekday: 'short', timeZone: 'UTC' });
  const weekdays = [1, 2, 3, 4, 5, 6, 7].map((n) => fmt.format(new Date(Date.UTC(2024, 0, n))));  // 1 January 2024 is a Monday
  const longDay = new Intl.DateTimeFormat(locale(), { weekday: 'long', timeZone: 'UTC' });
  const bars = (vals, labels) => {
    const max = Math.max(1, ...vals);
    return `<div class="rbars">${vals.map((v, i) => `<div class="rbar" title="${esc(labels[i])}: ${fmtInt(v)}"><i${v === max ? ' class="peak"' : ''} style="height:${Math.max(v ? 4 : 1, Math.round(v * 100 / max))}%"></i><span>${esc(labels[i])}</span></div>`).join('')}</div>`;
  };
  const top = (vals) => vals.indexOf(Math.max(...vals));
  const day = top(d.weekdays), hour = top(d.hours);
  const change = d.previous && d.previous.seconds > 0 ? Math.round((d.seconds - d.previous.seconds) * 100 / d.previous.seconds) : null;
  const total = d.sources.reduce((n, s) => n + s.plays, 0) || 1;
  resetLists();
  setList('r', d.top_songs, 'radio');
  view.innerHTML = head + `
    <section class="acard rhero"><div class="sub" style="margin:0">${esc(tr('You listened for'))}</div><div class="big">${esc(fmtHours(d.seconds))}</div>
      ${change === null ? '' : `<div class="delta ${change >= 0 ? 'up' : 'down'}">${change >= 0 ? '▲' : '▼'} ${esc(tr('{n} % compared to the period before', { n: Math.abs(change) }))}</div>`}</section>
    <div class="stats">${stat(fmtInt(d.plays), tr('Plays'))}${stat(fmtInt(d.songs), tr('Different songs'))}${stat(fmtInt(d.artists), tr('Different artists'))}${stat(fmtInt(d.active_days), tr('Days with music'))}
      ${stat(tr('{n} days', { n: d.streak.current }), tr('Current streak'))}${stat(tr('{n} days', { n: d.streak.longest }), tr('Longest streak'))}</div>
    <h2>${esc(tr('Top artists'))}</h2>
    <div class="hscroll">${d.top_artists.map((a) => `<div class="card round"${a.id ? ` data-nav="#/artist/${esc(a.id)}"` : ''}>${cover(a.thumb, 'round')}<div class="t">${esc(a.name)}</div><div class="a">${esc(trn(a.plays, '{n} play', '{n} plays'))}</div></div>`).join('')}</div>
    <h2>${esc(tr('Top songs'))}</h2>${rows('r')}
    <section class="acard"><h3>${esc(tr('When you listen'))}</h3>
      <div class="sub" style="margin:0 0 10px">${esc(tr('Most active on {day}, around {hour}:00', { day: longDay.format(new Date(Date.UTC(2024, 0, day + 1))), hour: String(hour).padStart(2, '0') }))}</div>
      <h4>${esc(tr('By weekday'))}</h4>${bars(d.weekdays, weekdays)}
      <h4>${esc(tr('By hour'))}</h4>${bars(d.hours, d.hours.map((_, h) => (h % 3 === 0 ? String(h) : '')))}</section>
    <section class="acard"><h3>${esc(tr('Where it came from'))}</h3>${d.sources.map((s) => `<div class="rsrc"><span>${esc(s.source === 'local' ? tr('Library') : s.name)}</span><div class="rtrack"><i style="width:${Math.round(s.plays * 100 / total)}%"></i></div><b>${Math.round(s.plays * 100 / total)} %</b></div>`).join('')}</section>
    ${d.new_artists.count ? `<section class="acard"><h3>${esc(tr('Discoveries'))}</h3><div class="sub" style="margin:0">${esc(trn(d.new_artists.count, 'You discovered {n} new artist: {names}', 'You discovered {n} new artists: {names}', { names: d.new_artists.names.join(', ') }))}</div></section>` : ''}
    ${d.best_day ? `<section class="acard"><h3>${esc(tr('Your biggest day'))}</h3><div class="sub" style="margin:0">${esc(tr('{day}: {time}', { day: new Date(d.best_day.day + 'T00:00:00Z').toLocaleDateString(locale(), { dateStyle: 'full', timeZone: 'UTC' }), time: fmtHours(d.best_day.seconds) }))}</div></section>` : ''}</div>`;
}

function adminTabs(sub) {
  const tabs = [['', tr('Overview')], ['users', tr('Users')], ['plugins', tr('Plugins')], ['system', tr('System')]];
  return `<nav class="asub">${tabs.map(([k, l]) => `<a href="#/admin${k ? '/' + k : ''}" class="${sub === k ? 'on' : ''}">${l}</a>`).join('')}</nav>`;
}

async function adminOverview() {
  const d = await api('/admin/dashboard');
  const free = d.disk.total ? Math.round(d.disk.free * 100 / d.disk.total) : null;
  const lib = d.library;
  return `<div class="stats">
      ${stat(fmtInt(d.plays_total), tr('Tracks played in total'))}
      ${stat(fmtHours(d.seconds_total), tr('Listened in total'))}
      ${stat(fmtInt(d.plays_7d), tr('Tracks, last 7 days'))}
      ${stat(fmtHours(d.seconds_7d), tr('Listened, last 7 days'))}
      ${stat(tr('{a} of {n}', { a: d.active_7d, n: d.users }), tr('Users active in 7 days'))}
      ${stat(fmtSec((streamAvg(d.stream_stats, ['client_desktop', 'client_ios']) || { sec: null }).sec), tr('Avg. wait until first sound'))}
    </div>
    <section class="acard"><h3>${esc(tr('Library'))}</h3>
      <div class="health">
        <div><span>${esc(tr('Tracks'))}</span><b>${fmtInt(lib.tracks)}</b></div>
        <div><span>${esc(tr('Artists'))}</span><b>${fmtInt(lib.artists)}</b></div>
        <div><span>${esc(tr('Albums'))}</span><b>${fmtInt(lib.albums)}</b></div>
        <div><span>${esc(tr('Total duration'))}</span><b>${fmtHours(lib.seconds)}</b></div>
        <div><span>${esc(tr('Size'))}</span><b>${fmtBytes(lib.bytes)}</b></div>
      </div>
      ${lib.tracks === 0 ? `<div class="err" style="padding:8px 0 0">${esc(tr('The library is empty. Put music files into the music folder and start a scan under “System”.'))}</div>` : ''}
    </section>
    <section class="acard"><h3>${esc(tr('Health'))}</h3>
      <div class="health">
        <div><span>${esc(tr('Free disk space'))}</span><b>${d.disk.total ? fmtBytes(d.disk.free) + ` (${free} %)` : esc(tr('unknown'))}</b></div>
        <div><span>${esc(tr('Start to sound (7 days)'))}</span><b>${esc(streamStatsParts(d.stream_stats))}</b></div>
        <div><span>${esc(tr('Version'))}</span><b>${esc(d.version)}</b></div>
      </div>
      ${free !== null && free < 10 ? `<div class="err" style="padding:8px 0 0">${esc(tr('Disk space is running low.'))}</div>` : ''}
    </section>`;
}

async function adminUsers() {
  const adm = await api('/admin/users');
  return `<section class="acard utable-card"><table class="utable"><thead><tr><th>${esc(tr('Name'))}</th><th>${esc(tr('Last active'))}</th><th></th></tr></thead><tbody id="ulist">${adm.users.map((u) => userRow(u, S.user.id)).join('')}</tbody></table></section>
    <section class="acard"><h3>${esc(tr('Create user'))}</h3>
    <form id="addUser" autocomplete="off">${nameField('', tr('Name'), 'new')}
    <div class="pwline"><input name="password" type="text" placeholder="${esc(tr('Initial password (at least 8 characters)'))}" minlength="8" autocomplete="off" required><button type="button" class="chip" data-act="genPw">${esc(tr('Generate'))}</button></div>
    <label class="chk"><input type="checkbox" name="admin"> ${esc(tr('Admin'))}</label>
    <button class="btn" type="submit" data-needs-name disabled>${esc(tr('Create'))}</button><div class="err" id="addErr"></div></form></section>`;
}

function streamAvg(s, keys) {
  let n = 0, ms = 0;
  keys.forEach((k) => { if (s && s[k]) { n += s[k].n; ms += s[k].avg_ms * s[k].n; } });
  return n ? { n, sec: ms / n / 1000 } : null;
}
const fmtSec = (v) => (v === null ? '–' : fmtDec(v) + ' s');

function streamStatsParts(s) {
  const names = { client_desktop: tr('Desktop / Web'), client_ios: tr('Mobile') };
  const parts = Object.keys(names).filter((k) => s && s[k]).map((k) => `${names[k]}: Ø ${fmtDec(s[k].avg_ms / 1000)} s (${fmtInt(s[k].n)}×)`);
  return parts.length ? parts.join(' · ') : tr('No measurements yet (7 days).');
}

const logLevelLabel = (l) => ({ OFF: tr('Off'), ERROR: tr('Errors'), WARNING: tr('Warnings'), INFO: tr('Info'), DEBUG: tr('Debug') }[l] || l);

// One plugin as a card: a closed row (status dot, name, version, badges) that opens to the details and the buttons.
S.popen = new Set();  // plugins whose card is open (kept when the page is drawn again)
S.pl = {};            // the plugins of the last drawing, by ID (for the update pop-up)
function pluginCard(p) {
  S.pl[p.id] = p;
  const busy = ['installing', 'starting'].includes(p.state);
  const err = p.state === 'failed' ? p.message : p.last_error;
  const upd = p.update && p.update.available ? p.update : null;
  const tone = err ? 'err' : !p.enabled ? 'off' : (p.kind !== 'managed' || p.state === 'running') ? 'ok' : busy ? 'info' : 'off';  // green running, yellow in progress, red error, grey off
  const stateText = err ? tr('Error') : p.kind !== 'managed' ? (p.enabled ? tr('Active') : tr('Turned off'))
    : !p.enabled ? tr('Turned off') : p.state === 'running' ? tr('Running') : busy ? (p.message || tr('Starting …')) : tr('Stopped');
  const where = p.kind === 'managed' ? (p.source === 'upload' ? esc(tr('Uploaded as a ZIP')) : p.source ? `${esc(tr('Repository'))} <code>${esc(p.source)}</code>${p.rev ? ' @' + esc(p.rev) : ''}` : esc(tr('Installed by hand on the server'))) : `${esc(tr('Remote'))}: <code>${esc(p.url)}</code>`;
  const logTail = p.state === 'failed' && p.log && p.log.length ? `<pre class="dim small plog">${esc(p.log.slice(-6).join('\n'))}</pre>` : '';
  const id = esc(p.id);
  const btn = (act, icon, label, cls = '', extra = '') => `<button class="pbtn ${cls}" data-act="${act}" data-id="${id}"${extra}>${IC[icon]}<span>${esc(label)}</span></button>`;
  const updateBtn = p.kind !== 'managed' ? ''
    : p.source === 'upload' ? `<button class="pbtn" data-act="pluginUploadPick" data-replace="${id}">${IC.upload}<span>${esc(tr('Upload new version'))}</span></button>`
    : !p.source || (p.update && p.update.supported === false) ? ''
    : upd ? btn('pluginUpdateInfo', 'download', tr('Update available'), 'ok')
    : btn('pluginCheck', 'check', tr('Check for updates'));
  const hasSettings = (p.settings && p.settings.length) || (p.actions && p.actions.length);
  return `<details class="plg${upd ? ' hasupd' : ''}" data-id="${id}" data-sig="${esc([p.state, p.message, p.enabled, p.last_error, p.version].join('|'))}" data-name="${esc((p.name + ' ' + p.id).toLowerCase())}" data-plugin-busy="${busy ? 1 : 0}"${S.popen.has(p.id) || err || busy ? ' open' : ''}>
    <summary><span class="pdot ${tone}"></span>
      <span class="pmain"><span class="pname">${esc(p.name)} <span class="dim">${p.version ? esc(p.version) : ''}</span></span><span class="psub dim small">${esc(p.description || '')}</span></span>
      <span class="pbadges">${upd ? `<span class="pbadge ok">${esc(tr('Update available'))}</span>` : ''}<span class="pbadge ${tone}">${esc(stateText)}</span></span>
      <span class="pchev">${IC.chev}</span></summary>
    <div class="pbody">
      ${p.description ? `<p class="small" style="margin:0 0 8px">${esc(p.description)}</p>` : ''}
      <div class="dim small pwhere"><code>${id}</code> · ${where}</div>
      ${err ? `<div class="st-err small" style="margin-top:8px">${esc(tr('Error'))}: ${esc(err)}</div>` : ''}${logTail}
      <div class="pbtns">${btn('pluginToggle', 'power', p.enabled ? tr('Turn off') : tr('Turn on'), '', ` data-on="${p.enabled ? '' : '1'}"`)}
        ${btn('pluginRefresh', 'refresh', p.kind === 'managed' ? tr('Restart') : tr('Check'))}${updateBtn}
        ${p.kind === 'managed' ? btn('pluginLog', 'lists', tr('Log')) : p.kind === 'remote' ? btn('pluginLog', 'lists', tr('Log')) : ''}
        ${hasSettings ? `<button class="pbtn icon" data-act="pluginSettings" data-id="${id}" aria-label="${esc(tr('Settings'))}" title="${esc(tr('Settings'))}">${IC.gear}</button>` : ''}
        <button class="pbtn danger" data-act="pluginRemove" data-id="${id}" data-name="${esc(p.name)}">${IC.trash}<span>${esc(tr('Remove'))}</span></button></div>
    </div></details>`;
}

// Pop-up with what a plugin update brings (the new commits as a change list) and the button to install it.
function pluginUpdateSheet(id, u) {
  const p = S.pl[id] || { name: id, version: '' };
  openSheet(`<div class="sh-title">${esc(tr('Update available: {name}', { name: p.name }))}</div>
    <div class="upd-info"><div class="dim small">${esc(tr('Installed'))}: ${p.version ? esc(p.version) + ' · ' : ''}<code>${esc(u.current || '')}</code></div>
    <div class="small">${esc(tr('New'))}: ${u.version ? '<b>' + esc(u.version) + '</b> · ' : ''}<code>${esc(u.latest || '')}</code></div>
    <h4>${esc(tr('Changes'))}</h4>
    ${u.changes && u.changes.length ? `<ul class="changes">${u.changes.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>` : `<div class="dim small">${esc(tr('No change notes.'))}</div>`}
    <p class="dim small" style="margin:12px 0 0">${esc(tr('The plugin restarts after the update.'))}</p></div>
    <div class="actions"><button class="btn" id="updGo" type="button">${esc(tr('Update now'))}</button><button class="btn sec" data-close>${esc(tr('Later'))}</button></div>`);
  $('#updGo').onclick = async () => {
    const b = $('#updGo');
    b.disabled = true;
    try { await api('/admin/plugins/' + encodeURIComponent(id) + '/update', { method: 'POST' }, 200000); closeSheet(); toast(tr('Updated')); await loadBell(); route(); } catch (e) { toast(e.message, 6000, true); b.disabled = false; }
  };
}

function serverUpdateSheet(s) {
  openSheet(`<div class="sh-title">${esc(tr('Server update available: {version}', { version: s.latest }))}</div>
    <div class="upd-info"><div class="dim small">${esc(tr('This server runs {current}.', { current: s.current }))}</div>
    <h4>${esc(tr('Release notes'))}</h4>
    ${s.notes ? `<pre class="notes">${esc(s.notes)}</pre>` : `<div class="dim small">${esc(tr('No release notes.'))}</div>`}
    <p class="dim small" style="margin:12px 0 0">${esc(tr('To update, fetch the new version on the server and rebuild the container (git pull, then docker compose up -d --build).'))}</p></div>
    <div class="actions">${s.url ? `<a class="btn" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(tr('Open the release page'))}</a>` : ''}<button class="btn sec" data-close>${esc(tr('Close'))}</button></div>`);
}

// The log of one plugin: what it printed and what the server said about it.
async function pluginLogSheet(id) {
  const draw = async () => {
    const { lines } = await api('/admin/plugins/' + encodeURIComponent(id) + '/log');
    const p = S.pl[id] || { name: id };
    openSheet(`<div class="sh-title">${esc(tr('Log: {name}', { name: p.name }))}</div>
      <pre class="plugin-log" id="plLog">${lines.length ? lines.map((l) => `<span class="${l.level === 'ERROR' || l.level === 'WARNING' ? 'lw' : l.level === 'OUTPUT' ? 'lo' : ''}">${esc(l.time.slice(11))}  ${esc(l.level === 'OUTPUT' ? '' : l.level + ' ')}${esc(l.message)}</span>`).join('\n') : esc(tr('Nothing logged yet.'))}</pre>
      <div class="actions"><button class="btn sec" id="plRefresh" type="button">${esc(tr('Refresh'))}</button><button class="btn sec" id="plCopy" type="button">${esc(tr('Copy'))}</button><button class="btn sec" data-close>${esc(tr('Close'))}</button></div>`);
    const pre = $('#plLog');
    pre.scrollTop = pre.scrollHeight;
    $('#plRefresh').onclick = () => draw().catch((e) => toast(e.message, 3000, true));
    $('#plCopy').onclick = async () => { try { await navigator.clipboard.writeText(pre.textContent); toast(tr('Copied')); } catch (_) { toast(tr('Copying is not possible here'), 3000, true); } };
  };
  await draw();
}

// Plugins with a problem first, then those with an update, then by name.
const pluginRank = (p) => ((p.state === 'failed' || p.last_error) ? 0 : (p.update && p.update.available) ? 1 : 2);

async function adminPlugins() {
  const [pl, up] = await Promise.all([api('/admin/plugins'), api('/admin/updates').catch(() => null)]);
  const list = pl.plugins.slice().sort((x, y) => pluginRank(x) - pluginRank(y) || x.name.localeCompare(y.name));
  const withUpdate = list.filter((p) => p.update && p.update.available).length;
  return `<section class="acard"><div class="phead2"><h3>${esc(tr('Plugins'))} <span class="dim">${list.length}</span></h3>
      ${withUpdate ? `<span class="pbadge ok">${esc(trn(withUpdate, '{n} update available', '{n} updates available'))}</span>` : ''}
      <span class="grow"></span>${up ? `<button class="pbtn" data-act="updatesCheck">${IC.refresh}<span>${esc(tr('Check for updates'))}</span></button>` : ''}</div>
    ${list.length > 4 ? `<input id="pluginFilter" class="pfilter" type="search" placeholder="${esc(tr('Filter plugins'))}" autocomplete="off">` : ''}
    <div id="pluginList">${list.length ? list.map(pluginCard).join('') : `<div class="sub" style="padding:0 0 8px">${esc(tr('No plugins installed.'))}</div>`}</div>
    ${Object.keys(pl.invalid).length ? `<div class="sub" style="padding:0 0 10px;color:#e57373">${Object.entries(pl.invalid).map(([n, why]) => `${esc(tr('Folder'))} <code>${esc(n)}</code>: ${esc(why)}`).join('<br>')}</div>` : ''}
    ${pl.dir_problem ? `<div class="sub" style="padding:0 0 10px;color:#e57373">${esc(pl.dir_problem)}</div>` : ''}</section>
    <section class="acard"><h3>${esc(tr('Add a plugin'))}</h3>
    ${pl.repo_install ? `<div class="sub" style="padding:0 0 4px"><b>${esc(tr('Install from a repository'))}</b></div>
    <input id="pluginRepo" placeholder="https://github.com/…/my-plugin" autocapitalize="off" autocorrect="off" style="width:100%;margin-bottom:8px">
    <div class="actions" style="margin:0"><button class="btn sec" data-act="pluginInstall">${esc(tr('Install'))}</button></div>`
      : `<div class="sub" style="padding:0">${esc(tr('Installation by repository address is turned off on this server.'))}</div>`}
    ${pl.upload_install ? `<div class="sub" style="padding:12px 0 4px"><b>${esc(tr('Upload a ZIP'))}</b></div>
    <div class="actions" style="margin:0"><button class="btn sec" data-act="pluginUploadPick">${esc(tr('Choose ZIP …'))}</button><input type="file" id="pluginZip" accept=".zip,application/zip" hidden></div>`
      : `<div class="sub" style="padding:8px 0 0">${esc(tr('Installation by ZIP upload is turned off on this server.'))}</div>`}
    <details style="margin-top:12px"><summary class="sub" style="cursor:pointer">${esc(tr('Register a remote plugin (separate service)'))}</summary>
    <input id="pluginUrl" placeholder="${esc(tr('Address, e.g. http://plugin:8100'))}" autocapitalize="off" autocorrect="off" style="width:100%;margin:8px 0">
    <input id="pluginToken" placeholder="${esc(tr('Access token (if set up)'))}" autocapitalize="off" autocorrect="off" style="width:100%;margin-bottom:8px">
    <div class="actions" style="margin:0"><button class="btn sec" data-act="pluginAdd">${esc(tr('Register'))}</button></div></details>
    <p class="dim small" style="margin:10px 0 0">${esc(tr('Plugins connect further music sources (protocol version {v}, see PLUGINS.md). They run as a separate process of the server, but they can deliver audio and metadata and install programs during setup. Only add plugins from sources you trust. Plugins installed from a repository are checked for updates by the server.', { v: pl.api_version }))}
    ${esc(tr('By hand works too: put the plugin folder into {dir} on the server and restart the server.', { dir: pl.plugins_dir }))}</p></section>`;
}

// Settings and actions of a plugin: the forms are built from the schema in the plugin's manifest (bool, int, choice, text); the server validates and applies them.
function fieldRow(e, v) {
  const help = e.help ? `<div class="dim small pshelp">${esc(e.help)}</div>` : '';
  const dep = e.depends_on ? ` data-dep="${esc(e.depends_on)}"` : '';
  if (e.type === 'bool') return `<div class="psrow"${dep}><label class="chk"><input type="checkbox" name="${esc(e.key)}"${v ? ' checked' : ''}> ${esc(e.label)}</label>${help}</div>`;
  const ctl = e.type === 'int'
    ? `<input type="number" name="${esc(e.key)}" value="${esc(v)}" min="${e.min}" max="${e.max}" inputmode="numeric" class="psnum">${e.unit ? ` <span class="dim small">${esc(e.unit)}</span>` : ''}`
    : e.type === 'text'
      ? `<input type="${e.secret ? 'password' : 'text'}" name="${esc(e.key)}" value="${esc(v)}" maxlength="${e.max_length}" placeholder="${esc(e.secret && e.has_value ? tr('Saved. Leave empty to keep it.') : e.placeholder || '')}" autocomplete="${e.secret ? 'new-password' : 'off'}" autocapitalize="off" autocorrect="off" class="pstext">`
      : `<select name="${esc(e.key)}">${e.choices.map((c) => `<option value="${esc(c.value)}"${c.value === v ? ' selected' : ''}>${esc(c.label)}</option>`).join('')}</select>`;
  return `<div class="psrow"${dep}><div class="pslabel">${esc(e.label)}${e.required ? ' *' : ''}</div>${ctl}${help}</div>`;
}

function bindFields(form) {  // a field with depends_on is only shown while the switch it names is on
  const sync = () => form.querySelectorAll('[data-dep]').forEach((r) => { const c = form.elements[r.dataset.dep]; r.hidden = !(c && c.checked); });
  form.addEventListener('change', sync);
  sync();
  form.onsubmit = (e) => e.preventDefault();
}

function readFields(form, fields) {  // -> values, or null (after a message) if a number is not a whole number
  const values = {};
  for (const e of fields) {
    const el = form.elements[e.key];
    values[e.key] = e.type === 'bool' ? el.checked : e.type === 'int' ? (el.value.trim() === '' ? NaN : Number(el.value)) : e.type === 'text' ? el.value.trim() : el.value;
    if (e.type === 'int' && !Number.isInteger(values[e.key])) { toast(tr('Please enter a whole number for “{name}”.', { name: e.label }), 3000, true); return null; }
  }
  return values;
}

async function pluginSettingsSheet(id) {
  const [pl, d] = await Promise.all([api('/admin/plugins'), api('/admin/plugins/' + encodeURIComponent(id) + '/settings')]);
  const p = pl.plugins.find((x) => x.id === id);
  const actions = d.actions || [];
  openSheet(`<div class="sh-title">${esc(p ? p.name : id)}</div>
    ${d.schema.length ? `<form id="psForm" class="psform">${d.schema.map((e) => fieldRow(e, d.values[e.key])).join('')}</form>` : ''}
    ${d.info.length ? `<div class="psinfo">${d.info.map((i) => `<div class="dim small">${esc(i.label)}: ${esc(i.value)}</div>`).join('')}</div>` : ''}
    ${actions.length ? `<div class="psactions">${actions.map((a, n) => `<button class="btn sec" type="button" data-psaction="${n}">${esc(a.label)} …</button>`).join('')}</div>` : ''}
    <div class="actions">${d.schema.length ? `<button class="btn" id="psSave" type="button">${esc(tr('Save'))}</button>` : ''}<button class="btn sec" data-close>${d.schema.length ? esc(tr('Cancel')) : esc(tr('Close'))}</button></div>`);
  actions.forEach((a, n) => { $(`[data-psaction="${n}"]`).onclick = () => pluginActionSheet(id, p ? p.name : id, a); });
  if (!d.schema.length) return;
  const form = $('#psForm');
  bindFields(form);
  $('#psSave').onclick = async () => {
    const values = readFields(form, d.schema);
    if (!values) return;
    for (const e of d.schema) if (e.secret && !values[e.key]) delete values[e.key];  // left empty: the server keeps the saved secret
    try {
      await api('/admin/plugins/' + encodeURIComponent(id) + '/settings', { method: 'PUT', json: { values } });
      closeSheet();
      toast(tr('Saved'));
    } catch (e) { toast(e.message, 3500, true); }
  };
}

// An action of a plugin (e.g. "add a station"): a small form; the result stays visible so several can be run one after the other.
function pluginActionSheet(id, pluginName, action) {
  openSheet(`<div class="sh-title">${esc(action.label)}</div>
    ${action.help ? `<div class="dim small" style="padding:0 16px 8px">${esc(action.help)}</div>` : ''}
    <form id="paForm" class="psform">${action.fields.map((e) => fieldRow(e, e.default)).join('')}</form>
    <div id="paResult" class="psresult small" hidden></div>
    <div class="actions"><button class="btn" id="paRun" type="button">${esc(action.label)}</button><button class="btn sec" id="paBack" type="button">${esc(tr('Back'))}</button></div>`);
  const form = $('#paForm');
  bindFields(form);
  $('#paBack').onclick = () => pluginSettingsSheet(id).catch((e) => toast(e.message, 3000, true));
  $('#paRun').onclick = async () => {
    const values = readFields(form, action.fields);
    if (!values) return;
    const out = $('#paResult'), btn = $('#paRun');
    btn.disabled = true;
    try {
      const r = await api('/admin/plugins/' + encodeURIComponent(id) + '/actions/' + encodeURIComponent(action.id), { method: 'POST', json: { values } });
      out.className = 'psresult small ' + (r.ok ? 'st-ok' : 'st-err');
      out.textContent = r.message || (r.ok ? tr('Done') : tr('Failed'));
      out.hidden = false;
    } catch (e) { toast(e.message, 3500, true); }
    btn.disabled = false;
  };
}

// Server version and updates (the server looks on GitHub).
function updatesCard(up) {
  if (!up) return '';
  const s = up.server;
  const state = !s.enabled ? `<span class="dim">${esc(tr('Not looking for server updates (UPDATE_REPO is empty).'))}</span>`
    : s.available ? `<button class="pbtn ok" data-act="serverUpdateInfo">${IC.download}<span>${esc(tr('Update available: {version}', { version: s.latest }))}</span></button>`
    : s.latest ? `<span class="st-ok">${esc(tr('Up to date'))}</span>` : `<span class="dim">${esc(tr('Not checked yet'))}</span>`;
  return `<section class="acard"><div class="phead2"><h3>${esc(tr('Updates'))}</h3><span class="grow"></span><button class="pbtn" data-act="updatesCheck">${IC.refresh}<span>${esc(tr('Check for updates'))}</span></button></div>
    <div class="sub" style="margin:0 0 8px">${esc(tr('Server'))} <b>${esc(s.current)}</b> ${state}</div>
    <p class="dim small" style="margin:0">${s.checked ? esc(tr('Last checked {when}', { when: ago(s.checked) })) + ' · ' : ''}${up.check_hours ? esc(tr('The server looks every {n} h.', { n: up.check_hours })) : esc(tr('Automatic checks are off (UPDATE_CHECK_HOURS=0).'))}
    ${s.error ? ' · ' + esc(tr('Last attempt failed: {why}', { why: s.error })) : ''}</p></section>`;
}

async function adminSystem() {
  const [lib, en, lg, up, com] = await Promise.all([api('/admin/library'), api('/admin/enrich'), api('/admin/logging'), api('/admin/updates').catch(() => null), api('/admin/community')]);
  const sc = lib.scan;
  const enState = !en.enabled ? tr('Off') : en.running ? tr('Looking up …')
    : en.finished ? tr('Last run {when}', { when: ago(en.finished) }) + (en.error ? ' (' + tr('aborted: {why}', { why: en.error }) + ')' : '') : tr('Waiting for the next scan');
  const state = sc.running ? tr('Scan running … {n} files seen', { n: fmtInt(sc.seen) })
    : sc.finished ? tr('Last scan {when}: {added} new, {updated} changed, {removed} removed', { when: ago(sc.finished), added: fmtInt(sc.added), updated: fmtInt(sc.updated), removed: fmtInt(sc.removed) }) + (sc.errors ? ', ' + tr('{n} errors', { n: fmtInt(sc.errors) }) : '') : tr('No scan yet');
  return `<section class="acard"><h3>${esc(tr('Library'))}</h3>
    <div class="sub" style="padding:0 0 8px">${esc(trn(lib.tracks, '{n} track in', '{n} tracks in'))} <code>${esc(lib.music_dir)}</code> · ${fmtBytes(lib.bytes)}</div>
    <div class="sub" style="padding:0">${state}</div>
    <div class="actions" style="margin:12px 0 0"><button class="btn sec" data-act="scanLibrary"${sc.running ? ' disabled' : ''}>${esc(tr('Scan now'))}</button><button class="btn sec" data-act="scanLibrary" data-full="1"${sc.running ? ' disabled' : ''}>${esc(tr('Re-read everything'))}</button></div>
    <p class="dim small" style="margin:10px 0 0">${esc(tr('New and changed files are detected at startup and after every scan. “Re-read everything” also re-reads unchanged files (e.g. after re-tagging covers).'))}</p></section>
    <section class="acard"><h3>${esc(tr('Information from the internet'))}</h3>
    <div class="sub" style="padding:0 0 8px">${esc(enState)} · ${esc(tr('found: {a} artists, {b} albums', { a: fmtInt(en.artists_found), b: fmtInt(en.albums_found) }))} · ${esc(tr('without match: {n}', { n: fmtInt(en.artists_missed + en.albums_missed) }))}</div>
    <div class="actions" style="margin:0"><button class="btn sec" data-act="enrichToggle" data-on="${en.enabled ? '' : '1'}">${esc(en.enabled ? tr('Turn off') : tr('Turn on'))}</button><button class="btn sec" data-act="enrichRun"${en.enabled && !en.running ? '' : ' disabled'}>${esc(tr('Look up now'))}</button></div>
    <p class="dim small" style="margin:10px 0 0">${esc(tr('Fills in missing release year, cover and a short description from {source}. Only artist and album names are sent. Existing tags and covers stay unchanged.', { source: en.source }))}${en.from_admin ? '' : ' ' + esc(tr('Default from the environment variable')) + ' <code>ENRICH</code>.'}</p></section>
    ${updatesCard(up)}
    <section class="acard"><h3>${esc(tr('Community'))}</h3>
    <div class="sub" style="margin:0 0 8px">${esc(com.enabled ? tr('On') : tr('Off'))}${com.enabled ? ' · ' + esc(tr('{n} of {total} users share what they listen to', { n: com.sharing, total: com.users })) : ''}</div>
    <div class="actions" style="margin:0"><button class="btn sec" data-act="communityToggle" data-on="${com.enabled ? '' : '1'}">${esc(com.enabled ? tr('Turn off') : tr('Turn on'))}</button></div>
    <p class="dim small" style="margin:10px 0 0">${esc(tr('Users can follow each other and, if they choose to share, see what their friends listen to on the home page. Nothing is shared unless a user switches it on.'))}</p></section>
    <section class="acard"><h3>${esc(tr('Logging'))}</h3>
    ${lg.categories.map((c) => `<div class="logrow"><span>${esc(c.label)}</span><span class="logctl"><select data-logcat="${esc(c.id)}">${lg.levels.map((l) => `<option value="${l}"${l === c.level ? ' selected' : ''}>${esc(logLevelLabel(l))}</option>`).join('')}</select><small class="dim">${esc(c.source === 'admin' ? tr('Set in the admin area') : c.source === 'env' ? tr('From environment variable') : tr('Default'))}</small>${c.source === 'admin' ? `<button class="chip" data-act="logReset" data-cat="${esc(c.id)}">${esc(tr('Reset'))}</button>` : ''}</span></div>`).join('')}
    <div class="actions" style="margin:12px 0 0"><button class="btn sec" data-act="logShow">${esc(tr('Show recent messages'))}</button></div>
    <pre id="logView" class="dim small" style="white-space:pre-wrap;margin:10px 0 0;max-height:18em;overflow:auto" hidden></pre>
    <p class="dim small" style="margin:10px 0 0">${esc(tr('Choose what the server logs. Changes apply at once; the environment variables LOG_LEVEL and LOG_LEVEL_<CATEGORY> set the defaults.'))}</p></section>
    <section class="acard"><h3>${esc(tr('Backup'))}</h3>
    <div class="actions" style="margin:0"><a class="btn sec" href="/api/admin/backup" download>${esc(tr('Download database backup'))}</a><button class="btn sec" data-act="restorePick">${esc(tr('Restore backup …'))}</button><input type="file" id="restoreFile" accept=".db,application/octet-stream" hidden></div>
    <p class="dim small" style="margin:10px 0 0">${esc(tr('The backup contains users (with password hashes), playlists and history, but no sessions. Keep it safe. The music files are not included. Restoring replaces everything with the state of the backup (the server first saves a copy of the current state in the data folder); everyone has to log in again afterwards.'))}</p></section>`;
}

async function viewAdmin(my, sub = '') {
  setTab('admin');
  const view = $('#view');
  view.innerHTML = loading();
  if (!S.user || !S.user.is_admin) { view.innerHTML = errorBox(tr('Admins only.')); return; }
  if (!['', 'users', 'plugins', 'system'].includes(sub)) sub = '';
  const body = await (sub === 'users' ? adminUsers() : sub === 'plugins' ? adminPlugins() : sub === 'system' ? adminSystem() : adminOverview());
  if (my !== S.nav) return;
  view.innerHTML = `<div class="acct wide">
    <div class="acct-head"><div><h1>${esc(tr('Admin area'))}</h1></div></div>
    ${adminTabs(sub)}${body}</div>`;
  if (sub === 'plugins' && view.querySelector('[data-plugin-busy="1"]')) setTimeout(() => pollBusyPlugins(my), 3000);  // a plugin is being set up
}

// While a plugin is installed or started, only its own card is refreshed, not the whole page: filter, open cards and typed text stay as they are.
async function pollBusyPlugins(my) {
  const view = $('#view');
  if (my !== S.nav || !view.querySelector('[data-plugin-busy="1"]')) return;
  let pl;
  try { pl = await api('/admin/plugins'); } catch (_) { setTimeout(() => pollBusyPlugins(my), 3000); return; }
  if (my !== S.nav) return;
  let known = true;
  pl.plugins.forEach((p) => {
    const el = view.querySelector(`details.plg[data-id="${p.id}"]`);
    if (!el) { known = false; return; }
    const sig = [p.state, p.message, p.enabled, p.last_error, p.version].join('|');
    if (el.dataset.sig === sig) return;
    const wasBusy = el.dataset.pluginBusy === '1';
    el.outerHTML = pluginCard(p);
    const fresh = view.querySelector(`details.plg[data-id="${p.id}"]`);
    if (fresh) fresh.dataset.sig = sig;
    if (wasBusy && !['installing', 'starting'].includes(p.state)) loadSources();  // it is ready: its search tabs and sources count now
  });
  if (!known) { route(); return; }  // a plugin that is not on the page yet (installed elsewhere): draw the list once
  if (view.querySelector('[data-plugin-busy="1"]')) setTimeout(() => pollBusyPlugins(my), 3000);
}

const randomPw = () => {
  const abc = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  const buf = crypto.getRandomValues(new Uint32Array(12));
  return Array.from(buf, (n) => abc[n % abc.length]).join('');
};

Object.assign(actions, {
  tour: () => showTutorial(),
  logout: async () => { try { await api('/logout', { method: 'POST' }); } catch (_) { /* ignore */ } showLogin(); },
  pickName: (n) => { const inp = n.closest('form').elements.name; inp.value = n.dataset.name; runNameCheck(inp); },
  genPw: (n) => { const f = n.closest('form'); f.elements.password.value = randomPw(); },
  uReset: async (n) => {
    if (!(await uiConfirm(tr('Reset the password of “{name}”? They will be logged out everywhere.', { name: n.dataset.name })))) return;
    try {
      const { password } = await api(`/admin/users/${n.dataset.uid}/reset-password`, { method: 'POST' });
      openSheet(`<div class="sh-title">${esc(tr('New password for {name}', { name: n.dataset.name }))}</div>
        <div class="pwshow"><code>${esc(password)}</code></div>
        <p class="dim small" style="padding:0 16px">${esc(tr('Shown only now. Pass it on to the person; they choose their own at the first login.'))}</p>
        <div class="actions"><button class="btn" data-copy="${esc(password)}">${esc(tr('Copy'))}</button><button class="btn sec" data-close>${esc(tr('Done'))}</button></div>`);
      route();
    } catch (e) { toast(e.message, 3000, true); }
  },
  scanLibrary: async (n) => {
    n.disabled = true;
    try {
      await api('/admin/library/scan' + (n.dataset.full ? '?full=true' : ''), { method: 'POST' });
      toast(tr('Scan started'));
      const poll = async () => {
        const l = await api('/admin/library');
        if (l.scan.running) { setTimeout(poll, 1500); return; }
        route();
      };
      setTimeout(poll, 1000);
    } catch (e) { toast(e.message, 3000, true); n.disabled = false; }
  },
  restorePick: () => $('#restoreFile').click(),
  pluginUploadPick: (n) => { S.zipReplace = n.dataset.replace || ''; $('#pluginZip').click(); },
  logReset: async (n) => {
    try { await api('/admin/logging', { method: 'PUT', json: { levels: { [n.dataset.cat]: '' } } }); route(); } catch (e) { toast(e.message, 3000, true); }
  },
  logShow: async (n) => {
    const box = $('#logView');
    if (!box.hidden) { box.hidden = true; n.textContent = tr('Show recent messages'); return; }
    try {
      const { lines } = await api('/admin/logs?limit=200');
      box.textContent = lines.length ? lines.map((l) => `${l.time} ${l.level} ${l.logger}: ${l.message}`).join('\n') : tr('No messages yet.');
      box.hidden = false;
      box.scrollTop = box.scrollHeight;
      n.textContent = tr('Hide messages');
    } catch (e) { toast(e.message, 3000, true); }
  },
  pluginAdd: async () => {
    const url = $('#pluginUrl').value.trim();
    if (!url) { toast(tr('Please enter the plugin address'), 3000, true); return; }
    if (!(await uiConfirm(tr('Add this plugin?\n\nPlugins can deliver audio and metadata. Only add plugins from trusted sources.')))) return;
    try { const p = await api('/admin/plugins', { json: { url, token: $('#pluginToken').value.trim() } }); toast(tr('“{name}” added', { name: p.name })); route(); } catch (e) { toast(e.message, 5000, true); }
  },
  pluginInstall: async (n) => {
    const repo = $('#pluginRepo').value.trim();
    if (!repo) { toast(tr('Please enter the repository address'), 3000, true); return; }
    if (!(await uiConfirm(tr('Install a plugin from this repository?\n\n{repo}\n\nThe server downloads the code, installs its dependencies and runs it. Only install plugins from sources you trust.', { repo })))) return;
    n.disabled = true;
    toast(tr('Downloading plugin …'), 60000);
    try { const p = await api('/admin/plugins/install', { json: { repo } }, 200000); toast(tr('“{name}” installed, setting up', { name: p.name })); route(); } catch (e) { toast(e.message, 6000, true); n.disabled = false; }
  },
  pluginUpdate: async (n) => {
    n.disabled = true;
    try { await api('/admin/plugins/' + encodeURIComponent(n.dataset.id) + '/update', { method: 'POST' }, 200000); toast(tr('Updated')); route(); } catch (e) { toast(e.message, 5000, true); n.disabled = false; }
  },
  pluginToggle: async (n) => {
    try { await api('/admin/plugins/' + encodeURIComponent(n.dataset.id), { method: 'PATCH', json: { enabled: !!n.dataset.on } }); await loadSources(); route(); } catch (e) { toast(e.message, 3000, true); }
  },
  pluginSettings: async (n) => {
    try { await pluginSettingsSheet(n.dataset.id); } catch (e) { toast(e.message, 3000, true); }
  },
  follow: async (n) => {
    n.disabled = true;
    try { await api('/community/follow/' + n.dataset.id, { method: n.dataset.on ? 'POST' : 'DELETE' }); route(); } catch (e) { toast(e.message, 3000, true); n.disabled = false; }
  },
  communityToggle: async (n) => {
    try { await api('/admin/community', { method: 'PUT', json: { enabled: !!n.dataset.on } }); await loadCommunity(); route(); } catch (e) { toast(e.message, 3000, true); }
  },
  pluginCheck: async (n) => {
    n.disabled = true;
    try {
      const u = await api('/admin/plugins/' + encodeURIComponent(n.dataset.id) + '/check-update', { method: 'POST' }, 90000);
      if (u.available && S.pl[n.dataset.id]) S.pl[n.dataset.id].update = u;
      if (u.available) { route(); pluginUpdateSheet(n.dataset.id, u); return; }
      toast(tr('Up to date'));
      route();
    } catch (e) { toast(e.message, 4000, true); n.disabled = false; }
  },
  pluginUpdateInfo: (n) => { const p = S.pl[n.dataset.id]; if (p && p.update) pluginUpdateSheet(n.dataset.id, p.update); },
  updatesCheck: async (n) => {
    n.disabled = true;
    toast(tr('Looking for updates …'), 60000);
    try { await api('/admin/updates/check', { method: 'POST' }, 180000); toast(tr('Done')); await loadBell(); route(); } catch (e) { toast(e.message, 5000, true); n.disabled = false; }
  },
  serverUpdateInfo: async () => {
    try { serverUpdateSheet((await api('/admin/updates')).server); } catch (e) { toast(e.message, 4000, true); }
  },
  pluginLog: async (n) => { try { await pluginLogSheet(n.dataset.id); } catch (e) { toast(e.message, 4000, true); } },
  pluginRefresh: async (n) => {
    try { await api('/admin/plugins/' + encodeURIComponent(n.dataset.id) + '/refresh', { method: 'POST' }); route(); } catch (e) { toast(e.message, 3000, true); }
  },
  pluginRemove: async (n) => {
    if (!(await uiConfirm(tr('Remove plugin “{name}”? Tracks from it in playlists can then no longer be played.', { name: n.dataset.name })))) return;
    try { await api('/admin/plugins/' + encodeURIComponent(n.dataset.id), { method: 'DELETE' }); await loadSources(); route(); } catch (e) { toast(e.message, 3000, true); }
  },
  enrichToggle: async (n) => {
    try { await api('/admin/enrich', { json: { enabled: !!n.dataset.on } }); route(); } catch (e) { toast(e.message, 3000, true); }
  },
  enrichRun: async (n) => {
    n.disabled = true;
    try {
      await api('/admin/enrich/run?retry=true', { method: 'POST' });
      toast(tr('Lookup started'));
      const poll = async () => {
        const e = await api('/admin/enrich');
        if (e.running) { setTimeout(poll, 3000); return; }
        route();
      };
      setTimeout(poll, 2000);
    } catch (e) { toast(e.message, 3000, true); n.disabled = false; }
  },
  uDel: async (n) => {
    if (!(await uiConfirm(tr('Delete “{name}” including playlists and history?', { name: n.dataset.name })))) return;
    try { await api('/admin/users/' + n.dataset.uid, { method: 'DELETE' }); route(); } catch (e) { toast(e.message, 3000, true); }
  },
});

$('#view').addEventListener('submit', async (e) => {
  const f = e.target;
  if (f.id === 'nameForm') {
    e.preventDefault();
    try {
      const r = await api('/me/name', { method: 'PUT', json: { name: f.elements.name.value } });
      S.user.name = r.name;
      $('#userBtn').textContent = (r.name[0] || '?').toUpperCase();
      toast(tr('Name changed'));
      route();
    } catch (er) { toast(er.message, 3000, true); }
  } else if (f.id === 'pwForm') {
    e.preventDefault();
    $('#pwErr').textContent = '';
    try {
      await api('/me/password', { method: 'PUT', json: { old: f.old.value, new: f.new.value } });
      showLogin();
      toast(tr('Password changed. Please log in again.'), 3500);
    } catch (er) { $('#pwErr').textContent = er.message; }
  } else if (f.id === 'addUser') {
    e.preventDefault();
    $('#addErr').textContent = '';
    try {
      await api('/admin/users', { json: { name: f.elements.name.value, password: f.elements.password.value, is_admin: f.elements.admin.checked } });
      toast(tr('User created'));
      route();
    } catch (er) { $('#addErr').textContent = er.message; }
  }
});

async function removeFromPlaylist(t) {
  try { await api(`/playlists/${S.plId}/tracks/${t.entry_id}`, { method: 'DELETE' }); route(); } catch (e) { toast(e.message); }
}

// ---------- Search ----------

function setHeaders(isSearch) {
  $('#mainHeader').hidden = isSearch;
  $('#searchHeader').hidden = !isSearch;
}

function syncSearchBtns() {
  const has = !!$('#q').value;
  $('#qClear').hidden = !has;
}

function hideSuggest() { $('#suggest').hidden = true; }

async function openSuggest() {
  $('#suggest').hidden = false;
  renderSuggest();
  try { S.hist = await api('/searches'); } catch (e) { return; }
  if (!$('#suggest').hidden) renderSuggest();
}

function sRow(text, icon) {
  return `<div class="srow" data-sq="${esc(text)}"><span class="sic">${IC[icon]}</span><span class="stxt">${esc(text)}</span><button type="button" class="sfill" data-sfill="${esc(text)}" aria-label="${esc(tr('Use in search'))}">${IC.fill}</button></div>`;
}

function pickCard(p, i) {
  return `<div class="pcard" data-pick="${i}">${cover(p.thumb, p.type === 'artist' ? 'round' : '')}<div class="t">${esc(p.title)}</div></div>`;
}

function renderSuggest() {
  const text = $('#q').value.trim();
  let html = '';
  if (!text) {
    const { queries, picks } = S.hist;
    if (!queries.length && !picks.length) {
      html = `<div class="empty">${esc(tr('Search for songs, artists and albums.'))}</div>`;
    } else {
      html = `<div class="shead"><span>${esc(tr('Recent searches'))}</span><button type="button" data-sclear>${esc(tr('Clear'))}</button></div>`;
      if (picks.length) html += `<div class="hscroll picks">${picks.map(pickCard).join('')}</div>`;
      html += queries.map((x) => sRow(x.query, 'history')).join('');
    }
  } else {
    const low = text.toLowerCase();
    const local = S.hist.queries.map((x) => x.query).filter((x) => x.toLowerCase().startsWith(low) && x.toLowerCase() !== low).slice(0, 3);
    const seen = new Set(local.map((x) => x.toLowerCase()));
    const remote = (S.suggFor === text ? S.sugg : []).filter((x) => !seen.has(x.toLowerCase()));
    html = local.map((x) => sRow(x, 'history')).join('') + remote.map((x) => sRow(x, 'search')).join('');
    if (!local.length && !remote.length) html = sRow(text, 'search');
  }
  $('#suggest').innerHTML = html;
}

let suggTimer;
function fetchSuggest() {
  clearTimeout(suggTimer);
  const text = $('#q').value.trim();
  if (!text) { S.sugg = []; return; }
  suggTimer = setTimeout(async () => {
    const seq = ++S.suggSeq;
    const stale = () => seq !== S.suggSeq || $('#suggest').hidden;
    // Sources (plugins) are slower than the library: both requests start at once, the library shows up first.
    const more = S.multiSource ? api('/suggest?source=plugins&q=' + encodeURIComponent(text), {}, 8000).catch(() => ({ suggestions: [] })) : null;
    try {
      const r = await api('/suggest?source=local&q=' + encodeURIComponent(text), {}, 8000);
      if (stale()) return;
      S.sugg = r.suggestions;
      S.suggFor = text;
      renderSuggest();
    } catch (_) { /* suggestions are optional */ }
    if (!more) return;
    const extra = (await more).suggestions;
    if (stale() || !extra.length) return;
    const have = new Set((S.suggFor === text ? S.sugg : []).map((x) => x.toLowerCase()));
    S.sugg = (S.suggFor === text ? S.sugg : []).concat(extra.filter((x) => !have.has(x.toLowerCase())));
    S.suggFor = text;
    renderSuggest();
  }, 120);
}

function submitSearch(q) {
  q = (q || '').trim();
  if (!q) return;
  hideSuggest();
  $('#q').blur();
  go('#/search?q=' + encodeURIComponent(q));  // no tab chosen: the one with the most hits is selected (see viewSearch)
}

function recordPick(p) {
  api('/me/search-picks', { json: { type: p.type, id: p.id, title: p.title || '', subtitle: p.subtitle || '', thumb: p.thumb || '', track: p.track || null } }).catch(() => {});
}

const trackPick = (t) => ({ type: 'track', id: t.id, title: t.title, subtitle: t.artist, thumb: t.thumb, track: pick(t) });

function openPick(p) {
  if (!p) return;
  hideSuggest();
  $('#q').blur();
  recordPick(p);
  if (p.type === 'track' && p.track) {
    startRadio(p.track);
    if (!S.searchQ) go('#/home');
  } else if (p.type === 'artist') go('#/artist/' + encodeURIComponent(p.id));
  else if (p.type === 'album') go('#/album/' + encodeURIComponent(p.id));
}

$('#suggest').addEventListener('click', async (e) => {
  let n;
  if ((n = e.target.closest('[data-sfill]'))) {
    $('#q').value = n.dataset.sfill;
    $('#q').focus();
    syncSearchBtns();
    fetchSuggest();
    renderSuggest();
    return;
  }
  if ((n = e.target.closest('[data-sq]'))) { submitSearch(n.dataset.sq); return; }
  if ((n = e.target.closest('[data-pick]'))) { openPick(S.hist.picks[+n.dataset.pick]); return; }
  if (e.target.closest('[data-sclear]')) {
    try { await api('/searches', { method: 'DELETE' }); S.hist = { queries: [], picks: [] }; renderSuggest(); } catch (err) { toast(err.message); }
  }
});

$('#searchForm').addEventListener('submit', (e) => { e.preventDefault(); submitSearch($('#q').value); });
$('#q').addEventListener('input', () => { syncSearchBtns(); $('#suggest').hidden = false; fetchSuggest(); renderSuggest(); });
$('#q').addEventListener('focus', () => { if (location.hash.startsWith('#/search')) { openSuggest(); fetchSuggest(); } });
$('#qClear').onclick = () => { $('#q').value = ''; S.sugg = []; syncSearchBtns(); $('#q').focus(); openSuggest(); };
$('#searchOpen').onclick = () => {
  setHeaders(true);
  $('#q').value = '';
  syncSearchBtns();
  $('#q').focus();   // inside the click handler, otherwise iOS does not open the keyboard
  go('#/search');
};
$('#searchBack').onclick = () => {
  if (!$('#suggest').hidden && S.searchQ) { hideSuggest(); $('#q').value = S.searchQ; $('#q').blur(); syncSearchBtns(); return; }
  if (S.routes > 1) history.back(); else go('#/home');
};
async function viewSearch(my, params) {
  setHeaders(true);
  await refreshSources();
  if (my !== S.nav) return;
  const q = (params.get('q') || '').trim();
  const extra = S.categories || [];  // plugin categories (live radio, podcasts, ...): shown like songs, results from that plugin only
  const asked = params.get('kind');
  const kind = ['songs', 'artists', 'albums', ...extra.map((c) => c.id)].includes(asked) ? asked : 'songs';
  const auto = !asked;  // no tab chosen: once all tabs are in, the one with the most hits is selected
  const songlike = kind === 'songs' || extra.some((c) => c.id === kind);
  S.searchQ = q;
  $('#q').value = q;
  syncSearchBtns();
  const view = $('#view');
  if (!q) {
    resetLists();
    view.innerHTML = '';
    openSuggest();
    if (document.activeElement !== $('#q')) $('#q').focus();
    return;
  }
  hideSuggest();
  const kinds = [['songs', tr('Songs')], ['artists', tr('Artists')], ['albums', tr('Albums')], ...extra.map((c) => [c.id, c.label])];
  // Results per tab for this search term: every tab shows how many hits it has, and switching tabs is instant.
  // The counts are what the search returns (plugins hand over a limited number of hits per source), "100+" from 100 on.
  if (!S.sc || S.sc.q !== q || Date.now() - S.sc.t > 300000) S.sc = { q, t: Date.now(), items: {}, counts: {}, loading: new Map() };
  const sc = S.sc;
  const countText = (k) => (sc.counts[k] == null ? '' : ` <span class="cnt">(${sc.counts[k] >= 100 ? '100+' : sc.counts[k]})</span>`);
  const head = () => `<div class="chips" data-q="${esc(q)}">${kinds.map(([k, l]) => `<button class="chip${k === kind ? ' on' : ''}" data-act="kind" data-kind="${k}">${esc(l)}${countText(k)}</button>`).join('')}</div>`;
  const refreshChips = () => {  // only the counts change, so the highlighted tab stays whatever the user chose meanwhile
    const el = $('#view .chips');
    if (!el || el.dataset.q !== q || S.sc !== sc) return;
    el.querySelectorAll('[data-kind]').forEach((b) => { const k = b.dataset.kind; b.innerHTML = esc((kinds.find((x) => x[0] === k) || [0, k])[1]) + countText(k); });
  };
  const isCat = (k) => !['songs', 'artists', 'albums'].includes(k);
  const fetchParts = (k) => {  // the own library shows up at once; hits from plugins (slower) follow
    const u = (source) => `/search?q=${encodeURIComponent(q)}&kind=${encodeURIComponent(k)}&source=${source}`;
    return { plugins: api(u('plugins')).catch(() => ({ items: [] })), local: isCat(k) ? Promise.resolve({ items: [] }) : api(u('local')) };  // a plugin category has no library part
  };
  const setItems = (k, items) => { sc.items[k] = items; sc.counts[k] = items.length; };
  const ensure = (k) => {  // fetches a tab once (also in the background); resolves when it is in, or has failed
    if (sc.items[k]) return Promise.resolve();
    if (!sc.loading.has(k)) {
      const p = fetchParts(k);
      sc.loading.set(k, Promise.all([p.local, p.plugins]).then(([l, m]) => { setItems(k, l.items.concat(m.items)); refreshChips(); }).catch(() => {}));
    }
    return sc.loading.get(k);
  };
  // A hit "matches" if its title, artist or album contains every word of the search (or the words run together, "srf3" ~ "SRF 3"; accents and case are ignored).
  // Sources that always answer with a full list (25 loosely related songs) must not win just by their count.
  const fold = (s) => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const words = fold(q).split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  const matches = (x) => {
    const hay = fold([x.title, x.name, x.artist, x.album].join(' '));
    return words.length > 0 && (words.every((w) => hay.includes(w)) || hay.replace(/[^\p{L}\p{N}]/gu, '').includes(words.join('')));
  };
  const score = (k) => (sc.items[k] || []).filter(matches).length;
  const pickBest = () => {  // without a chosen tab: switch to the tab with the most matching hits, if none matches to the one with the most hits (a tie keeps the first, songs), unless the user moved on
    if (!auto || my !== S.nav || S.sc !== sc) return;
    const rank = kinds.some(([k]) => score(k) > 0) ? score : (k) => sc.counts[k] || 0;
    const best = kinds.reduce((b, [k]) => (rank(k) > rank(b) ? k : b), kind);
    if (best === kind) return;
    history.replaceState(null, '', '#/search?q=' + encodeURIComponent(q) + '&kind=' + encodeURIComponent(best));
    route();
  };
  const loadOthers = () => Promise.race([Promise.all(kinds.map(([k]) => ensure(k))), new Promise((r) => setTimeout(r, 5000))]).then(pickBest);
  const paint = (items, pending) => {
    resetLists();
    let body = '';
    if (songlike) {
      setList('r', items, 'radio');
      body = items.length ? rows('r') : '';
    } else {
      const cards = items.map((a) => (a.type === 'artist'
        ? { type: 'artist', id: a.id, title: a.name, subtitle: [tr('Artist'), srcName(a)].filter(Boolean).join(' · '), thumb: a.thumb }
        : { type: 'album', id: a.id, title: a.title, subtitle: [a.artist, a.kind, a.year, srcName(a)].filter(Boolean).join(' · '), thumb: a.thumb }));
      setList('p', cards, 'pick');
      body = cards.map((p, i) => `<div class="track" data-l="p" data-i="${i}">${cover(p.thumb, p.type === 'artist' ? 'round' : '')}<div class="meta"><div class="t">${esc(p.title)}</div><div class="a">${esc(p.subtitle)}</div></div></div>`).join('');
    }
    if (!items.length && !pending) { view.innerHTML = head() + `<div class="empty">${esc(tr('Nothing found.'))}</div>`; return; }
    view.innerHTML = head() + '<div>' + body + '</div>' + (pending ? `<div class="empty" style="padding:16px">${esc(tr('More sources …'))}</div>` : '');
  };
  if (sc.items[kind]) {  // already fetched (background or earlier visit)
    paint(sc.items[kind], false);
    loadOthers();
    return;
  }
  view.innerHTML = head() + loading(tr('Searching …'));
  const parts = fetchParts(kind);
  let local;
  try {
    local = await parts.local;
  } catch (e) {
    if (my === S.nav && e.status !== 401) view.innerHTML = head() + errorBox(e.message);
    return;
  }
  if (my !== S.nav) return;
  paint(local.items, true);
  const more = await parts.plugins;
  setItems(kind, local.items.concat(more.items));
  refreshChips();
  if (my !== S.nav) return;
  paint(sc.items[kind], false);
  loadOthers();
}

// ---------- Routing ----------

function route() {
  const my = ++S.nav;
  S.routes++;
  const [path, qs] = (location.hash.slice(1) || '/home').split('?');
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
  const params = new URLSearchParams(qs || '');
  const isSearch = parts[0] === 'search';
  if (!isSearch) { setHeaders(false); hideSuggest(); }
  $('#view').scrollTop = 0;
  let job;
  switch (parts[0]) {
    case 'search': job = viewSearch(my, params); break;
    case 'artist': job = viewArtist(my, parts[1] || '', params.get('name') || ''); break;
    case 'album': job = viewAlbum(my, parts[1] || ''); break;
    case 'playlists': job = viewPlaylists(my); break;
    case 'playlist': job = viewPlaylist(my, parts[1] || ''); break;
    case 'auto': job = viewAuto(my, parts[1] || ''); break;
    case 'likes': job = viewLikes(my); break;
    case 'community': job = viewCommunity(my); break;
    case 'account': job = parts[1] === 'recap' ? viewRecap(my, params) : viewAccount(my); break;
    case 'admin': job = viewAdmin(my, parts[1] || ''); break;
    case 'users': job = viewAdmin(my, 'users'); break;
    default: job = renderHome(my);
  }
  job.catch((e) => {
    if (my !== S.nav || e.status === 401) return;
    $('#view').innerHTML = errorBox(e.message);
  });
}

window.addEventListener('hashchange', route);

document.addEventListener('toggle', (e) => {  // remember which plugin cards are open
  const d = e.target;
  if (d && d.matches && d.matches('details.plg')) { if (d.open) S.popen.add(d.dataset.id); else S.popen.delete(d.dataset.id); }
}, true);
document.addEventListener('input', (e) => {  // filter the plugin list
  if (e.target.id !== 'pluginFilter') return;
  const q = e.target.value.trim().toLowerCase();
  document.querySelectorAll('#pluginList .plg').forEach((d) => { d.hidden = !!q && !d.dataset.name.includes(q); });
});

document.addEventListener('change', async (e) => {
  if (e.target.dataset && e.target.dataset.logcat) {  // log level of a category (admin area)
    try { await api('/admin/logging', { method: 'PUT', json: { levels: { [e.target.dataset.logcat]: e.target.value } } }); toast(tr('Log level saved')); route(); } catch (er) { toast(er.message, 3000, true); }
    return;
  }
  if (e.target.id === 'pluginZip' && e.target.files[0]) {  // plugin ZIP upload (admin area)
    const file = e.target.files[0], replace = S.zipReplace || '';
    e.target.value = '';
    if (!(await uiConfirm(tr('Install a plugin from the ZIP file “{name}”?\n\nThe server unpacks it, installs its dependencies and runs it. Only install plugins from sources you trust.', { name: file.name })))) return;
    toast(tr('Uploading plugin …'), 60000);
    try {
      const body = new FormData();  // multipart/form-data: what proxies and firewalls expect for uploads
      body.append('file', file, file.name);
      const r = await fetch('/api/admin/plugins/upload' + (replace ? '?replace=' + encodeURIComponent(replace) : ''), { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': S.csrf }, body });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || tr('Upload failed (HTTP {code})', { code: r.status }));
      toast(tr('“{name}” installed, setting up', { name: d.name }));
      await loadSources();
      route();
    } catch (er) { toast(er.message, 6000, true); }
    return;
  }
  if (e.target.id === 'langSel') {  // language choice on the account page
    const pref = e.target.value;
    try {
      await api('/me/lang', { method: 'PUT', json: { lang: pref } });
      S.user.lang = pref;
      await setLanguage(pref);
      await loadSources();
      route();
    } catch (er) { toast(er.message, 3000, true); }
    return;
  }
  if (e.target.id !== 'restoreFile' || !e.target.files[0]) return;
  const file = e.target.files[0];
  e.target.value = '';
  if (!(await uiConfirm(tr('Restore backup “{name}”?\n\nThis replaces all users, playlists, histories and settings with the state of the backup. Everyone has to log in again afterwards.', { name: file.name })))) return;
  toast(tr('Restoring backup …'), 60000);
  try {
    const body = new FormData();
    body.append('file', file, file.name);
    const r = await fetch('/api/admin/restore', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': S.csrf }, body });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || tr('Restoring failed (HTTP {code})', { code: r.status }));
    await uiAlert(tr('Backup restored. You will be logged out; please log in again.'));
    location.reload();
  } catch (err) { toast(err.message, 5000, true); }
});

// ---------- Playback ----------

function playList(tracks, i) {
  S.radioToken++;
  S.queue = tracks.slice();
  S.idx = -1;
  playIndex(i);
}

function playFromList(key, i) {
  const list = S.lists[key] || [];
  if (!list[i]) return;
  if (key === 'queue') { playIndex(i); return; }
  if (S.modes[key] === 'pick') { openPick(list[i]); return; }
  if (S.modes[key] === 'radio') { recordPick(trackPick(list[i])); startRadio(list[i]); return; }
  playList(list, i);
}

async function startRadio(track) {
  toast(tr('Starting radio …'));
  const token = ++S.radioToken;
  S.queue = [track];
  S.idx = -1;
  playIndex(0);
  try {
    const { tracks } = await api(`/radio/${encodeURIComponent(track.id)}${DJ.on ? '?dj=1' : ''}`);
    if (token !== S.radioToken) return;
    S.queue = [track, ...tracks.filter((t) => t.id !== track.id)];
    refreshQueueIfOpen();
  } catch (e) { toast(tr('Radio unavailable: {why}', { why: e.message })); }
}

function extendRadio() {
  if (S.extendP) return S.extendP;
  if (!S.queue.length) return Promise.resolve();
  S.extendP = (async () => {
    try {
      const seed = S.queue[S.queue.length - 1].id;
      const { tracks } = await api(`/radio/${encodeURIComponent(seed)}${DJ.on ? '?dj=1' : ''}`);
      const have = new Set(S.queue.map((t) => t.id));
      S.queue.push(...tracks.filter((t) => !have.has(t.id)));
      refreshQueueIfOpen();
    } catch (_) { /* next attempt on the next track */ }
  })().finally(() => { S.extendP = null; });
  return S.extendP;
}

function playIndex(i, opts = {}) {
  if (i < 0 || i >= S.queue.length) return;
  if (!opts.mix) djAbort();
  S.restored = null;
  if (!opts.mix) { S.stRetry = 0; S.t0 = performance.now(); }
  S.idx = i;
  S.reported = false;
  const t = S.queue[i];
  if (!opts.mix) {  // during a DJ transition the track is already playing on the other element
    const prevBlob = S.curBlob || '';
    let src = `/api/stream/${encodeURIComponent(t.id)}`;
    S.curBlob = '';
    if (PRE.url && PRE.id === t.id) {  // already in memory: starts without network
      src = PRE.url; S.curBlob = PRE.url; PRE.url = ''; PRE.id = ''; S.t0 = 0;
      evlog('Start from preloaded memory ' + t.id.slice(0, 6));
    } else {
      preloadFree();
      if (IOS) evlog('Start over network ' + t.id.slice(0, 6));
    }
    S.preTried = '';
    audio.src = src;
    if (prevBlob) URL.revokeObjectURL(prevBlob);
    audio.play().catch((e) => { evlog('play() rejected: ' + (e && e.name)); if (e && e.name === 'NotAllowedError') setPlaying(false, false); });  // iOS refuses: show the play button instead of pause
    setPlaying(S.wantPlay, true);
  }
  updateNow();
  if (!opts.mix) watchLoading();
  setMediaSession(t);
  if (S.queue.length - i < 5 && S.repeat !== 'all') extendRadio();
  document.querySelectorAll('.track[data-l]').forEach((r) => {
    const item = (S.lists[r.dataset.l] || [])[+r.dataset.i];
    r.classList.toggle('now', !!item && item.id === t.id);
  });
  updateQueueTitle();
}

function enqueue(t) {
  S.queue.push(t);
  toast(tr('Added to queue'));
  if (S.idx < 0) playIndex(S.queue.length - 1);
  else refreshQueueIfOpen();
}

function playNext(t) {
  if (S.idx < 0) { playList([t], 0); return; }
  S.queue.splice(S.idx + 1, 0, t);
  toast(tr('Plays next'));
  refreshQueueIfOpen();
}

// Move track `from` to the end position `to` (to = index after removal)
function moveInQueue(from, to) {
  to = Math.min(Math.max(to, 0), S.queue.length - 1);
  if (from === to || from < 0 || from >= S.queue.length) return;
  const [t] = S.queue.splice(from, 1);
  S.queue.splice(to, 0, t);
  if (S.idx === from) S.idx = to;
  else if (from < S.idx && to >= S.idx) S.idx--;
  else if (from > S.idx && to <= S.idx) S.idx++;
  renderQueuePanel();
}

function moveNextInQueue(i) {
  if (i === S.idx) { toast(tr('Playing right now')); return; }
  moveInQueue(i, i < S.idx ? S.idx : S.idx + 1);
  toast(tr('Plays next'));
}

function clearRestOfQueue() {
  if (S.idx < 0 || S.idx + 1 >= S.queue.length) { toast(tr('Nothing left in the queue')); return; }
  S.radioToken++;  // discard a radio refill that is in progress
  S.queue.splice(S.idx + 1);
  renderQueuePanel();
  toast(tr('Rest of the queue cleared'));
}

function removeFromQueue(i) {
  if (i === S.idx) { toast(tr('Playing right now')); return; }
  S.queue.splice(i, 1);
  if (i < S.idx) S.idx--;
  renderQueuePanel();
}

async function next() {
  if (S.idx + 1 >= S.queue.length) {
    if (S.repeat === 'all' && S.queue.length) { playIndex(0); return; }
    await extendRadio();
  }
  if (S.idx + 1 < S.queue.length) playIndex(S.idx + 1);
  else audio.pause();
}

function prev() {
  if (audio.currentTime > 3 || S.idx <= 0) { audio.currentTime = 0; return; }
  playIndex(S.idx - 1);
}

function togglePlay() {
  if (!currentTrack()) return;
  if (S.restored) {  // after reload: only now load the track, jump to the remembered position
    const pos = S.restored.pos; S.restored = null;
    S.wantPlay = true;
    playIndex(S.idx);
    if (pos > 3) audio.addEventListener('loadedmetadata', () => { if (audio.currentTime < 2 && pos < audio.duration - 3) audio.currentTime = pos; }, { once: true });
    return;
  }
  if (audio.paused) audio.play().catch(() => {});
  else audio.pause();
}

function toggleShuffle() {
  S.shuffle = !S.shuffle;
  if (S.shuffle && S.idx >= 0) {
    S.queue = S.queue.slice(0, S.idx + 1).concat(shuffled(S.queue.slice(S.idx + 1)));
    refreshQueueIfOpen();
    toast(tr('Queue shuffled'));
  }
  syncModes();
}

function cycleRepeat() {
  S.repeat = { off: 'all', all: 'one', one: 'off' }[S.repeat];
  toast({ off: tr('Repeat off'), all: tr('Repeat all'), one: tr('Repeat song') }[S.repeat]);
  syncModes();
}

// ---------- Player display ----------

const fmt = (s) => (isFinite(s) ? Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0') : '0:00');

function setImg(img, url) {
  img.classList.remove('ld');
  if (url) { img.src = url; img.style.visibility = ''; } else { img.removeAttribute('src'); img.style.visibility = 'hidden'; }
}

// Background colour of the large player from the cover (only if the image is readable via CORS, otherwise the default stays)
const _glow = new Map();
function coverGlow(url) {
  const full = $('#full');
  const set = (c) => { if (c) full.style.setProperty('--glow', c); else full.style.removeProperty('--glow'); };
  if (!url) return set(null);
  if (_glow.has(url)) return set(_glow.get(url));
  set(null);
  const im = new Image();
  im.crossOrigin = 'anonymous';
  im.referrerPolicy = 'no-referrer';
  im.onload = () => {
    try {
      const c = document.createElement('canvas');
      c.width = c.height = 12;
      const x = c.getContext('2d', { willReadFrequently: true });
      x.drawImage(im, 0, 0, 12, 12);
      const d = x.getImageData(0, 0, 12, 12).data;
      let r = 0, g = 0, b = 0, w = 0;
      for (let i = 0; i < d.length; i += 4) {
        const mx = Math.max(d[i], d[i + 1], d[i + 2]), mn = Math.min(d[i], d[i + 1], d[i + 2]);
        const wt = 0.15 + (mx - mn) / 255;  // coloured pixels count more than grey ones
        r += d[i] * wt; g += d[i + 1] * wt; b += d[i + 2] * wt; w += wt;
      }
      const col = `rgba(${Math.round(r / w)},${Math.round(g / w)},${Math.round(b / w)},.5)`;
      _glow.set(url, col);
      if (currentTrack() && currentTrack().thumb === url) set(col);
    } catch (e) { /* canvas blocked: default colour */ }
  };
  im.src = url;
}

// A live stream (radio) has no end: the plugin says so (`live`), or the track has no length and the audio reports none either although it is playing.
// The player then shows "Live" between two thin lines instead of the progress bar.
function isLive(t) {
  if (!t) return false;
  if (t.live) return true;
  const d = audio.duration;
  return !t.duration && !(d > 0 && isFinite(d)) && audio.currentTime > 2;
}
// Live radio: which song plays right now. The plugin reads it from the station's stream; the player asks about every 20 s while a live track plays.
const NP_EVERY = 20000;
function nowPlayingText() { return S.np && S.np.title ? (S.np.artist ? S.np.artist + ' – ' : '') + S.np.title : ''; }
function renderNowPlaying() {
  const t = currentTrack(), text = t && S.live ? nowPlayingText() : '';
  $('#fNow').hidden = !text;
  $('#fNow').innerHTML = text ? `<span class="dim">${esc(tr('Now playing'))}</span>${esc(text)}` : '';
  if (t && !S.loading) $('#mArtist').textContent = text || t.artist;
  if (t) setMediaSession(t);
}
function nowPlayingStop() {
  clearInterval(S.npTimer);
  S.npTimer = 0;
  S.np = null;
}
async function nowPlayingPoll(id) {
  try {
    const r = await api('/nowplaying/' + encodeURIComponent(id), { noRedirect: true });
    const t = currentTrack();
    if (!t || t.id !== id || !S.live) return;
    S.np = r && r.title ? r : null;
    renderNowPlaying();
  } catch (_) { /* optional: no answer, no display */ }
}
function nowPlayingStart() {
  nowPlayingStop();
  const t = currentTrack();
  if (!t || !t.id.includes(':') || t.id.startsWith('local:')) return;  // only plugin tracks can have it
  nowPlayingPoll(t.id);
  S.npTimer = setInterval(() => { if (!audio.paused) nowPlayingPoll(t.id); }, NP_EVERY);
}

function syncLive() {
  const live = isLive(currentTrack());
  if (S.live === live) return;
  S.live = live;
  if (live) nowPlayingStart(); else { nowPlayingStop(); renderNowPlaying(); }
  $('#fBar').hidden = live;
  document.querySelector('.f-times').hidden = live;
  $('#fLive').hidden = !live;
  $('#mini').classList.toggle('live', live);
  $('#mTime').textContent = live ? tr('Live') : '';
  if (live) setProgress(0);
}

function updateNow() {
  const t = currentTrack();
  if (!t) return;
  coverGlow(t.thumb);
  document.body.classList.add('has-track');
  $('#mini').hidden = false;
  setImg($('#mCover'), t.thumb);
  setImg($('#fCover'), t.thumb);
  $('#mTitle').textContent = t.title;
  $('#mArtist').textContent = t.artist;
  $('#fTitleText').textContent = t.title;
  $('#fTitle').classList.toggle('link', !!t.album_id);
  $('#fArtist').textContent = t.artist;
  $('#fSrc').textContent = srcName(t);
  $('#fArtistBtn').hidden = !t.artist_id;
  $('#fAlbumBtn').hidden = !t.album_id;
  $('#fCur').textContent = '0:00';
  $('#fDur').textContent = t.duration || '';
  resetLyrics();
  setProgress(0);
  S.live = null;  // decided anew for every track
  nowPlayingStop();
  syncLive();
  syncLike();
}

function syncLike() {
  const t = currentTrack();
  const on = !!t && S.likes.has(t.id);
  const b = $('#fLike');
  b.classList.toggle('on', on);
  b.innerHTML = (on ? IC.liked : IC.like) + `<span>${esc(tr('Like'))}</span>`;
}

function syncModes() {
  $('#fShuffle').classList.toggle('on', S.shuffle);
  const r = $('#fRepeat');
  r.innerHTML = S.repeat === 'one' ? IC.repeat1 : IC.repeat;
  r.classList.toggle('on', S.repeat !== 'off');
}

// Progress display: via transform (GPU), no CSS transition, so dragging and jumping react immediately
function setProgress(f) {
  f = Math.min(1, Math.max(0, f || 0));
  $('#mProg').style.transform = `scaleX(${f})`;
  if (!S.scrub) {
    $('#fProg').style.transform = `scaleX(${f})`;
    $('#fKnob').style.left = (f * 100) + '%';
  }
}

// Smooth motion: depending on the browser, currentTime only changes with audio buffers or timeupdate (~4x per second).
// Between two changes it is therefore extrapolated per frame (at most 0.3 s); every real change takes over again.
(function smoothProgress() {
  let lastCt = -1, stamp = 0, shown = 0;
  const tick = (now) => {
    S.raf = requestAnimationFrame(tick);
    const d = audio.duration;
    if (!d || !isFinite(d)) return;
    const ct = audio.currentTime;
    if (ct !== lastCt) { lastCt = ct; stamp = now; }
    let pos = ct;
    if (!audio.paused && !S.loading && !audio.seeking) pos += Math.min((now - stamp) / 1000, 0.3) * audio.playbackRate;
    pos = Math.min(d, pos);
    if (pos < shown && shown - pos < 0.5 && !audio.seeking) pos = shown;  // never jump back by a few milliseconds
    shown = pos;
    setProgress(pos / d);
  };
  const start = () => { if (!S.raf && document.visibilityState === 'visible') S.raf = requestAnimationFrame(tick); };
  const stop = () => { if (S.raf) { cancelAnimationFrame(S.raf); S.raf = 0; } };
  audio.addEventListener('playing', start);
  audio.addEventListener('play', start);
  audio.addEventListener('pause', () => { stop(); const d = audio.duration; if (d) setProgress(audio.currentTime / d); });
  audio.addEventListener('seeked', () => { const d = audio.duration; if (d) setProgress(audio.currentTime / d); });
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && !audio.paused) start(); else stop(); });
})();

// ---------- Event log (on the device only, for diagnosis) ----------
const EV_KEY = 'app_evlog';
let evLast = ['', 0];
function evlog(msg, throttle = 0) {
  try {
    const n = Date.now();
    if (throttle && evLast[0] === msg && n - evLast[1] < throttle) return;
    evLast = [msg, n];
    const a = JSON.parse(localStorage.getItem(EV_KEY) || '[]');
    a.push(new Date().toTimeString().slice(0, 8) + ' ' + msg);
    localStorage.setItem(EV_KEY, JSON.stringify(a.slice(-40)));
  } catch (_) { /* storage blocked: then without */ }
}
function evText(n = 10) {
  try { return JSON.parse(localStorage.getItem(EV_KEY) || '[]').slice(-n).join('\n') || '(empty)'; } catch (_) { return '(unreadable)'; }
}

// Tells the server which tracks come next, so plugins can fetch them ahead of time (best effort; each plugin decides how many it takes).
function hintUpcoming() {
  const cur = currentTrack();
  if (!cur) return;
  const ids = S.queue.slice(S.idx + 1, S.idx + 11).map((t) => t.id).filter((id) => id.includes(':') && !id.startsWith('local:'));
  const key = cur.id + '>' + ids.join(',');
  if (!ids.length || S.hinted === key) return;
  S.hinted = key;
  api('/prefetch', { method: 'POST', json: { ids }, noRedirect: true }).catch(() => {});
}

// ---------- iPhone: preload the next track into memory ----------
// iOS freezes the page in the background as soon as no sound plays for a moment between two tracks. If the next track is already
// in memory as a blob, it starts without a network gap.
const PRE = { id: '', url: '', ctl: null };
function preloadFree() {
  if (PRE.ctl) { PRE.ctl.abort(); PRE.ctl = null; }
  if (PRE.url) URL.revokeObjectURL(PRE.url);
  PRE.id = ''; PRE.url = '';
}
async function preloadNext() {
  const nt = S.queue[S.idx + 1];
  if (!IOS || !S.user || !nt || PRE.id === nt.id) return;
  preloadFree();
  const ctl = new AbortController();
  PRE.ctl = ctl;
  const timer = setTimeout(() => ctl.abort(), 180000);
  try {
    const r = await fetch(`/api/stream/${encodeURIComponent(nt.id)}?pre=1`, { credentials: 'same-origin', signal: ctl.signal });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const b = await r.blob();
    if (ctl.signal.aborted || PRE.ctl !== ctl || (S.queue[S.idx + 1] || {}).id !== nt.id) return;  // the order has changed
    PRE.id = nt.id;
    PRE.url = URL.createObjectURL(b);
    evlog(`Preloaded ${nt.id.slice(0, 6)}, ${(b.size / 1048576).toFixed(1)} MB`);
  } catch (e) {
    if (e && e.name !== 'AbortError') evlog('Preload failed: ' + (e.message || e.name));
  } finally {
    clearTimeout(timer);
    if (PRE.ctl === ctl) PRE.ctl = null;
  }
}

// ---------- Self-healing: the element says "playing" but audio is dead (e.g. after another app took over the audio session) ----------
function resetAudio(t, why) {
  S.stRetry = (S.stRetry || 0) + 1;
  S.stAt = Date.now();
  S.resetting = true;
  const pos = audio.currentTime || 0;
  evlog(`Audio reset (${why}) at ${pos.toFixed(0)} s`);
  setPlaying(true, true);
  watchLoading();
  try { audio.pause(); audio.removeAttribute('src'); audio.load(); } catch (_) { /* ignore */ }
  setTimeout(() => {
    S.resetting = false;
    if (currentTrack() !== t) return;
    audio.src = `/api/stream/${encodeURIComponent(t.id)}?r=${Date.now()}`;
    if (pos > 1) audio.addEventListener('loadedmetadata', () => { if (audio.currentTime < pos - 1) audio.currentTime = pos; }, { once: true });
    audio.play().catch((e) => { evlog('play() after reset: ' + (e && e.name)); if (e && e.name === 'NotAllowedError') setPlaying(false, false); });
  }, 400);
}

// Last stage: reload the page (track and position come back from the saved player state). At most twice in 10 minutes.
function autoReload(t) {
  try {
    const now = Date.now();
    const hist = JSON.parse(sessionStorage.getItem('app_rl') || '[]').filter((x) => now - x < 600000);
    if (hist.length >= 2) return false;
    hist.push(now);
    sessionStorage.setItem('app_rl', JSON.stringify(hist));
    localStorage.setItem('app_resume', JSON.stringify({ id: t.id, ts: now }));
  } catch (_) { return false; }
  evlog('Page reloads (playback dead)');
  savePlayerState(true);
  location.reload();
  return true;
}

// Back in the app, music should play but time stands still: rebuild immediately, not only after 6 s and several attempts
function checkAlive() {
  const t = currentTrack();
  if (!t || !S.user || S.restored || !S.wantPlay || S.loading || S.resetting || !audio.getAttribute('src') || audio.ended) return;
  const ct = audio.currentTime;
  setTimeout(() => {
    if (currentTrack() !== t || !S.wantPlay || S.loading || S.resetting || audio.seeking || audio.ended) return;
    if (Math.abs(audio.currentTime - ct) < 0.05) { evlog('Back in the app, time stands still'); resetAudio(t, 'return'); }
  }, 2000);
}

function showLoadInfo(text) {
  if (text && !S.loading) return;  // a late answer after start-up must not bring the display back
  const t = currentTrack();
  $('#fLoad').textContent = text;
  $('#mArtist').textContent = text || (t ? t.artist : '');
}

const stateText = () => `paused ${audio.paused}, time ${audio.currentTime.toFixed(2)}, ended ${audio.ended}, seeking ${audio.seeking}, rate ${audio.playbackRate}, muted ${audio.muted}, volume ${audio.volume}, visible ${document.visibilityState}`;

// Tap = real gesture: does the audio element accept play()?
async function playTest() {
  const out = $('#diagOut');
  let pr;
  try {
    pr = await Promise.race([audio.play().then(() => 'play() ok', (er) => `play() error ${er.name}: ${er.message}`), new Promise((r) => setTimeout(() => r('play() hangs (3 s without answer)'), 3000))]);
  } catch (er) { pr = 'play() exception ' + er.message; }
  await new Promise((r) => setTimeout(r, 2000));
  out.textContent = `Test: ${pr}\nAfterwards: ${stateText()}`;
  if (audio.paused === false) audio.pause();
}

// What does the device really see? A small test request and the state of the audio element, as text to pass on
async function diagnose(t) {
  const e = audio.error;
  let probe;
  try {
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), 15000);
    const r = await fetch(`/api/stream/${encodeURIComponent(t.id)}?diag=1`, { headers: { Range: 'bytes=0-4095' }, credentials: 'same-origin', signal: ctl.signal });
    const b = await r.arrayBuffer();
    clearTimeout(to);
    probe = `HTTP ${r.status}, ${r.headers.get('content-type') || '?'}, ${b.byteLength} bytes, Range ${r.headers.get('content-range') || '-'}, ${r.headers.get('server') || ''}`;
  } catch (err) { probe = 'Request failed: ' + (err && err.name === 'AbortError' ? 'timeout (15 s)' : err.message); }
  let br = '-';
  try { br = audio.buffered.length ? audio.buffered.end(audio.buffered.length - 1).toFixed(1) + ' s' : 'empty'; } catch (_) { /* ignore */ }
  return [`Version ${S.ver ? S.ver.version : "?"}`, `State: ${stateText()}`, `Track ${t.id}`, `Test request: ${probe}`, `Audio: error ${e ? e.code + ' ' + (e.message || '') : 'none'}, networkState ${audio.networkState}, readyState ${audio.readyState}, buffer ${br}, duration ${audio.duration || '?'}`,
    `Device: ${navigator.userAgent}`, `Online: ${navigator.onLine}, connection: ${(navigator.connection && navigator.connection.effectiveType) || '?'}`, `Log:\n${evText(10)}`].join('\n');
}

function stallRetry(t) {
  S.stRetry = (S.stRetry || 0) + 1;
  S.stAt = Date.now();
  const pos = audio.currentTime;
  audio.src = `/api/stream/${encodeURIComponent(t.id)}?r=${Date.now()}`;
  if (pos > 1) audio.addEventListener('loadedmetadata', () => { if (audio.currentTime < pos - 1) audio.currentTime = pos; }, { once: true });
  audio.play().catch(() => {});
}

// Watchdog: if music should play but the time stands still for 6 s, and the server no longer loads by itself (file is ready),
// then the transfer or the audio element got stuck (iOS often does not report this at all). Request again; give up after 4 attempts.
setInterval(async () => {
  const t = currentTrack();
  if (!t || !S.user || S.restored || !S.wantPlay || !audio.getAttribute('src') || audio.ended || S.smBusy || S.resetting) return;
  const ct = audio.currentTime;
  if (S.smId !== t.id || Math.abs(ct - S.smCt) > 0.05 || audio.seeking) { S.smId = t.id; S.smCt = ct; S.smAt = Date.now(); return; }
  if (Date.now() - S.smAt < 6000) return;
  S.smBusy = true;
  try {
    if (currentTrack() !== t || !S.wantPlay || Math.abs(audio.currentTime - ct) > 0.05) return;
    const n = S.stRetry || 0;
    if (n < 4) {
      if (!S.loading) { setPlaying(true, true); watchLoading(); }
      if (n < 2) { evlog(`Watchdog: request again (${n + 1})`); stallRetry(t); } else resetAudio(t, 'watchdog');
    } else if (autoReload(t)) {
      return;
    } else {
      setPlaying(false, false);
      toast(tr('Playback is stuck. Tap play.'), 4000, true);
      diagnose(t).then((msg) => { if (currentTrack() === t && !S.wantPlay) openSheet(`<div class="sh-title">${esc(tr('Playback is stuck'))}</div><div class="news"><p class="dim small" style="padding:0 12px">${esc(tr('Please send a screenshot of this:'))}</p><pre class="diag">${esc(msg)}</pre><pre class="diag" id="diagOut" style="margin-top:8px">${esc(tr('Test not run yet'))}</pre></div><div class="actions"><button class="btn sec" id="diagPlay">${esc(tr('Test playback'))}</button><button class="btn" data-close>${esc(tr('Close'))}</button></div>`); const b = $('#diagPlay'); if (b) b.onclick = playTest; });
    }
    S.smAt = Date.now();
  } catch (_) { /* next attempt */ } finally { S.smBusy = false; }
}, 1000);

function watchLoading() {
  clearInterval(S.loadTimer);
  const t = currentTrack();
  if (!t) return;
  S.wdCt = null; S.lastCt = null;
  const since = Date.now();
  S.loadTimer = setInterval(async () => {
    if (!S.loading || currentTrack() !== t) { clearInterval(S.loadTimer); showLoadInfo(''); return; }
    // Stuck watchdog: time actually keeps running -> it is not loading any more
    const ct = audio.currentTime;
    if (!audio.paused && !audio.seeking && S.wdCt != null && ct > S.wdCt + 0.3) { S.wdCt = null; setPlaying(true, false); return; }
    S.wdCt = ct;
  }, 1000);
}

function setPlaying(p, loading = S.loading) {
  S.wantPlay = p;
  S.loading = loading;
  document.body.classList.toggle('playing', p && !loading);
  if ('mediaSession' in navigator) navigator.mediaSession.playbackState = p ? 'playing' : (currentTrack() ? 'paused' : 'none');
  if (!loading) { clearInterval(S.loadTimer); showLoadInfo(''); }
  const ic = loading ? SPIN : (p ? IC.pause : IC.play);
  $('#mPlay').innerHTML = ic;
  $('#fPlay').innerHTML = ic;
}

function openFull() { if (currentTrack()) $('#full').classList.add('open'); }
function closeFull() { $('#full').classList.remove('open'); closeQueue(); }

function updateQueueTitle() {
  const n = S.queue.length - S.idx - 1;
  $('#qTitle').textContent = n > 0 ? tr('Up next ({n})', { n }) : tr('Queue');
  $('#fQueueLabel').textContent = n > 0 ? tr('Up next · {n}', { n }) : tr('Queue');
}

function renderQueuePanel() {
  setList('queue', S.queue);
  $('#qList').innerHTML = rows('queue');
  updateQueueTitle();
}

function refreshQueueIfOpen() {
  if ($('#qPanel').classList.contains('open')) renderQueuePanel(); else updateQueueTitle();
}

function openQueue() {
  renderQueuePanel();
  $('#qPanel').classList.add('open');
  const cur = $('#qList .track.now');
  if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: 'center' });
}
function closeQueue() { $('#qPanel').classList.remove('open'); }

async function toggleLike(t) {
  if (!t) return;
  haptic();
  const was = S.likes.has(t.id);
  if (was) S.likes.delete(t.id); else S.likes.add(t.id);
  syncLike();
  try {
    if (was) await api('/likes/' + encodeURIComponent(t.id), { method: 'DELETE' });
    else await api('/likes', { json: pick(t) });
    toast(was ? tr('Like removed') : tr('Added to liked songs'));
  } catch (e) {
    if (was) S.likes.add(t.id); else S.likes.delete(t.id);
    syncLike();
    toast(e.message);
  }
}

// Progress bar: tap or drag
(function bindScrub() {
  const bar = $('#fBar');
  const frac = (e) => { const r = bar.getBoundingClientRect(); return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)); };
  const show = (f) => {
    $('#fProg').style.transform = `scaleX(${f})`;
    $('#fKnob').style.left = (f * 100) + '%';
    $('#fCur').textContent = fmt(f * (audio.duration || 0));
  };
  bar.addEventListener('pointerdown', (e) => {
    if (!audio.duration) return;
    S.scrub = true;
    try { bar.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
    show(frac(e));
  });
  bar.addEventListener('pointermove', (e) => { if (S.scrub) show(frac(e)); });
  bar.addEventListener('pointerup', (e) => { if (!S.scrub) return; S.scrub = false; audio.currentTime = frac(e) * audio.duration; });
  bar.addEventListener('pointercancel', () => { S.scrub = false; });
})();

$('#mPlay').onclick = togglePlay;
$('#fPlay').onclick = togglePlay;
$('#mNext').onclick = next;
$('#mPrev').onclick = prev;
$('#mQueue').onclick = () => { openFull(); openQueue(); };

// Volume (visible on desktop only; on iOS the volume is up to the hardware buttons)
(function bindVolume() {
  let v = 1;
  try { const s = parseFloat(localStorage.getItem('app.vol')); if (s >= 0 && s <= 1) v = s; } catch (_) { /* ignore */ }
  const apply = (x) => {
    audio.volume = x;
    $('#mVol').value = x;
    $('#mMute').innerHTML = x === 0 ? IC.mute : IC.vol;
    try { localStorage.setItem('app.vol', String(x)); } catch (_) { /* ignore */ }
  };
  S.setVol = apply;
  S.vol = () => audio.volume;
  apply(v);
  $('#mVol').addEventListener('input', (e) => { S.premute = 0; apply(+e.target.value); });
  $('#mMute').onclick = () => {
    if (audio.volume > 0) { S.premute = audio.volume; apply(0); } else apply(S.premute || 0.8);
  };
})();

// Progress bar of the player bar (desktop) for jumping
$('#mProgWrap').addEventListener('click', (e) => {
  if (!window.matchMedia('(min-width: 900px)').matches || !audio.duration) return;
  const r = e.currentTarget.getBoundingClientRect();
  audio.currentTime = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * audio.duration;
});

// Keyboard shortcuts (desktop)
document.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = (e.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable) return;
  if (!$('#app') || $('#app').hidden) return;
  const k = e.key;
  const seek = (s) => { if (audio.duration) audio.currentTime = Math.min(audio.duration, Math.max(0, audio.currentTime + s)); };
  if (k === ' ' || k === 'k') { e.preventDefault(); togglePlay(); }
  else if (k === 'ArrowRight' && e.shiftKey || k === 'n') next();
  else if (k === 'ArrowLeft' && e.shiftKey || k === 'p') prev();
  else if (k === 'ArrowRight') { e.preventDefault(); seek(10); }
  else if (k === 'ArrowLeft') { e.preventDefault(); seek(-10); }
  else if (k === 'ArrowUp') { e.preventDefault(); S.setVol(Math.min(1, +(audio.volume + 0.05).toFixed(2))); }
  else if (k === 'ArrowDown') { e.preventDefault(); S.setVol(Math.max(0, +(audio.volume - 0.05).toFixed(2))); }
  else if (k === 'm') $('#mMute').click();
  else if (k === 'l') toggleLike(currentTrack());
  else if (k === '/') { e.preventDefault(); $('#searchOpen').click(); }
  else if (k === 'Escape') { if (!$('#sheet').hidden) closeSheet(); else if ($('#qPanel').classList.contains('open')) closeQueue(); else closeFull(); }
});
$('#fNext').onclick = next;
$('#fPrev').onclick = prev;
$('#mOpen').onclick = openFull;
$('#fClose').onclick = closeFull;
$('#fShuffle').onclick = toggleShuffle;
$('#fRepeat').onclick = cycleRepeat;
$('#fLike').onclick = () => toggleLike(currentTrack());
$('#fAdd').onclick = () => { const t = currentTrack(); if (t) openPlaylistPicker([t]); };
$('#fArtist').onclick = $('#fArtistBtn').onclick = () => { const t = currentTrack(); if (t && t.artist_id) go('#/artist/' + t.artist_id + '?name=' + encodeURIComponent((t.artist || '').split(', ')[0])); };
$('#fTitle').onclick = $('#fAlbumBtn').onclick = () => { const t = currentTrack(); if (t && t.album_id) go('#/album/' + t.album_id); };
$('#fMenu').onclick = () => { const t = currentTrack(); if (t) trackMenu(t, 'now', S.idx); };
$('#fQueue').onclick = openQueue;
$('#qClose').onclick = closeQueue;
$('#qRest').onclick = clearRestOfQueue;

// Reorder the queue by dragging the handle
(function bindQueueDrag() {
  const list = $('#qList');
  let d = null;
  list.addEventListener('pointerdown', (e) => {
    const h = e.target.closest('[data-drag]');
    if (!h) return;
    const row = h.closest('.track');
    const rows = [...list.querySelectorAll('.track')];
    const from = rows.indexOf(row);
    if (from < 0) return;
    d = { row, rows, from, to: from, y0: e.clientY, h: row.offsetHeight, s0: list.scrollTop };
    row.classList.add('dragging');
    list.classList.add('sorting');
    try { h.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
    e.preventDefault();
  });
  list.addEventListener('pointermove', (e) => {
    if (!d) return;
    const dy = e.clientY - d.y0 + (list.scrollTop - d.s0);
    d.row.style.transform = `translateY(${dy}px)`;
    d.to = Math.min(d.rows.length - 1, Math.max(0, d.from + Math.round(dy / d.h)));
    d.rows.forEach((r, i) => {
      if (r === d.row) return;
      let shift = 0;
      if (d.from < d.to && i > d.from && i <= d.to) shift = -d.h;
      if (d.from > d.to && i < d.from && i >= d.to) shift = d.h;
      r.style.transform = shift ? `translateY(${shift}px)` : '';
    });
    const r = list.getBoundingClientRect();
    if (e.clientY < r.top + 50) list.scrollTop -= 8; else if (e.clientY > r.bottom - 50) list.scrollTop += 8;
  });
  const end = () => {
    if (!d) return;
    const { from, to } = d;
    d = null;
    list.classList.remove('sorting');
    list.querySelectorAll('.track').forEach((r) => { r.style.transform = ''; r.classList.remove('dragging'); });
    if (from !== to) moveInQueue(from, to);
  };
  list.addEventListener('pointerup', end);
  list.addEventListener('pointercancel', end);
})();
$('#fAdd').innerHTML = IC.plus + `<span>${esc(tr('Playlist'))}</span>`;
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (!$('#sheet').hidden) closeSheet();
  else if ($('#qPanel').classList.contains('open')) closeQueue();
  else closeFull();
});

// Remember the position of long tracks (DJ sets) and resume next time
function savePos() {
  const t = currentTrack();
  const d = audio.duration;
  if (!t || !d || d < 600 || !S.user) return;
  S.lastPosSave = Date.now();
  fetch('/api/me/position', { method: 'PUT', credentials: 'same-origin', keepalive: true, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': S.csrf }, body: JSON.stringify({ id: t.id, pos: audio.currentTime, dur: d }) }).catch(() => {});
}

// ---------- Lyrics ----------
const LY = { id: '', data: null, open: false, idx: -1 };

function resetLyrics() {
  LY.id = ''; LY.data = null; LY.idx = -1;
  $('#fLyr').innerHTML = '';
  if (LY.open) loadLyrics();
}

function setLyricsOpen(on) {
  LY.open = on;
  $('#full').classList.toggle('lyr', on);
  $('#fLyr').hidden = !on;
  $('#fLyrBtn').classList.toggle('on', on);
  if (on) loadLyrics();
}

async function loadLyrics() {
  const t = currentTrack();
  const box = $('#fLyr');
  if (!t) return;
  if (LY.id === t.id && LY.data) return;
  LY.id = t.id;
  box.className = 'f-lyr';
  box.innerHTML = `<div class="msg">${esc(tr('Loading …'))}</div>`;
  let r;
  try {
    r = await api(`/lyrics/${encodeURIComponent(t.id)}?title=${encodeURIComponent(t.title)}&artist=${encodeURIComponent(t.artist || '')}&duration=${encodeURIComponent(t.duration || '')}`, {}, 30000);
  } catch (e) { r = { found: false }; }
  if (currentId() !== t.id) return;
  LY.data = r;
  if (!r.found) { box.innerHTML = `<div class="msg">${esc(tr('There are no lyrics for this track.'))}</div>`; return; }
  if (r.synced && r.synced.length) {
    box.className = 'f-lyr sync';
    box.innerHTML = r.synced.map((l, i) => `<p data-i="${i}">${esc(l[1]) || '♪'}</p>`).join('');
    LY.idx = -1;
    syncLyrics(true);
  } else {
    box.className = 'f-lyr plain';
    box.innerHTML = r.plain.split(/\n{2,}/).map((s) => `<p>${esc(s).replace(/\n/g, '<br>')}</p>`).join('');
  }
}

function syncLyrics(force) {
  if (!LY.open || !LY.data || !LY.data.synced || !LY.data.synced.length) return;
  const c = audio.currentTime + 0.25;
  let idx = -1;
  const L = LY.data.synced;
  for (let i = 0; i < L.length; i++) { if (L[i][0] <= c) idx = i; else break; }
  if (idx === LY.idx && !force) return;
  const box = $('#fLyr');
  const old = box.querySelector('p.on');
  if (old) old.classList.remove('on');
  LY.idx = idx;
  const cur = idx >= 0 ? box.querySelector(`p[data-i="${idx}"]`) : null;
  if (cur) {
    cur.classList.add('on');
    box.scrollTo({ top: cur.offsetTop - box.clientHeight * 0.3, behavior: force ? 'auto' : 'smooth' });
  }
}

$('#fLyrBtn').addEventListener('click', () => setLyricsOpen(!LY.open));
$('#fLyr').addEventListener('click', (e) => {
  const p = e.target.closest('p[data-i]');
  if (p && LY.data && LY.data.synced) { audio.currentTime = LY.data.synced[+p.dataset.i][0]; if (audio.paused) audio.play().catch(() => {}); }
});

audio.addEventListener('loadedmetadata', async () => {
  const t = currentTrack();
  if (!t || !audio.duration || audio.duration < 600) return;
  try {
    const r = await api('/me/position/' + encodeURIComponent(t.id));
    if (currentId() !== t.id || audio.currentTime > 5 || !r.pos) return;
    if (r.pos >= 30 && r.pos < audio.duration - 30) {
      audio.currentTime = r.pos;
      toast(tr('Resuming at {time}', { time: fmt(r.pos) }));
    }
  } catch (_) { /* ignore */ }
});
audio.addEventListener('pause', savePos);

// ---------- Player survives a reload ----------
// Per user in the browser (not on the server): Mac and iPhone each have their own queue.
const PSTATE_KEY = (uid) => 'app_player_' + uid;
let pSig = '';

function savePlayerState(force = false) {
  if (!S.user) return;
  try {
    if (S.queue.length === 0 || S.idx < 0) { localStorage.removeItem(PSTATE_KEY(S.user.id)); pSig = ''; return; }
    const pos = S.restored ? S.restored.pos : (audio.currentTime || 0);
    const sig = S.queue.length + '|' + S.idx + '|' + S.queue[0].id + '|' + S.queue[S.queue.length - 1].id + '|' + S.repeat + S.shuffle;
    if (!force && sig === pSig && !S.wantPlay) return;
    pSig = sig;
    const q = S.queue.slice(Math.max(0, S.idx - 20), S.idx + 200);  // only the useful excerpt around the current track
    const keep = ['id', 'title', 'artist', 'artist_id', 'album', 'album_id', 'duration', 'thumb'];
    localStorage.setItem(PSTATE_KEY(S.user.id), JSON.stringify({
      v: 1, idx: S.idx - Math.max(0, S.idx - 20), repeat: S.repeat, shuffle: S.shuffle, pos,
      queue: q.map((t) => Object.fromEntries(keep.map((k) => [k, t[k] ?? '']))),
    }));
  } catch (_) { /* storage full or blocked: then without */ }
}

function restorePlayerState() {
  if (!S.user || S.queue.length) return;
  try {
    const raw = localStorage.getItem(PSTATE_KEY(S.user.id));
    if (!raw) return;
    const d = JSON.parse(raw);
    const ok = d && d.v === 1 && Array.isArray(d.queue) && d.queue.length && Number.isInteger(d.idx) && d.idx >= 0 && d.idx < d.queue.length;
    const clean = ok && d.queue.every((t) => t && typeof t.id === 'string' && /^[\w-]{6,20}$/.test(t.id) && typeof t.title === 'string');
    if (!clean) { localStorage.removeItem(PSTATE_KEY(S.user.id)); return; }
    S.queue = d.queue;
    S.idx = d.idx;
    S.repeat = ['off', 'all', 'one'].includes(d.repeat) ? d.repeat : 'off';
    S.shuffle = !!d.shuffle;
    S.restored = { pos: Number(d.pos) || 0 };
    S.wantPlay = false;
    updateNow();
    setMediaSession(S.queue[S.idx]);
    syncModes();
    updateQueueTitle();
    const t = S.queue[S.idx];
    $('#mTime').textContent = '';
    if (t.duration) $('#fDur').textContent = t.duration;
  } catch (_) { try { localStorage.removeItem(PSTATE_KEY(S.user.id)); } catch (__) { /* ignore */ } }
}

setInterval(() => savePlayerState(), 3000);
window.addEventListener('pagehide', () => savePlayerState(true));
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') savePlayerState(true); });
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') savePos(); });
window.addEventListener('pagehide', savePos);

audio.addEventListener('ended', () => {
  evlog(`ended at ${audio.currentTime.toFixed(0)} s${document.visibilityState === 'hidden' ? ' (background)' : ''}`);
  savePos();
  if (S.repeat === 'one') { audio.currentTime = 0; audio.play().catch(() => {}); } else next();
  S.endedKick = true;
  S.advanceCheck = setTimeout(kickPlayback, 2500);
});

// iOS likes to pause between two tracks in the background: if the user wanted music and it stands still, kick it again
function kickPlayback() {
  clearTimeout(S.advanceCheck);
  if (!S.user || !currentTrack() || S.restored || !(S.wantPlay || S.endedKick)) return;
  if (audio.paused || audio.ended) {
    if (audio.ended && S.idx + 1 < S.queue.length) { playIndex(S.idx + 1); return; }
    if (audio.getAttribute('src')) audio.play().catch(() => {});
  }
}
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') setTimeout(kickPlayback, 150); });
window.addEventListener('pageshow', () => setTimeout(kickPlayback, 150));
audio.addEventListener('play', () => setPlaying(true, S.loading));
audio.addEventListener('pause', () => { if (S.resetting) return; setPlaying(false, false); });
['pause', 'waiting', 'stalled', 'playing', 'emptied'].forEach((ev) => audio.addEventListener(ev, () => evlog(`${ev} at ${audio.currentTime.toFixed(0)} s${document.visibilityState === 'hidden' ? ' (background)' : ''}`, 3000)));
document.addEventListener('visibilitychange', () => {
  evlog(document.visibilityState === 'visible' ? 'App visible' : 'App in background');
  if (document.visibilityState === 'visible') checkAlive();
});
audio.addEventListener('playing', () => {
  if (S.t0 && S.user && !S.stRetry) {  // start until sound, measured on the device (not on re-requests)
    const ms = Math.round(performance.now() - S.t0);
    api('/startstat', { json: { ms: Math.min(ms, 300000), platform: IOS ? 'ios' : 'desktop' } }).catch(() => {});
  }
  S.t0 = 0;
  S.errStreak = 0; S.endedKick = false; setPlaying(true, false); });
audio.addEventListener('canplay', () => { if (S.loading) setPlaying(!audio.paused, false); });
audio.addEventListener('waiting', () => { if (!audio.paused) { const was = S.loading; setPlaying(true, true); if (!was) watchLoading(); } });
audio.addEventListener('error', async () => {
  const src = audio.getAttribute('src');
  if (!src) return;
  const t = currentTrack();
  const code = audio.error ? audio.error.code : 0;
  evlog('Audio error ' + code);
  let reason = '';
  try {
    // The audio tag gives no reason: ask the same URL briefly (errors come back from the backend's cache at once)
    const r = await fetch(src, { headers: { Range: 'bytes=0-0' } });
    if (r.status === 401) { showLogin(); return; }  // session gone: login screen, do not skip on
    if (!r.ok) {
      try { reason = (await r.json()).detail || ''; } catch (_) { /* not JSON */ }
      if (!reason) reason = tr('Server error {code}', { code: r.status });
    }
  } catch (_) { reason = tr('Server unreachable'); }
  if (audio.getAttribute('src') !== src) return;  // a different track in the meantime
  if (!reason) reason = code === 3 || code === 4 ? tr('Format not supported by the browser') : code === 2 ? tr('Network error while loading') : tr('Unknown error');
  const name = t ? `“${t.title}”` : tr('Track');
  S.errStreak++;
  if (S.errStreak >= 3) {
    S.errStreak = 0;
    setPlaying(false, false);
    toast(tr('{name} cannot be loaded: {reason}. Playback stopped.', { name, reason }), 8000, true);
    return;
  }
  setPlaying(S.wantPlay, false);
  toast(tr('{name} cannot be loaded: {reason}. Skipping …', { name, reason }), 5000, true);
  setTimeout(next, 1800);
});

// Report real listening time (seconds, in batches) for the admin dashboard
const LISTEN = { acc: 0, lastC: -1, lastW: 0 };
function flushListen() {
  const s = Math.round(LISTEN.acc * 10) / 10;
  if (s < 1 || !S.user) return;
  LISTEN.acc = 0;
  try {
    fetch('/api/listen', { method: 'POST', keepalive: true, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': S.csrf }, body: JSON.stringify({ seconds: Math.min(s, 300) }) }).catch(() => {});
  } catch (_) { /* ignore */ }
}
audio.addEventListener('timeupdate', () => {
  const c = audio.currentTime, now = performance.now();
  if (!audio.paused && !audio.seeking && LISTEN.lastC >= 0 && c > LISTEN.lastC) {
    const dt = (now - LISTEN.lastW) / 1000;
    if (dt > 0 && dt < 3) LISTEN.acc += dt;
  }
  LISTEN.lastC = c; LISTEN.lastW = now;
  if (LISTEN.acc >= 30) flushListen();
});
audio.addEventListener('pause', () => { LISTEN.lastC = -1; flushListen(); });
audio.addEventListener('ended', () => { LISTEN.lastC = -1; flushListen(); });
audio.addEventListener('emptied', () => { LISTEN.lastC = -1; });
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flushListen(); });
window.addEventListener('pagehide', flushListen);

audio.addEventListener('timeupdate', () => {
  const d = audio.duration;
  const c = audio.currentTime;
  const f = d && isFinite(d) ? Math.min(1, c / d) * 100 : 0;
  if (!S.raf && !S.live) setProgress(f / 100);  // in the background (no rAF) timeupdate is enough
  syncLive();
  $('#mTime').textContent = S.live ? tr('Live') : d ? fmt(c) + ' / ' + fmt(d) : '';
  if (!S.scrub) $('#fCur').textContent = fmt(c);
  syncLyrics();
  // If the audio is actually running, "loading" is over (iOS does not always report 'playing' after 'waiting')
  if (S.loading && !audio.paused && !audio.seeking && (audio.readyState >= 3 || (S.lastCt != null && c > S.lastCt + 0.2))) setPlaying(true, false);
  if (!S.loading || S.lastCt == null || c < S.lastCt) S.lastCt = c;
  if (d) $('#fDur').textContent = fmt(d);
  if (d && !audio.paused && c > 3) hintUpcoming();
  if (IOS && d && !audio.paused && c > Math.min(20, d * 0.2)) {  // preload the next track (once per pair of tracks)
    const cur = currentTrack(), nt = S.queue[S.idx + 1];
    if (cur && nt && S.preTried !== cur.id + '>' + nt.id) { S.preTried = cur.id + '>' + nt.id; preloadNext(); }
  }
  if ('mediaSession' in navigator && d && navigator.mediaSession.setPositionState) {
    try { navigator.mediaSession.setPositionState({ duration: d, position: c, playbackRate: audio.playbackRate }); } catch (_) { /* ignore */ }
  }
  if (d && d >= 600 && !audio.paused && Date.now() - S.lastPosSave > 15000) savePos();
  // Counts as "listened": 30 s or half of the track. This feeds the home page.
  if (!S.reported && S.user && d && c >= Math.min(30, d / 2)) {
    S.reported = true;
    const t = currentTrack();
    if (t) {
      api('/me/played', { json: pick(t) }).then(async () => {  // update the home page to the new listening history right away, without a jump
        if (!onHome()) return;
        const view = $('#view'), top = view.scrollTop;
        await renderHome(S.nav, true);
        view.scrollTop = top;
      }).catch(() => {});
    }
  }
});

// Media keys (Mac keyboard, Logitech, headphones, lock screen): also work in the background
// once the page has played audio. The handlers are set once at startup.
function initMediaKeys() {
  if (!('mediaSession' in navigator)) return;
  const set = (name, fn) => { try { navigator.mediaSession.setActionHandler(name, fn); } catch (_) { /* action not supported */ } };
  set('play', () => { if (S.restored) togglePlay(); else audio.play().catch(() => {}); });
  set('pause', () => audio.pause());
  set('stop', () => audio.pause());
  set('nexttrack', next);
  set('previoustrack', prev);
  // Without seekbackward/seekforward iOS shows previous/next on the lock screen instead of a 10-second jump
  set('seekbackward', null);
  set('seekforward', null);
  set('seekto', (d) => { if (d.seekTime != null) audio.currentTime = d.seekTime; });
}
initMediaKeys();

function setMediaSession(t) {
  if (!('mediaSession' in navigator)) return;
  initMediaKeys();  // iOS likes to drop handlers on a track change: set them again per track so previous/next stays instead of a 10-s jump
  const np = S.live && S.np && S.np.title && currentTrack() === t ? S.np : null;  // live radio: the song that plays now, with the station as album
  navigator.mediaSession.metadata = new MediaMetadata({ title: np ? np.title : t.title, artist: np ? np.artist || t.title : t.artist, album: np ? t.title : t.album || '', artwork: t.thumb ? [{ src: t.thumb, sizes: '512x512' }] : [] });
}

// ---------- DJ mode ----------
// Match the tempo, crossfade on the beat, with mix (bass swap), filter sweep or echo.
// Only runs if both tracks have a detected tempo; otherwise it stays a normal track change.
const DJ = {
  on: false, style: 'auto', ctx: null, nodes: null, unlocked: false,
  info: {}, plan: null, mixing: false, timer: 0, warmSec: 6, rateTimer: 0,
};
// iPhone/iPad: DJ mode off (iOS does not clock two audio elements reliably in the background: stuttering, loops, drifting tempo)
const IOS = /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const AC = IOS ? null : (window.AudioContext || window.webkitAudioContext);
try { DJ.on = !!AC && localStorage.getItem('app_dj') === '1'; DJ.style = localStorage.getItem('app_dj_style') || 'auto'; } catch (_) { /* without storage */ }
const djSave = () => { try { localStorage.setItem('app_dj', DJ.on ? '1' : '0'); localStorage.setItem('app_dj_style', DJ.style); } catch (_) { /* ignore */ } };
const otherEl = () => (curEl === elA ? elB : elA);
const nodeOf = (el) => (el === elA ? DJ.nodes.A : DJ.nodes.B);

function djGraph() {
  if (DJ.ctx) return DJ.ctx;
  const ctx = new AC({ latencyHint: 'playback' });
  const master = ctx.createGain();
  master.connect(ctx.destination);
  const delay = ctx.createDelay(2);
  const fb = ctx.createGain();
  fb.gain.value = 0.55;
  const tone = ctx.createBiquadFilter();
  tone.type = 'lowpass';
  tone.frequency.value = 3200;
  const wet = ctx.createGain();
  wet.gain.value = 0.9;
  delay.connect(tone); tone.connect(fb); fb.connect(delay); tone.connect(wet); wet.connect(master);
  const mk = (el) => {
    const src = ctx.createMediaElementSource(el);
    const hp = ctx.createBiquadFilter(); hp.type = 'highpass'; hp.frequency.value = 10; hp.Q.value = 0.7;
    const lp = ctx.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 20000; lp.Q.value = 0.7;
    const gain = ctx.createGain();
    const send = ctx.createGain(); send.gain.value = 0;
    src.connect(hp); hp.connect(lp); lp.connect(gain); gain.connect(master); lp.connect(send); send.connect(delay);
    return { hp, lp, gain, send };
  };
  DJ.nodes = { A: mk(elA), B: mk(elB), delay };
  DJ.ctx = ctx;
  ctx.addEventListener('statechange', () => { if (DJ.on && ctx.state !== 'running' && !audio.paused) ctx.resume().catch(() => {}); });
  return ctx;
}

function djNeutral(n, gain = 1) {
  const t = DJ.ctx.currentTime;
  [n.hp.frequency, n.lp.frequency, n.gain.gain, n.send.gain].forEach((p) => p.cancelScheduledValues(t));
  n.hp.frequency.setValueAtTime(10, t);
  n.lp.frequency.setValueAtTime(20000, t);
  n.gain.gain.setValueAtTime(gain, t);
  n.send.gain.setValueAtTime(0, t);
}

// Safari only allows play() without a gesture for elements that were started by a tap once
function djUnlock() {
  if (DJ.unlocked) return;
  DJ.unlocked = true;
  elB.src = '/silence.wav';
  elB.play().then(() => { elB.pause(); elB.removeAttribute('src'); elB.load(); }).catch(() => { DJ.unlocked = false; });
}

async function djEnable(on) {
  if (on) {
    if (!AC) { toast(tr('DJ mode does not work in this browser'), 3000, true); return; }
    try {
      djGraph();
      await DJ.ctx.resume();
      djUnlock();
    } catch (e) { toast(tr('DJ mode does not work on this device'), 3000, true); return; }
    DJ.on = true;
    toast(tr('DJ mode on: transitions on the beat'));
    if (!DJ.timer) djLoop();
  } else {
    DJ.on = false;
    djAbort();
    toast(tr('DJ mode off'));
  }
  djSave();
  syncDj();
}

function syncDj() {
  const b = $('#fDjBtn');
  b.hidden = !AC;
  b.classList.toggle('on', DJ.on);
  const names = { auto: tr('Random'), blend: tr('Mix'), filter: tr('Filter'), echo: tr('Echo') };
  $('#fDjLbl').innerHTML = DJ.on ? `DJ <small>${names[DJ.style] || ''}</small>` : 'DJ';
}

// On the first tap after start, build and unlock the audio context (iOS needs a gesture)
document.addEventListener('click', () => {
  if (!DJ.on || !AC) return;
  try {
    djGraph();
    if (DJ.ctx.state !== 'running') DJ.ctx.resume().catch(() => {});
    djUnlock();
    if (!DJ.timer) djLoop();
  } catch (_) { /* ignore */ }
}, true);

async function djFetch(t) {
  const c = DJ.info[t.id];
  if (c && (c.ready || Date.now() < c.next)) return;
  DJ.info[t.id] = { ...(c || {}), next: Date.now() + 4000 };
  try {
    const r = await api(`/bpm/${encodeURIComponent(t.id)}?duration=${encodeURIComponent(t.duration || '')}`, {}, 60000);
    DJ.info[t.id] = { ...r, next: Date.now() + 4000 };
  } catch (_) { /* try again later */ }
}

// Do the keys fit together? Camelot wheel: same number or neighbour = harmonic. 1 fits, 0 unclear, -1 sounds off
function djKeyFit(a, b) {
  const m = (s) => /^(\d+)([AB])$/.exec(s || '');
  const p = m(a.key), q = m(b.key);
  if (!p || !q || (a.key_conf || 0) < 0.35 || (b.key_conf || 0) < 0.35) return 0;
  const n = Math.abs(p[1] - q[1]);
  const d = Math.min(n, 12 - n) + (p[2] === q[2] ? 0 : (n === 0 ? 0 : 1));
  return d <= 1 ? 1 : d <= 2 ? 0 : -1;
}

function djPickStyle(lock, ctx = {}) {
  if (!lock) return DJ.last = 'echo';
  if (DJ.style !== 'auto') return DJ.style;
  // Random without direct repetition. Clashing keys or an immediately loud entry: no bass swap, rather echo/filter
  const pool = [['blend', ctx.fit < 0 || ctx.hot ? 0 : 5], ['filter', 3], ['echo', ctx.hot || ctx.fit < 0 ? 5 : 2]].filter(([k, w]) => w > 0 && k !== DJ.last);
  let r = Math.random() * pool.reduce((s, [, w]) => s + w, 0);
  for (const [k, w] of pool) { if ((r -= w) < 0) return (DJ.last = k); }
  return (DJ.last = pool[0][0]);
}

function djPlan(nt, a, b, dur) {
  if (!a.usable || !b.usable) return { skip: true };
  let bb = b.bpm;  // bring B's tempo to the same octave as A (e.g. 70 instead of 140)
  [b.bpm * 2, b.bpm / 2].forEach((c) => { if (Math.abs(Math.log(c / a.bpm)) < Math.abs(Math.log(bb / a.bpm))) bb = c; });
  let rate = a.bpm / bb;
  const lock = Math.abs(rate - 1) <= 0.08;
  if (!lock) rate = 1;
  const beatA = 60 / a.bpm, beatB = 60 / bb;
  // Grid: align to the downbeat (the one) if both tracks have it confidently, otherwise to the beat
  const bars = !!(a.bar_ok && b.bar_ok);
  const g = bars ? 4 : 1;
  const fA = bars ? a.first_bar : a.first_beat, fB = bars ? b.first_bar : b.first_beat;
  // Entry point in B (after warming up), on B's grid
  const minX = DJ.warmSec * rate + 0.5;
  const X = fB + Math.max(0, Math.ceil((minX - fB) / (g * beatB))) * g * beatB;
  // Structure: does A have an outro? Is B already loud when the transition starts (then probably vocals/drums)?
  const endA = Math.min(dur, a.audio_end || dur) - 0.8;
  const hasOutro = a.outro_start != null && endA - a.outro_start >= 8 * beatA;
  const introBeats = b.intro_end != null ? (b.intro_end - X) / beatB : null;
  const hot = introBeats !== null && introBeats < 4;
  const fit = djKeyFit(a, b);
  const style = djPickStyle(lock, { fit, hot });
  // Length: as long as A's outro and B's intro allow (8 to 32 beats), short for echo, short for clashing key/hot entry
  const q4 = (v) => Math.max(8, Math.min(32, Math.floor(v / 4) * 4));
  let mb = style === 'echo' ? 8 : 16;
  if (style !== 'echo') {
    if (hasOutro) mb = q4((endA - a.outro_start) / beatA);
    if (introBeats !== null && introBeats >= 8) mb = Math.min(mb, q4(introBeats));
    if (fit < 0 || hot) mb = Math.min(mb, 8);
  }
  const mixSec = mb * beatA;
  const gA = g * beatA;
  const latest = endA - mixSec;
  const onGrid = (t, up) => fA + (up ? Math.ceil((t - fA) / gA - 1e-6) : Math.floor((t - fA) / gA)) * gA;
  let tA = hasOutro ? onGrid(a.outro_start, true) : onGrid(latest, false);
  if (tA > latest) tA = onGrid(latest, false);
  if (tA - audio.currentTime < DJ.warmSec + 1) tA = onGrid(audio.currentTime + DJ.warmSec + 1, true);  // outro is already running: next bar
  if (tA > latest + 0.01) return { skip: true };
  return { id: nt.id, style, lock, rate, tA, mixSec, beatA, fbA: fA, fbB: fB, bpmB: bb, X, warm: false, started: false, switched: false, info: { fit, hot, hasOutro, bars, mb } };
}

function djPhaseErr(p, ea, eb) {
  const pa = (ea.currentTime - p.fbA) / p.beatA;
  const pb = (eb.currentTime - p.fbB) / (60 / p.bpmB);
  let e = (pb - pa) % 1;
  if (e > 0.5) e -= 1;
  if (e < -0.5) e += 1;
  return e;
}

function djPll(p, ea, eb) {
  if (!p.lock || eb.paused || eb.seeking) return;
  const e = djPhaseErr(p, ea, eb);
  eb.playbackRate = p.rate * (1 - Math.max(-0.05, Math.min(0.05, e * 0.6)));
}

function djWarm(p) {
  p.warm = true;
  const el = otherEl();
  const n = nodeOf(el);
  djNeutral(n, 0);
  el.preservesPitch = true; el.webkitPreservesPitch = true;
  el.playbackRate = p.rate;
  el.src = `/api/stream/${encodeURIComponent(p.id)}`;
  const start = Math.max(0, p.X - DJ.warmSec * p.rate);
  const go = () => { el.currentTime = start; el.play().catch(() => djAbort()); };
  if (el.readyState >= 1) go(); else el.addEventListener('loadedmetadata', go, { once: true });
}

function djStart(p) {
  p.started = true;
  DJ.mixing = true;
  const A = nodeOf(curEl), B = nodeOf(otherEl());
  const t0 = DJ.ctx.currentTime + 0.02, M = p.mixSec, bt = p.beatA;
  const curve = (up) => Float32Array.from({ length: 64 }, (_, i) => (up ? Math.sin : Math.cos)((i / 63) * Math.PI / 2));
  const fade = (g, up, at, len) => { g.cancelScheduledValues(at); g.setValueCurveAtTime(curve(up), at, len); };
  const cl = (n) => { [n.hp.frequency, n.lp.frequency, n.gain.gain, n.send.gain].forEach((x) => x.cancelScheduledValues(t0)); };
  cl(A); cl(B);
  if (p.style === 'blend') {  // swap bass: B comes in without bass, in the middle the bass switches from A to B
    fade(A.gain.gain, false, t0, M); fade(B.gain.gain, true, t0, M);
    B.hp.frequency.setValueAtTime(450, t0); B.hp.frequency.setValueAtTime(450, t0 + M * 0.5); B.hp.frequency.linearRampToValueAtTime(10, t0 + M * 0.5 + 0.4);
    A.hp.frequency.setValueAtTime(10, t0 + M * 0.5); A.hp.frequency.linearRampToValueAtTime(450, t0 + M * 0.5 + 0.4);
  } else if (p.style === 'filter') {  // A gets dull and quiet, B opens up
    A.lp.frequency.setValueAtTime(20000, t0); A.lp.frequency.exponentialRampToValueAtTime(240, t0 + M * 0.9);
    A.gain.gain.setValueAtTime(1, t0); A.gain.gain.setValueAtTime(1, t0 + M * 0.5); A.gain.gain.linearRampToValueAtTime(0, t0 + M);
    B.hp.frequency.setValueAtTime(900, t0); B.hp.frequency.exponentialRampToValueAtTime(10, t0 + M * 0.7);
    fade(B.gain.gain, true, t0, M * 0.4);
  } else {  // Echo: A fades out in the echo, B comes in on the beat
    DJ.nodes.delay.delayTime.setValueAtTime(bt * 0.75, t0);
    A.gain.gain.setValueAtTime(1, t0); A.gain.gain.linearRampToValueAtTime(0, t0 + bt * 0.5);
    A.send.gain.setValueAtTime(0.9, t0); A.send.gain.linearRampToValueAtTime(0, t0 + bt);
    B.hp.frequency.setValueAtTime(900, t0); B.hp.frequency.exponentialRampToValueAtTime(10, t0 + bt * 2);
    fade(B.gain.gain, true, t0, bt * 1.5);
  }
  p.t0 = performance.now();
}

function djSwitch(p) {
  p.switched = true;
  const old = curEl;
  curEl = otherEl();
  playIndex(S.idx + 1, { mix: true });
  setPlaying(true, false);
  void old;
}

function djFinish() {
  const p = DJ.plan;
  const old = otherEl();  // the faded-out element
  old.pause(); old.removeAttribute('src'); old.load();
  djNeutral(nodeOf(old), 1);
  djNeutral(nodeOf(curEl), 1);
  DJ.mixing = false;
  DJ.plan = null;
  if (p && Math.abs(curEl.playbackRate - 1) > 0.001) djRateBack(curEl);
}

function djRateBack(el) {  // gently return the tempo to 1 after the transition
  clearInterval(DJ.rateTimer);
  DJ.rateTimer = setInterval(() => {
    if (el !== curEl) { clearInterval(DJ.rateTimer); return; }
    const d = 1 - el.playbackRate;
    if (Math.abs(d) < 0.002) { el.playbackRate = 1; clearInterval(DJ.rateTimer); return; }
    el.playbackRate += d * 0.12;
  }, 250);
}

function djAbort() {
  if (!DJ.ctx || (!DJ.plan && !DJ.mixing)) { DJ.plan = null; return; }
  DJ.plan = null;
  DJ.mixing = false;
  const old = otherEl();
  try {
    old.pause(); old.removeAttribute('src'); old.load();
    djNeutral(nodeOf(old), 1);
    djNeutral(nodeOf(curEl), 1);
    curEl.playbackRate = 1;
  } catch (_) { /* ignore */ }
}

function djTick() {
  if (!DJ.on || !DJ.ctx || audio.paused) return;
  if (DJ.ctx.state !== 'running') { DJ.ctx.resume().catch(() => {}); return; }
  const p = DJ.plan;
  if (p && p.warm && !p.skip) {
    if (!p.switched && (S.queue[S.idx + 1] || {}).id !== p.id) { djAbort(); return; }  // the queue was changed
    const ea = p.switched ? otherEl() : curEl, eb = p.switched ? curEl : otherEl();
    if (!p.started) {
      if (p.tA - ea.currentTime <= 0.03) djStart(p);
      else if (!eb.paused) djPll(p, ea, eb);
      return;
    }
    djPll(p, ea, eb);
    const prog = (performance.now() - p.t0) / 1000 / p.mixSec;
    if (prog >= 0.5 && !p.switched) djSwitch(p);
    if (prog >= 1) djFinish();
    return;
  }
  const t = currentTrack(), nt = S.queue[S.idx + 1];
  const dur = audio.duration;
  if (!t || !nt || S.repeat === 'one' || !isFinite(dur) || dur < 60 || dur >= 600 || S.loading) return;
  if (audio.currentTime < 3) djFetch(t);
  if (dur - audio.currentTime > 140) return;
  djFetch(t); djFetch(nt);
  if (!p) {
    const a = DJ.info[t.id], b = DJ.info[nt.id];
    if (a && b && a.ready && b.ready) DJ.plan = djPlan(nt, a, b, dur);
    return;
  }
  if (p.skip) return;
  if (p.tA - audio.currentTime <= DJ.warmSec + 0.1) djWarm(p);
}

function djLoop() {
  clearTimeout(DJ.timer);
  const step = () => {
    try { djTick(); } catch (e) { djAbort(); }
    DJ.timer = setTimeout(step, DJ.plan && DJ.plan.warm ? 40 : 300);
  };
  step();
}

// A pause or seek by the user aborts a running transition
[elA, elB].forEach((el) => {
  el.addEventListener('pause', () => { if (el === curEl && (DJ.mixing || (DJ.plan && DJ.plan.warm))) djAbort(); });
  el.addEventListener('seeking', () => { if (el === curEl && DJ.plan && DJ.plan.warm && !DJ.plan.started) djAbort(); });
});
elB.addEventListener('error', () => { if (elB !== curEl && DJ.plan && DJ.plan.warm) djAbort(); });
elA.addEventListener('error', () => { if (elA !== curEl && DJ.plan && DJ.plan.warm) djAbort(); });

$('#fDjBtn').addEventListener('click', () => {
  if (!DJ.on) { djEnable(true); return; }
  const styles = [['auto', tr('Random')], ['blend', tr('Mix (swap bass)')], ['filter', tr('Filter sweep')], ['echo', tr('Echo')]];
  sheetMenu(tr('DJ mode'), [
    ...styles.map(([k, l]) => ({ label: (DJ.style === k ? '✓ ' : '') + tr('Style: {name}', { name: l }), fn: () => { DJ.style = k; djSave(); syncDj(); } })),
    { label: tr('Turn off DJ mode'), fn: () => djEnable(false), danger: true },
  ]);
});
syncDj();
if (DJ.on) djLoop();

let loginMode = 'login';

async function loadAuthState() {
  try {
    const st = await (await fetch('/api/auth-state', { credentials: 'same-origin' })).json();
    loginMode = st.setup ? 'setup' : 'login';
    $('#loginBtn').textContent = st.setup ? tr('Create admin account') : tr('Log in');
    $('#loginPw').autocomplete = st.setup ? 'new-password' : 'current-password';
    $('#loginHint').textContent = st.setup
      ? tr('Welcome! Create the admin account. You can add further users later.')
      : st.claim ? tr('Update: “{name}” (admin) sets the password with the first login (at least 8 characters).', { name: st.claim })
        : tr('Log in. Accounts are created by the admin.');
  } catch (_) { /* login works anyway */ }
}

function showUnreachable() {
  $('#login').hidden = true;
  $('#app').hidden = true;
  $('#unreach').hidden = false;
}

function showLogin() {
  document.body.classList.remove('has-track');
  S.restored = null;
  S.user = null;
  S.csrf = '';
  S.queue = [];
  S.idx = -1;
  S.likes = new Set();
  audio.pause();
  audio.removeAttribute('src');
  closeFull();
  closeSheet();
  $('#mini').hidden = true;
  $('#app').hidden = true;
  $('#sideUsers').hidden = true;
  $('#login').hidden = false;
  $('#loginPw').value = '';
  loadAuthState();
}

// "What's new": once after an update, for existing users only (new accounts see the introduction)
async function showNews() {
  try {
    const n = await api('/news');
    if (!n.items.length || !S.user || document.body.classList.contains('tour-open')) return;
    const PER = 4, pages = Math.ceil(n.items.length / PER);
    let pg = 0;
    const render = () => {
      const part = n.items.slice(pg * PER, pg * PER + PER);
      $('#newsList').innerHTML = part.map((i) => `<div class="news-item"><b>${esc(i.title)}</b><p>${esc(i.text)}</p></div>`).join('');
      if (pages > 1) {
        $('#newsDots').innerHTML = Array.from({ length: pages }, (_, k) => `<i class="${k === pg ? 'on' : ''}"></i>`).join('');
        $('#newsPrev').disabled = pg === 0;
        $('#newsNext').disabled = pg === pages - 1;
      }
    };
    openSheet(`<div class="sh-title">${esc(tr("What's new"))}</div>
      <div class="news" id="newsList"${pages > 1 ? ' style="min-height:330px"' : ''}></div>
      ${pages > 1 ? `<div class="news-pager"><button class="chip" id="newsPrev" type="button">${esc(tr('Back'))}</button><span id="newsDots" class="dots"></span><button class="chip" id="newsNext" type="button">${esc(tr('Next'))}</button></div>` : ''}
      <div class="actions"><button class="btn" data-close>${esc(tr('Got it'))}</button></div>`);
    render();
    if (pages > 1) {
      $('#newsPrev').onclick = () => { if (pg > 0) { pg--; render(); } };
      $('#newsNext').onclick = () => { if (pg < pages - 1) { pg++; render(); } };
    }
    api('/news/seen', { method: 'POST' }).catch(() => {});  // seen from now on, even if the window is closed by tapping outside
  } catch (_) { /* news are an extra */ }
}

let _srcAt = 0;
// Plugins can be installed, started or removed while the page is open: look at the sources and search tabs again when the search opens.
async function refreshSources(maxAgeMs = 3000) { if (Date.now() - _srcAt > maxAgeMs) await loadSources(); }
async function loadSources() {
  _srcAt = Date.now();
  try {
    const { sources } = await api('/sources');
    S.sources = Object.fromEntries(sources.map((s) => [s.id, s.name]));
    S.multiSource = sources.length > 1;  // only if there is more than the own library is the origin worth showing
  } catch (_) { S.sources = {}; S.multiSource = false; }
  try { S.categories = (await api('/search-categories')).categories; } catch (_) { S.categories = []; }  // extra search tabs from plugins
}
const srcName = (t) => (S.multiSource ? S.sources[String((t && t.id) || '').split(':')[0]] || '' : '');
const srcTag = (t) => { const n = srcName(t); return n ? ` · <span class="src">${esc(n)}</span>` : ''; };

// ---------- The bell: messages for the user; admins also get system messages (disk space, updates) ----------

function renderBell() {
  const n = S.notes ? S.notes.unread : 0;
  const c = $('#bellCount');
  c.hidden = !n;
  c.textContent = n > 9 ? '9+' : String(n);
  $('#bellBtn').classList.toggle('has', n > 0);
}

async function loadBell() {
  if (!S.user) return;
  try { S.notes = await api('/notifications', { noRedirect: true }); renderBell(); } catch (_) { /* the bell is optional */ }
}

function openBell() {
  const items = (S.notes && S.notes.items) || [];
  const row = (n) => `<div class="note${n.unread ? ' unread' : ''}" data-nid="${n.id}"${n.nav ? ` data-nav-to="${esc(n.nav)}"` : ''}>
    <div class="nbody"><div class="ntitle">${n.system ? `<span class="nsys">${esc(tr('System'))}</span>` : ''}${esc(n.title)}</div>${n.body ? `<div class="dim small">${esc(n.body)}</div>` : ''}<div class="dim small">${esc(ago(n.created_at))}</div></div>
    <button class="nx" type="button" data-dismiss="${n.id}" aria-label="${esc(tr('Dismiss'))}">${IC.close}</button></div>`;
  openSheet(`<div class="sh-title">${esc(tr('Notifications'))}</div><div class="notelist">${items.length ? items.map(row).join('') : `<div class="empty" style="margin:24px 0 32px">${esc(tr('Nothing new.'))}</div>`}</div>`);
  const box = $('#sheet .panel');
  box.querySelectorAll('[data-dismiss]').forEach((b) => {
    b.onclick = async (e) => {
      e.stopPropagation();
      try { await api('/notifications/' + b.dataset.dismiss, { method: 'DELETE' }); b.closest('.note').remove(); await loadBell(); if (!box.querySelector('.note')) closeSheet(); } catch (er) { toast(er.message, 3000, true); }
    };
  });
  box.querySelectorAll('[data-nav-to]').forEach((r) => { r.onclick = () => { closeSheet(); go(r.dataset.navTo); }; });
  if (S.notes && S.notes.unread) api('/notifications/seen', { method: 'POST' }).then(() => { S.notes.unread = 0; renderBell(); }).catch(() => {});
}

$('#bellBtn').addEventListener('click', openBell);
setInterval(loadBell, 60000);
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') loadBell(); });

function boot(user) {
  S.user = user;
  loadSources();
  loadBell();
  loadCommunity();
  $('#login').hidden = true;
  $('#app').hidden = false;
  $('#userBtn').textContent = (user.name[0] || '?').toUpperCase();
  $('#sideUsers').hidden = !user.is_admin;
  restorePlayerState();
  try {  // after a self-healing reload, restart playback right away (iOS may require a tap on play)
    const rs = JSON.parse(localStorage.getItem('app_resume') || 'null');
    localStorage.removeItem('app_resume');
    if (rs && Date.now() - rs.ts < 60000 && S.restored && currentTrack() && currentTrack().id === rs.id) {
      evlog('Reloaded, resuming playback');
      togglePlay();
      S.t0 = 0;
    }
  } catch (_) { /* ignore */ }
  api('/likes').then((r) => { S.likes = new Set(r.tracks.map((t) => t.id)); syncLike(); }).catch(() => {});
  route();
  if (user.must_change_password) { go('#/account'); toast(tr('Please choose your own password now'), 4500); }
  // Introduction only at the first login of an account (skipping counts as seen, too)
  if (!user.onboarded) showTutorial(() => { user.onboarded = true; api('/me/onboarded', { method: 'POST' }).catch(() => {}); });
  setTimeout(showNews, 900);
}

$('#loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('#loginErr').textContent = '';
  $('#loginBtn').disabled = true;
  try {
    const r = await api(loginMode === 'setup' ? '/setup' : '/login', { json: { name: $('#loginName').value, password: $('#loginPw').value }, noRedirect: true });
    $('#loginPw').value = '';
    await syncLang(r.user);
    boot(r.user);
  } catch (err) { $('#loginErr').textContent = err.message; } finally { $('#loginBtn').disabled = false; }
});

$('#userBtn').addEventListener('click', () => {
  sheetMenu(S.user ? S.user.name : '', [
    { label: tr('Account'), fn: () => go('#/account') },
    ...(S.user && S.user.is_admin ? [{ label: tr('Admin area'), fn: () => go('#/admin') }] : []),
    { label: tr('No longer recommended …'), fn: showDislikes },
    {
      label: tr('Log out'),
      fn: async () => {
        try { await api('/logout', { method: 'POST' }); } catch (_) { /* ignore */ }
        try { localStorage.removeItem(PSTATE_KEY(S.user.id)); } catch (_) { /* ignore */ }
        showLogin();
      },
    },
  ]);
});

function goHome() { if (!S.user) return; closeSheet(); go('#/home'); window.scrollTo(0, 0); }
document.querySelectorAll('.brand,.side-brand').forEach((el) => {
  el.addEventListener('click', goHome);
  el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goHome(); } });
});
$('#mExpand').addEventListener('click', openFull);

initPull();
setPlaying(false);
syncModes();
// Language first (saved choice, else browser), then the session: the server's saved choice for the user wins.
async function syncLang(user) {
  let pref = '';
  try { pref = localStorage.getItem('app_lang') || ''; } catch (_) { /* no storage */ }
  if (user && (user.lang || '') !== pref) await setLanguage(user.lang || '');
}
(async () => {
  let pref = '';
  try { pref = localStorage.getItem('app_lang') || ''; } catch (_) { /* no storage */ }
  await setLanguage(pref);
  try {
    const r = await api('/me');
    await syncLang(r.user);
    boot(r.user);
  } catch (e) { if (e && e.status === 401) showLogin(); else showUnreachable(); }  // not logged in, or no connection to the server
})();
