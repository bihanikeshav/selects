// travelcull mockup — view nav, keyboard shortcuts, theme toggle, burst promote
(() => {
  const root = document.documentElement;

  /* ---------- theme toggle ---------- */
  const savedTheme = localStorage.getItem('tc-theme');
  if (savedTheme) root.setAttribute('data-theme', savedTheme);

  const themeBtn = document.querySelector('[data-action="toggle-theme"]');
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      if (next === 'light') root.removeAttribute('data-theme');
      else root.setAttribute('data-theme', 'dark');
      localStorage.setItem('tc-theme', next);
    });
  }

  /* ---------- view switching via rail items + tab pill ---------- */
  const viewLinks = document.querySelectorAll('[data-view-link]');
  viewLinks.forEach(el => {
    el.addEventListener('click', e => {
      const target = el.getAttribute('data-view-link');
      if (target && !target.startsWith('#')) return;
    });
  });

  /* ---------- burst gold promotion (numeric keys) ---------- */
  const goldImg = document.querySelector('[data-gold-img]');
  const goldName = document.querySelector('[data-gold-name]');
  const thumbs = document.querySelectorAll('.burst-thumb');

  function promote(idx) {
    if (idx < 0 || idx >= thumbs.length) return;
    const thumb = thumbs[idx];
    const img = thumb.querySelector('img');
    if (goldImg && img) {
      goldImg.style.opacity = '0';
      setTimeout(() => {
        goldImg.src = img.dataset.large || img.src;
        goldImg.style.opacity = '1';
      }, 150);
    }
    if (goldName) goldName.textContent = thumb.dataset.filename || goldName.textContent;
    thumbs.forEach(t => t.classList.remove('is-gold'));
    thumb.classList.add('is-gold');
  }

  thumbs.forEach((t, i) => {
    t.addEventListener('click', () => promote(i));
  });

  /* ---------- keyboard hints ---------- */
  document.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    const key = e.key.toLowerCase();

    // numeric promote
    if (/^[1-9]$/.test(key)) {
      promote(parseInt(key, 10) - 1);
      flash(`promoted ${key}`);
      return;
    }

    if (key === 'j') { flash('rejected', 'danger'); }
    else if (key === 'k') { flash('kept gold', 'primary'); }
    else if (key === 'l') { flash('kept gold + silver', 'positive'); }
    else if (key === ';') { flash('kept all', 'positive'); }
    else if (key === '1' && e.shiftKey) { /* noop */ }
    else if (key === 't' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      themeBtn && themeBtn.click();
    }
  });

  /* ---------- flash a tiny toast in the kbd footer ---------- */
  function flash(msg, tone) {
    let toast = document.querySelector('.flash-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.className = 'flash-toast';
      Object.assign(toast.style, {
        position: 'fixed',
        bottom: '76px',
        left: '50%',
        transform: 'translateX(-50%) translateY(8px)',
        padding: '10px 20px',
        background: 'var(--md-surface-c-highest)',
        color: 'var(--md-on-surface)',
        borderRadius: '999px',
        fontFamily: 'var(--font-display)',
        fontSize: '13px',
        fontWeight: '500',
        boxShadow: 'var(--md-elev-3)',
        opacity: '0',
        transition: 'opacity 200ms, transform 200ms cubic-bezier(0.2, 0, 0, 1)',
        zIndex: '20',
        pointerEvents: 'none'
      });
      document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.style.borderLeft =
      tone === 'danger'   ? '3px solid var(--g-red)'    :
      tone === 'positive' ? '3px solid var(--g-green)'  :
      tone === 'primary'  ? '3px solid var(--g-blue)'   :
      '3px solid var(--md-outline)';
    requestAnimationFrame(() => {
      toast.style.opacity = '1';
      toast.style.transform = 'translateX(-50%) translateY(0)';
    });
    clearTimeout(flash._t);
    flash._t = setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(-50%) translateY(8px)';
    }, 1200);
  }
})();
