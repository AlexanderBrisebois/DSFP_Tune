// DSFP Tune dashboard.
//
// The local Python server does the pitch detection (dsfp_tune YIN) and keeps the snap
// settings; this page decodes audio for the waveform and playback, streams microphone
// samples to the server, and draws everything on canvases.
'use strict';

const $ = (id) => document.getElementById(id);
const LIVE_WINDOW_S = 8;          // seconds of history shown while the mic is on
const HOLD_S = 0.15;              // keep showing the last pitch this long after it drops out
const MAX_RECORDING_S = 300;      // keep at most this much of a microphone take for playback/autotune
const MIN_RECORDING_S = 0.3;

// Advanced correction settings (PitchCorrector arguments, sent to /api/autotune). Slider values are in
// the units shown; `div` converts to the API's units (cents -> semitones, % -> 0..1).
const ADVANCED = [
  { id: 'retune_ms', label: 'Attack (retune speed)', min: 0, max: 200, step: 5, def: 25, unit: 'ms',
    help: 'How fast the voice is pulled onto the note. 0 = instant and robotic; 100+ = gentle.' },
  { id: 'release_ms', label: 'Release', min: 0, max: 500, step: 10, def: 25, unit: 'ms',
    help: 'How fast the correction lets go when a note ends or its pitch is lost.' },
  { id: 'amount', label: 'Correction amount', min: 0, max: 100, step: 5, def: 100, unit: '%', div: 100,
    help: 'How far notes are pulled: 100% lands exactly on the note, 50% goes halfway.' },
  { id: 'hysteresis', label: 'Note stickiness', min: 0, max: 50, step: 5, def: 15, unit: '¢', div: 100,
    help: 'How far past the halfway point between two notes before the target switches.' },
  { id: 'note_window_ms', label: 'Note decision window', min: 0, max: 400, step: 10, def: 120, unit: 'ms',
    help: "The pitch is averaged over this long to choose the note, so vibrato doesn't flip notes." },
  { id: 'note_jump', label: 'Instant note change', min: 25, max: 200, step: 5, def: 75, unit: '¢', div: 100,
    help: 'A jump bigger than this switches to the new note right away.' },
  { id: 'hold_ms', label: 'Hold through dropouts', min: 0, max: 300, step: 10, def: 60, unit: 'ms',
    help: 'Keeps correcting through short gaps in pitch detection inside a note.' },
  { id: 'max_correction', label: 'Maximum correction', min: 50, max: 1200, step: 50, def: 200, unit: '¢', div: 100,
    help: 'Never shifts further than this, so pitch-detection errors are left alone.' },
];
const ADVANCED_STORE = 'dsfp-advanced';

const state = {
  theory: null,
  settings: { mode: 'chromatic', tonic: 0, scale: 'major', harmony: 0, pitch_method: 'yin' },
  source: 'file',
  file: null,      // { name, duration, sampleRate, samples, peaks, track, midi }
  live: null,      // see startMic()
  recording: null, // last microphone take: see finishRecording()
  advanced: loadAdvanced(),  // see ADVANCED; kept in this browser's localStorage
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
  const harmony = $('harmonyRange');
  harmony.addEventListener('input', () => { $('harmonyValue').textContent = harmonyLabel(+harmony.value / 100); });
  harmony.addEventListener('change', () => { state.settings.harmony = +harmony.value / 100; settingsChanged(); });
  $('pitchMethod').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-method]');
    if (!b || b.dataset.method === state.settings.pitch_method) return;
    state.settings.pitch_method = b.dataset.method;
    settingsChanged();
    pitchMethodChanged();
  });
}

const PITCH_LABELS = { yin: 'YIN', fft: 'FFT peak' };
function pitchMethod() { return state.settings.pitch_method || 'yin'; }

