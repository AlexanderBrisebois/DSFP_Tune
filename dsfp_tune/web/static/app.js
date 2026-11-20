// DSFP Tune dashboard.
//
// The local Python server does the pitch detection (dsfp_tune YIN) and keeps the snap
// settings; this page decodes audio for the waveform and playback, streams microphone
// samples to the server, and draws everything on canvases.
'use strict';

const $ = (id) => document.getElementById(id);
const LIVE_WINDOW_S = 8;          // seconds of history shown while the mic is on
const HOLD_S = 0.15;              // keep showing the last pitch this long after it drops out

const state = {
  theory: null,
  settings: { mode: 'chromatic', tonic: 0, scale: 'major' },
  source: 'file',
  file: null,      // { name, duration, sampleRate, samples, peaks, track, midi }
  live: null,      // see startMic()
  view: null,      // { t0, t1 } for file mode
  playing: false,
  colors: {},
};

// ---------------------------------------------------------------------------
// Music helpers
// ---------------------------------------------------------------------------
const freqToMidi = (f) => 69 + 12 * Math.log2(f / 440);
const mod12 = (n) => ((n % 12) + 12) % 12;

function keyInfo() {
  const k = state.theory && state.theory.keys[`${state.settings.tonic}:${state.settings.scale}`];
  return k || { name: '', pitch_classes: [], note_names: [], uses_flats: false };
}

function keySet() { return new Set(keyInfo().pitch_classes); }

function noteName(midi, withOctave = true) {
  const m = Math.round(midi);
  const k = keyInfo();
  // Use the key's own spelling for its notes (Eb rather than D# in Bb major).
  const i = k.pitch_classes.indexOf(mod12(m));
  let name;
  if (i >= 0 && k.note_names[i]) name = k.note_names[i];
  else {
    const names = state.theory ? (k.uses_flats ? state.theory.note_names_flat : state.theory.note_names_sharp)
      : ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
    name = names[mod12(m)];
  }
  if (!withOctave) return name;
  // Octave follows the written letter (B#3 is C4, Cb4 is B3).
  let octave = Math.floor(m / 12) - 1;
  if (name.startsWith('B') && mod12(m) === 0) octave -= 1;
  if (name.startsWith('C') && mod12(m) === 11) octave += 1;
  return name + octave;
}

/** The note a pitch would be snapped to under the current settings, or null. */
function snapTarget(midi) {
  const mode = state.settings.mode;
  if (mode === 'off' || !Number.isFinite(midi)) return null;
  if (mode === 'chromatic') return Math.round(midi);
  const pcs = keySet();
  if (!pcs.size) return Math.round(midi);
  let best = null;
  for (let m = Math.floor(midi) - 6; m <= Math.ceil(midi) + 6; m++) {
    if (pcs.has(mod12(m)) && (best === null || Math.abs(m - midi) < Math.abs(best - midi))) best = m;
  }
  return best;
}

// ---------------------------------------------------------------------------
// Server
// ---------------------------------------------------------------------------
async function api(path, options = {}) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

let saveTimer = null;
function saveSettings() {
  const el = $('saveState');
  el.textContent = 'Saving…';
  el.classList.remove('is-error');
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      state.settings = await api('/api/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(state.settings),
      });
      el.textContent = 'Saved';
    } catch (err) {
      el.textContent = 'Not saved';
      el.classList.add('is-error');
      el.title = err.message;
    }
  }, 250);
}

// ---------------------------------------------------------------------------
// Settings UI
// ---------------------------------------------------------------------------
function buildSettingsUI() {
  const t = state.theory;
  const tonic = $('tonicSelect');
  // Show both spellings for the black keys, e.g. "C# / Db".
  tonic.innerHTML = t.note_names_sharp.map((s, i) => {
    const f = t.note_names_flat[i];
    return `<option value="${i}">${s === f ? s : `${s} / ${f}`}</option>`;
  }).join('');
  $('scaleSelect').innerHTML = t.scales.map((s) => `<option value="${s.id}">${s.label}</option>`).join('');

  $('snapModes').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-mode]');
    if (!b) return;
    state.settings.mode = b.dataset.mode;
    settingsChanged();
  });
  tonic.addEventListener('change', () => { state.settings.tonic = +tonic.value; settingsChanged(); });
  $('scaleSelect').addEventListener('change', (e) => { state.settings.scale = e.target.value; settingsChanged(); });
}

function syncSettingsUI() {
  const s = state.settings;
  for (const b of $('snapModes').querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.mode === s.mode));
  $('tonicSelect').value = String(s.tonic);
  $('scaleSelect').value = s.scale;
  const keyMode = s.mode === 'key';
  $('keyFields').classList.toggle('is-disabled', !keyMode);
  const k = keyInfo();
  $('scaleChips').innerHTML = k.note_names
    .map((n, i) => `<span class="chip${i === 0 ? ' chip--tonic' : ''}" title="degree ${i + 1}">${n}</span>`).join('');
  $('scaleChips').classList.toggle('is-dim', !keyMode);
  $('scaleChips').title = keyMode ? k.name : `${k.name} (used for the in-key colouring)`;
}

