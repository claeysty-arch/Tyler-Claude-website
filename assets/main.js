/* Tyler portfolio — interactions */
(() => {
  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- Theme toggle ---------- */
  const themeToggle = document.getElementById('theme-toggle');
  const stored = localStorage.getItem('theme');
  const systemDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const initialTheme = stored || (systemDark ? 'dark' : 'light');
  document.documentElement.setAttribute('data-theme', initialTheme);
  themeToggle?.addEventListener('click', () => {
    const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
  });

  /* ---------- Scroll progress + nav shadow ---------- */
  const progress = document.querySelector('.scroll-progress span');
  const nav = document.querySelector('.nav');
  const onScroll = () => {
    const h = document.documentElement;
    const scrolled = (h.scrollTop) / (h.scrollHeight - h.clientHeight);
    if (progress) progress.style.width = `${Math.min(100, scrolled * 100)}%`;
    if (nav) nav.classList.toggle('is-scrolled', h.scrollTop > 8);
  };
  document.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  /* ---------- Cursor glow ---------- */
  const glow = document.querySelector('.cursor-glow');
  if (glow && !prefersReduced && window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
    let tx = 0, ty = 0, cx = 0, cy = 0;
    window.addEventListener('pointermove', (e) => { tx = e.clientX; ty = e.clientY; });
    const tick = () => {
      cx += (tx - cx) * 0.12;
      cy += (ty - cy) * 0.12;
      glow.style.transform = `translate(${cx}px, ${cy}px) translate(-50%, -50%)`;
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  /* ---------- Reveal on scroll ---------- */
  const revealEls = document.querySelectorAll('.reveal');
  // Add stagger index inside project grid
  document.querySelectorAll('.projects .reveal').forEach((el, i) => {
    el.style.setProperty('--i', i);
  });

  if ('IntersectionObserver' in window && !prefersReduced) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('in-view');
          io.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' });
    revealEls.forEach((el) => io.observe(el));
  } else {
    revealEls.forEach((el) => el.classList.add('in-view'));
  }

  /* ---------- Counter animation ---------- */
  const counters = document.querySelectorAll('[data-count]');
  const animateCount = (el) => {
    const target = parseInt(el.dataset.count, 10);
    if (prefersReduced) { el.textContent = target; return; }
    const dur = 1200;
    const start = performance.now();
    const tick = (now) => {
      const p = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(target * eased);
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };
  if ('IntersectionObserver' in window) {
    const co = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          animateCount(entry.target);
          co.unobserve(entry.target);
        }
      });
    }, { threshold: 0.5 });
    counters.forEach((el) => co.observe(el));
  } else {
    counters.forEach(animateCount);
  }

  /* ---------- Project filters ---------- */
  const chips = document.querySelectorAll('.chip');
  const projects = document.querySelectorAll('.project');
  chips.forEach((chip) => {
    chip.addEventListener('click', () => {
      chips.forEach((c) => { c.classList.remove('is-active'); c.setAttribute('aria-selected', 'false'); });
      chip.classList.add('is-active');
      chip.setAttribute('aria-selected', 'true');
      const filter = chip.dataset.filter;
      projects.forEach((p) => {
        const tags = (p.dataset.tags || '').split(/\s+/);
        const show = filter === 'all' || tags.includes(filter);
        p.classList.toggle('is-hidden', !show);
      });
    });
  });

  /* ---------- Tilt effect on projects ---------- */
  if (!prefersReduced && window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
    projects.forEach((card) => {
      const visual = card.querySelector('.project-visual');
      if (!visual) return;
      card.addEventListener('pointermove', (e) => {
        const rect = card.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width - 0.5;
        const y = (e.clientY - rect.top) / rect.height - 0.5;
        visual.style.transform = `perspective(900px) rotateX(${y * -4}deg) rotateY(${x * 6}deg) translateZ(0)`;
      });
      card.addEventListener('pointerleave', () => {
        visual.style.transform = '';
      });
    });
  }

  /* ---------- Contact form validation ---------- */
  const form = document.getElementById('contact-form');
  if (form) {
    const status = form.querySelector('.form-status');
    const setError = (field, msg) => {
      const group = field.closest('.field');
      const err = group.querySelector('.error');
      if (msg) { group.classList.add('is-invalid'); err.textContent = msg; }
      else { group.classList.remove('is-invalid'); err.textContent = ''; }
    };

    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const name = form.name;
      const email = form.email;
      const message = form.message;
      let ok = true;

      if (!name.value.trim()) { setError(name, 'Please tell me your name.'); ok = false; } else setError(name, '');
      if (!/^\S+@\S+\.\S+$/.test(email.value)) { setError(email, 'That email looks off — try again.'); ok = false; } else setError(email, '');
      if (message.value.trim().length < 10) { setError(message, 'A couple of sentences helps me reply well.'); ok = false; } else setError(message, '');

      if (!ok) {
        form.querySelector('.is-invalid input, .is-invalid textarea')?.focus();
        return;
      }

      status.textContent = 'Sending…';
      setTimeout(() => {
        status.textContent = 'Thanks — your message is in. I will reply within two business days.';
        form.reset();
      }, 800);
    });
  }

  /* ---------- Year ---------- */
  const year = document.getElementById('year');
  if (year) year.textContent = new Date().getFullYear();
})();