/** New pitch detector: re-analyse the loaded file, and switch a running microphone over. */
async function pitchMethodChanged() {
  syncServerStatus();
  const F = state.file;
  if (F && F.bytes) {
    const method = pitchMethod();
    $('busy').hidden = false; $('busyText').textContent = `Detecting pitch (${PITCH_LABELS[method]})…`;
    try {
      const track = await api(`/api/analyze?pitch_method=${method}`, {
        method: 'POST', headers: { 'X-Filename': F.name, 'Content-Type': 'application/octet-stream' }, body: F.bytes,
      });
      if (state.file === F && pitchMethod() === method) {
        F.track = track;
        F.midi = track.f0.map((f) => (f ? freqToMidi(f) : null));
        F.hist = histogram(F.midi);
        F.range = fileRange(F.midi);
        $('fileMeta').textContent = fileMetaText(F.duration, track);
      }
    } catch (err) {
      $('fileMeta').textContent = `Pitch analysis failed: ${err.message}`;
    } finally {
      $('busy').hidden = true;
      requestDraw();
    }
  }
  const L = state.live;
  if (L && L.running) {
    try {
      const session = await api('/api/live/start', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sample_rate: L.sampleRate, pitch_method: pitchMethod() }),
      });
      if (state.live !== L || !L.running) { fetch(`/api/live/${session.id}/stop`, { method: 'POST' }).catch(() => {}); return; }
      const old = L.id;
      L.id = session.id; L.frame = session.frame_seconds;
      // A request to the old session may still be in flight; let it finish before forgetting it.
      setTimeout(() => fetch(`/api/live/${old}/stop`, { method: 'POST' }).catch(() => {}), 3000);
    } catch (err) {
      $('micHint').textContent = `Couldn't switch the pitch detector: ${err.message}`;
      $('micHint').classList.add('is-error');
    }
  }
}

function fileMetaText(duration, track) {
  return `${fmtTime(duration)} · ${track.sample_rate / 1000} kHz · `
    + `${Math.round(track.voiced_fraction * 100)}% pitched (${PITCH_LABELS[track.pitch_method] || 'YIN'}) · analysed in ${track.analysis_seconds.toFixed(1)} s`;
}

function syncServerStatus() {
  if ($('serverStatus').classList.contains('is-error')) return;
  $('serverStatus').textContent = `Connected to the local server · pitch by dsfp_tune ${PITCH_LABELS[pitchMethod()]}`;
}

function harmonyLabel(level) { return level > 0 ? `${Math.round(level * 100)}%` : 'Off'; }

/** Chord harmony needs a key with 7 notes (the chord is built from the key's own notes). */
function harmonyAvailable() { return state.settings.mode === 'key' && keyInfo().pitch_classes.length === 7; }

function syncHarmonyUI() {
  const s = state.settings, ok = harmonyAvailable(), names = keyInfo().note_names;
  $('harmonyRange').value = String(Math.round((s.harmony || 0) * 100));
  $('harmonyRange').disabled = !ok;
  $('harmonyValue').textContent = ok ? harmonyLabel(s.harmony || 0) : 'Off';
  $('harmonyField').classList.toggle('is-disabled', !ok);
  // Example: the chord on the 6th note of the key (C major: A -> A C E).
  $('harmonyHint').textContent = ok
    ? `Adds the key's chord on each snapped note, e.g. ${names[5]} → ${names[5]} ${names[0]} ${names[2]}.`
    : s.mode === 'key'
      ? 'Chords need a 7-note scale (major, minor or a mode).'
      : 'Choose "Nearest note in key" to add chords from the key.';
}

function syncSettingsUI() {
  const s = state.settings;
  for (const b of $('snapModes').querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.mode === s.mode));
  $('tonicSelect').value = String(s.tonic);
  $('scaleSelect').value = s.scale;
  for (const b of $('pitchMethod').querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.method === pitchMethod()));
  const keyMode = s.mode === 'key';
  $('keyFields').classList.toggle('is-disabled', !keyMode);
  const k = keyInfo();
  $('scaleChips').innerHTML = k.note_names
    .map((n, i) => `<span class="chip${i === 0 ? ' chip--tonic' : ''}" title="degree ${i + 1}">${n}</span>`).join('');
  $('scaleChips').classList.toggle('is-dim', !keyMode);
  $('scaleChips').title = keyMode ? k.name : `${k.name} (used for the in-key colouring)`;
  syncHarmonyUI();
}