function settingsChanged() {
  syncSettingsUI();
  saveSettings();
  if (state.file) state.file.hist = histogram(state.file.midi);
  drawAll();
}

// ---------------------------------------------------------------------------
// Canvas helpers
// ---------------------------------------------------------------------------
function readColors() {
  const cs = getComputedStyle(document.documentElement);
  const v = (n) => cs.getPropertyValue(n).trim();
  state.colors = {
    text: v('--text'), muted: v('--muted'), faint: v('--faint'), accent: v('--accent'), off: v('--off-key'),
    target: v('--target'), laneIn: v('--lane-in'), laneTonic: v('--lane-tonic'), grid: v('--grid'),
    border: v('--border'), borderStrong: v('--border-strong'), canvas: v('--canvas'), panel2: v('--panel-2'),
    danger: v('--danger'),
  };
}

function prep(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

function fmtTime(s) {
  if (!Number.isFinite(s)) s = 0;
  const m = Math.floor(s / 60);
  return `${m}:${(s - 60 * m).toFixed(1).padStart(4, '0')}`;
}

function niceStep(span, px, minPx = 70) {
  const raw = span / Math.max(1, px / minPx);
  const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
  return steps.find((s) => s >= raw) || 600;
}

/** Current time window and data for whichever source is showing. */
function currentView() {
  if (state.source === 'mic' && state.live) {
    const t1 = Math.max(LIVE_WINDOW_S, state.live.samples / state.live.sampleRate);
    return { t0: t1 - LIVE_WINDOW_S, t1, live: true };
  }
  if (state.source === 'file' && state.file) return { ...state.view, live: false };
  return null;
}

// ---------------------------------------------------------------------------
// Waveform
// ---------------------------------------------------------------------------
const PEAK_BLOCK = 256;

function buildPeaks(x) {
  const n = Math.ceil(x.length / PEAK_BLOCK);
  const mn = new Float32Array(n), mx = new Float32Array(n);
  for (let b = 0; b < n; b++) {
    let lo = 1, hi = -1;
    const end = Math.min(x.length, (b + 1) * PEAK_BLOCK);
    for (let i = b * PEAK_BLOCK; i < end; i++) { const v = x[i]; if (v < lo) lo = v; if (v > hi) hi = v; }
    mn[b] = lo; mx[b] = hi;
  }
  return { mn, mx };
}

function drawWave() {
  const { ctx, w, h } = prep($('waveCanvas'));
  const c = state.colors;
  const view = currentView();
  $('waveEmpty').hidden = !!view;
  if (!view) return;
  const mid = h / 2, amp = h / 2 - 6;

  ctx.strokeStyle = c.grid;
  ctx.beginPath(); ctx.moveTo(0, mid + 0.5); ctx.lineTo(w, mid + 0.5); ctx.stroke();

  ctx.fillStyle = c.accent;
  ctx.globalAlpha = 0.85;
  const span = view.t1 - view.t0;
  if (view.live) {
    const L = state.live, sr = L.sampleRate, ring = L.ring;
    const endSample = L.samples, startSample = endSample - LIVE_WINDOW_S * sr;
    for (let x = 0; x < w; x++) {
      const a = Math.floor(startSample + (x / w) * LIVE_WINDOW_S * sr);
      const b = Math.floor(startSample + ((x + 1) / w) * LIVE_WINDOW_S * sr);
      let lo = 0, hi = 0;
      for (let i = Math.max(a, endSample - ring.length, 0); i < b; i++) {
        const v = ring[i % ring.length]; if (v < lo) lo = v; if (v > hi) hi = v;
      }
      if (hi > lo) ctx.fillRect(x, mid - hi * amp, 1, Math.max(1, (hi - lo) * amp));
    }
  } else {
    const F = state.file, sr = F.sampleRate, x0 = F.samples;
    for (let x = 0; x < w; x++) {
      const a = Math.max(0, Math.floor((view.t0 + (x / w) * span) * sr));
      const b = Math.min(x0.length, Math.floor((view.t0 + ((x + 1) / w) * span) * sr));
      if (b <= a) continue;
      let lo = 1, hi = -1;
      if (b - a > PEAK_BLOCK * 2) {
        for (let k = Math.floor(a / PEAK_BLOCK); k < Math.ceil(b / PEAK_BLOCK); k++) {
          if (F.peaks.mn[k] < lo) lo = F.peaks.mn[k]; if (F.peaks.mx[k] > hi) hi = F.peaks.mx[k];
        }
      } else {
        for (let i = a; i < b; i++) { const v = x0[i]; if (v < lo) lo = v; if (v > hi) hi = v; }
      }
      ctx.fillRect(x, mid - hi * amp, 1, Math.max(1, (hi - lo) * amp));
    }
    drawPlayhead(ctx, w, h, view);
  }
  ctx.globalAlpha = 1;
}

function drawPlayhead(ctx, w, h, view, x0 = 0) {
  const t = $('player').currentTime;
  if (t < view.t0 || t > view.t1) return;
  const x = x0 + ((t - view.t0) / (view.t1 - view.t0)) * (w - x0);
  ctx.globalAlpha = 1;
  ctx.fillStyle = state.colors.text;
  ctx.fillRect(Math.round(x) - 1, 0, 2, h);
}

// ---------------------------------------------------------------------------
// Pitch roll (time vs note, with the key's notes as lanes)
// ---------------------------------------------------------------------------
const GUTTER = 46, AXIS_H = 20;

function rollFrames(view) {
  // -> { times(i), midi[], conf[] } accessor for the source on screen
  if (view.live) {
    const L = state.live;
    return { n: L.midi.length, t: (i) => L.times[i], midi: L.midi, conf: L.conf };
  }
  const F = state.file, tr = F.track;
  return { n: F.midi.length, t: (i) => tr.t0 + i * tr.hop_seconds, midi: F.midi, conf: tr.confidence };
}

function rollRange(view) {
  if (view.live) {
    const L = state.live;
    // Follow the singer: aim at the last few seconds of voiced pitch, move smoothly.
    const recent = [];
    for (let i = L.midi.length - 1; i >= 0 && L.times[i] > view.t1 - 4; i--) if (L.midi[i] !== null) recent.push(L.midi[i]);
    let lo = 45, hi = 72;
    if (recent.length > 5) {
      recent.sort((a, b) => a - b);
      lo = recent[Math.floor(recent.length * 0.05)] - 4; hi = recent[Math.floor(recent.length * 0.95)] + 4;
    }
    if (hi - lo < 15) { const m = (hi + lo) / 2; lo = m - 7.5; hi = m + 7.5; }
    const r = L.range || { lo, hi };
    r.lo += (lo - r.lo) * 0.08; r.hi += (hi - r.hi) * 0.08;
    L.range = r;
    return r;
  }
  return state.file.range;
}

function drawRoll() {
  const { ctx, w, h } = prep($('rollCanvas'));
  const c = state.colors;
  const view = currentView();
  const plotH = h - AXIS_H, plotW = w - GUTTER;
  const { lo, hi } = view ? rollRange(view) : { lo: 48, hi: 72 };
  const y = (m) => plotH - ((m - lo) / (hi - lo)) * plotH;
  const pcs = keySet(), tonic = state.settings.tonic;
  const laneH = plotH / (hi - lo);

  // Lanes and note labels.
  ctx.font = '600 11px Inter, ui-sans-serif, system-ui, sans-serif';
  ctx.textBaseline = 'middle';
  for (let m = Math.ceil(lo); m <= Math.floor(hi); m++) {
    const pc = mod12(m), yc = y(m);
    if (pcs.has(pc)) {
      ctx.fillStyle = pc === tonic ? c.laneTonic : c.laneIn;
      ctx.fillRect(GUTTER, yc - laneH / 2, plotW, laneH);
    }
    if (pc === 0) {
      ctx.fillStyle = c.grid;
      ctx.fillRect(GUTTER, Math.round(yc + laneH / 2), plotW, 1);
    }
    if ((laneH >= 12 || pc === 0 || (laneH >= 7 && pcs.has(pc))) && yc > 6 && yc < plotH - 4) {
      ctx.fillStyle = pcs.has(pc) ? (pc === tonic ? c.accent : c.text) : c.faint;
      ctx.textAlign = 'right';
      ctx.fillText(noteName(m), GUTTER - 8, yc);
    }
  }
  ctx.fillStyle = c.border;
  ctx.fillRect(GUTTER, 0, 1, plotH);
  ctx.fillRect(GUTTER, plotH, plotW, 1);

  if (!view) {
    ctx.fillStyle = c.faint; ctx.textAlign = 'center';
    ctx.fillText('The detected pitch will be drawn here, against the notes of the key', GUTTER + plotW / 2, plotH / 2);
    return;
  }

  // Time axis.
  const span = view.t1 - view.t0, X = (t) => GUTTER + ((t - view.t0) / span) * plotW;
  const step = niceStep(span, plotW);
  ctx.fillStyle = c.muted; ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
  for (let t = Math.ceil(view.t0 / step) * step; t <= view.t1 + 1e-9; t += step) {
    const x = X(t);
    ctx.fillStyle = c.grid; ctx.fillRect(Math.round(x), 0, 1, plotH);
    ctx.fillStyle = c.muted;
    ctx.fillText(view.live ? `${(t - view.t1).toFixed(step < 1 ? 1 : 0)} s` : axisTime(t, step), x, h - 5);
  }

  // Pitch and snap target.
  const fr = rollFrames(view);
  ctx.save();
  ctx.beginPath(); ctx.rect(GUTTER + 1, 0, plotW, plotH); ctx.clip();
  const first = view.live ? 0 : Math.max(0, Math.floor((view.t0 - state.file.track.t0) / state.file.track.hop_seconds) - 1);
  const stride = view.live ? 1 : Math.max(1, Math.floor(span / state.file.track.hop_seconds / (plotW * 2)));
  const mode = state.settings.mode;

  // target (dashed, behind)
  if (mode !== 'off') {
    ctx.strokeStyle = c.target; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]); ctx.globalAlpha = 0.9;
    ctx.beginPath();
    let prevT = null, pen = false;
    for (let i = first; i < fr.n; i += stride) {
      const t = fr.t(i); if (t > view.t1) break;
      const m = fr.midi[i];
      if (m === null || m < lo - 1 || m > hi + 1) { pen = false; continue; }
      const tg = snapTarget(m), x = X(t), yy = y(tg);
      if (!pen) { ctx.moveTo(x, yy); pen = true; } else { if (tg !== prevT) ctx.lineTo(x, y(prevT)); ctx.lineTo(x, yy); }
      prevT = tg;
    }
    ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
  }

  // detected pitch: one path per colour so in-key and out-of-key read at a glance
  ctx.lineWidth = 2.2; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
  for (const inKey of [true, false]) {
    ctx.strokeStyle = inKey ? c.accent : c.off;
    ctx.beginPath();
    let pen = false;
    for (let i = first; i < fr.n; i += stride) {
      const t = fr.t(i); if (t > view.t1 + 0.05) break;
      const m = fr.midi[i];
      const ok = m !== null && pcs.has(mod12(Math.round(m))) === inKey;
      if (!ok) { pen = false; continue; }
      const x = X(t), yy = y(m);
      if (!pen) { ctx.moveTo(x, yy); ctx.lineTo(x + 0.01, yy); pen = true; } else ctx.lineTo(x, yy);
      // let the segment reach the next frame so colours join up
      const j = i + stride;
      if (j < fr.n && fr.midi[j] !== null && pcs.has(mod12(Math.round(fr.midi[j]))) !== inKey) ctx.lineTo(X(fr.t(j)), y(fr.midi[j]));
    }
    ctx.stroke();
  }
  ctx.restore();

  if (!view.live) drawPlayhead(ctx, w, plotH, view, GUTTER);

  // Hover read-out.
  if (rollHover && !view.live) {
    const t = view.t0 + ((rollHover.x - GUTTER) / plotW) * span;
    const i = Math.round((t - state.file.track.t0) / state.file.track.hop_seconds);
    const m = state.file.midi[i];
    ctx.fillStyle = c.muted; ctx.fillRect(Math.round(rollHover.x), 0, 1, plotH);
    const label = m == null ? `${fmtTime(t)}  no pitch` : `${fmtTime(t)}  ${noteName(m)} ${fmtCents(m)}`;
    ctx.font = '600 11px Inter, ui-sans-serif, system-ui, sans-serif';
    const tw = ctx.measureText(label).width + 14;
    const bx = Math.min(w - tw - 4, rollHover.x + 8);
    ctx.fillStyle = c.panel2; ctx.strokeStyle = c.borderStrong;
    ctx.beginPath(); ctx.roundRect(bx, 6, tw, 22, 6); ctx.fill(); ctx.stroke();
    ctx.fillStyle = c.text; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(label, bx + 7, 17);
  }
}

