// Introduction after the first login: skippable, lightly animated, with instructions for "Add to Home Screen".
// Only needs #tour from index.html. Call: showTutorial(onDone). The texts are built per call so they follow the chosen language (tr() from app.js).

const TOUR_ICONS = {
  share: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l4 4-1.4 1.4L13 6.8V15h-2V6.8L9.4 8.4 8 7l4-4zM6 10h3v2H6v8h12v-8h-3v-2h3a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2z"/></svg>',
  plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zm6 5v3H8v2h3v3h2v-3h3v-2h-3V8h-2z"/></svg>',
  dots: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8a2 2 0 1 0 0-4 2 2 0 0 0 0 4zm0 2a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm0 6a2 2 0 1 0 0 4 2 2 0 0 0 0-4z"/></svg>',
  install: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 20h14v-2H5v2zM19 9h-4V3H9v6H5l7 7 7-7z"/></svg>',
  check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 16.2l-3.5-3.5L4 14.2l5 5 11-11-1.5-1.5z"/></svg>',
};

const tourInstall = () => ({
  ios: {
    note: tr('This only works in Safari (other iPhone browsers sometimes hide the menu item).'),
    steps: [
      [tr('In Safari, tap <b>Share</b> at the bottom'), 'share'],
      [tr('Scroll and choose <b>Add to Home Screen</b>'), 'plus'],
      [tr('Tap <b>Add</b> at the top right'), 'check'],
    ],
  },
  android: {
    note: tr('In Chrome. Other browsers name the item similarly.'),
    steps: [
      [tr('Tap the <b>menu</b> (three dots) at the top right'), 'dots'],
      [tr('Choose <b>Install app</b> or <b>Add to Home screen</b>'), 'install'],
      [tr('Confirm with <b>Install</b> or <b>Add</b>'), 'check'],
    ],
  },
});

const tourSlides = () => [
  {
    title: tr('Welcome'),
    text: tr('Your own music player. The short tour takes half a minute. You can skip it at any time.'),
    art: '<div class="art art-bars"><i></i><i></i><i></i><i></i><i></i></div>',
  },
  {
    title: tr('Search and radio'),
    text: tr('Search for songs, artists or albums. Tapping a song starts the radio: the queue fills with matching tracks and keeps refilling endlessly.'),
    art: '<div class="art art-search"><span class="ring"></span><span class="ring r2"></span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15.5 14h-.8l-.3-.3A6.5 6.5 0 1 0 14 15.5l.3.3v.8l5 5 1.5-1.5-5-5zm-6 0a4.5 4.5 0 1 1 0-9 4.5 4.5 0 0 1 0 9z"/></svg></div>',
  },
  {
    title: tr('The queue under control'),
    text: tr('Drag tracks by the handle into the right order. In the menu (⋮) you find “Play next”, “Move to end of queue” and remove.'),
    art: '<div class="art art-queue"><div class="qrow a"><b></b><s></s></div><div class="qrow b"><b></b><s></s></div><div class="qrow c"><b></b><s></s></div></div>',
  },
  {
    title: tr('Likes and your mix'),
    text: tr('Use the heart to remember tracks. The home page learns from what you listen to. If something does not fit, “Stop recommending” (in the ⋮ menu) hides tracks or artists.'),
    art: '<div class="art art-like"><span class="h h1">♥</span><span class="h h2">♥</span><span class="h h3">♥</span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21l-1.5-1.4C5.4 15 2 12 2 8.5A4.5 4.5 0 0 1 6.5 4c1.7 0 3.4.8 4.5 2.1C12.1 4.8 13.8 4 15.5 4A4.5 4.5 0 0 1 20 8.5c0 3.5-3.4 6.5-8.5 11.1L12 21z"/></svg></div>',
  },
  {
    title: tr('Long sets'),
    text: tr('For tracks of 10 minutes and more the player remembers the position and resumes there.'),
    art: '<div class="art art-set"><div class="bar"><i></i><b></b></div><div class="lbl"><span>1:12:40</span><span>1:32:05</span></div></div>',
  },
  { install: true, title: tr('As an app on the home screen'), text: '' },
];