function settingsChanged() {
  syncSettingsUI();
  saveSettings();
  if (state.file) state.file.hist = histogram(state.file.midi);
  if (state.recording) scheduleAutotune();
  if (state.file) scheduleFileAutotune();
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
    const L = state.live;
    let t1 = Math.max(LIVE_WINDOW_S, L.samples / L.sampleRate);
    const pt = takePlayhead();
    if (!L.running && L.viewT1) t1 = L.viewT1;
    const offScreen = pt !== null && (pt < t1 - LIVE_WINDOW_S || pt > t1);
    const userScrolled = performance.now() - (L.userScroll || 0) < 1500;
    if (pt !== null && !userScrolled && ((!recPlayer.paused && !scrub) || offScreen)) {
      // Follow the playhead: it moves until the middle, then the view scrolls under it.
      L.viewT1 = t1 = Math.min(Math.max(LIVE_WINDOW_S, pt + LIVE_WINDOW_S / 2), Math.max(LIVE_WINDOW_S, state.recording.duration));
    }
    return { t0: t1 - LIVE_WINDOW_S, t1, live: true };
  }
  if (state.source === 'file' && state.file) {
    // While playing, keep the playhead in the middle once it gets there (unless the user just scrolled).
    const v = state.view, span = v.t1 - v.t0, D = state.file.duration;
    if (state.playing && !scrub && !drag && span < D && performance.now() - (state.userScroll || 0) > 1500) {
      const t0 = Math.max(0, Math.min(D - span, $('player').currentTime - span / 2));
      if (Math.abs(t0 - v.t0) > 1e-4) state.view = { t0, t1: t0 + span };
    }
    return { ...state.view, live: false };
  }
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
    const L = state.live, sr = L.sampleRate, ring = L.ring, R = state.recording;
    const take = !L.running && R && R.sampleRate === sr ? R.samples : null;
    const startSample = view.t0 * sr, endSample = Math.min(L.samples, Math.round(view.t1 * sr));
    for (let x = 0; x < w; x++) {
      const a = Math.floor(startSample + (x / w) * LIVE_WINDOW_S * sr);
      const b = Math.min(endSample, Math.floor(startSample + ((x + 1) / w) * LIVE_WINDOW_S * sr));
      let lo = 0, hi = 0;
      if (take) {
        for (let i = Math.max(a, 0); i < Math.min(b, take.length); i++) { const v = take[i]; if (v < lo) lo = v; if (v > hi) hi = v; }
      } else {
        for (let i = Math.max(a, L.samples - ring.length, 0); i < b; i++) {
          const v = ring[i % ring.length]; if (v < lo) lo = v; if (v > hi) hi = v;
        }
      }
      if (hi > lo) ctx.fillRect(x, mid - hi * amp, 1, Math.max(1, (hi - lo) * amp));
    }
    drawPlayhead(ctx, w, h, view);
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

/** Where the recorded take is playing (seconds), or null when it isn't. */
function takePlayhead() {
  const L = state.live, R = state.recording;
  if (!L || L.running || !R) return null;
  if (recPlayer.dataset.which) return recPlayer.currentTime;
  return Number.isFinite(R.cursor) ? R.cursor : null;   // where Play will start from
}

/** Move the take's playback position (also while it plays). */
function seekTake(t) {
  const R = state.recording;
  if (!R) return;
  R.cursor = Math.max(0, Math.min(R.duration, t));
  if (recPlayer.dataset.which) recPlayer.currentTime = R.cursor;
  requestDraw();
}

function drawPlayhead(ctx, w, h, view, x0 = 0) {
  const t = view.live ? takePlayhead() : $('player').currentTime;
  if (t === null || t < view.t0 || t > view.t1) return;
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
  state.rollRange = { lo, hi };   // the spectrogram uses the same note range
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
    ctx.fillText(view.live && state.live.running ? `${(t - view.t1).toFixed(step < 1 ? 1 : 0)} s` : axisTime(t, step), x, h - 5);
  }

  // Pitch and snap target.
  const fr = rollFrames(view);
  ctx.save();
  ctx.beginPath(); ctx.rect(GUTTER + 1, 0, plotW, plotH); ctx.clip();
  const first = view.live ? Math.max(0, lowerBound(state.live.times, view.t0) - 1) : Math.max(0, Math.floor((view.t0 - state.file.track.t0) / state.file.track.hop_seconds) - 1);
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

  drawPlayhead(ctx, w, plotH, view, GUTTER);

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
function lowerBound(arr, x) {   // first index with arr[i] >= x
  let a = 0, b = arr.length;
  while (a < b) { const m = (a + b) >> 1; if (arr[m] < x) a = m + 1; else b = m; }
  return a;
}