function axisTime(t, step) {
  const m = Math.floor(t / 60 + 1e-9), sec = t - 60 * m;
  const ss = step < 1 ? sec.toFixed(1) : Math.round(sec).toString();
  return m ? `${m}:${ss.padStart(step < 1 ? 4 : 2, '0')}` : `${ss} s`;
}

const fmtCents = (m) => { const c = Math.round(100 * (m - Math.round(m))); return `${c >= 0 ? '+' : '−'}${Math.abs(c)}¢`; };

// ---------------------------------------------------------------------------
// Pitch-class histogram ("where the notes land")
// ---------------------------------------------------------------------------
function histogram(midi, from = 0) {
  const h = new Float64Array(12);
  for (let i = from; i < midi.length; i++) if (midi[i] !== null) h[mod12(Math.round(midi[i]))] += 1;
  return h;
}

function drawKeys() {
  const { ctx, w, h } = prep($('keysCanvas'));
  const c = state.colors;
  const pcs = keySet(), k = keyInfo(), tonic = state.settings.tonic;
  const hist = state.source === 'mic' ? (state.live && state.live.hist) : (state.file && state.file.hist);
  const total = hist ? hist.reduce((a, b) => a + b, 0) : 0;
  const maxv = hist ? Math.max(...hist, 1) : 1;
  const cur = currentPitch();
  const curPc = cur ? mod12(Math.round(cur.midi)) : null;

  const cellW = w / 12, labelH = 40, barTop = 22, barH = h - labelH - barTop - 4;
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  // Start the strip at the tonic so the scale reads left to right.
  for (let j = 0; j < 12; j++) {
    const pc = mod12(tonic + j), x = j * cellW, inKey = pcs.has(pc);
    const share = total ? hist[pc] / total : 0;
    // bar
    const bh = total ? (hist[pc] / maxv) * barH : 0;
    ctx.fillStyle = inKey ? c.accent : c.off;
    ctx.globalAlpha = inKey ? 0.85 : 0.75;
    if (bh > 0) { ctx.beginPath(); ctx.roundRect(x + cellW * 0.2, barTop + barH - bh, cellW * 0.6, bh, 4); ctx.fill(); }
    ctx.globalAlpha = 1;
    if (total && share >= 0.005) {
      ctx.fillStyle = c.muted; ctx.font = '600 10px Inter, ui-sans-serif, system-ui, sans-serif';
      ctx.fillText(`${Math.round(share * 100)}%`, x + cellW / 2, barTop + barH - bh - 9);
    }
    // note cell
    const cy = h - labelH;
    ctx.fillStyle = inKey ? (pc === tonic ? c.laneTonic : c.laneIn) : 'transparent';
    ctx.beginPath(); ctx.roundRect(x + 3, cy, cellW - 6, labelH - 4, 7); ctx.fill();
    ctx.strokeStyle = pc === curPc ? c.text : (pc === tonic ? c.accent : c.border);
    ctx.lineWidth = pc === curPc ? 2 : 1;
    ctx.stroke();
    ctx.lineWidth = 1;
    const di = k.pitch_classes.indexOf(pc);
    ctx.fillStyle = inKey ? c.text : c.faint;
    ctx.font = `${inKey ? 750 : 600} 13px Inter, ui-sans-serif, system-ui, sans-serif`;
    ctx.fillText(noteName(pc + 60, false), x + cellW / 2, cy + 13);
    ctx.font = '600 10px Inter, ui-sans-serif, system-ui, sans-serif';
    ctx.fillStyle = inKey ? (pc === tonic ? c.accent : c.muted) : c.faint;
    ctx.fillText(cellW >= 64 ? (di >= 0 ? `degree ${di + 1}` : 'not in key') : (di >= 0 ? `${di + 1}` : '·'), x + cellW / 2, cy + 27);
  }
  ctx.fillStyle = c.border; ctx.fillRect(0, barTop + barH, w, 1);

  let inShare = 0;
  if (total) for (const pc of pcs) inShare += hist[pc];
  $('histHint').textContent = total
    ? `${Math.round((100 * inShare) / total)}% of pitched time is in ${k.name}, all octaves`
    : `time spent on each note of ${k.name}, all octaves`;
}

