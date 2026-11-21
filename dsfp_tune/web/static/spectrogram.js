// Spectrogram heatmap: time across, a fixed log-frequency axis (50 Hz - 8 kHz) up the side,
// colour = loudness in dB. The frequency axis never rescales; only time follows the view. Computed in the browser with a Hann-windowed STFT.
//
// Sources:
//   file          the decoded upload (state.file.spec)
//   live mic      columns computed from the mic ring buffer as samples arrive
//   last take     state.recording.spec.original / .tuned (the autotuned WAV from the server)
//
// Uses globals from app.js (state, $, prep, currentView, noteName, keySet, mod12, GUTTER, AXIS_H, ...).
'use strict';

const SPEC_N = 4096;              // FFT size: ~11 Hz bins at 44.1 kHz
const SPEC_HOP = 512;
const SPEC_F_MIN = 50, SPEC_F_MAX = 8000;   // fixed frequency axis, Hz
const hzToMidi = (f) => 69 + 12 * Math.log2(f / 440);
const SPEC_LO = hzToMidi(SPEC_F_MIN);       // rows are spaced evenly in log frequency
const SPEC_HI = hzToMidi(SPEC_F_MAX);
const SPEC_RPS = 4;               // rows per semitone
const SPEC_ROWS = Math.round((SPEC_HI - SPEC_LO) * SPEC_RPS);
const SPEC_DB_MIN = -85, SPEC_DB_MAX = -5;
const SPEC_MAX_COLS = 30000;      // keep the image canvas under browser limits for long files

const spec = { which: 'original', live: null };

// --- FFT (radix-2, in place) ------------------------------------------------
const fftCache = {};
function fftTables(n) {
  if (fftCache[n]) return fftCache[n];
  const rev = new Uint32Array(n), bits = Math.log2(n);
  for (let i = 0; i < n; i++) { let r = 0; for (let b = 0; b < bits; b++) r |= ((i >> b) & 1) << (bits - 1 - b); rev[i] = r; }
  const cos = new Float64Array(n / 2), sin = new Float64Array(n / 2);
  for (let i = 0; i < n / 2; i++) { cos[i] = Math.cos((2 * Math.PI * i) / n); sin[i] = -Math.sin((2 * Math.PI * i) / n); }
  const win = new Float64Array(n);
  for (let i = 0; i < n; i++) win[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / n);
  return (fftCache[n] = { rev, cos, sin, win });
}

function fft(re, im) {
  const n = re.length, { rev, cos, sin } = fftTables(n);
  for (let i = 0; i < n; i++) {
    const j = rev[i];
    if (j > i) { let t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; }
  }
  for (let size = 2; size <= n; size <<= 1) {
    const half = size >> 1, step = n / size;
    for (let s = 0; s < n; s += size) {
      for (let k = 0; k < half; k++) {
        const wr = cos[k * step], wi = sin[k * step], a = s + k, b = a + half;
        const xr = re[b] * wr - im[b] * wi, xi = re[b] * wi + im[b] * wr;
        re[b] = re[a] - xr; im[b] = im[a] - xi; re[a] += xr; im[a] += xi;
      }
    }
  }
}

// --- one STFT column -> note-aligned rows (0..255) ---------------------------
const rowMapCache = {};
function rowMap(sr) {
  if (rowMapCache[sr]) return rowMapCache[sr];
  const binHz = sr / SPEC_N, lo = new Float64Array(SPEC_ROWS), hi = new Float64Array(SPEC_ROWS);
  const f = (m) => 440 * 2 ** ((m - 69) / 12);
  for (let r = 0; r < SPEC_ROWS; r++) {
    lo[r] = f(SPEC_LO + r / SPEC_RPS) / binHz;
    hi[r] = f(SPEC_LO + (r + 1) / SPEC_RPS) / binHz;
  }
  return (rowMapCache[sr] = { lo, hi });
}