function showTutorial(onDone) {
  const root = document.getElementById('tour');
  if (!root) { if (onDone) onDone(); return; }
  const SLIDES = tourSlides();
  const INSTALL = tourInstall();
  const last = SLIDES.length - 1;
  let i = 0;
  let os = /android/i.test(navigator.userAgent) ? 'android' : 'ios';
  let dir = 1;

  const stepsHtml = () => {
    const d = INSTALL[os];
    return `<ol class="steps">${d.steps.map(([t, ic], n) =>
      `<li style="--d:${n * 2}s"><span class="num">${n + 1}</span><span class="st">${t}</span><span class="tic">${TOUR_ICONS[ic]}</span></li>`).join('')}</ol>
      <p class="note">${d.note}</p>`;
  };

  const slideHtml = (s) => s.install
    ? `<div class="art art-home"><div class="screen"><span class="app"></span><span class="app"></span><span class="app"></span><span class="app me"><img src="icon.svg" alt=""></span></div></div>
       <h2>${s.title}</h2>
       <div class="os-tabs" role="tablist"><button type="button" data-os="ios" class="${os === 'ios' ? 'on' : ''}">iPhone</button><button type="button" data-os="android" class="${os === 'android' ? 'on' : ''}">Android</button></div>
       <div id="tourSteps">${stepsHtml()}</div>`
    : `${s.art}<h2>${s.title}</h2><p>${s.text}</p>`;

  const render = () => {
    root.innerHTML = `
      <div class="tour-card">
        <button type="button" class="tour-skip" data-t="skip">${i === last ? tr('Close') : tr('Skip')}</button>
        <div class="tour-slide ${dir > 0 ? 'from-r' : 'from-l'}" key="${i}">${slideHtml(SLIDES[i])}</div>
        <div class="tour-dots">${SLIDES.map((_, n) => `<button type="button" data-t="dot" data-n="${n}" class="${n === i ? 'on' : ''}" aria-label="${tr('Page {n}', { n: n + 1 })}"></button>`).join('')}</div>
        <div class="tour-nav">
          <button type="button" class="btn sec" data-t="back" ${i === 0 ? 'style="visibility:hidden"' : ''}>${tr('Back')}</button>
          <button type="button" class="btn" data-t="next">${i === last ? tr('Let’s go') : tr('Next')}</button>
        </div>
      </div>`;
    const nx = root.querySelector('[data-t=next]');
    if (nx) nx.focus({ preventScroll: true });
  };

  const goTo = (n) => { if (n < 0 || n > last || n === i) return; dir = n > i ? 1 : -1; i = n; render(); };
  const finish = () => {
    root.hidden = true;
    root.innerHTML = '';
    document.body.classList.remove('tour-open');
    document.removeEventListener('keydown', onKey, true);
    if (onDone) onDone();
  };
  const onKey = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(); }
    else if (e.key === 'ArrowRight') { e.stopPropagation(); i === last ? finish() : goTo(i + 1); }
    else if (e.key === 'ArrowLeft') { e.stopPropagation(); goTo(i - 1); }
  };

  root.onclick = (e) => {
    const t = e.target.closest('[data-t],[data-os]');
    if (!t) return;
    if (t.dataset.os) {
      os = t.dataset.os;
      root.querySelectorAll('[data-os]').forEach((b) => b.classList.toggle('on', b.dataset.os === os));
      const box = root.querySelector('#tourSteps');
      if (box) box.innerHTML = stepsHtml();
      return;
    }
    const a = t.dataset.t;
    if (a === 'skip') finish();
    else if (a === 'next') (i === last ? finish() : goTo(i + 1));
    else if (a === 'back') goTo(i - 1);
    else if (a === 'dot') goTo(+t.dataset.n);
  };

  // Swipe
  let x0 = null;
  root.ontouchstart = (e) => { x0 = e.touches[0].clientX; };
  root.ontouchend = (e) => {
    if (x0 === null) return;
    const dx = e.changedTouches[0].clientX - x0;
    x0 = null;
    if (Math.abs(dx) < 50) return;
    if (dx < 0) { if (i < last) goTo(i + 1); } else goTo(i - 1);
  };

  document.addEventListener('keydown', onKey, true);
  document.body.classList.add('tour-open');
  root.hidden = false;
  render();
}
