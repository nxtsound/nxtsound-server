'use strict';
// Developer tools. Loaded by app.js only on a server started with DEVTOOLS=1, and only for admins (the server decides what it hands out).
// English only on purpose: the audience is people working on the code. Nothing here changes how the player or the server behave.
// Tabs: Queue (why a song is in the radio), DJ (the plan of every transition), Analysis (what the tempo analysis saw), Home (seeds, cache,
// timings), Plugins (calls to plugins), Requests (calls to the API). A fixed dice repeats radio and mixes; Snapshot saves the data they are made from.

(() => {
  const MAX_LOG = 60;
  const SLOW_MS = 500;
  const PART_MAX = { artist: 3, album: 2, genre: 2, year: 1.5, dj: 2, random: 2 };  // the largest value of each part of a radio score (library.py)
  const PART_NOTE = {
    artist: 'same artist', album: 'same album', genre: 'same genre', year: 'release year close by',
    dj: 'tempo and key fit (only if both songs are analyzed)', random: 'dice roll, new on every request',
  };
  // How a transition is judged (milliseconds between the beats of the two songs, and so on). Measured, not heard: see the developer guide.
  const PHASE_WARN_MS = 40, PHASE_BAD_MS = 80, LAG_WARN_MS = 400, REACT_S = 15;
  const TABS = [['queue', 'Queue'], ['dj', 'DJ'], ['analysis', 'Analysis'], ['home', 'Home'], ['plugins', 'Plugins'], ['requests', 'Requests']];
  const log = [];
  const djLog = [];
  let homeDev = null;
  let analysis = null;       // {id, title, data|error}
  let pluginCalls = [];
  let open = false;
  let tab = 'queue';

  const css = document.createElement('style');
  css.textContent = `
    #devBtn2{font:600 11px/1 ui-monospace,Menlo,monospace;color:var(--dim);letter-spacing:.04em}
    #devPanel{position:fixed;z-index:60;right:0;top:0;bottom:0;width:min(440px,100vw);background:var(--card,#181818);border-left:1px solid #333;
      display:flex;flex-direction:column;font-size:13px;box-shadow:-8px 0 24px #0008}
    #devPanel[hidden]{display:none}
    #devPanel header{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:10px 12px;border-bottom:1px solid #333}
    #devPanel header b{flex:1;font:600 12px ui-monospace,Menlo,monospace;letter-spacing:.04em}
    #devPanel .dtab{padding:4px 10px;border-radius:12px;background:var(--card2,#262626);color:var(--dim)}
    #devPanel .dtab.on{background:#fff;color:#000}
    #devPanel .dtabs{display:flex;flex-wrap:wrap;gap:6px;width:100%}
    #devPanel .dbody{flex:1;overflow:auto;overflow-x:hidden;padding:8px 12px 24px;font-family:ui-monospace,Menlo,monospace;font-size:12px}
    #devPanel .dfoot{display:flex;gap:8px;align-items:center;padding:8px 12px;border-top:1px solid #333;font-size:12px;color:var(--dim)}
    #devPanel .dfoot input{width:70px;background:var(--card2,#262626);color:inherit;border:1px solid #444;border-radius:6px;padding:3px 6px;font:inherit}
    #devPanel .dfoot a{color:#fff;text-decoration:underline}
    #devPanel table{width:100%;border-collapse:collapse;table-layout:auto}
    #devPanel td,#devPanel th{padding:3px 6px 3px 0;text-align:left;vertical-align:top;white-space:nowrap}
    #devPanel td.n,#devPanel th.n{text-align:right}
    #devPanel td.w{white-space:normal;word-break:break-word}
    #devPanel .slow{color:#ffb454}.bad{color:#ff6b6b}.ok{color:#5dd39e}.dim{color:var(--dim)}
    #devPanel .dcard{background:var(--card2,#222);border-radius:8px;padding:8px 10px;margin:0 0 10px}
    #devPanel .dcard h4{margin:0 0 4px;font:600 12px ui-monospace,Menlo,monospace;white-space:normal}
    #devPanel .state{font-size:10px;padding:1px 6px;border-radius:8px;background:#333;margin-left:6px;color:#ddd}
    #devPanel .light{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:4px;vertical-align:baseline}
    #devPanel .l0{background:#5dd39e}#devPanel .l1{background:#ffb454}#devPanel .l2{background:#ff6b6b}#devPanel .l9{background:#666}
    #devPanel details{margin:2px 0}
    #devPanel pre{white-space:pre-wrap;word-break:break-all;margin:4px 0;color:var(--dim)}
    #devPanel svg{width:100%;height:auto;display:block;margin:6px 0}
    #devPanel button.mini{padding:3px 9px;border-radius:10px;background:var(--card2,#262626);color:#fff}
    .devchip{font:600 11px ui-monospace,Menlo,monospace;color:var(--dim);background:var(--card2,#262626);padding:3px 7px;border-radius:9px;margin-right:4px;flex:none}
    .devwhy .bar{display:inline-block;height:8px;border-radius:4px;background:#6d4aff;vertical-align:middle}
    .devwhy table{width:100%;border-collapse:collapse;font-size:13px}
    .devwhy td{padding:5px 8px 5px 0;vertical-align:middle}
    .devwhy td.n{text-align:right;font-variant-numeric:tabular-nums}
    .devwhy p{margin:8px 0;color:var(--dim)}`;
  document.head.appendChild(css);

  const fmt = (v) => (v === null || v === undefined ? '–' : Number(v).toFixed(2));
  const f1 = (v) => (v === null || v === undefined || Number.isNaN(+v) ? '–' : Number(v).toFixed(1));
  const title = (t) => `${t.title}${t.artist ? ' · ' + t.artist : ''}`;
  const short = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);

  // ---------- Why is this song here ----------

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
        <p>The dice roll can lift or drop a song by up to 2 points, so the same request can give a slightly different list. Fix the dice in the footer of the panel to repeat it.</p>`;
    } else if (d.kind === 'filler') {
      body = '<p>The library has too few similar songs, so the list was filled up with a random song.</p>';
    } else if (d.kind === 'list') {
      body = `<p>List <b>${esc(d.list)}</b>:</p><ul>${d.lines.map((l) => `<li>${esc(l)}</li>`).join('')}</ul>
        <p>Lists are computed on every request from the plays, likes and library; the mixes are shuffled once a day.</p>`;
    } else if (d.kind === 'seed') {
      body = `<p>Seed for recommendations: score ${fmt(d.score)}, ${d.plays} of the last 300 plays${d.liked ? ', liked (+1.5)' : ''}.</p>`;
    } else {
      body = `<p>This is the plugin's own idea of “similar” (position ${esc(String(d.rank))} in its answer). The server only filters it (dislikes) and keeps the order.</p>`;
    }
    openSheet(`<div class="sh-title">Why “${esc(title(t))}”?</div><div class="devwhy" style="padding:0 12px 12px">${body}</div>`);
  }

  function chip(t, key, i) {
    const d = t.dev;
    const text = d.kind === 'similar' ? fmt(d.score) : d.kind === 'filler' ? 'rnd' : d.kind === 'list' ? d.label : d.kind === 'seed' ? fmt(d.score) : 'plug';
    return `<button class="devchip" data-dev-why="${esc(key)}:${i}" aria-label="Why this song? (developer)">${esc(String(text))}</button>`;
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

  // ---------- Queue ----------

  function queueTab() {
    const q = S.queue.map((t, i) => ({ t, i })).filter((x) => x.t.dev);
    if (!q.length) return '<p class="dim">No explanations in the queue. Start a radio from a song of your library.</p>';
    const rows = q.map(({ t, i }) => {
      const d = t.dev;
      const now = i === S.idx ? ' ◀' : '';
      const parts = d.kind === 'similar' ? Object.keys(PART_MAX).map((k) => fmt(d.parts[k])).join(' ') : d.kind;
      return `<tr><td class="n dim">${i + 1}</td><td>${esc(short(t.title, 28))}${now}</td><td class="n">${d.kind === 'similar' ? fmt(d.score) : '–'}</td><td class="dim">${esc(parts)}</td></tr>`;
    }).join('');
    return `<table><tr><th class="n">#</th><th>song</th><th class="n">score</th><th>artist album genre year dj random</th></tr>${rows}</table>
      <p class="dim">Parts of the score in that order (dj fit is empty until both songs are analyzed). Tap the number on a queue row for the full explanation.</p>`;
  }

  // ---------- DJ ----------
  // The plans are made and run by app.js (DJ.plan). Here they are only watched: the panel keeps what each transition was planned as and how it went.

  let lastPlan = null;
  let lastEntry = null;

  function finish(e) {
    const pl = e.plan;
    e.state = pl.skip ? 'skipped' : !pl.started ? 'dropped'
      : (pl.switched && pl.t0 && performance.now() - pl.t0 >= pl.mixSec * 950) ? 'done' : 'aborted';  // the player empties DJ.plan when it is over, and also when it gives up
  }

  // While a transition runs: how far apart are the beats of the two songs, how fast is the new one played, did the audio stall?
  function sampleMix() {
    const p = lastPlan, e = lastEntry;
    if (!p || !e || !p.started || p.skip || typeof curEl === 'undefined') return;
    const ea = p.switched ? otherEl() : curEl, eb = p.switched ? curEl : otherEl();
    const m = e.m || (e.m = { phase: [], rateMin: 9, rateMax: 0, stalls: 0, lagMs: null });
    if (m.lagMs === null) m.lagMs = Math.round((ea.currentTime - p.tA) * 1000);
    if (p.lock && !p.fade && !eb.paused && !eb.seeking) m.phase.push(Math.abs(djPhaseErr(p, ea, eb)) * (60 / p.bpmB) * 1000);
    m.rateMin = Math.min(m.rateMin, eb.playbackRate);
    m.rateMax = Math.max(m.rateMax, eb.playbackRate);
  }

  // Did the listener stay? Leaving the new song within REACT_S seconds after the switch counts against the transition.
  function watchReaction() {
    const now = performance.now();
    for (const e of djLog) {
      if (e.reaction || !e.switchedAt) continue;
      const cur = currentTrack();
      if (!cur || !e.to || cur.id !== e.to.id) e.reaction = 'left';
      else if (now - e.switchedAt > REACT_S * 1000) e.reaction = 'stayed';
    }
  }

  // The value that 9 in 10 samples stay below: one stray sample while the beats are still locking in does not decide a transition
  const p90 = (arr) => [...arr].sort((a, b) => a - b)[Math.floor(0.9 * (arr.length - 1))];

  function verdict(e) {
    const m = e.m, why = [];
    let level = 0;
    const bump = (l, t) => { level = Math.max(level, l); why.push(t); };
    if (e.state === 'skipped') return { level: 9, why: ['no transition'] };
    if (e.state === 'aborted') bump(2, 'the player gave up in the middle');
    if (m && m.stalls) bump(2, `audio stalled ${m.stalls}×`);
    if (m && m.phase.length) {
      const mx = p90(m.phase);
      if (mx > PHASE_BAD_MS) bump(2, `beats ${Math.round(mx)} ms apart (9 in 10 samples below)`);
      else if (mx > PHASE_WARN_MS) bump(1, `beats ${Math.round(mx)} ms apart (9 in 10 samples below)`);
    }
    if (m && m.lagMs !== null && m.lagMs > LAG_WARN_MS) bump(1, `started ${m.lagMs} ms late`);
    if (e.reaction === 'left') bump(2, `the listener left within ${REACT_S} s`);
    if (!why.length) why.push(e.state === 'done' ? 'nothing to complain about' : 'not finished yet');
    return { level: e.state === 'done' || level ? level : 9, why };
  }

  function watchDj() {
    const p = (typeof DJ !== 'undefined') ? DJ.plan : null;
    if (p !== lastPlan) {
      if (lastEntry && !p) finish(lastEntry);
      if (p) {
        const from = currentTrack(), to = S.queue[S.idx + 1] || null;
        lastEntry = { n: djLog.length ? djLog[0].n + 1 : 1, time: new Date().toTimeString().slice(0, 8), from, to, plan: p, state: 'planned',
          a: from && DJ.info[from.id] ? { ...DJ.info[from.id] } : null, b: to && DJ.info[to.id] ? { ...DJ.info[to.id] } : null, dur: audio.duration || 0 };
        djLog.unshift(lastEntry);
        if (djLog.length > 20) djLog.pop();
      }
      lastPlan = p;
    }
    if (lastEntry && p) {
      lastEntry.state = p.skip ? 'skipped' : p.switched ? 'switched' : p.started ? 'mixing' : p.warm ? 'warming' : 'planned';
      if (p.switched && !lastEntry.switchedAt) lastEntry.switchedAt = performance.now();
    }
    watchReaction();
    if (open && tab === 'dj') render();
  }

  function timeline(e) {
    const p = e.plan, a = e.a, dur = e.dur;
    if (!dur || p.skip) return '';
    const W = 400, H = 34, x = (s) => Math.max(0, Math.min(W, (s / dur) * W));
    const parts = [`<rect x="0" y="12" width="${W}" height="10" rx="3" fill="#3a3a44"/>`];
    if (a && a.outro_start != null) parts.push(`<rect x="${x(a.outro_start)}" y="12" width="${x(a.audio_end || dur) - x(a.outro_start)}" height="10" fill="#6d4aff55"/>`);
    parts.push(`<rect x="${x(p.tA)}" y="8" width="${Math.max(2, x(p.tA + p.mixSec) - x(p.tA))}" height="18" rx="3" fill="#e8703a"/>`);
    parts.push(`<text x="${x(p.tA)}" y="6" font-size="9" fill="#e8703a">mix at ${f1(p.tA)} s</text><text x="${W}" y="33" text-anchor="end" font-size="9" fill="#888">${f1(dur)} s</text>`);
    return `<svg viewBox="0 0 ${W} ${H}"><title>Where the transition happens in the old song</title>${parts.join('')}</svg>`;
  }

  function djCard(e) {
    const p = e.plan, a = e.a, b = e.b, info = p.info || {};
    const name = (t) => (t ? esc(short(t.title, 22)) : '?');
    const row = (k, v) => `<tr><td class="dim">${k}</td><td class="w">${v}</td></tr>`;
    const rows = [];
    if (p.skip) rows.push(row('no transition', esc(p.why || 'not planned')));
    else {
      const fade = !!p.fade;
      rows.push(row('style', `<b>${esc(p.style)}</b>${fade ? ' (plain crossfade: no tempo or key used)' : ''}`));
      if (!fade) {
        rows.push(row('tempo', `${f1(a && a.bpm)} → ${f1(b && b.bpm)} BPM${b && Math.abs(p.bpmB - b.bpm) > 0.5 ? ` (read as ${f1(p.bpmB)}, half or double)` : ''}`));
        rows.push(row('tempo match', p.lock ? `<span class="ok">locked</span>, the next song is played at ${(p.rate * 100).toFixed(1)} % speed` : '<span class="slow">not locked</span> (more than 8 % apart): echo transition, speed unchanged'));
        rows.push(row('key', `${esc((a && a.key) || '?')} → ${esc((b && b.key) || '?')}: ${info.fit > 0 ? '<span class="ok">fits</span>' : info.fit < 0 ? '<span class="bad">clashes</span> (no bass swap)' : 'unclear or not sure enough (confidence under 0.35)'}`));
        rows.push(row('grid', info.bars ? 'bars: both songs have a sure downbeat' : 'single beats: the downbeat is not sure in both songs'));
        rows.push(row('old song', info.hasOutro ? `has an outro from ${f1(a && a.outro_start)} s` : 'no usable outro: the transition starts as late as fits'));
        rows.push(row('new song', info.hot ? '<span class="slow">loud right away</span> (intro shorter than 4 beats): short transition, no bass swap' : 'has an intro to mix over'));
        rows.push(row('length', `${info.mb} beats = ${f1(p.mixSec)} s, entering the new song at ${f1(p.X)} s`));
      } else rows.push(row('length', `${f1(p.mixSec)} s`));
      rows.push(row('starts at', `${f1(p.tA)} s of the old song`));
    }
    const v = verdict(e), m = e.m;
    const res = [row('result', `<span class="light l${v.level}"></span>${esc(v.why.join('; '))}`)];
    if (m && m.phase.length) res.push(row('beats apart', `${Math.round(m.phase.reduce((a, b) => a + b, 0) / m.phase.length)} ms on average, ${Math.round(p90(m.phase))} ms at the 90th percentile, ${Math.round(Math.max(...m.phase))} ms at most (${m.phase.length} samples)`));
    if (m && m.rateMax > 0) res.push(row('speed', `${(m.rateMin * 100).toFixed(1)} to ${(m.rateMax * 100).toFixed(1)} % (the new song)`));
    res.push(row('listener', e.reaction === 'stayed' ? '<span class="ok">stayed</span>' : e.reaction === 'left' ? `<span class="bad">left within ${REACT_S} s</span>` : e.switchedAt ? 'waiting …' : '–'));
    res.push(row('your verdict', ['up', 'down'].map((k) => `<button class="mini" data-dev-rate="${e.n}:${k}" ${e.rating === k ? 'style="background:#fff;color:#000"' : ''}>${k === 'up' ? '👍 good' : '👎 bad'}</button>`).join(' ')));
    return `<div class="dcard"><h4>${name(e.from)} → ${name(e.to)}<span class="state">${esc(e.state)}</span> <span class="dim">${esc(e.time)}</span></h4>
      ${timeline(e)}<table>${rows.join('')}<tr><td colspan="2"><hr style="border:0;border-top:1px solid #444;margin:4px 0"></td></tr>${res.join('')}</table></div>`;
  }

  // Per style: how often it went well. Only finished transitions count.
  function djStats() {
    const by = {};
    for (const e of djLog.filter((x) => x.state === 'done' || x.state === 'aborted')) {
      const s = e.plan.fade ? 'fade' : e.plan.style, v = verdict(e).level;
      const r = by[s] || (by[s] = { n: 0, g: 0, y: 0, r: 0, up: 0, down: 0, ph: [] });
      r.n++; if (v === 0) r.g++; else if (v === 1) r.y++; else r.r++;
      if (e.rating === 'up') r.up++; if (e.rating === 'down') r.down++;
      if (e.m && e.m.phase.length) r.ph.push(p90(e.m.phase));
    }
    const rows = Object.entries(by).map(([s, r]) => `<tr><td>${esc(s)}</td><td class="n">${r.n}</td><td class="n"><span class="light l0"></span>${r.g}</td><td class="n"><span class="light l1"></span>${r.y}</td><td class="n"><span class="light l2"></span>${r.r}</td>
      <td class="n">${r.ph.length ? Math.round(r.ph.reduce((a, b) => a + b, 0) / r.ph.length) + ' ms' : '–'}</td><td class="n">${r.up}/${r.down}</td></tr>`).join('');
    return rows ? `<table><tr><th>style</th><th class="n">n</th><th class="n">ok</th><th class="n">meh</th><th class="n">bad</th><th class="n">beats</th><th class="n">👍/👎</th></tr>${rows}</table>
      <p class="dim">Since the page was opened. Light: ok / meh (beats ${PHASE_WARN_MS}+ ms apart or a late start) / bad (${PHASE_BAD_MS}+ ms, stalled, given up, or the listener left within ${REACT_S} s). “beats” is how far apart the beats were (9 in 10 samples below), averaged over the transitions.</p>` : '';
  }

  function djReport() {
    return djLog.map((e) => ({ time: e.time, from: e.from && e.from.title, to: e.to && e.to.title, state: e.state, style: e.plan.fade ? 'fade' : e.plan.style, lock: !!e.plan.lock,
      rate: e.plan.rate, bpm: [e.a && e.a.bpm, e.b && e.b.bpm], key: [e.a && e.a.key, e.b && e.b.key], info: e.plan.info, why: e.plan.why, mixSec: e.plan.mixSec, tA: e.plan.tA,
      metrics: e.m && { maxBeatsApartMs: e.m.phase.length ? Math.round(Math.max(...e.m.phase)) : null, p90BeatsApartMs: e.m.phase.length ? Math.round(p90(e.m.phase)) : null, samples: e.m.phase.length, rate: [e.m.rateMin, e.m.rateMax], stalls: e.m.stalls, startLagMs: e.m.lagMs },
      reaction: e.reaction || null, rating: e.rating || null, result: verdict(e) }));
  }

  function djTab() {
    const on = typeof DJ !== 'undefined' && DJ.on;
    const state = `<p class="dim">DJ mode is ${on ? '<span class="ok">on</span>' : 'off'}${on ? `, style ${esc(DJ.style)}, plain fade ${DJ.fadeSec} s${DJ.fadeFallback ? '' : ' (no fallback)'}` : ' (it is not available on iPhone and iPad)'}. A plan appears about 2 minutes before a song ends.</p>`;
    const tools = djLog.length ? '<p><button class="mini" data-dev-copy>Copy report (JSON)</button></p>' : '';
    return state + djStats() + tools + (djLog.length ? djLog.map(djCard).join('') : '<p class="dim">No transition planned yet. Turn DJ mode on in the player settings and let a song with a next song run towards its end.</p>');
  }

  // ---------- Analysis ----------

  async function loadAnalysis() {
    const t = currentTrack();
    if (!t) { analysis = null; return; }
    if (analysis && analysis.id === t.id && !analysis.error) return;
    analysis = { id: t.id, title: title(t), loading: true };
    render();
    try {
      analysis = { id: t.id, title: title(t), data: await api('/dev/analysis/' + encodeURIComponent(t.id), {}, 90000) };
    } catch (e) { analysis = { id: t.id, title: title(t), error: e.message }; }
    render();
  }

  function analysisChart(d) {
    const c = d.curves, s = d.stored;
    if (!c) return '';
    const W = 400, H = 150, T = c.seconds || 1, X = (sec) => (sec / T) * W;
    const line = (arr, step, color, fill) => {
      if (!arr.length) return '';
      const pts = arr.map((v, i) => `${X(i * step).toFixed(1)},${(H - 6 - v * (H - 24)).toFixed(1)}`).join(' ');
      return fill ? `<polygon points="${X(0)},${H - 6} ${pts} ${X((arr.length - 1) * step)},${H - 6}" fill="${color}" opacity=".35"/>` : `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1"/>`;
    };
    const marks = [];
    if (s && s.bpm > 0) {
      const beat = 60 / s.bpm, span = c.onset.length * c.onset_step;
      const bars = s.first_bar != null ? s.first_bar : s.first_beat;
      for (let t = s.first_beat; t < span; t += beat) marks.push(`<line x1="${X(t).toFixed(1)}" x2="${X(t).toFixed(1)}" y1="${H - 12}" y2="${H - 6}" stroke="#5dd39e" stroke-width=".6"/>`);
      for (let t = bars; t < span; t += beat * 4) marks.push(`<line x1="${X(t).toFixed(1)}" x2="${X(t).toFixed(1)}" y1="14" y2="${H - 6}" stroke="${s.bar_ok ? '#5dd39e' : '#777'}" stroke-width=".5" opacity=".6"/>`);
    }
    const flags = [['intro_end', '#ffb454', 'intro ends'], ['outro_start', '#e8703a', 'outro'], ['audio_end', '#ff6b6b', 'end']]
      .filter(([k]) => s && s[k] != null)
      .map(([k, color, label]) => `<line x1="${X(s[k])}" x2="${X(s[k])}" y1="10" y2="${H - 6}" stroke="${color}" stroke-width="1.2"/><text x="${Math.min(X(s[k]) + 2, W - 40)}" y="10" font-size="8" fill="${color}">${label}</text>`);
    return `<svg viewBox="0 0 ${W} ${H}"><title>Loudness (filled), onsets (line), beats and bars (green)</title>
      <rect x="0" y="0" width="${W}" height="${H}" fill="#14141a" rx="4"/>${line(c.loudness, c.loudness_step, '#6d4aff', true)}${line(c.onset, c.onset_step, '#ddd', false)}${marks.join('')}${flags.join('')}</svg>
      <div class="dim">Loudness over the whole song (${f1(c.seconds)} s). Onsets, beats and bars only for the first ${f1(c.onset.length * c.onset_step)} s, the part that is analyzed.</div>`;
  }

  function analysisTab() {
    const t = currentTrack();
    if (!t) return '<p class="dim">Play a song of your library first.</p>';
    if (!analysis || analysis.id !== t.id) { setTimeout(loadAnalysis, 0); return '<p class="dim">Reading the file …</p>'; }
    if (analysis.loading) return `<p class="dim">Analyzing “${esc(analysis.title)}” …</p>`;
    if (analysis.error) return `<p class="bad">${esc(analysis.error)}</p><button class="mini" data-dev-retry>Try again</button>`;
    const d = analysis.data;
    if (!d.usable) return `<p class="dim">${esc(analysis.title)}: ${esc(d.reason)}</p>`;
    const s = d.stored;
    const rows = s ? Object.entries(s).filter(([k]) => !['ready', 'usable'].includes(k)).map(([k, v]) => `<tr><td class="dim">${esc(k)}</td><td>${esc(typeof v === 'number' ? String(Math.round(v * 1000) / 1000) : String(v))}</td></tr>`).join('') : '';
    const verdict = !s ? 'no result' : s.conf >= d.min_conf ? `<span class="ok">usable</span> (confidence ${fmt(s.conf)} ≥ ${d.min_conf})` : `<span class="bad">not usable</span> (confidence ${fmt(s.conf)} < ${d.min_conf}): DJ mode will crossfade this song`;
    return `<h4 style="margin:0 0 4px">${esc(analysis.title)}</h4><div>${verdict}, analysis version ${d.analysis_version}${d.analyzed_now ? " (analyzed just now)" : ""}</div>${analysisChart(d)}<table>${rows}</table>
      <p class="dim">The curves are computed again from the file when you open this tab; the values are what the server stored.</p>`;
  }

  // ---------- Home ----------

  function homeTab() {
    if (!homeDev) return '<p class="dim">Open the home page (it is asked again each time you come back to it).</p>';
    const d = homeDev, sim = d.similar || {};
    const seeds = (d.seeds || []).map((s, i) => `<tr><td class="n dim">${i + 1}</td><td>${esc(short(s.title, 24))}</td><td class="n">${fmt(s.score)}</td><td class="n">${s.plays ?? '–'}×</td><td>${s.liked ? '♥' : ''}</td><td class="n dim">${sim.counts ? sim.counts[i] ?? '–' : '–'}</td></tr>`).join('');
    const widgets = (d.widgets || []).map((w) => `<tr class="${w.ms >= SLOW_MS ? 'slow' : ''}"><td>${esc(w.type)}</td><td class="dim">${w.shown ? '' : 'left out'}</td><td class="n">${Math.round(w.ms)} ms</td></tr>`).join('');
    const cache = sim.cache === 'hit' ? `from the cache (${sim.age_s} s old, kept 15 minutes, dropped at the next play)` : sim.cache ? `computed again (${esc(sim.cache)})` : 'not needed by this layout';
    return `<h4 style="margin:0 0 4px">Seeds</h4>
      <table><tr><th class="n">#</th><th>song</th><th class="n">score</th><th class="n">plays</th><th></th><th class="n">similar</th></tr>${seeds || '<tr><td class="dim" colspan="6">none (nothing played yet)</td></tr>'}</table>
      <p class="dim">Similar songs: ${cache}. Score: 1 / (1 + age / 15) per play of the last 300, +1.5 for a like. One song per artist.</p>
      <h4 style="margin:10px 0 4px">Widgets</h4><table>${widgets}</table>`;
  }

  // ---------- Plugins ----------

  async function loadPluginCalls() {
    try { pluginCalls = (await api('/dev/plugin-calls')).calls; } catch (_) { /* later */ }
    if (open && tab === 'plugins') render();
  }

  function pluginsTab() {
    if (!pluginCalls.length) return '<p class="dim">No calls to plugins yet (search, radio, artist pages, … with a plugin on).</p>';
    const rows = pluginCalls.map((c) => {
      const cls = c.status === 'error' || c.status >= 400 ? 'bad' : c.ms >= SLOW_MS ? 'slow' : '';
      const params = Object.entries(c.params || {}).map(([k, v]) => `${k}=${v}`).join('&');
      const detail = `${params ? params + '\n' : ''}${c.error ? 'error: ' + c.error + '\n' : ''}${c.preview || ''}`;
      return `<tr class="${cls}"><td class="dim">${esc(new Date(c.time * 1000).toTimeString().slice(0, 8))}</td><td>${esc(c.plugin)}</td>
        <td class="w"><details><summary>${esc(c.method)} ${esc(c.path)}</summary><pre>${esc(detail || '(nothing)')}</pre></details></td><td class="n">${esc(String(c.status))}</td><td class="n">${Math.round(c.ms)} ms</td></tr>`;
    }).join('');
    return `<table>${rows}</table><p class="dim">All calls of the server to plugins, whoever caused them. Bodies are never kept, answers to settings calls are left out.</p>`;
  }

  // ---------- Requests ----------

  function requestsTab() {
    if (!log.length) return '<p class="dim">No requests yet.</p>';
    const rows = log.map((x) => {
      const cls = x.status >= 400 ? 'bad' : x.ms >= SLOW_MS ? 'slow' : '';
      return `<tr class="${cls}"><td class="dim">${esc(x.time)}</td><td>${esc(x.method)}</td><td>${esc(short(x.path, 26))}</td><td class="n">${x.status}</td><td class="n">${Math.round(x.ms)} ms</td></tr>`;
    }).join('');
    return `<table>${rows}</table><p class="dim">Time as the browser saw it (network included). Orange: ${SLOW_MS} ms or more.</p>`;
  }

  // ---------- The panel ----------

  function render() {
    const p = document.getElementById('devPanel');
    if (!p || p.hidden) return;
    p.querySelectorAll('.dtab[data-tab]').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
    const body = p.querySelector('.dbody');
    const top = body.scrollTop;
    body.innerHTML = { queue: queueTab, dj: djTab, analysis: analysisTab, home: homeTab, plugins: pluginsTab, requests: requestsTab }[tab]();
    body.scrollTop = top;
    const dice = p.querySelector('[data-dev-dice]');
    if (document.activeElement !== dice) dice.value = S.devRng === undefined ? '' : S.devRng;
  }

  function logRequest(method, path, status, ms) {
    log.unshift({ method, path, status, ms, time: new Date().toTimeString().slice(0, 8) });
    if (log.length > MAX_LOG) log.pop();
    if (open && tab === 'requests') render();
  }

  function toggle(force) {
    open = force === undefined ? !open : force;
    document.getElementById('devPanel').hidden = !open;
    if (open && tab === 'plugins') loadPluginCalls();
    render();
  }

  function mount() {
    const panel = document.createElement('aside');
    panel.id = 'devPanel';
    panel.hidden = true;
    panel.setAttribute('aria-label', 'Developer tools');
    panel.innerHTML = `<header><b>DEVELOPER TOOLS</b><button class="dtab" data-close aria-label="Close">✕</button>
      <div class="dtabs">${TABS.map(([k, n]) => `<button class="dtab" data-tab="${k}">${n}</button>`).join('')}</div></header><div class="dbody"></div>
      <div class="dfoot"><label>Dice <input data-dev-dice type="number" min="0" step="1" placeholder="free" aria-label="Fixed dice"></label>
      <span title="A fixed dice repeats radio and home mixes">fixed = repeatable</span><span style="flex:1"></span><a href="/api/dev/export" download>Snapshot ⬇</a></div>`;
    panel.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.close !== undefined) toggle(false);
      else if (b.dataset.tab) {
        tab = b.dataset.tab;
        if (tab === 'plugins') loadPluginCalls();
        if (tab === 'analysis') loadAnalysis();
        render();
      } else if (b.dataset.devRetry !== undefined) { analysis = null; loadAnalysis(); }
      else if (b.dataset.devRate) {
        const [n, k] = b.dataset.devRate.split(':'), e = djLog.find((x) => x.n === +n);
        if (e) { e.rating = e.rating === k ? undefined : k; render(); }
      } else if (b.dataset.devCopy !== undefined) {
        navigator.clipboard.writeText(JSON.stringify(djReport(), null, 1)).then(() => { b.textContent = 'Copied'; }, () => { b.textContent = 'Could not copy'; });
      }
    });
    panel.querySelector('[data-dev-dice]').addEventListener('change', (e) => {
      const v = e.target.value.trim();
      S.devRng = v === '' ? undefined : Math.max(0, Math.floor(+v));
      render();
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
    // things that change while the panel is open: look again now and then (cheap, only while it is open)
    setInterval(() => { if (open && (tab === 'queue' || (tab === 'analysis' && currentTrack() && (!analysis || analysis.id !== currentTrack().id)))) render(); }, 1500);
    setInterval(() => { if (open && tab === 'plugins') loadPluginCalls(); }, 3000);
    setInterval(watchDj, 400);  // the plan of a transition is made by the player, here it is only watched
    setInterval(sampleMix, 100);  // finer while a transition runs: the beats of the two songs
    [elA, elB].forEach((el) => el.addEventListener('waiting', () => {
      if (lastEntry && lastPlan && lastPlan.started && lastEntry.m) lastEntry.m.stalls++;
    }));
  }

  window.DEV = { log: logRequest, chip, why, home: (d) => { homeDev = d; if (open && tab === 'home') render(); } };
  mount();
})();