// ---------------------------------------------------------------------------
// Read-out (current note, cents needle, snap target)
// ---------------------------------------------------------------------------
function currentPitch() {
  if (state.source === 'mic' && state.live) {
    const L = state.live, now = L.samples / L.sampleRate;
    for (let i = L.midi.length - 1; i >= 0 && L.times[i] > now - HOLD_S - 0.1; i--) {
      if (L.midi[i] !== null) return { midi: L.midi[i], f0: L.f0[i] };
    }
    return null;
  }
  if (state.source === 'file' && state.file) {
    const tr = state.file.track;
    const i = Math.round(($('player').currentTime - tr.t0) / tr.hop_seconds);
    for (let j = i; j >= 0 && j > i - HOLD_S / tr.hop_seconds; j--) {
      if (state.file.midi[j] != null) return { midi: state.file.midi[j], f0: tr.f0[j] };
    }
  }
  return null;
}

function drawReadout() {
  const p = currentPitch();
  const needle = $('needle');
  if (!p) {
    $('noteName').textContent = '–'; $('noteHz').textContent = 'no pitch';
    $('noteCents').textContent = 'cents off the nearest note';
    $('targetName').textContent = '–'; $('targetShift').innerHTML = '&nbsp;';
    needle.style.left = '50%'; needle.className = 'tuner__needle';
    return;
  }
  const near = Math.round(p.midi), cents = 100 * (p.midi - near);
  $('noteName').textContent = noteName(near);
  $('noteHz').textContent = `${p.f0.toFixed(1)} Hz`;
  $('noteCents').textContent = `${cents >= 0 ? '+' : '−'}${Math.abs(cents).toFixed(0)} cents ${cents >= 0 ? 'sharp of' : 'flat of'} ${noteName(near)}`;
  needle.style.left = `${50 + Math.max(-50, Math.min(50, cents))}%`;
  needle.className = `tuner__needle ${keySet().has(mod12(near)) ? 'is-in' : 'is-out'}`;
  const tg = snapTarget(p.midi);
  if (tg === null) {
    $('targetName').textContent = 'Off'; $('targetShift').textContent = 'snapping is off';
  } else {
    const shift = 100 * (tg - p.midi);
    $('targetName').textContent = noteName(tg);
    $('targetShift').textContent = `shift ${shift >= 0 ? '+' : '−'}${Math.abs(shift).toFixed(0)} cents`;
  }
}

