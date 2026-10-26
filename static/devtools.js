'use strict';
// Developer tools. Loaded by app.js only on a server started with DEVTOOLS=1, and only for admins (the server decides what it hands out).
// English only on purpose: the audience is people working on the code. Nothing here changes how the player or the server behave.

(() => {
  const MAX_LOG = 60;
  const SLOW_MS = 500;
  const PART_MAX = { artist: 3, album: 2, genre: 2, year: 1.5, dj: 2, random: 2 };  // the largest value of each part of a radio score (library.py)
  const PART_NOTE = {
    artist: 'same artist', album: 'same album', genre: 'same genre', year: 'release year close by',
    dj: 'tempo and key fit (only if both songs are analyzed)', random: 'dice roll, new on every request',
  };
  const log = [];
  let open = false;
  let tab = 'queue';

  const css = document.createElement('style');
  css.textContent = `
    #devBtn2{font:600 11px/1 ui-monospace,Menlo,monospace;color:var(--dim);letter-spacing:.04em}
    #devPanel{position:fixed;z-index:60;right:0;top:0;bottom:0;width:min(420px,100vw);background:var(--card,#181818);border-left:1px solid #333;
      display:flex;flex-direction:column;font-size:13px;box-shadow:-8px 0 24px #0008}
    #devPanel[hidden]{display:none}
    #devPanel header{display:flex;gap:6px;align-items:center;padding:10px 12px;border-bottom:1px solid #333}
    #devPanel header b{flex:1;font:600 12px ui-monospace,Menlo,monospace;letter-spacing:.04em}
    #devPanel .dtab{padding:4px 10px;border-radius:12px;background:var(--card2,#262626);color:var(--dim)}
    #devPanel .dtab.on{background:#fff;color:#000}
    #devPanel .dbody{flex:1;overflow:auto;overflow-x:hidden;padding:8px 12px 24px;font-family:ui-monospace,Menlo,monospace;font-size:12px}
    #devPanel table{width:100%;border-collapse:collapse}
    #devPanel td,#devPanel th{padding:3px 6px 3px 0;text-align:left;vertical-align:top;white-space:nowrap}
    #devPanel table{table-layout:auto}
    #devPanel td.n,#devPanel th.n{text-align:right}
    #devPanel .slow{color:#ffb454}.bad{color:#ff6b6b}.dim{color:var(--dim)}
    .devchip{font:600 11px ui-monospace,Menlo,monospace;color:var(--dim);background:var(--card2,#262626);padding:3px 7px;border-radius:9px;margin-right:4px;flex:none}
    .devwhy .bar{display:inline-block;height:8px;border-radius:4px;background:#6d4aff;vertical-align:middle}
    .devwhy table{width:100%;border-collapse:collapse;font-size:13px}
    .devwhy td{padding:5px 8px 5px 0;vertical-align:middle}
    .devwhy td.n{text-align:right;font-variant-numeric:tabular-nums}
    .devwhy p{margin:8px 0;color:var(--dim)}`;
  document.head.appendChild(css);

  const fmt = (v) => (v === null || v === undefined ? '–' : Number(v).toFixed(2));
  const title = (t) => `${t.title}${t.artist ? ' · ' + t.artist : ''}`;

  function why(t) {
    const d = t.dev;
    if (!d) return;
    let body;
    if (d.kind === 'similar') {
      const rows = Object.keys(PART_MAX).map((k) => {
        const v = d.parts[k];
        const w = v ? Math.round((100 * v) / PART_MAX[k]) : 0;
        return `<tr><td>${esc(k)}</td><td><span class="bar" style="width:${w}px"></span></td><td class="n">${fmt(v)}</td><td class="dim">${esc(PART_NOTE[k])}</td></tr>`;
      }).join('');
      const margin = d.cutoff === null ? 'every candidate fit into the list' : `${fmt(d.score - d.cutoff)} points above the first song that did not make it (${fmt(d.cutoff)})`;
      body = `<table>${rows}<tr><td><b>score</b></td><td></td><td class="n"><b>${fmt(d.score)}</b></td><td class="dim">sum of the parts</td></tr></table>
        <p>Rank ${d.rank} of ${d.candidates} candidates that share at least artist, album, genre or year with the seed; ${esc(margin)}.</p>
        <p>The dice roll can lift or drop a song by up to 2 points, so the same request can give a slightly different list.</p>`;
    } else if (d.kind === 'filler') {
      body = '<p>The library has too few similar songs, so the list was filled up with a random song.</p>';
    } else {
      body = `<p>This is the plugin's own idea of “similar” (position ${esc(String(d.rank))} in its answer). The server only filters it (dislikes) and keeps the order.</p>`;
    }
    openSheet(`<div class="sh-title">Why “${esc(title(t))}”?</div><div class="devwhy" style="padding:0 12px 12px">${body}</div>`);
  }

  function chip(t, key, i) {
    const d = t.dev;
    const text = d.kind === 'similar' ? fmt(d.score) : d.kind === 'filler' ? 'rnd' : 'plug';
    return `<button class="devchip" data-dev-why="${esc(key)}:${i}" aria-label="Why this song? (developer)">${text}</button>`;
  }

  // The chip opens the explanation and must not start the song (the row's own handler listens too)
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-dev-why]');
    if (!b) return;
    e.stopPropagation();
    e.preventDefault();
    const [key, i] = b.dataset.devWhy.split(':');
    const t = (S.lists[key] || [])[+i];
    if (t) why(t);
  }, true);

  function queueTab() {
    const q = S.queue.map((t, i) => ({ t, i })).filter((x) => x.t.dev);
    if (!q.length) return '<p class="dim">No explanations in the queue. Start a radio from a song of your library.</p>';
    const rows = q.map(({ t, i }) => {
      const d = t.dev;
      const now = i === S.idx ? ' ◀' : '';
      const parts = d.kind === 'similar' ? Object.keys(PART_MAX).map((k) => fmt(d.parts[k])).join(' ') : d.kind;
      return `<tr><td class="n dim">${i + 1}</td><td>${esc(t.title.slice(0, 28))}${now}</td><td class="n">${d.kind === 'similar' ? fmt(d.score) : '–'}</td><td class="dim">${esc(parts)}</td></tr>`;
    }).join('');
    return `<table><tr><th class="n">#</th><th>song</th><th class="n">score</th><th>artist album genre year dj random</th></tr>${rows}</table>
      <p class="dim">Parts of the score in that order (dj fit is empty until both songs are analyzed). Tap the number on a queue row for the full explanation.</p>`;
  }

  function requestsTab() {
    if (!log.length) return '<p class="dim">No requests yet.</p>';
    const rows = log.map((x) => {
      const cls = x.status >= 400 ? 'bad' : x.ms >= SLOW_MS ? 'slow' : '';
      return `<tr class="${cls}"><td class="dim">${esc(x.time)}</td><td>${esc(x.method)}</td><td>${esc(x.path.length > 26 ? x.path.slice(0, 25) + '…' : x.path)}</td><td class="n">${x.status}</td><td class="n">${Math.round(x.ms)} ms</td></tr>`;
    }).join('');
    return `<table>${rows}</table><p class="dim">Time as the browser saw it (network included). Orange: ${SLOW_MS} ms or more.</p>`;
  }

  function render() {
    const p = document.getElementById('devPanel');
    if (!p || p.hidden) return;
    p.querySelectorAll('.dtab').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
    p.querySelector('.dbody').innerHTML = tab === 'queue' ? queueTab() : requestsTab();
  }

  function log_(method, path, status, ms) {
    const d = new Date();
    log.unshift({ method, path, status, ms, time: d.toTimeString().slice(0, 8) });
    if (log.length > MAX_LOG) log.pop();
    if (open && tab === 'requests') render();
  }

  function toggle(force) {
    open = force === undefined ? !open : force;
    const p = document.getElementById('devPanel');
    p.hidden = !open;
    render();
  }

  function mount() {
    const panel = document.createElement('aside');
    panel.id = 'devPanel';
    panel.hidden = true;
    panel.setAttribute('aria-label', 'Developer tools');
    panel.innerHTML = `<header><b>DEVELOPER TOOLS</b><button class="dtab" data-tab="queue">Queue</button><button class="dtab" data-tab="requests">Requests</button>
      <button class="dtab" data-close aria-label="Close">✕</button></header><div class="dbody"></div>`;
    panel.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.close !== undefined) toggle(false);
      else if (b.dataset.tab) { tab = b.dataset.tab; render(); }
    });
    document.body.appendChild(panel);
    const btn = document.createElement('button');
    btn.id = 'devBtn2';
    btn.className = 'ibtn';
    btn.textContent = 'DEV';
    btn.setAttribute('aria-label', 'Developer tools');
    btn.addEventListener('click', () => toggle());
    const bell = document.getElementById('bellBtn');
    bell.parentNode.insertBefore(btn, bell);
    // the queue changes while the panel is open: look again now and then (cheap, only while it is open)
    setInterval(() => { if (open && tab === 'queue') render(); }, 1500);
  }

  window.DEV = { log: log_, chip, why };
  mount();
})();
