// Scrolling through time: scroll bars under the waveform and the spectrogram, and
// wheel / trackpad scrolling on the waveform, pitch view and spectrogram. All of them
// move the same time window, so the three views stay in sync.
//
// Uses globals from app.js (state, currentView, takeShown, requestDraw, LIVE_WINDOW_S).
'use strict';

/** The scrollable time window for what's on screen, or null (nothing loaded, or the mic is live). */
function timeline() {
  if (takeShown()) {
    const L = state.live, R = state.recording, v = currentView();
    const total = Math.max(LIVE_WINDOW_S, R.duration);
    return {
      t0: v.t0, t1: v.t1, total,
      set(t0) {
        L.viewT1 = Math.max(LIVE_WINDOW_S, Math.min(total, t0 + LIVE_WINDOW_S));
        L.userScroll = performance.now();   // pause following the playhead for a moment
      },
    };
  }
  if (state.source === 'file' && state.file && state.view) {
    const D = state.file.duration, v = state.view, span = v.t1 - v.t0;
    return {
      t0: v.t0, t1: v.t1, total: D,
      set(t0) { t0 = Math.max(0, Math.min(D - span, t0)); state.view = { t0, t1: t0 + span }; },
    };
  }
  return null;
}

function scrollTimeBy(dt) {
  const tl = timeline();
  if (!tl) return;
  tl.set(tl.t0 + dt);
  requestDraw();
}

function syncTimebars() {
  const tl = timeline();
  for (const bar of document.querySelectorAll('.timebar')) {
    const full = !tl || tl.t1 - tl.t0 >= tl.total - 1e-6;
    bar.classList.toggle('is-full', full);
    const thumb = bar.firstElementChild;
    if (full) { thumb.style.left = '0'; thumb.style.width = '100%'; continue; }
    thumb.style.left = `${(100 * tl.t0) / tl.total}%`;
    thumb.style.width = `${Math.max(2, (100 * (tl.t1 - tl.t0)) / tl.total)}%`;
  }
}

function initTimebars() {
  for (const bar of document.querySelectorAll('.timebar')) {
    let grab = null;
    bar.addEventListener('pointerdown', (e) => {
      const tl = timeline();
      if (!tl || bar.classList.contains('is-full')) return;
      const r = bar.getBoundingClientRect(), span = tl.t1 - tl.t0;
      const t = ((e.clientX - r.left) / r.width) * tl.total;
      // On the thumb: drag it. On the track: centre the window there, then drag.
      if (e.target !== bar.firstElementChild) tl.set(t - span / 2);
      grab = { x: e.clientX, t0: timeline().t0 };
      bar.setPointerCapture(e.pointerId);
      requestDraw();
    });
    bar.addEventListener('pointermove', (e) => {
      if (!grab) return;
      const tl = timeline(); if (!tl) return;
      const r = bar.getBoundingClientRect();
      tl.set(grab.t0 + ((e.clientX - grab.x) / r.width) * tl.total);
      requestDraw();
    });
    const end = () => { grab = null; };
    bar.addEventListener('pointerup', end);
    bar.addEventListener('pointercancel', end);
  }
}
