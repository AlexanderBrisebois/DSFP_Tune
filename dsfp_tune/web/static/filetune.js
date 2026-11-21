// Autotuned version of an uploaded file: the decoded samples go to /api/autotune with the
// current snap / chord / advanced settings (same pipeline as microphone takes), and the
// Original / Autotuned switch swaps what the player plays, keeping the position.
//
// Uses globals from app.js (state, $, api helpers, harmonyAvailable, harmonyLabel,
// advancedParams, wavBlob, requestDraw) and spectrogram.js (computeSpec).
'use strict';

let fileTuneTimer = null;

function scheduleFileAutotune() {
  clearTimeout(fileTuneTimer);
  fileTuneTimer = setTimeout(autotuneFile, 300);
}

function setFileTuneHint(text, kind = '') {
  const h = $('fileTuneHint');
  h.textContent = text;
  h.classList.toggle('is-error', kind === 'error');
  h.classList.toggle('is-busy', kind === 'busy');
}

function syncFileVersionUI() {
  const F = state.file;
  $('fileTune').hidden = !F;
  if (!F) return;
  $('fileTunedButton').disabled = !F.tunedUrl;
  for (const b of $('fileVersion').querySelectorAll('button')) b.setAttribute('aria-selected', String(b.dataset.which === state.fileWhich));
  $('fileDownloadTuned').hidden = !F.tunedUrl;
  if (F.tunedUrl) {
    $('fileDownloadTuned').href = F.tunedUrl;
    $('fileDownloadTuned').download = F.name.replace(/\.[^.]+$/, '') + '_autotuned.wav';
  }
}

/** Play the original or the autotuned file, from the same position. */
function setFileVersion(which) {
  const F = state.file;
  if (!F || (which === 'tuned' && !F.tunedUrl)) return;
  const p = $('player'), t = p.currentTime, playing = !p.paused;
  const url = which === 'tuned' ? F.tunedUrl : F.url;
  state.fileWhich = which;
  if (p.src !== url) {
    p.src = url;
    p.currentTime = t;
    p.addEventListener('loadedmetadata', () => { p.currentTime = t; if (playing) p.play(); }, { once: true });
  }
  syncFileVersionUI();
  requestDraw();
}

async function autotuneFile() {
  const F = state.file;
  if (!F) return;
  const job = F.tuneJob = (F.tuneJob || 0) + 1;
  const s = state.settings;
  const dropTuned = () => {
    if (state.fileWhich === 'tuned') setFileVersion('original');
    if (F.tunedUrl) URL.revokeObjectURL(F.tunedUrl);
    F.tunedUrl = null; F.specTuned = null;
    syncFileVersionUI(); requestDraw();
  };
  if (s.mode === 'off') {
    dropTuned();
    setFileTuneHint('Snapping is off. Choose "Nearest note" or "Nearest note in key" to hear the autotuned version.');
    return;
  }
  setFileTuneHint('Autotuning…', 'busy');
  const q = new URLSearchParams({
    sample_rate: F.sampleRate, mode: s.mode, tonic: s.tonic, scale: s.scale,
    harmony: harmonyAvailable() ? (s.harmony || 0) : 0,
    pitch_method: pitchMethod(),
    ...advancedParams(),
  });
  try {
    const res = await fetch(`/api/autotune?${q}`, {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: F.samples,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || `${res.status} ${res.statusText}`);
    }
    const summary = JSON.parse(res.headers.get('X-Autotune-Summary') || '{}');
    const blob = await res.blob();
    if (state.file !== F || job !== F.tuneJob) return;   // a newer file or setting won
    const old = F.tunedUrl;
    F.tunedUrl = URL.createObjectURL(blob);
    if (state.fileWhich === 'tuned') { state.fileWhich = ''; setFileVersion('tuned'); }   // swap in the new version
    if (old) URL.revokeObjectURL(old);
    syncFileVersionUI();
    const target = summary.key || 'the nearest notes';
    setFileTuneHint(summary.voiced_fraction > 0
      ? `Snapped to ${target} · average shift ${Math.round(summary.mean_shift_cents)}¢ (max ${Math.round(summary.max_shift_cents)}¢) · retune ${summary.retune_ms} ms${summary.harmony > 0 ? ` · chords ${harmonyLabel(summary.harmony)}` : ''}${summary.pitch_method === 'fft' ? ' · FFT-peak pitch' : ''}`
      : 'No sung pitch found in this file, so the autotuned version sounds the same.');
    // Spectrogram of the autotuned version, for the heatmap's Autotuned view.
    const actx = new (window.AudioContext || window.webkitAudioContext)();
    const audio = await actx.decodeAudioData(await blob.arrayBuffer());
    actx.close();
    if (state.file !== F || job !== F.tuneJob) return;
    F.specTuned = computeSpec(audio.getChannelData(0), audio.sampleRate);
    requestDraw();
  } catch (err) {
    if (state.file !== F || job !== F.tuneJob) return;
    setFileTuneHint(`Couldn't autotune: ${err.message}`, 'error');
  }
}

function initFileTune() {
  state.fileWhich = 'original';
  $('fileVersion').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-which]');
    if (b && !b.disabled) setFileVersion(b.dataset.which);
  });
}