function drawAll() {
  drawWave(); drawRoll(); drawKeys(); drawReadout();
  const F = state.file;
  $('timeLabel').textContent = `${fmtTime($('player').currentTime)} / ${fmtTime(F ? F.duration : 0)}`;
}

let rafPending = false;
function requestDraw() {
  if (rafPending) return;
  rafPending = true;
  requestAnimationFrame(() => { rafPending = false; drawAll(); });
}

function loop() {
  if (state.playing || (state.source === 'mic' && state.live && state.live.running)) drawAll();
  requestAnimationFrame(loop);
}

// ---------------------------------------------------------------------------
// File input
// ---------------------------------------------------------------------------
async function loadFile(file) {
  stopMic();
  setSource('file');
  const player = $('player');
  player.pause();
  if (state.file && state.file.url) URL.revokeObjectURL(state.file.url);
  state.file = null;
  $('fileInfo').hidden = false;
  $('fileName').textContent = file.name;
  $('fileMeta').textContent = 'Decoding…';
  $('fileMeta').classList.remove('hint', 'is-error');
  $('playButton').disabled = true;
  $('busy').hidden = false; $('busyText').textContent = 'Detecting pitch…';
  requestDraw();

  const bytes = await file.arrayBuffer();
  const analysis = api('/api/analyze', {
    method: 'POST', headers: { 'X-Filename': file.name, 'Content-Type': 'application/octet-stream' }, body: bytes,
  });
  try {
    const actx = new (window.AudioContext || window.webkitAudioContext)();
    const audio = await actx.decodeAudioData(bytes.slice(0));
    actx.close();
    const samples = audio.numberOfChannels > 1 ? mixDown(audio) : audio.getChannelData(0);
    const track = await analysis;
    const midi = track.f0.map((f) => (f ? freqToMidi(f) : null));
    const url = URL.createObjectURL(file);
    player.src = url;
    state.file = {
      name: file.name, url, duration: audio.duration, sampleRate: audio.sampleRate, samples,
      peaks: buildPeaks(samples), track, midi, hist: histogram(midi), range: fileRange(midi),
    };
    state.view = { t0: 0, t1: audio.duration };
    $('fileMeta').textContent = `${fmtTime(audio.duration)} · ${track.sample_rate / 1000} kHz · `
      + `${Math.round(track.voiced_fraction * 100)}% pitched · analysed in ${track.analysis_seconds.toFixed(1)} s`;
    $('playButton').disabled = false;
    $('viewHint').textContent = 'scroll to zoom · drag to pan · double-click to reset · click to seek';
  } catch (err) {
    $('fileMeta').textContent = `Couldn't load this file: ${err.message}`;
    $('fileMeta').classList.add('hint', 'is-error');
  } finally {
    $('busy').hidden = true;
    requestDraw();
  }
}

