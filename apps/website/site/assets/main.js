/* AlgoGators — shared interactions */
(() => {
  'use strict';

  /* ── nav: solid background after scrolling past the hero top ── */
  const nav = document.getElementById('siteNav');
  if (nav) {
    const onScroll = () => nav.classList.toggle('scrolled', window.scrollY > 24);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
  }

  /* ── mobile menu ── */
  const burger = document.getElementById('navBurger');
  const menu = document.getElementById('mobileMenu');
  if (burger && menu) {
    burger.addEventListener('click', () => {
      const open = menu.classList.toggle('open');
      burger.classList.toggle('open', open);
      burger.setAttribute('aria-expanded', open);
      document.body.classList.toggle('menu-open', open);
      document.body.style.overflow = open ? 'hidden' : '';
    });
  }

  /* ── scroll reveals ── */
  const revealer = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (e.isIntersecting) {
        e.target.classList.add('in');
        revealer.unobserve(e.target);
      }
    });
  }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });
  document.querySelectorAll('[data-reveal]').forEach((el) => revealer.observe(el));

  /* ── count-up stats ── */
  const animateCount = (el) => {
    const target = parseFloat(el.dataset.count);
    const prefix = el.dataset.prefix || '';
    const suffix = el.dataset.suffix || '';
    const dur = 1600;
    const t0 = performance.now();
    const tick = (t) => {
      const p = Math.min((t - t0) / dur, 1);
      const eased = 1 - Math.pow(1 - p, 4);
      el.textContent = prefix + Math.round(target * eased) + suffix;
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };
  const counter = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (e.isIntersecting) {
        animateCount(e.target);
        counter.unobserve(e.target);
      }
    });
  }, { threshold: 0.5 });
  document.querySelectorAll('[data-count]').forEach((el) => counter.observe(el));

  /* ── contact form → Formspree ── */
  const form = document.getElementById('contactForm');
  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const id = form.dataset.formspreeId;
      if (!id || id === 'YOUR_FORM_ID') {
        console.warn('Formspree ID not set — update data-formspree-id on #contactForm');
        form.classList.add('sent');
        return;
      }
      const btn = form.querySelector('[type="submit"]');
      btn.disabled = true;
      try {
        const res = await fetch(`https://formspree.io/f/${id}`, {
          method: 'POST',
          headers: { 'Accept': 'application/json' },
          body: new FormData(form),
        });
        if (res.ok) {
          form.classList.add('sent');
          form.reset();
        } else {
          btn.disabled = false;
          alert('Something went wrong — please try again.');
        }
      } catch {
        btn.disabled = false;
        alert('Network error — please try again.');
      }
    });
  }

  /* ── hero terminal clock + market-hours indicator ── */
  const clock = document.querySelector('[data-clock]');
  const market = document.querySelector('[data-market]');
  if (clock) {
    const tickClock = () => {
      const et = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' }));
      clock.textContent = et.toLocaleTimeString('en-US', { hour12: false }) + ' ET';
      if (market) {
        // NYSE regular session: Mon–Fri 9:30–16:00 ET (holidays not accounted for)
        const mins = et.getHours() * 60 + et.getMinutes();
        const open = et.getDay() >= 1 && et.getDay() <= 5 && mins >= 570 && mins < 960;
        market.textContent = open ? '● Markets Open' : '○ Markets Closed';
        market.classList.toggle('closed', !open);
      }
    };
    tickClock();
    setInterval(tickClock, 1000);
  }

  /* ── footer year ── */
  document.querySelectorAll('[data-year]').forEach((el) => {
    el.textContent = new Date().getFullYear();
  });
})();


/* ── Application window ───────────────────────────────────────────────────
   The only thing to edit for a new intake. `closes` drives which copy shows;
   `term` and `closesLabel` are written into every place the page names them,
   so the date and the semester are never typed into markup by hand.
   Elements tagged [data-when="open"|"closed"] flip on the deadline with no
   redeploy; closed variants ship `hidden`, so a visitor with JS disabled sees
   the open copy rather than both and there is no flash on load. Reads the
   visitor's clock, which is fine for an application deadline.              */
var APPLICATIONS = {
  term: 'Fall 2026',
  closes: '2026-09-05T04:00:00Z',
  closesLabel: 'September 4 · Midnight',
};

(function applicationWindow() {
  var open = Date.now() < Date.parse(APPLICATIONS.closes);
  document.querySelectorAll('[data-when]').forEach(function (el) {
    el.hidden = (el.dataset.when === 'open') !== open;
  });
  document.querySelectorAll('[data-deadline]').forEach(function (el) {
    el.textContent = APPLICATIONS.closesLabel;
  });
  document.querySelectorAll('[data-term]').forEach(function (el) {
    el.textContent = APPLICATIONS.term;
  });
})();