function currentPitch() {
  if (state.source === 'mic' && state.live) {
    const L = state.live, pt = takePlayhead();
    if (pt !== null) {   // playing the take back: the pitch at the playhead
      const i = lowerBound(L.times, pt);
      for (let j = Math.min(i, L.times.length - 1); j >= 0 && L.times[j] > pt - HOLD_S; j--) {
        if (L.midi[j] !== null) return { midi: L.midi[j], f0: L.f0[j] };
      }
      return null;
    }
    const now = L.samples / L.sampleRate;
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
  drawWave(); drawRoll(); drawSpec(); drawKeys(); drawReadout(); syncTimebars();
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
  if (state.playing || !recPlayer.paused || (state.source === 'mic' && state.live && state.live.running)) drawAll();
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
  const analysis = api(`/api/analyze?pitch_method=${pitchMethod()}`, {
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
      name: file.name, bytes, url, duration: audio.duration, sampleRate: audio.sampleRate, samples,
      peaks: buildPeaks(samples), track, midi, hist: histogram(midi), range: fileRange(midi),
    };
    state.view = { t0: 0, t1: audio.duration > 30 ? 20 : audio.duration };
    specForFile(state.file);
    state.fileWhich = 'original';
    syncFileVersionUI();
    autotuneFile();
    $('fileMeta').textContent = fileMetaText(audio.duration, track);
    $('playButton').disabled = false;
    $('viewHint').textContent = 'scroll to move through time · pinch or Ctrl+scroll to zoom · drag the playhead to seek';
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
let drag = null, rollHover = null, scrub = false;
const takeShown = () => state.source === 'mic' && state.live && !state.live.running && state.recording;

function attachViewControls(canvas, x0) {
  const playheadX = () => {   // canvas x of the playhead, or null
    const v = currentView(); if (!v) return null;
    const t = v.live ? takePlayhead() : $('player').currentTime;
    if (t === null || t < v.t0 || t > v.t1) return null;
    return x0 + ((t - v.t0) / (v.t1 - v.t0)) * (canvas.clientWidth - x0);
  };
  const nearPlayhead = (e) => {
    const px = playheadX(); if (px === null) return false;
    return Math.abs(e.clientX - canvas.getBoundingClientRect().left - px) <= 7;
  };
  const seek = (e) => {
    const t = timeAt(e);
    if (takeShown()) seekTake(t); else { $('player').currentTime = t; requestDraw(); }
  };
  const timeAt = (e) => {
    const r = canvas.getBoundingClientRect(), v = currentView();
    const frac = Math.max(0, Math.min(1, (e.clientX - r.left - x0) / (r.width - x0)));
    return v.t0 + frac * (v.t1 - v.t0);
  };
  canvas.addEventListener('wheel', (e) => {
    const tl = timeline();
    if (!tl) return;
    e.preventDefault();
    const zoom = (e.ctrlKey || e.metaKey) && state.source === 'file';   // trackpad pinch arrives as Ctrl+wheel
    if (!zoom) {
      const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
      scrollTimeBy((d / canvas.clientWidth) * (tl.t1 - tl.t0));
      return;
    }
    const v = state.view, D = state.file.duration;
    {
      const t = timeAt(e), f = Math.exp(e.deltaY * 0.005);
      const span = Math.max(0.25, Math.min(D, (v.t1 - v.t0) * f));
      let t0 = t - ((t - v.t0) / (v.t1 - v.t0)) * span;
      t0 = Math.max(0, Math.min(D - span, t0));
      state.view = { t0, t1: t0 + span };
    }
    requestDraw();
  }, { passive: false });
  canvas.addEventListener('pointerdown', (e) => {
    const take = takeShown();
    if (!take && (state.source !== 'file' || !state.file)) return;
    // On a take every drag scrubs; on a file, grab the playhead to scrub or drag elsewhere to pan.
    const isScrub = take || nearPlayhead(e);
    drag = { x: e.clientX, view: state.view ? { ...state.view } : null, moved: false, canvas, scrub: isScrub };
    scrub = isScrub;
    if (isScrub) seek(e);
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointermove', (e) => {
    if (canvas === $('rollCanvas')) {
      const r = canvas.getBoundingClientRect();
      rollHover = state.file && state.source === 'file' && e.clientX - r.left > x0 ? { x: e.clientX - r.left } : null;
      requestDraw();
    }
    if (!drag) canvas.style.cursor = nearPlayhead(e) || takeShown() ? 'ew-resize' : '';
    if (!drag || drag.canvas !== canvas) return;
    if (drag.scrub) { drag.moved = true; seek(e); return; }
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
    if (drag && !drag.moved && !drag.scrub) seek(e);
    drag = null; scrub = false;
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
  stopRecordingPlayback();
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
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sample_rate: sr, pitch_method: pitchMethod() }),
    });
  } catch (err) {
    stream.getTracks().forEach((t) => t.stop()); ctx.close();
    hint.textContent = `Local server error: ${err.message}`; hint.classList.add('is-error');
    return;
  }
  const L = state.live = {
    running: true, ctx, stream, id: session.id, sampleRate: sr, hop: session.hop_seconds,
    frame: session.frame_seconds, ring: new Float32Array(Math.ceil(sr * (LIVE_WINDOW_S + 1))), samples: 0,
    pending: [], pendingLen: 0, inflight: false, estimates: 0, chunks: [], recorded: 0,
    times: [], midi: [], f0: [], conf: [], hist: new Float64Array(12), level: 0, range: null,
  };
  const src = ctx.createMediaStreamSource(stream);
  const mute = ctx.createGain(); mute.gain.value = 0; mute.connect(ctx.destination);
  const onChunk = (chunk) => {
    if (!L.running) return;
    if (L.recorded < MAX_RECORDING_S * sr) { L.chunks.push(chunk); L.recorded += chunk.length; }
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
    const cut = L.times.findIndex((t) => t > L.samples / L.sampleRate - MAX_RECORDING_S - LIVE_WINDOW_S);
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
  finishRecording(L);
  requestDraw();
}

// ---------------------------------------------------------------------------
// Last recording: play the original, or the autotuned version from the server
// ---------------------------------------------------------------------------
const recPlayer = new Audio();

function finishRecording(L) {
  const n = L.recorded;
  if (n < MIN_RECORDING_S * L.sampleRate) return;   // a stray click on Start/Stop: keep the previous take
  const samples = new Float32Array(n);
  let o = 0;
  for (const c of L.chunks) { samples.set(c, o); o += c.length; }
  L.chunks = [];
  discardRecording();
  const R = state.recording = {
    samples, sampleRate: L.sampleRate, duration: n / L.sampleRate,
    originalUrl: URL.createObjectURL(wavBlob(samples, L.sampleRate)), tunedUrl: null, job: 0,
    capped: L.recorded >= MAX_RECORDING_S * L.sampleRate,
  };
  $('recording').hidden = false;
  $('recMeta').textContent = `${fmtTime(R.duration)} · ${R.sampleRate / 1000} kHz${R.capped ? ' · first 5 min' : ''}`;
  $('downloadOriginal').href = R.originalUrl;
  specForRecording(R);
  autotuneRecording();
}

function discardRecording() {
  const R = state.recording;
  if (!R) return;
  stopRecordingPlayback();
  URL.revokeObjectURL(R.originalUrl);
  if (R.tunedUrl) URL.revokeObjectURL(R.tunedUrl);
  state.recording = null;
}

function retuneMs() { return state.advanced.retune_ms; }

function setRecHint(text, kind = '') {
  const h = $('recHint');
  h.textContent = text;
  h.classList.toggle('is-error', kind === 'error');
  h.classList.toggle('is-busy', kind === 'busy');
}

let autotuneTimer = null;
function scheduleAutotune() {
  clearTimeout(autotuneTimer);
  autotuneTimer = setTimeout(autotuneRecording, 300);
}

async function autotuneRecording() {
  const R = state.recording;
  if (!R) return;
  const job = ++R.job;
  if (recPlayer.dataset.which === 'tuned') stopRecordingPlayback();
  if (R.tunedUrl) { URL.revokeObjectURL(R.tunedUrl); R.tunedUrl = null; }
  $('playTuned').disabled = true;
  $('downloadTuned').hidden = true;

  const s = state.settings;
  if (s.mode === 'off') {
    setRecHint('Snapping is off. Choose "Nearest note" or "Nearest note in key" to hear the autotuned version.');
    return;
  }
  setRecHint('Autotuning…', 'busy');
  const q = new URLSearchParams({
    sample_rate: R.sampleRate, mode: s.mode, tonic: s.tonic, scale: s.scale,
    harmony: harmonyAvailable() ? (s.harmony || 0) : 0,
    pitch_method: pitchMethod(),
    ...advancedParams(),
  });
  try {
    const res = await fetch(`/api/autotune?${q}`, {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: R.samples,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || `${res.status} ${res.statusText}`);
    }
    const summary = JSON.parse(res.headers.get('X-Autotune-Summary') || '{}');
    const blob = await res.blob();
    if (state.recording !== R || job !== R.job) return;   // superseded by a newer take or setting
    R.tunedUrl = URL.createObjectURL(blob);
    specForTuned(R, blob);
    R.summary = summary;
    $('playTuned').disabled = false;
    $('downloadTuned').href = R.tunedUrl;
    $('downloadTuned').hidden = false;
    const target = summary.key || 'the nearest notes';
    setRecHint(summary.voiced_fraction > 0
      ? `Snapped to ${target} · average shift ${Math.round(summary.mean_shift_cents)}¢ (max ${Math.round(summary.max_shift_cents)}¢) · retune ${summary.retune_ms} ms${summary.harmony > 0 ? ` · chords ${harmonyLabel(summary.harmony)}` : ''}${summary.pitch_method === 'fft' ? ' · FFT-peak pitch' : ''}${summary.advanced && summary.advanced.amount < 1 ? ` · amount ${Math.round(summary.advanced.amount * 100)}%` : ''}`
      : 'No sung pitch found in this take, so the autotuned version sounds the same.');
  } catch (err) {
    if (state.recording !== R || job !== R.job) return;
    setRecHint(`Couldn't autotune: ${err.message}`, 'error');
  }
}

function playRecording(which) {
  const R = state.recording;
  if (!R) return;
  if (recPlayer.dataset.which === which && !recPlayer.paused) { stopRecordingPlayback(); return; }
  const url = which === 'tuned' ? R.tunedUrl : R.originalUrl;
  if (!url) return;
  $('player').pause();
  let from = recPlayer.dataset.which ? recPlayer.currentTime : (R.cursor || 0);
  if (from >= R.duration - 0.05) from = 0;   // at the end: start again
  recPlayer.src = url;
  recPlayer.dataset.which = which;
  recPlayer.currentTime = from;
  recPlayer.addEventListener('loadedmetadata', () => { recPlayer.currentTime = from; }, { once: true });
  recPlayer.play().catch((err) => setRecHint(`Playback failed: ${err.message}`, 'error'));
  syncRecordingButtons();
}

function stopRecordingPlayback() {
  if (state.recording && recPlayer.dataset.which) state.recording.cursor = recPlayer.currentTime;
  recPlayer.pause();
  recPlayer.dataset.which = '';
  syncRecordingButtons();
}

function syncRecordingButtons() {
  const playing = recPlayer.paused ? '' : recPlayer.dataset.which;
  $('playOriginal').setAttribute('aria-pressed', String(playing === 'original'));
  $('playTuned').setAttribute('aria-pressed', String(playing === 'tuned'));
}

// ---------------------------------------------------------------------------
// Advanced correction settings (Correction card > Advanced tab)
// ---------------------------------------------------------------------------
function loadAdvanced() {
  const a = Object.fromEntries(ADVANCED.map((k) => [k.id, k.def]));
  try {
    const saved = JSON.parse(localStorage.getItem(ADVANCED_STORE) || '{}');
    for (const k of ADVANCED) {
      const v = Number(saved[k.id]);
      if (saved[k.id] != null && Number.isFinite(v) && v >= k.min && v <= k.max) a[k.id] = v;
    }
  } catch (e) { /* storage unavailable: use the defaults */ }
  return a;
}

function saveAdvanced() {
  try { localStorage.setItem(ADVANCED_STORE, JSON.stringify(state.advanced)); } catch (e) { /* private mode */ }
}

const knobText = (k, v) => (k.unit === 'ms' ? `${v} ms` : `${v}${k.unit}`);

function buildAdvancedUI() {
  $('advancedFields').innerHTML = ADVANCED.map((k) => `
    <div class="knob" data-knob="${k.id}">
      <label class="field-label" for="adv-${k.id}">${k.label}<output id="adv-${k.id}-value" for="adv-${k.id}"></output></label>
      <input type="range" id="adv-${k.id}" min="${k.min}" max="${k.max}" step="${k.step}">
      <p class="knob__help">${k.help}</p>
    </div>`).join('');
  for (const k of ADVANCED) {
    const input = $(`adv-${k.id}`);
    input.addEventListener('input', () => setAdvanced(k.id, +input.value, false));
    input.addEventListener('change', () => setAdvanced(k.id, +input.value, true));
  }
  $('advancedReset').addEventListener('click', () => {
    for (const k of ADVANCED) state.advanced[k.id] = k.def;
    saveAdvanced();
    syncAdvancedUI();
    if (state.recording) scheduleAutotune();
    if (state.file) scheduleFileAutotune();
  });
  $('settingsTabs').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-tab]');
    if (b) setSettingsTab(b.dataset.tab);
  });
  syncAdvancedUI();
}