function mixDown(buf) {
  const out = new Float32Array(buf.length);
  for (let c = 0; c < buf.numberOfChannels; c++) {
    const d = buf.getChannelData(c);
    for (let i = 0; i < d.length; i++) out[i] += d[i] / buf.numberOfChannels;
  }
  return out;
}

function fileRange(midi) {
  const v = midi.filter((m) => m !== null).sort((a, b) => a - b);
  if (v.length < 5) return { lo: 48, hi: 72 };
  let lo = Math.floor(v[Math.floor(v.length * 0.02)]) - 2, hi = Math.ceil(v[Math.floor(v.length * 0.98)]) + 2;
  if (hi - lo < 14) { const m = (hi + lo) / 2; lo = Math.floor(m - 7); hi = Math.ceil(m + 7); }
  return { lo: lo - 0.5, hi: hi + 0.5 };
}

// Zoom / pan / seek on the waveform and the pitch roll.
let drag = null, rollHover = null;
function attachViewControls(canvas, x0) {
  const timeAt = (e) => {
    const r = canvas.getBoundingClientRect(), v = state.view;
    const frac = Math.max(0, Math.min(1, (e.clientX - r.left - x0) / (r.width - x0)));
    return v.t0 + frac * (v.t1 - v.t0);
  };
  canvas.addEventListener('wheel', (e) => {
    if (state.source !== 'file' || !state.file) return;
    e.preventDefault();
    const v = state.view, D = state.file.duration;
    if (Math.abs(e.deltaX) > Math.abs(e.deltaY) || e.shiftKey) {
      const d = ((e.shiftKey ? e.deltaY : e.deltaX) / canvas.clientWidth) * (v.t1 - v.t0);
      const t0 = Math.max(0, Math.min(D - (v.t1 - v.t0), v.t0 + d));
      state.view = { t0, t1: t0 + (v.t1 - v.t0) };
    } else {
      const t = timeAt(e), f = Math.exp(e.deltaY * 0.0015);
      const span = Math.max(0.25, Math.min(D, (v.t1 - v.t0) * f));
      let t0 = t - ((t - v.t0) / (v.t1 - v.t0)) * span;
      t0 = Math.max(0, Math.min(D - span, t0));
      state.view = { t0, t1: t0 + span };
    }
    requestDraw();
  }, { passive: false });
  canvas.addEventListener('pointerdown', (e) => {
    if (state.source !== 'file' || !state.file) return;
    drag = { x: e.clientX, view: { ...state.view }, moved: false, canvas };
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointermove', (e) => {
    if (canvas === $('rollCanvas')) {
      const r = canvas.getBoundingClientRect();
      rollHover = state.file && state.source === 'file' && e.clientX - r.left > x0 ? { x: e.clientX - r.left } : null;
      requestDraw();
    }
    if (!drag || drag.canvas !== canvas) return;
    const dx = e.clientX - drag.x;
    if (Math.abs(dx) > 3) drag.moved = true;
    if (!drag.moved) return;
    const span = drag.view.t1 - drag.view.t0, D = state.file.duration;
    const t0 = Math.max(0, Math.min(D - span, drag.view.t0 - (dx / (canvas.clientWidth - x0)) * span));
    state.view = { t0, t1: t0 + span };
    requestDraw();
  });
  canvas.addEventListener('pointerleave', () => { if (rollHover) { rollHover = null; requestDraw(); } });
  canvas.addEventListener('pointerup', (e) => {
    if (drag && !drag.moved) { $('player').currentTime = timeAt(e); requestDraw(); }
    drag = null;
  });
  canvas.addEventListener('dblclick', () => {
    if (state.file) { state.view = { t0: 0, t1: state.file.duration }; requestDraw(); }
  });
}