const specRe = new Float64Array(SPEC_N), specIm = new Float64Array(SPEC_N), specMag = new Float64Array(SPEC_N / 2 + 1);
/** get(i) returns sample i of the frame. Writes SPEC_ROWS bytes into out[offset..]. */
function specColumn(get, sr, out, offset) {
  const { win } = fftTables(SPEC_N), { lo, hi } = rowMap(sr);
  for (let i = 0; i < SPEC_N; i++) { specRe[i] = get(i) * win[i]; specIm[i] = 0; }
  fft(specRe, specIm);
  const norm = 4 / SPEC_N;   // full-scale sine -> 0 dB
  for (let k = 0; k <= SPEC_N / 2; k++) specMag[k] = Math.hypot(specRe[k], specIm[k]) * norm;
  for (let r = 0; r < SPEC_ROWS; r++) {
    let m;
    const a = lo[r], b = hi[r];
    if (b - a < 1) {               // low notes: several rows share a bin, interpolate
      const c = (a + b) / 2, k = Math.floor(c), t = c - k;
      m = specMag[k] * (1 - t) + specMag[Math.min(k + 1, SPEC_N / 2)] * t;
    } else {                       // high notes: a row spans several bins, take the loudest
      m = 0;
      for (let k = Math.floor(a); k <= Math.min(Math.ceil(b), SPEC_N / 2); k++) if (specMag[k] > m) m = specMag[k];
    }
    const db = 20 * Math.log10(m + 1e-12);
    out[offset + r] = Math.max(0, Math.min(255, Math.round(((db - SPEC_DB_MIN) / (SPEC_DB_MAX - SPEC_DB_MIN)) * 255)));
  }
}

// --- colour map (dark purple -> orange -> pale yellow) ---------------------
const SPEC_LUT = (() => {
  const stops = [[0, [8, 8, 20]], [0.25, [52, 16, 104]], [0.5, [140, 41, 129]], [0.7, [222, 73, 104]], [0.86, [254, 159, 109]], [1, [252, 253, 191]]];
  const lut = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const x = i / 255;
    let j = 0; while (j < stops.length - 2 && x > stops[j + 1][0]) j++;
    const [x0, c0] = stops[j], [x1, c1] = stops[j + 1], t = (x - x0) / (x1 - x0);
    for (let k = 0; k < 3; k++) lut[i * 3 + k] = c0[k] + (c1[k] - c0[k]) * t;
  }
  return lut;
})();

function paintColumns(S, from, to) {
  // Write columns [from, to) of S.data into S.canvas (rows flipped: high notes on top).
  const w = to - from;
  if (w <= 0) return;
  const img = S.ctx.createImageData(w, SPEC_ROWS), px = img.data;
  for (let c = 0; c < w; c++) {
    const base = (from + c) * SPEC_ROWS;
    for (let r = 0; r < SPEC_ROWS; r++) {
      const v = S.data[base + r] * 3, o = ((SPEC_ROWS - 1 - r) * w + c) * 4;
      px[o] = SPEC_LUT[v]; px[o + 1] = SPEC_LUT[v + 1]; px[o + 2] = SPEC_LUT[v + 2]; px[o + 3] = 255;
    }
  }
  S.ctx.putImageData(img, from, 0);
}

function newSpec(cols, sr, hop) {
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, cols); canvas.height = SPEC_ROWS;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = 'rgb(8,8,20)'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  return { cols, sr, hop, data: new Uint8Array(Math.max(1, cols) * SPEC_ROWS), canvas, ctx, done: 0, cancelled: false };
}

/** Whole-signal spectrogram, computed in slices so the page stays responsive. */
function computeSpec(samples, sr) {
  const hop = Math.max(SPEC_HOP, Math.ceil(samples.length / SPEC_MAX_COLS));
  const cols = Math.max(1, Math.ceil(samples.length / hop));
  const S = newSpec(cols, sr, hop);
  const get = (start) => (i) => { const j = start + i - SPEC_N / 2; return j >= 0 && j < samples.length ? samples[j] : 0; };
  const slice = () => {
    if (S.cancelled) return;
    const end = Math.min(cols, S.done + 400);
    for (let c = S.done; c < end; c++) specColumn(get(c * hop), sr, S.data, c * SPEC_ROWS);
    paintColumns(S, S.done, end);
    S.done = end;
    requestDraw();
    if (end < cols) setTimeout(slice, 0);
  };
  setTimeout(slice, 0);
  return S;   // column c is centred on sample c * hop
}

// Hooks called from app.js -----------------------------------------------------
function specForFile(F) { F.spec = computeSpec(F.samples, F.sampleRate); }

function specForRecording(R) {
  R.spec = { original: computeSpec(R.samples, R.sampleRate), tuned: null };
  syncSpecToggle();
}

async function specForTuned(R, blob) {
  if (!R.spec) return;
  if (R.spec.tuned) R.spec.tuned.cancelled = true;
  R.spec.tuned = null;
  syncSpecToggle();
  try {
    const actx = new (window.AudioContext || window.webkitAudioContext)();
    const audio = await actx.decodeAudioData(await blob.arrayBuffer());
    actx.close();
    if (state.recording !== R) return;
    R.spec.tuned = computeSpec(audio.getChannelData(0), audio.sampleRate);
  } catch (err) { /* keep showing the original */ }
  syncSpecToggle();
}

