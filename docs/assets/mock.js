// Interactive demo of the selects app for the landing page.
// Everything here is sample data; nothing is loaded or uploaded.
(() => {
  const root = document.getElementById('mock');
  if (!root) return;
  const main = root.querySelector('.m-main');
  const railEl = root.querySelector('.m-rail');

  // Icons are the same ones the app's sidebar uses.
  const I = {
    review: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>',
    curated: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>',
    people: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M3 20c0-3 3-5 6-5s6 2 6 5"/><path d="M14 20c0-2 2-4 4-4s3.5 1 3.5 3.5"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    map: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4l6 2 6-2v15l-6 2-6-2-6 2V6z"/><path d="M9 4v15M15 6v15"/></svg>',
    videos: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="5" width="20" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/></svg>',
    dupes: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>',
    library: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5v14M8 5v14"/><rect x="11" y="4" width="4" height="16" rx="1"/><path d="M18 5l3 14-3 .6-2.5-.5"/></svg>',
    chev: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>',
    back: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>',
    folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  };

  const RAIL = [['review', 'Review'], ['curated', 'Curated'], null, ['people', 'People'], ['search', 'Search'], ['map', 'Map'], ['videos', 'Videos'], ['dupes', 'Dupes'], 'sp', ['library', 'Library']];
  railEl.innerHTML = RAIL.map(r => r === null ? '<hr>' : r === 'sp' ? '<span class="sp"></span>'
    : `<button data-a="rail:${r[0]}" title="${r[1]}">${I[r[0]]}${r[1]}</button>`).join('');

  // ---------- sample library ----------
  const PLACES = [
    { name: 'Reine', x: 24, y: 50, blurb: 'Red cabins and the peaks behind them' },
    { name: 'Hamnøy', x: 42, y: 24, blurb: 'The bridge at blue hour' },
    { name: 'Kvalvika', x: 66, y: 40, blurb: 'Beach hike, windy' },
    { name: 'Henningsvær', x: 80, y: 72, blurb: 'Harbour and the football pitch' },
    { name: 'Nusfjord', x: 46, y: 76, blurb: 'Fishing village, rain' },
  ];
  const DAYS = [
    { name: 'Monday 12 August', blurb: 'Landed in Leknes, drove to Reine for sunset.' },
    { name: 'Tuesday 13 August', blurb: 'Kvalvika hike in the morning, Hamnøy at night.' },
    { name: 'Wednesday 14 August', blurb: 'Rain in Nusfjord, then Henningsvær harbour.' },
  ];
  const PEOPLE = [
    { id: 'ana', name: 'Ana', col: '#806c5d', n: 281 },
    { id: 'dev', name: 'Dev', col: '#677682', n: 226 },
    { id: 'ines', name: 'Ines', col: '#7a6a7f', n: 97 },
    { id: 'u1', name: null, col: '#5d6b62', n: 40 },
    { id: 'u2', name: null, col: '#6f6556', n: 12 },
  ];
  const SHAPES = [
    'polygon(0 100%,0 42%,16% 47%,26% 35%,42% 56%,54% 31%,68% 55%,80% 42%,100% 52%,100% 100%)',
    'polygon(0 100%,0 58%,22% 50%,40% 60%,60% 48%,78% 56%,100% 50%,100% 100%)',
    'polygon(0 100%,0 30%,10% 52%,24% 22%,36% 48%,50% 40%,62% 18%,76% 46%,90% 34%,100% 44%,100% 100%)',
  ];
  // c1 sky, c2 land, score, quality bucket, place, day, time, tags, people, shape, sun
  const RAW = [
    ['#30465a', '#9c8b75', 9.1, null, 0, 0, '21:48', ['mountains', 'sunset', 'cabins'], ['ana'], 0, 1],
    ['#172836', '#7593a7', 8.4, null, 1, 1, '23:10', ['bridge', 'blue hour', 'mountains'], [], 2, 0],
    ['#1f2c37', '#c9d3d5', 6.2, 'over', 2, 1, '09:15', ['beach', 'sand', 'sea'], ['dev'], 1, 0],
    ['#2f1e28', '#ba9561', 8.8, null, 0, 0, '22:05', ['sunset', 'sea', 'boat'], ['ana', 'dev'], 1, 1],
    ['#1e2933', '#a79d85', 3.1, 'focus', 4, 2, '13:40', ['rain', 'village'], [], 0, 0],
    ['#132e39', '#ddca9d', 7.9, null, 2, 1, '10:02', ['beach', 'hike', 'sea'], ['ines'], 1, 0],
    ['#363b4b', '#e0d5c2', 5.8, 'soft', 1, 1, '23:31', ['bridge', 'night'], [], 2, 0],
    ['#171d28', '#dfe2e4', 2.6, 'under', 4, 2, '14:12', ['rain', 'harbour'], ['dev'], 0, 0],
    ['#1d2029', '#ac8a69', 8.1, null, 3, 2, '18:20', ['harbour', 'boat'], ['ana'], 1, 0],
    ['#2c3946', '#89a3b3', 7.2, null, 0, 0, '20:55', ['mountains', 'cabins', 'water'], [], 2, 0],
    ['#383549', '#b99389', 6.9, 'soft', 3, 2, '19:01', ['football pitch', 'sea'], ['ines', 'dev'], 1, 0],
    ['#283b48', '#7d9baa', 8.6, null, 2, 1, '08:40', ['hike', 'mountains', 'sea'], ['ana', 'ines'], 2, 1],
    ['#523e36', '#b59872', 4.4, 'under', 4, 2, '15:30', ['village', 'rain'], [], 0, 0],
    ['#395361', '#89adba', 9.3, null, 1, 1, '22:48', ['bridge', 'mountains', 'reflection'], [], 0, 0],
    ['#2e3a39', '#67827f', 6.0, 'focus', 3, 2, '17:12', ['harbour'], ['dev'], 1, 0],
    ['#9e6048', '#d7b183', 8.2, null, 0, 0, '22:31', ['sunset', 'mountains'], ['ana', 'dev'], 2, 1],
    ['#304753', '#cc9d79', 7.5, null, 3, 2, '19:44', ['harbour', 'sunset', 'boat'], [], 1, 1],
    ['#392e2e', '#917e74', 3.8, 'under', 4, 2, '13:05', ['rain', 'cabins'], ['ines'], 0, 0],
    ['#20324a', '#c7b48e', 8.9, null, 2, 1, '07:55', ['beach', 'sunrise', 'sea'], ['ana'], 1, 1],
    ['#41455a', '#a4a9b6', 5.1, 'over', 0, 0, '16:20', ['cabins', 'water'], [], 2, 0],
  ];
  const P = RAW.map((r, i) => ({
    i, c1: r[0], c2: r[1], score: r[2], q: r[3], place: r[4], day: r[5], time: r[6], tags: r[7],
    people: r[8], shape: r[9], sun: !!r[10], file: 'IMG_' + (4630 + i * 7) + '.HEIC',
  }));
  const QUALITY = [['', 'All'], ['under', 'Underexposed'], ['over', 'Overexposed'], ['focus', 'Out of focus'], ['soft', 'Soft but good']];
  const VIDEOS = [
    { name: 'VID_4702.MOV', dur: '2:14', secs: 134, p: 0, hi: [[18, 31], [62, 78]], dead: [[0, 6], [118, 134]] },
    { name: 'VID_4731.MOV', dur: '0:48', secs: 48, p: 13, hi: [[5, 22]], dead: [[40, 48]] },
    { name: 'VID_4755.MOV', dur: '4:02', secs: 242, p: 5, hi: [[30, 44], [120, 141], [200, 214]], dead: [[0, 12], [80, 104], [228, 242]] },
    { name: 'VID_4790.MOV', dur: '1:05', secs: 65, p: 16, hi: [[40, 58]], dead: [[0, 9]] },
  ];
  const DUPES = [
    { base: 13, label: 'Hamnøy bridge, 4 shots in 3 seconds', v: ['', 'soft', 'focus', 'over'] },
    { base: 3, label: 'Reine sunset, 3 shots', v: ['', 'under', 'soft'] },
    { base: 18, label: 'Kvalvika sunrise, 4 shots', v: ['soft', '', 'focus', 'under'] },
  ];
  const TOTAL = 641, BASE_KEPT = 24, BASE_REJ = 58;

  const st = {
    page: 'review', tab: 'all', show: '', sort: 'best', menu: false,
    cur: 0, win: 0, dec: {}, hist: [], edited: {}, zoom: false, cmp: false, level: false,
    coll: null, story: null, person: null, naming: null, q: '', pin: null, clip: 0,
    dupePick: DUPES.map(d => d.v.indexOf('')), dupeDone: {}, lib: 0,
  };
  P.forEach(p => { if (p.score >= 8.5) st.dec[p.i] = 'keep'; });

  // ---------- helpers ----------
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const art = (p, extra = '') => `<div class="m-art ${extra}"><i style="background:${p.c1}"></i><i style="background:linear-gradient(180deg,${p.c2} 0 45%,${p.c1} 100%);clip-path:${SHAPES[p.shape]}"></i>${p.sun ? '<i class="sun"></i>' : ''}</div>`;
  const qClass = p => (st.edited[p.i] ? 'edited' : (p.q || ''));
  const tile = (p, attrs = '', score = true) => `<button class="m-tile" ${attrs} aria-label="${esc(p.file)}, score ${p.score}">${art(p, qClass(p))}${score ? `<span class="sc">${p.score.toFixed(1)}</span>` : ''}</button>`;
  const decCol = { keep: 'var(--keep)', reject: 'var(--rej)' };

  function reviewList() {
    let l = P.filter(p => !st.show || p.q === st.show);
    if (st.sort === 'best') l = l.slice().sort((a, b) => b.score - a.score);
    else if (st.sort === 'time') l = l.slice().sort((a, b) => a.day - b.day || a.time.localeCompare(b.time));
    else l = l.slice().sort((a, b) => ((a.i * 7919) % 23) - ((b.i * 7919) % 23));
    return l;
  }
  // Desktop's side column fits three thumbnails; phones show four in a row.
  const winSize = () => (matchMedia('(max-width:760px)').matches ? 4 : 3);
  const kept = () => P.filter(p => st.dec[p.i] === 'keep');
  function counts() {
    const v = Object.values(st.dec);
    const k = BASE_KEPT + v.filter(x => x === 'keep').length, r = BASE_REJ + v.filter(x => x === 'reject').length;
    return { k, r, left: TOTAL - k - r };
  }

  // ---------- views ----------
  function topbar() {
    const title = { review: 'Review', curated: 'Curated', people: 'People', search: 'Search', map: 'Map', videos: 'Videos', dupes: 'Duplicates', library: 'Library' }[st.page];
    const seg = (st.page === 'review' || st.page === 'curated')
      ? `<div class="m-seg" role="tablist" aria-label="View">${[['all', 'All'], ['clusters', 'Collections'], ['stories', 'Stories']].map(([k, l]) => `<button role="tab" aria-selected="${st.tab === k}" data-a="tab:${k}">${l}</button>`).join('')}</div>` : '';
    return `<div class="m-top"><div class="m-crumb"><img src="assets/logo.svg" alt=""><b>selects</b><i>/</i><span>${title}</span></div>${seg}</div>`;
  }

  function showMenu() {
    const label = QUALITY.find(q => q[0] === st.show)[1];
    const items = QUALITY.map(([k, l]) => {
      const n = k ? P.filter(p => p.q === k).length : P.length;
      return `<button role="option" aria-selected="${st.show === k}" data-a="show:${k}">${l}<small>${n}</small></button>`;
    }).join('');
    return `<div class="m-dd"><button aria-haspopup="listbox" aria-expanded="${st.menu}" data-a="menu">${label}${I.chev}</button>${st.menu ? `<div class="m-menu" role="listbox" aria-label="Show">${items}</div>` : ''}</div>`;
  }

  function reviewAll() {
    const list = reviewList();
    const c = counts();
    const head = `<div class="m-head"><div><h3>Review</h3><div class="m-sub">${list.length ? (st.cur + 1) + ' of ' + list.length + ' shown' : 'Nothing here'} · ${c.k} kept · ${c.r} rejected · ${c.left} left</div></div>
      <div class="m-controls"><span>Show</span>${showMenu()}<div class="m-sort" role="group" aria-label="Sort order">${[['best', 'Best'], ['time', 'Time'], ['random', 'Random']].map(([k, l]) => `<button aria-pressed="${st.sort === k}" data-a="sort:${k}">${l}</button>`).join('')}</div></div></div>`;
    if (!list.length) return head + `<div class="m-empty"><div><b>No photos in this filter</b>Nothing here is ${QUALITY.find(q => q[0] === st.show)[1].toLowerCase()}.<br><button data-a="show:">Show all</button></div></div>`;
    st.cur = Math.min(st.cur, list.length - 1);
    if (st.cur < st.win) st.win = st.cur;
    const W = winSize();
    if (st.cur > st.win + W - 1) st.win = st.cur - W + 1;
    st.win = Math.max(0, Math.min(st.win, Math.max(0, list.length - W)));
    const p = list[st.cur], next = list[(st.cur + 1) % list.length], d = st.dec[p.i];
    const pane = (q, extra, tag) => `<div class="m-pane${extra}">${art(q, qClass(q))}${tag ? `<span class="m-pane-tag">${tag}</span>` : ''}</div>`;
    const hist = Array.from({ length: 14 }, (_, k) => { const x = (k - 6 - (p.i % 3)) / 4; return `<span style="height:${Math.round(18 + 82 * Math.exp(-x * x))}%"></span>`; }).join('');
    let strip = '';
    for (let k = st.win; k < Math.min(list.length, st.win + W); k++) {
      const q = list[k], qd = st.dec[q.i];
      strip += `<button class="m-thumb" data-a="pick:${k}" aria-current="${k === st.cur}" aria-label="Photo ${k + 1}${qd ? ', ' + (qd === 'keep' ? 'kept' : 'rejected') : ''}">${art(q, qClass(q))}<span class="n">${k + 1}</span>${qd ? `<span class="d" style="background:${decCol[qd]}">${qd === 'keep' ? 'kept' : 'out'}</span>` : ''}</button>`;
    }
    return head + `<div class="m-row">
      <div class="m-stage${st.level ? ' level' : ''}" data-stage>
        <div class="m-panes${st.cmp ? ' cmp' : ''}">${pane(p, st.zoom ? ' zoom' : '', st.cmp ? 'this one' : '')}${st.cmp ? pane(next, '', 'next') : ''}</div>
        <div class="m-grid3"></div>
        <div class="m-actions">
          <button class="m-act" data-a="act:keep">Keep <span class="k">K</span></button>
          <button class="m-act" data-a="act:reject">Reject <span class="k">X</span></button>
          <button class="m-act" data-a="act:edit" aria-pressed="${!!st.edited[p.i]}">Auto edit <span class="k">E</span></button>
        </div>
        <div class="m-file">${p.file}</div>
        ${d ? `<div class="m-verdict" style="background:${decCol[d]}">${d === 'keep' ? 'Kept' : 'Rejected'}</div>` : ''}
        <div class="m-toast" id="mToast"></div>
      </div>
      <div class="m-mobile-actions"><button data-a="act:undo">Undo</button><button data-a="act:reject">Reject</button><button data-a="act:keep">Keep</button></div>
      <div class="m-side">
        <div class="m-stats"><div class="t">PHOTO</div><div class="m-hist">${hist}</div>
          <div class="m-kv"><div><span>score</span><b>${p.score.toFixed(1)}</b></div><div><span>sharpness</span><b>${p.q === 'focus' ? 'low' : p.q === 'soft' ? 'fair' : 'good'}</b></div><div><span>faces</span><b>${p.people.length}</b></div></div></div>
        <div class="m-strip">${strip}</div>
      </div>
    </div>
    <div class="m-keys">
      ${[['keep', 'K', 'keep'], ['reject', 'X', 'reject'], ['undo', 'U', 'undo'], 0, ['prev', '←', 'prev'], ['next', '→', 'next'], ['zoom', 'Z', 'zoom', st.zoom], ['compare', 'V', 'compare', st.cmp], ['level', 'S', 'straighten', st.level]]
        .map(k => k === 0 ? '<span class="sep"></span>' : `<button data-a="act:${k[0]}"${k.length > 3 ? ` aria-pressed="${k[3]}"` : ''}><kbd>${k[1]}</kbd>${k[2]}</button>`).join('')}
    </div>`;
  }

  function header(title, sub, back) {
    return `<div class="m-head"><div>${back ? `<button class="m-back" data-a="${back}">${I.back}Back</button>` : ''}<h3>${esc(title)}</h3>${sub ? `<div class="m-sub">${sub}</div>` : ''}</div></div>`;
  }
  const grid = list => list.length ? `<div class="m-gridp">${list.map(p => tile(p, `data-a="open:${p.i}"`)).join('')}</div>` : '';

  function collections(list, label) {
    if (st.coll !== null) {
      const pl = PLACES[st.coll], items = list.filter(p => p.place === st.coll);
      return header(pl.name, `${items.length} photos · ${esc(pl.blurb)}`, 'coll:back') + `<div class="m-scroll">${grid(items) || empty('No keepers from here yet', 'Keep a few in Review and they show up here.')}</div>`;
    }
    const cards = PLACES.map((pl, k) => {
      const items = list.filter(p => p.place === k).sort((a, b) => b.score - a.score);
      if (!items.length) return '';
      const m = [0, 1, 2].map(j => items[j] ? `<div>${art(items[j], qClass(items[j]))}</div>` : '<div style="background:var(--surf2)"></div>').join('');
      return `<button class="m-card" data-a="coll:${k}"><div class="m-mosaic">${m}</div><h4>${pl.name}</h4><p>${items.length} ${label}</p></button>`;
    }).join('');
    return header('Collections', 'Grouped by place, scene and day') + `<div class="m-scroll">${cards ? `<div class="m-cards">${cards}</div>` : empty('Nothing kept yet', 'Keep a few in Review and they show up here.')}</div>`;
  }

  function stories(list) {
    const rows = DAYS.map((d, k) => {
      const items = list.filter(p => p.day === k).sort((a, b) => b.score - a.score).slice(0, 4);
      if (!items.length) return '';
      return `<div class="m-story"><h4>${d.name}</h4><p>${d.blurb}</p><div class="row">${items.map(p => tile(p, `data-a="open:${p.i}"`, false)).join('')}</div></div>`;
    }).join('');
    return header('Stories', 'The best of each day') + `<div class="m-scroll">${rows || empty('Nothing kept yet', 'Keep a few in Review and they show up here.')}</div>`;
  }

  function empty(title, text) { return `<div class="m-empty"><div><b>${title}</b>${text}</div></div>`; }

  function curatedAll() {
    const k = kept().sort((a, b) => b.score - a.score);
    return header('Curated', `${k.length} keepers, ready to edit and export`) + `<div class="m-scroll">${grid(k) || empty('Nothing kept yet', 'Keep a few in Review and they show up here.')}</div>`;
  }

  function people() {
    if (st.person) {
      const pe = PEOPLE.find(x => x.id === st.person);
      const items = P.filter(p => p.people.includes(pe.id));
      return header(pe.name || 'Unnamed', `${pe.n} photos in this library`, 'person:back') + `<div class="m-scroll">${grid(items) || empty('Only a few close-ups', 'The rest of their photos are in other trips.')}</div>`;
    }
    const cards = PEOPLE.map(pe => {
      if (st.naming === pe.id) {
        return `<div class="m-person unnamed"><span class="m-face" style="background:${pe.col}">?</span><form class="m-namefield" data-name="${pe.id}"><input aria-label="Name this person" placeholder="Name" maxlength="16"></form><small>Press Enter</small></div>`;
      }
      return pe.name
        ? `<button class="m-person" data-a="person:${pe.id}"><span class="m-face" style="background:${pe.col}">${pe.name[0]}</span><b>${esc(pe.name)}</b><small>${pe.n} photos</small></button>`
        : `<button class="m-person unnamed" data-a="name:${pe.id}"><span class="m-face">?</span><b>Who's this?</b><small>${pe.n} photos</small></button>`;
    }).join('');
    return header('People', 'Grouped by face. Name someone once.') + `<div class="m-scroll"><div class="m-people">${cards}</div></div>`;
  }

  function searchView() {
    const chips = ['sunset', 'bridge', 'beach', 'boat', 'rain', 'mountains'];
    return header('Search', 'Describe the photo you remember') + `<div class="m-scroll">
      <label class="m-search">${I.search}<input id="mQ" type="search" placeholder="Try &quot;boat at sunset&quot;" value="${esc(st.q)}" autocomplete="off"></label>
      <div class="m-chips">${chips.map(c => `<button data-a="chip:${c}" aria-pressed="${st.q === c}">${c}</button>`).join('')}</div>
      <div id="mResults">${searchResults()}</div></div>`;
  }
  function searchResults() {
    const words = st.q.toLowerCase().split(/\s+/).filter(w => w && !['a', 'at', 'the', 'on', 'in', 'of', 'with'].includes(w));
    if (!words.length) return `<div class="m-sub" style="margin-bottom:10px">Recent</div>` + grid(P.slice(0, 8));
    const hits = P.map(p => {
      const hay = (p.tags.join(' ') + ' ' + PLACES[p.place].name + ' ' + p.people.join(' ')).toLowerCase();
      return { p, s: words.filter(w => hay.includes(w.replace(/s$/, ''))).length };
    }).filter(h => h.s).sort((a, b) => b.s - a.s || b.p.score - a.p.score).map(h => h.p);
    return hits.length ? `<div class="m-sub" style="margin-bottom:10px">${hits.length} match${hits.length === 1 ? '' : 'es'}</div>` + grid(hits)
      : empty('No matches', 'Try one of the suggestions above.');
  }

  function mapView() {
    const pins = PLACES.map((pl, k) => `<button class="m-pin" style="left:${pl.x}%;top:${pl.y}%" data-a="pin:${k}" aria-pressed="${st.pin === k}"><span>${pl.name} ${P.filter(p => p.place === k).length}</span></button>`).join('');
    const sel = st.pin === null ? null : PLACES[st.pin];
    const items = st.pin === null ? [] : P.filter(p => p.place === st.pin);
    return header('Map', 'Photos with GPS, by place') + `<div class="m-scroll"><div class="m-map">${pins}</div>
      ${sel ? `<div class="m-sub" style="margin-bottom:10px">${sel.name}: ${items.length} photos</div>${grid(items)}` : '<div class="m-sub">Pick a pin to see what you shot there.</div>'}</div>`;
  }

  function videos() {
    const clips = VIDEOS.map((v, k) => {
      const seg = (a, col) => a.map(([s, e]) => `<i style="left:${s / v.secs * 100}%;width:${(e - s) / v.secs * 100}%;background:${col}"></i>`).join('');
      const keepS = v.hi.reduce((t, [s, e]) => t + e - s, 0);
      return `<button class="m-clip" data-a="clip:${k}" aria-pressed="${st.clip === k}"><div class="m-tile">${art(P[v.p])}<span class="dur">${v.dur}</span></div>
        <div><b>${v.name}</b><small>${v.hi.length} highlight${v.hi.length > 1 ? 's' : ''}, ${keepS}s worth keeping</small><div class="m-tl">${seg(v.dead, 'rgba(255,255,255,.22)')}${seg(v.hi, 'var(--keep)')}</div></div></button>`;
    }).join('');
    return header('Videos', 'Highlights found, dead footage flagged') + `<div class="m-scroll"><div class="m-legend"><span style="--c:var(--keep)">highlight</span><span style="--c:rgba(255,255,255,.3)">dead footage</span></div>${clips}</div>`;
  }

  function dupes() {
    const groups = DUPES.map((g, gi) => {
      const done = st.dupeDone[gi];
      const opts = g.v.map((v, j) => `<button data-a="dpick:${gi}:${j}" aria-pressed="${st.dupePick[gi] === j}" aria-label="Keep shot ${j + 1}"><span class="tag">${st.dupePick[gi] === j ? 'keep' : j + 1}</span><div class="m-tile">${art(P[g.base], v)}</div></button>`).join('');
      return `<div class="m-dupe"><header><div><b>${g.label}</b><br><small>${done ? 'Kept 1, moved ' + (g.v.length - 1) + ' to the reject pile' : 'Sharpest one is picked. Tap another to change it.'}</small></div>
        ${done ? '<span class="m-done">Done</span>' : `<button class="m-btn primary" data-a="dres:${gi}">Keep 1</button>`}</header>${done ? '' : `<div class="opts">${opts}</div>`}</div>`;
    }).join('');
    return header('Duplicates', `${DUPES.length - Object.keys(st.dupeDone).length} groups to sort`) + `<div class="m-scroll">${groups}</div>`;
  }

  function library() {
    const libs = [['Lofoten 2026', 'D:/Photos/2026-08 Lofoten', '641 photos, 4 videos'], ['Lisbon weekend', 'D:/Photos/2026-03 Lisbon', '212 photos'], ['Phone backup', 'C:/Users/you/Pictures/Camera Roll', '3,904 photos']];
    return header('Library', 'Folders selects has looked at') + `<div class="m-scroll">${libs.map(([n, path, c], k) => `<button class="m-lib${st.lib === k ? ' on' : ''}" data-a="lib:${k}" style="width:100%;text-align:left">${I.folder}<div><b>${n}</b><code>${path}</code></div><small>${st.lib === k ? 'Open' : c}</small></button>`).join('')}
      <button class="m-btn" data-a="lib:add">Add a folder</button><p class="m-note" id="mLibNote"></p></div>`;
  }

  function render() {
    railEl.querySelectorAll('[data-a^="rail:"]').forEach(b => b.setAttribute('aria-current', b.dataset.a === 'rail:' + st.page ? 'page' : 'false'));
    let body;
    if (st.page === 'review') body = st.tab === 'all' ? reviewAll() : st.tab === 'clusters' ? collections(P, 'photos') : stories(P);
    else if (st.page === 'curated') body = st.tab === 'all' ? curatedAll() : st.tab === 'clusters' ? collections(kept(), 'keepers') : stories(kept());
    else body = { people, search: searchView, map: mapView, videos, dupes, library }[st.page]();
    main.innerHTML = topbar() + `<div class="m-body">${body}</div>`;
    // keep the active section visible in the sideways rail on phones
    const on = railEl.querySelector('[aria-current="page"]');
    if (on && railEl.scrollWidth > railEl.clientWidth) railEl.scrollTo({ left: on.offsetLeft - railEl.clientWidth / 2 + on.offsetWidth / 2, behavior: 'smooth' });
    const nf = main.querySelector('.m-namefield input');
    if (nf) nf.focus();
  }

  function toast(text, col) {
    const t = main.querySelector('#mToast');
    if (!t) return;
    t.textContent = text; t.style.color = col || 'var(--ink)';
    t.classList.remove('show'); void t.offsetWidth; t.classList.add('show');
  }

  function act(a) {
    const list = reviewList();
    if (!list.length) return;
    const p = list[st.cur];
    let msg = null, col;
    if (a === 'keep' || a === 'reject') {
      st.hist.push({ i: p.i, prev: st.dec[p.i], cur: st.cur });
      st.dec[p.i] = a; st.zoom = false;
      st.cur = Math.min(list.length - 1, st.cur + 1);
      msg = a === 'keep' ? 'Kept' : 'Rejected'; col = decCol[a];
    } else if (a === 'undo') {
      const h = st.hist.pop();
      if (!h) msg = 'Nothing to undo';
      else { if (h.prev) st.dec[h.i] = h.prev; else delete st.dec[h.i]; st.cur = h.cur; msg = 'Undone'; }
    } else if (a === 'next') { st.cur = Math.min(list.length - 1, st.cur + 1); st.zoom = false; }
    else if (a === 'prev') { st.cur = Math.max(0, st.cur - 1); st.zoom = false; }
    else if (a === 'edit') { st.edited[p.i] = !st.edited[p.i]; msg = st.edited[p.i] ? 'Auto edit on' : 'Auto edit off'; }
    else if (a === 'zoom') st.zoom = !st.zoom;
    else if (a === 'compare') st.cmp = !st.cmp;
    else if (a === 'level') st.level = !st.level;
    render();
    if (msg) toast(msg, col);
  }

  function go(page) {
    st.page = page; st.menu = false; st.coll = null; st.person = null; st.naming = null;
    if (page === 'review' || page === 'curated') st.tab = 'all';
    render();
  }

  root.addEventListener('click', e => {
    const el = e.target.closest('[data-a]');
    if (!el || !root.contains(el)) { if (st.menu) { st.menu = false; render(); } return; }
    const [k, v, w] = el.dataset.a.split(':');
    if (k !== 'menu' && k !== 'show' && st.menu) st.menu = false;
    switch (k) {
      case 'rail': go(v); break;
      case 'tab': st.tab = v; st.coll = null; st.menu = false; render(); break;
      case 'menu': st.menu = !st.menu; render(); if (st.menu) main.querySelector('.m-menu button[aria-selected="true"]')?.focus(); break;
      case 'show': st.show = v; st.menu = false; st.cur = 0; st.win = 0; render(); break;
      case 'sort': st.sort = v; st.cur = 0; st.win = 0; render(); break;
      case 'pick': st.cur = +v; st.zoom = false; render(); break;
      case 'act': act(v); break;
      case 'coll': st.coll = v === 'back' ? null : +v; render(); break;
      case 'open': {
        // Opening a photo from any grid jumps to it in Review.
        st.page = 'review'; st.tab = 'all'; st.show = ''; st.coll = null; st.person = null;
        const list = reviewList(); st.cur = Math.max(0, list.findIndex(p => p.i === +v)); render(); break;
      }
      case 'person': st.person = v === 'back' ? null : v; render(); break;
      case 'name': st.naming = v; render(); break;
      case 'chip': st.q = st.q === v ? '' : v; render(); break;
      case 'pin': st.pin = st.pin === +v ? null : +v; render(); break;
      case 'clip': st.clip = +v; render(); break;
      case 'dpick': st.dupePick[+v] = +w; render(); break;
      case 'dres': st.dupeDone[+v] = true; render(); break;
      case 'lib':
        if (v === 'add') { const n = main.querySelector('#mLibNote'); if (n) n.textContent = 'In the app this opens a folder picker. Nothing gets copied or moved.'; }
        else { st.lib = +v; render(); }
        break;
    }
  });
  // composedPath is captured at dispatch, so it still holds the demo even after
  // a render has replaced the element that was clicked.
  document.addEventListener('click', e => { if (st.menu && !e.composedPath().includes(root)) { st.menu = false; render(); } });

  root.addEventListener('submit', e => {
    const f = e.target.closest('[data-name]');
    if (!f) return;
    e.preventDefault();
    const val = f.querySelector('input').value.trim();
    const pe = PEOPLE.find(x => x.id === f.dataset.name);
    if (val) pe.name = val;
    st.naming = null; render();
  });
  root.addEventListener('focusout', e => {
    if (e.target.matches('.m-namefield input') && !e.target.value.trim()) setTimeout(() => { if (st.naming) { st.naming = null; render(); } }, 120);
  });
  root.addEventListener('input', e => {
    if (e.target.id === 'mQ') {
      st.q = e.target.value;
      main.querySelector('#mResults').innerHTML = searchResults();
      main.querySelectorAll('[data-a^="chip:"]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.a === 'chip:' + st.q)));
    }
  });

  const keymap = { k: 'keep', x: 'reject', u: 'undo', e: 'edit', z: 'zoom', v: 'compare', s: 'level', arrowright: 'next', arrowleft: 'prev', arrowdown: 'next', arrowup: 'prev' };
  document.addEventListener('keydown', e => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (!(root.matches(':hover') || root.contains(document.activeElement))) return;
    if (e.target.closest && e.target.closest('input,textarea')) {
      if (e.key === 'Escape') e.target.blur();
      return;
    }
    if (e.key === 'Escape' && st.menu) { st.menu = false; render(); main.querySelector('[data-a="menu"]')?.focus(); return; }
    if (st.page !== 'review' || st.tab !== 'all') return;
    const a = keymap[e.key.toLowerCase()];
    if (a) { e.preventDefault(); act(a); }
  });

  // Swipe the photo: right keeps, left rejects.
  let sx = null, sy = null;
  root.addEventListener('pointerdown', e => { if (e.target.closest('[data-stage]') && !e.target.closest('button')) { sx = e.clientX; sy = e.clientY; } });
  root.addEventListener('pointerup', e => {
    if (sx === null) return;
    const dx = e.clientX - sx, dy = e.clientY - sy; sx = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) act(dx > 0 ? 'keep' : 'reject');
  });
  root.addEventListener('pointercancel', () => { sx = null; });

  render();
})();