function togglePlay() {
  const p = $('player');
  if (!state.file) return;
  if (p.paused) p.play(); else p.pause();
}

// ---------------------------------------------------------------------------
// Microphone (samples stream to the server's YinStream)
// ---------------------------------------------------------------------------
async function startMic() {
  $('player').pause();
  const hint = $('micHint');
  hint.classList.remove('is-error');
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    hint.textContent = 'This browser can only use the microphone on http://localhost (or https).';
    hint.classList.add('is-error');
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
    });
  } catch (err) {
    hint.textContent = `Microphone blocked (${err.name}). Allow it for this page in the browser's site settings.`;
    hint.classList.add('is-error');
    return;
  }
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const sr = ctx.sampleRate;
  let session;
  try {
    session = await api('/api/live/start', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sample_rate: sr }),
    });
  } catch (err) {
    stream.getTracks().forEach((t) => t.stop()); ctx.close();
    hint.textContent = `Local server error: ${err.message}`; hint.classList.add('is-error');
    return;
  }
  const L = state.live = {
    running: true, ctx, stream, id: session.id, sampleRate: sr, hop: session.hop_seconds,
    frame: session.frame_seconds, ring: new Float32Array(Math.ceil(sr * (LIVE_WINDOW_S + 1))), samples: 0,
    pending: [], pendingLen: 0, inflight: false, estimates: 0,
    times: [], midi: [], f0: [], conf: [], hist: new Float64Array(12), level: 0, range: null,
  };
  const src = ctx.createMediaStreamSource(stream);
  const mute = ctx.createGain(); mute.gain.value = 0; mute.connect(ctx.destination);
  const onChunk = (chunk) => {
    if (!L.running) return;
    for (let i = 0; i < chunk.length; i++) L.ring[(L.samples + i) % L.ring.length] = chunk[i];
    L.samples += chunk.length;
    let s = 0; for (let i = 0; i < chunk.length; i++) s += chunk[i] * chunk[i];
    L.level = Math.max(Math.sqrt(s / chunk.length), L.level * 0.85);
    L.pending.push(chunk); L.pendingLen += chunk.length;
    pump();
  };
  try {
    await ctx.audioWorklet.addModule('mic-worklet.js');
    const node = new AudioWorkletNode(ctx, 'mic-tap');
    node.port.onmessage = (e) => onChunk(e.data);
    src.connect(node); node.connect(mute);
    L.node = node;
  } catch (err) {
    // Older browsers: ScriptProcessorNode.
    const node = ctx.createScriptProcessor(1024, 1, 1);
    node.onaudioprocess = (e) => onChunk(new Float32Array(e.inputBuffer.getChannelData(0)));
    src.connect(node); node.connect(mute);
    L.node = node;
  }
  $('micButton').classList.add('is-live');
  $('micButtonLabel').textContent = 'Stop microphone';
  hint.textContent = `Listening at ${sr / 1000} kHz. Pitch frames every ${(session.hop_seconds * 1000).toFixed(0)} ms `
    + `(analysis window ${(session.frame_seconds * 1000).toFixed(0)} ms).`;
  setTimeout(() => {
    if (L.running && state.live === L && L.level === 0) {
      hint.textContent = 'No sound is arriving from the microphone. Check the input device in your system sound settings.';
      hint.classList.add('is-error');
    }
  }, 2500);
}