// Live columns straight from the mic ring buffer.
function updateLiveSpec(L) {
  let S = spec.live;
  if (!S || S.owner !== L) {
    const cols = Math.ceil(((LIVE_WINDOW_S + 1) * L.sampleRate) / SPEC_HOP);
    S = spec.live = newSpec(cols, L.sampleRate, SPEC_HOP);
    S.owner = L; S.next = 0; S.col = 0;
  }
  const ringLen = L.ring.length;
  // Don't fall behind by more than the ring holds.
  if (L.samples - S.next > ringLen - SPEC_N) {
    const skip = Math.ceil((L.samples - S.next - (ringLen - SPEC_N)) / SPEC_HOP);
    S.next += skip * SPEC_HOP; S.col += skip;
  }
  const first = S.col;
  while (S.next + SPEC_N <= L.samples) {
    const start = S.next;
    specColumn((i) => L.ring[(start + i) % ringLen], L.sampleRate, S.data, (S.col % S.cols) * SPEC_ROWS);
    S.next += SPEC_HOP; S.col += 1;
  }
  // Paint the new columns into the ring image (may wrap).
  for (let c = first; c < S.col; ) {
    const x = c % S.cols, run = Math.min(S.col - c, S.cols - x);
    paintColumns(S, x, x + run);
    c += run;
  }
}

function syncSpecToggle() {
  const F = state.file;
  if (state.source === 'file' && F) {   // files: the heatmap follows the Original / Autotuned playback switch
    $('specToggle').hidden = false;
    $('specTuned').disabled = !F.specTuned;
    for (const b of $('specToggle').querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.which === state.fileWhich));
    return;
  }
  const R = state.recording;
  const show = state.source === 'mic' && R && R.spec && !(state.live && state.live.running);
  const box = $('specToggle');
  box.hidden = !show;
  if (!show) return;
  const tunedReady = !!R.spec.tuned;
  $('specTuned').disabled = !tunedReady;
  // Show whichever version is playing.
  const playing = !recPlayer.paused && recPlayer.dataset.which;
  if (playing && playing !== spec.which && (playing === 'original' || tunedReady)) spec.which = playing;
  if (!tunedReady && spec.which === 'tuned') spec.which = 'original';
  for (const b of box.querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.which === spec.which));
}

/** Pick what to draw for the current view. Returns { S, colAt(t) } or null. */
function specSource(view) {
  if (!view) return null;
  const L = state.live;
  if (view.live && L && L.running) {
    updateLiveSpec(L);
    const S = spec.live;
    // Column k (absolute) is centred on sample k*hop + N/2.
    return { S, ring: true, colAt: (t) => (t * S.sr - SPEC_N / 2) / S.hop };
  }
  if (view.live) {
    const R = state.recording;
    if (R && R.spec) {
      const S = (spec.which === 'tuned' && R.spec.tuned) || R.spec.original;
      return { S, colAt: (t) => (t * S.sr) / S.hop };
    }
    const S = spec.live;
    return S && S.owner === L ? { S, ring: true, colAt: (t) => (t * S.sr - SPEC_N / 2) / S.hop } : null;
  }
  const F = state.file;
  if (!F || !F.spec) return null;
  const S = (state.fileWhich === 'tuned' && F.specTuned) || F.spec;
  return { S, colAt: (t) => (t * S.sr) / S.hop };
}