function setSettingsTab(tab) {
  for (const b of $('settingsTabs').querySelectorAll('button')) b.setAttribute('aria-selected', String(b.dataset.tab === tab));
  for (const p of document.querySelectorAll('.settings-pane')) p.hidden = p.dataset.pane !== tab;
}

/** Change one setting; `commit` (slider released) saves it and re-processes the last take. */
function setAdvanced(id, value, commit) {
  state.advanced[id] = value;
  syncAdvancedUI();
  if (commit) {
    saveAdvanced();
    if (state.recording) scheduleAutotune();
    if (state.file) scheduleFileAutotune();
  }
}

function syncAdvancedUI() {
  for (const k of ADVANCED) {
    const v = state.advanced[k.id], input = $(`adv-${k.id}`);
    if (+input.value !== v) input.value = String(v);
    $(`adv-${k.id}-value`).textContent = knobText(k, v);
    input.closest('.knob').classList.toggle('is-changed', v !== k.def);
  }
  $('retuneRange').value = String(state.advanced.retune_ms);
  $('retuneValue').textContent = `${state.advanced.retune_ms} ms`;
}

/** The advanced settings as /api/autotune query parameters, in the API's units. */
function advancedParams() {
  return Object.fromEntries(ADVANCED.map((k) => [k.id, state.advanced[k.id] / (k.div || 1)]));
}