async function pump() {
  const L = state.live;
  if (!L || !L.running || L.inflight || L.pendingLen < 2048) return;
  const buf = new Float32Array(L.pendingLen);
  let o = 0; for (const p of L.pending) { buf.set(p, o); o += p.length; }
  L.pending = []; L.pendingLen = 0; L.inflight = true;
  try {
    const res = await api(`/api/live/${L.id}`, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: buf.buffer });
    if (state.live !== L) return;
    for (const [f0, conf] of res.estimates) {
      L.estimates += 1;
      // Each estimate covers the frame ending at this sample; plot it at the frame's centre.
      const t = L.estimates * L.hop - L.frame / 2;
      const m = f0 ? freqToMidi(f0) : null;
      L.times.push(t); L.midi.push(m); L.f0.push(f0); L.conf.push(conf);
      if (m !== null) L.hist[mod12(Math.round(m))] += 1;
    }
    // Keep only what's on screen (plus a little).
    const cut = L.times.findIndex((t) => t > L.samples / L.sampleRate - LIVE_WINDOW_S - 1);
    if (cut > 2000) for (const a of [L.times, L.midi, L.f0, L.conf]) a.splice(0, cut);
  } catch (err) {
    $('micHint').textContent = err.message; $('micHint').classList.add('is-error');
    stopMic();
    return;
  } finally {
    L.inflight = false;
  }
  pump();
}

function stopMic() {
  const L = state.live;
  if (!L || !L.running) return;
  L.running = false;
  L.stream.getTracks().forEach((t) => t.stop());
  try { L.node.disconnect(); } catch (e) { /* already gone */ }
  L.ctx.close();
  fetch(`/api/live/${L.id}/stop`, { method: 'POST' }).catch(() => {});
  $('micButton').classList.remove('is-live');
  $('micButtonLabel').textContent = 'Start microphone';
  $('micMeter').style.width = '0';
  requestDraw();
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------
function setSource(src) {
  state.source = src;
  for (const b of $('sourceTabs').querySelectorAll('button')) b.setAttribute('aria-selected', String(b.dataset.source === src));
  for (const p of document.querySelectorAll('.source-pane')) p.hidden = p.dataset.pane !== src;
  $('transport').style.visibility = src === 'file' ? 'visible' : 'hidden';
  $('viewHint').textContent = src === 'file' && state.file ? 'scroll to zoom · drag to pan · double-click to reset · click to seek'
    : src === 'mic' ? `last ${LIVE_WINDOW_S} seconds` : '';
  if (src === 'file') stopMic(); else $('player').pause();
  requestDraw();
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('dsfp-theme', theme); } catch (e) { /* private mode */ }
  readColors(); requestDraw();
}

function meterLoop() {
  const L = state.live;
  if (L && L.running) {
    $('micMeter').style.width = `${Math.min(100, (100 * (20 * Math.log10(Math.max(L.level, 1e-5)) + 60)) / 60)}%`;
    L.level *= 0.9;
  }
  setTimeout(meterLoop, 50);
}

async function init() {
  readColors();
  $('themeToggle').addEventListener('click', () => setTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'));
  $('sourceTabs').addEventListener('click', (e) => { const b = e.target.closest('button[data-source]'); if (b) setSource(b.dataset.source); });
  $('fileInput').addEventListener('change', (e) => { if (e.target.files[0]) loadFile(e.target.files[0]); e.target.value = ''; });
  const dz = $('dropzone');
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('is-over'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('is-over'));
  dz.addEventListener('drop', (e) => { e.preventDefault(); dz.classList.remove('is-over'); if (e.dataTransfer.files[0]) loadFile(e.dataTransfer.files[0]); });
  document.addEventListener('dragover', (e) => e.preventDefault());
  document.addEventListener('drop', (e) => { e.preventDefault(); if (e.dataTransfer.files[0]) loadFile(e.dataTransfer.files[0]); });
  $('micButton').addEventListener('click', () => (state.live && state.live.running ? stopMic() : startMic()));
  $('playButton').addEventListener('click', togglePlay);
  const player = $('player');
  player.addEventListener('play', () => { state.playing = true; $('playButton').classList.add('is-playing'); });
  player.addEventListener('pause', () => { state.playing = false; $('playButton').classList.remove('is-playing'); requestDraw(); });
  player.addEventListener('timeupdate', () => {
    // Keep the playhead on screen while zoomed in.
    const v = state.view;
    if (state.playing && v && (player.currentTime > v.t1 || player.currentTime < v.t0)) {
      const span = v.t1 - v.t0, t0 = Math.max(0, Math.min(state.file.duration - span, player.currentTime - span * 0.1));
      state.view = { t0, t1: t0 + span };
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.code === 'Space' && !['INPUT', 'SELECT', 'BUTTON'].includes(document.activeElement.tagName)) { e.preventDefault(); togglePlay(); }
  });
  attachViewControls($('waveCanvas'), 0);
  attachViewControls($('rollCanvas'), GUTTER);
  new ResizeObserver(requestDraw).observe(document.querySelector('.main'));

  try {
    const [theory, settings] = await Promise.all([api('/api/theory'), api('/api/settings')]);
    state.theory = theory; state.settings = settings;
    buildSettingsUI(); syncSettingsUI();
    $('serverStatus').textContent = 'Connected to the local server · pitch by dsfp_tune YIN';
  } catch (err) {
    $('serverStatus').textContent = `Can't reach the local server (${err.message}). Start it with: python tune.py web`;
    $('serverStatus').classList.add('is-error');
  }
  setSource('file');
  requestDraw();
  loop();
  meterLoop();
}

init();