function drawSpec() {
  const { ctx, w, h } = prep($('specCanvas'));
  const c = state.colors;
  const view = currentView();
  const plotH = h - AXIS_H, plotW = w - GUTTER;
  const lo = SPEC_LO, hi = SPEC_HI;   // fixed: 50 Hz .. 8 kHz
  const y = (m) => plotH - ((m - lo) / (hi - lo)) * plotH;
  const src = specSource(view);
  syncSpecToggle();

  ctx.fillStyle = 'rgb(8,8,20)';
  ctx.fillRect(GUTTER, 0, plotW, plotH);
  if (src) {
    const { S } = src;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    // The whole frequency axis, always (image row 0 = top = SPEC_F_MAX).
    const sy0 = 0, sy1 = SPEC_ROWS;
    const c0 = src.colAt(view.t0), c1 = src.colAt(view.t1);
    const X = (col) => GUTTER + ((col - c0) / (c1 - c0)) * plotW;
    const blit = (imgX0, imgX1, absCol0) => {   // image columns [imgX0, imgX1) start at absolute column absCol0
      if (imgX1 <= imgX0) return;
      ctx.drawImage(S.canvas, imgX0, sy0, imgX1 - imgX0, sy1 - sy0, X(absCol0), 0, X(absCol0 + imgX1 - imgX0) - X(absCol0), plotH);
    };
    if (src.ring) {
      const a = Math.max(Math.floor(c0), S.col - S.cols + 1, 0), b = Math.min(Math.ceil(c1) + 1, S.col);
      for (let k = a; k < b; ) {
        const x = k % S.cols, run = Math.min(b - k, S.cols - x);
        blit(x, x + run, k);
        k += run;
      }
    } else {
      const a = Math.max(0, Math.floor(c0)), b = Math.min(S.done, Math.ceil(c1) + 1);
      blit(a, b, a);
    }
  }

  // Log-frequency axis: fixed ticks in Hz.
  ctx.font = '600 11px Inter, ui-sans-serif, system-ui, sans-serif';
  ctx.textBaseline = 'middle'; ctx.textAlign = 'right';
  for (const f of [50, 100, 200, 300, 500, 1000, 2000, 3000, 5000, 8000]) {
    const yc = y(hzToMidi(f));
    const major = [100, 1000].includes(f);
    ctx.fillStyle = major ? 'rgba(255,255,255,0.22)' : 'rgba(255,255,255,0.1)';
    if (yc > 1 && yc < plotH - 1) ctx.fillRect(GUTTER, Math.round(yc), plotW, 1);
    ctx.fillStyle = major ? c.text : c.muted;
    ctx.fillText(f >= 1000 ? `${f / 1000}k` : `${f}`, GUTTER - 8, Math.min(plotH - 6, Math.max(6, yc)));
  }
  ctx.fillStyle = c.faint; ctx.textAlign = 'left';
  ctx.fillText('Hz', 4, 8);
  ctx.fillStyle = c.border;
  ctx.fillRect(GUTTER, plotH, plotW, 1);

  if (!view) {
    ctx.fillStyle = 'rgba(255,255,255,0.5)'; ctx.textAlign = 'center';
    ctx.fillText('Frequency content over time will appear here', GUTTER + plotW / 2, plotH / 2);
    return;
  }

  // Time axis.
  const span = view.t1 - view.t0, TX = (t) => GUTTER + ((t - view.t0) / span) * plotW;
  const step = niceStep(span, plotW);
  ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
  for (let t = Math.ceil(view.t0 / step) * step; t <= view.t1 + 1e-9; t += step) {
    ctx.fillStyle = c.muted;
    ctx.fillText(view.live && state.live.running ? `${(t - view.t1).toFixed(step < 1 ? 1 : 0)} s` : axisTime(t, step), TX(t), h - 5);
  }

  // Detected pitch, thin, so the fundamental can be compared with the harmonics above it.
  if ($('specPitch').checked && !(view.live && state.recording && spec.which === 'tuned' && !(state.live && state.live.running))) {
    const fr = rollFrames(view);
    ctx.save();
    ctx.beginPath(); ctx.rect(GUTTER, 0, plotW, plotH); ctx.clip();
    ctx.strokeStyle = 'rgba(255,255,255,0.85)'; ctx.lineWidth = 1.2;
    ctx.beginPath();
    let pen = false;
    for (let i = 0; i < fr.n; i++) {
      const t = fr.t(i);
      if (t < view.t0 - 0.05) continue;
      if (t > view.t1 + 0.05) break;
      const m = fr.midi[i];
      if (m === null) { pen = false; continue; }
      if (!pen) { ctx.moveTo(TX(t), y(m)); pen = true; } else ctx.lineTo(TX(t), y(m));
    }
    ctx.stroke();
    ctx.restore();
  }

  drawPlayhead(ctx, w, plotH, view, GUTTER);

  const S = src && src.S;
  $('specHint').textContent = !S ? '' : (S.done < S.cols && !src.ring)
    ? `computing… ${Math.round((100 * S.done) / S.cols)}%` : '';
  $('specHint').title = S ? `${SPEC_N}-point FFT, ${(1000 * S.hop / S.sr).toFixed(0)} ms hop` : '';
}

function initSpec() {
  $('specToggle').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-which]');
    if (!b || b.disabled) return;
    if (state.source === 'file' && state.file) { setFileVersion(b.dataset.which); return; }
    spec.which = b.dataset.which;
    syncSpecToggle(); requestDraw();
  });
  $('specPitch').addEventListener('change', requestDraw);
}