/** 16-bit PCM WAV, so the original take can be played and downloaded. */
function wavBlob(samples, sampleRate) {
  const n = samples.length, buf = new ArrayBuffer(44 + 2 * n), v = new DataView(buf);
  const str = (o, t) => { for (let i = 0; i < t.length; i++) v.setUint8(o + i, t.charCodeAt(i)); };
  str(0, 'RIFF'); v.setUint32(4, 36 + 2 * n, true); str(8, 'WAVE');
  str(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sampleRate, true); v.setUint32(28, 2 * sampleRate, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, 'data'); v.setUint32(40, 2 * n, true);
  for (let i = 0; i < n; i++) v.setInt16(44 + 2 * i, Math.round(Math.max(-1, Math.min(1, samples[i])) * 32767), true);
  return new Blob([buf], { type: 'audio/wav' });
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------
function setSource(src) {
  state.source = src;
  for (const b of $('sourceTabs').querySelectorAll('button')) b.setAttribute('aria-selected', String(b.dataset.source === src));
  for (const p of document.querySelectorAll('.source-pane')) p.hidden = p.dataset.pane !== src;
  $('transport').style.visibility = src === 'file' ? 'visible' : 'hidden';
  $('viewHint').textContent = src === 'file' && state.file ? 'scroll to move through time · pinch or Ctrl+scroll to zoom · drag the playhead to seek'
    : src === 'mic' ? `last ${LIVE_WINDOW_S} seconds` : '';
  if (src === 'file') { stopMic(); stopRecordingPlayback(); } else $('player').pause();
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
  $('playOriginal').addEventListener('click', () => playRecording('original'));
  $('playTuned').addEventListener('click', () => playRecording('tuned'));
  for (const ev of ['play', 'pause', 'ended']) recPlayer.addEventListener(ev, () => { syncRecordingButtons(); requestDraw(); });
  // The recording panel's retune slider is the same setting as Advanced > Attack.
  $('retuneRange').addEventListener('input', (e) => setAdvanced('retune_ms', +e.target.value, false));
  $('retuneRange').addEventListener('change', (e) => setAdvanced('retune_ms', +e.target.value, true));
  buildAdvancedUI();
  const player = $('player');
  player.addEventListener('play', () => { state.playing = true; $('playButton').classList.add('is-playing'); stopRecordingPlayback(); });
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
  attachViewControls($('specCanvas'), GUTTER);
  initSpec();
  initTimebars();
  initFileTune();
  new ResizeObserver(requestDraw).observe(document.querySelector('.main'));

  try {
    const [theory, settings] = await Promise.all([api('/api/theory'), api('/api/settings')]);
    state.theory = theory; state.settings = settings;
    buildSettingsUI(); syncSettingsUI();
    syncServerStatus();
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
